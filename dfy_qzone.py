"""QQ 空间（说说）发布模块。

为什么单独一个文件：QQ 空间没有 OneBot 标准接口，只能走 Qzone 的 Web 协议
——用 NapCat 已经登录的 cookie（带 skey）算 g_tk，再调 emotion_cgi_publish_v6。
把它隔离在这里，qqbot.py / 桥接那边不用管这套 HTTP 细节。

对外提供：
    status()            状态（含今日计数、下次自动时间、历史）
    publish(text, ...)  发一条
    delete(tid)         删一条
    ai_write(hint)      让模型写一条
    start_scheduler()   启动定时自动发线程
"""

import json
import os
import random
import re
import threading
import time
import urllib.parse
import urllib.request

LOG_PATH = r"D:\大肥鱼缓存\qzone.log"
HIST_PATH = r"D:\大肥鱼缓存\qzone_hist.json"
STATE_PATH = r"D:\大肥鱼缓存\qzone_state.json"
SETTINGS = r"D:\大肥鱼AI\ds图片\settings.json"
STICKER_INDEX = r"D:\本地部署大肥鱼环境\data\plugins\nekowhale_stickers\index.json"
GIF_DIR = r"D:\本地部署大肥鱼环境\data\plugins\nekowhale_stickers\gif"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

_lock = threading.Lock()
_sched_started = False
_last_error = ""
_next_auto_at = 0


# ---------------- 基础设施 ----------------
def log(msg, level="INFO"):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s][%s] %s\n" % (time.strftime("%H:%M:%S"), level, msg))
    except Exception:
        pass


def _settings():
    try:
        return json.load(open(SETTINGS, encoding="utf-8-sig"))
    except Exception:
        return {}


def _cfg():
    d = _settings()
    return {
        "自动发": str(d.get("qzone_自动发", "0")) in ("1", "True", "true", "on"),
        "试运行": str(d.get("qzone_试运行", "1")) in ("1", "True", "true", "on"),
        "自动配图": str(d.get("qzone_自动配图", "1")) in ("1", "True", "true", "on"),
        "间隔分钟": int(d.get("qzone_间隔分钟", 180) or 180),
        "每日上限": int(d.get("qzone_每日上限", 3) or 3),
        "查看权限": int(d.get("qzone_查看权限", 1) or 1),
        "安静起": int(d.get("qzone_安静起", 1) or 0),
        "安静止": int(d.get("qzone_安静止", 8) or 0),
        "风格": str(d.get("qzone_风格", "") or ""),
    }


def _state():
    try:
        return json.load(open(STATE_PATH, encoding="utf-8"))
    except Exception:
        return {"day": "", "count": 0, "last_at": 0}


def _save_state(st):
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        json.dump(st, open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def _hist():
    try:
        return json.load(open(HIST_PATH, encoding="utf-8"))
    except Exception:
        return []


def _add_hist(item):
    try:
        h = _hist()
        h.insert(0, item)
        json.dump(h[:80], open(HIST_PATH, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception as e:
        log("写历史失败：%r" % e, "WARN")


def _today():
    return time.strftime("%Y-%m-%d")


# ---------------- NapCat / Qzone ----------------
def _api_base():
    d = _settings()
    # NapCat 的 HTTP API 端口（默认 3000），跟上报端口 6700 不是一回事
    port = int(d.get("qq_api_port", 3000) or 3000)
    return "http://127.0.0.1:%d" % port


_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _napcat(action, params=None, timeout=15):
    req = urllib.request.Request(
        _api_base() + "/" + action,
        data=json.dumps(params or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with _NO_PROXY.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _login_info():
    try:
        r = _napcat("get_login_info")
        d = r.get("data") or {}
        if d.get("user_id"):
            return {"uin": str(d["user_id"]), "nick": str(d.get("nickname") or "")}
    except Exception as e:
        log("取登录信息失败：%r" % e, "WARN")
    return {}


def _cookies():
    """拿 qzone 域名的 cookie 串。NapCat 的 get_cookies 直接给现成的。

    实测不同版本 NapCat 对 domain 的响应不一样：
      · "user.qzone.qq.com" → 有时直接超时
      · {}                  → retcode 400，data 是 null
      · "qzone.qq.com"      → 正常返回 {cookies, bkn}
    所以按可用性逐个试，拿到哪个用哪个（三个都带 skey/uin，够算 g_tk 和发布）。
    """
    for dom in ("qzone.qq.com", "user.qzone.qq.com", ""):
        # 客户端取 cookie 偶尔要 19.5 秒（NapCat 日志里成片的 ETIMEDOUT），
        # 15 秒就太紧：超时一次整条发布链就断了，所以放宽到 28 秒并重试一次
        r = None
        for attempt in (1, 2):
            try:
                r = _napcat("get_cookies", {"domain": dom} if dom else {}, timeout=28)
                break
            except Exception as e:
                log("get_cookies(%s) 第%d次失败：%r" % (dom or "{}", attempt, e), "WARN")
        if r is None:
            continue
        d = r.get("data")
        if isinstance(d, str) and d.strip():
            return d
        if isinstance(d, list):
            ck = "; ".join("%s=%s" % (c.get("name"), c.get("value"))
                           for c in d if isinstance(c, dict) and c.get("name"))
            if ck:
                return ck
        if isinstance(d, dict):
            ck = str(d.get("cookies") or "")
            if ck:
                return ck
    return ""


def _skey(cookie):
    m = re.search(r"(?:^|;\s*)skey=([^;]+)", cookie or "")
    return m.group(1) if m else ""


def _gtk(skey):
    """Qzone 的 g_tk 算法（老版）：从 skey 算出的校验码。"""
    h = 5381
    for c in skey:
        h += (h << 5) + ord(c)
        h &= 0x7FFFFFFF
    return h


def _http(req, timeout=25):
    """除了 200 也接受其它状态码，返回 (code, text)。"""
    try:
        with _NO_PROXY.open(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as he:
        try:
            return he.code, he.read().decode("utf-8", "ignore")
        except Exception:
            return he.code, ""


def _publish_via_napcat(text, images, ugc_right):
    """走 NapCat 的 send_qzone_msg 发说说（带图优先走这条）。

    为什么：Web 图床 cgi_upload_image 要求 albumid 指向真实相册，
    而大肥鱼这个号空间里一个相册都没有（list_album 返回空），
    写死 0 就永远是 ret=-207「相册不存在」→ 配图只能降级成纯文字。
    NapCat 是 QQ 客户端本体，图片由客户端上传，不吃相册这一套。

    成功返回 {ok, tid, images}；失败返回 None，交给 Web 协议兜底。
    """
    imgs = [str(p) for p in (images or []) if p]
    try:
        r = _napcat("send_qzone_msg", {
            "content": text,
            "images": imgs,
            "ugc_right": int(ugc_right or 1),
            "target_uins": [],
        })
    except Exception as e:
        log("NapCat 发说说失败，转 Web 协议：%r" % e, "WARN")
        return None
    try:
        okay = (str(r.get("status")) == "ok") or (int(r.get("retcode", -1)) == 0)
    except Exception:
        okay = False
    if not okay:
        log("NapCat 发说说被拒：%s" % str(r.get("message") or r.get("wording") or r)[:120], "WARN")
        return None
    d = r.get("data")
    tid = str((d.get("tid") if isinstance(d, dict) else d) or "")
    log("发说说成功（NapCat 直发，图=%d）：%s" % (len(imgs), text[:30]))
    return {"ok": True, "tid": tid, "images": bool(imgs)}


def publish(text, ugc_right=1, images=None):
    """发一条说说。返回 {ok, tid, error}。"""
    global _last_error
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "内容为空"}
    # 带图优先走 NapCat 客户端直发（Web 图床要相册，这个号没有）
    if images:
        r = _publish_via_napcat(text, images, ugc_right)
        if r:
            _last_error = ""
            return r
    ck = _cookies()
    if not ck:
        _last_error = "拿不到 QQ cookie，先确认 QQ 已登录、NapCat 在跑"
        return {"ok": False, "error": _last_error}
    sk = _skey(ck)
    if not sk:
        _last_error = "cookie 里没有 skey，登录态可能失效"
        return {"ok": False, "error": _last_error}
    info = _login_info()
    uin = info.get("uin") or ""
    if not uin:
        m = re.search(r"(?:^|;\s*)uin=o?(\d+)", ck)
        uin = m.group(1) if m else ""
    if not uin:
        _last_error = "取不到 QQ 号"
        return {"ok": False, "error": _last_error}

    gtk = _gtk(sk)
    # 配图先上传，拿到图片信息再拼进正文
    pic_bo = ""
    warn = ""
    if images:
        try:
            pic_bo = _upload_images(images, ck, gtk, uin)
        except Exception as e:
            log("配图上传失败：%r" % e, "WARN")
        if not pic_bo:
            # Qzone 的图床要绑一个相册，这个号没有可用相册时整条会失败，
            # 所以配图失败就退回纯文字，别把整条说说搞丢
            warn = "配图没上传成功，已发纯文字。"
            log(warn, "WARN")

    url = ("https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/"
           "cgi-bin/emotion_cgi_publish_v6?g_tk=%d" % gtk)
    form = {
        "syn_tweet_verson": "1",
        "paramstr": "1",
        "who": "1",
        "con": text,
        "feedversion": "1",
        "ver": "1",
        "ugc_right": str(ugc_right),
        "to_sign": "0",
        "hostuin": uin,
        "code_version": "1",
        "format": "json",
        "qzreferrer": "https://user.qzone.qq.com/%s" % uin,
    }
    if pic_bo:
        form["pic_bo"] = pic_bo
    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Cookie": ck,
        "Referer": "https://user.qzone.qq.com/%s" % uin,
        "Origin": "https://user.qzone.qq.com",
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    code, body = _http(req)
    log("发布返回 code=%s body=%s" % (code, body[:300]))
    tid = ""
    try:
        j = json.loads(body)
        tid = str(((j.get("data") or {}).get("tid")) or j.get("tid") or "")
        okc = int(j.get("code", j.get("ret", -1)))
    except Exception:
        j, okc = {}, -1
    if okc == 0 or tid:
        _last_error = ""
        out = {"ok": True, "tid": tid, "raw": body[:200], "images": bool(pic_bo)}
        if warn:
            out["warn"] = warn
        return out
    msg = ""
    try:
        msg = str(j.get("message") or j.get("msg") or body[:120])
    except Exception:
        msg = body[:120]
    _last_error = "发布被拒：%s" % msg
    return {"ok": False, "error": _last_error, "raw": body[:300]}


_album_cache = {"at": 0.0, "id": ""}


def _album_id(cookie, gtk, uin):
    """取一个真实存在的相册 id。

    空间图床的 cgi_upload_image 要求 albumid 指向真实相册，以前写死 "0"
    → ret=-207「相册不存在，点击封面重选相册」→ 配图全部静默降级成纯文字。
    这里查一次相册列表并缓存 1 小时；优先「所有人可见」的相册，
    免得图片传上去了却只有自己能看见。
    """
    global _album_cache
    if _album_cache["id"] and time.time() - _album_cache["at"] < 3600:
        return _album_cache["id"]
    try:
        url = ("https://user.qzone.qq.com/proxy/domain/photo.qzone.qq.com/fcgi-bin/"
               "fcg_list_album_v3?g_tk=%d&uin=%s&hostUin=%s&mode=0&inCharset=utf-8&"
               "outCharset=utf-8&notice=0&appid=4&format=jsonp&source=system&plat=qzone&"
               "qzonetoken=&callbackFun=cb" % (gtk, uin, uin))
        req = urllib.request.Request(url, headers={
            "Cookie": cookie, "User-Agent": UA,
            "Referer": "https://user.qzone.qq.com/%s" % uin})
        code, body = _http(req, timeout=20)
        js = body[body.index("(") + 1: body.rindex(")")]
        d = json.loads(js)
        lst = ((d.get("data") or {}).get("albumlist") or [])
        pick = None
        for a in lst:
            if str(a.get("priv")) == "1":
                pick = a
                break
        pick = pick or (lst[0] if lst else None)
        if pick and pick.get("id"):
            _album_cache = {"at": time.time(), "id": str(pick["id"])}
            log("图床相册：%s(id=%s)" % (pick.get("name"), pick.get("id")))
            return _album_cache["id"]
        log("这个号没有可用相册，配图只能退回纯文字", "WARN")
    except Exception as e:
        log("取相册列表失败：%r" % e, "WARN")
    return ""


def _upload_images(paths, cookie, gtk, uin, albumid=None):
    """把本地图片传到 Qzone，返回 pic_bo 串（失败返回空）。

    Qzone 的图床接口是 cgi_upload_image，需要 multipart。
    返回格式各家版本略有差异，这里尽量兼容：拿到 url 后按
    {url:1,width:..,height:..} 拼成 pic_bo。
    albumid 不给就自动查一个真实相册（写死 0 会报 -207）。
    """
    aid = str(albumid or "") or _album_id(cookie, gtk, uin) or "0"
    out = []
    for p in paths[:9]:
        try:
            raw = open(p, "rb").read()
        except Exception:
            continue
        fn = os.path.basename(p)
        boundary = "----dfy" + str(random.randint(10 ** 8, 10 ** 9))
        parts = []
        def field(name, val):
            parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                          % (boundary, name, val)).encode("utf-8"))
        field("uploadtype", "1")
        field("albumid", aid)
        field("uin", uin)
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"filename\"; "
                      "filename=\"%s\"\r\nContent-Type: image/gif\r\n\r\n"
                      % (boundary, fn)).encode("utf-8"))
        parts.append(raw)
        parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
        data = b"".join(parts)
        url = ("https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image?g_tk=%d&uin=%s"
               % (gtk, uin))
        req = urllib.request.Request(url, data=data, headers={
            "Cookie": cookie,
            "Referer": "https://user.qzone.qq.com/%s" % uin,
            "User-Agent": UA,
            "Content-Type": "multipart/form-data; boundary=%s" % boundary,
        })
        code, body = _http(req, timeout=40)
        log("图床上传 code=%s body=%s" % (code, body[:200]))
        try:
            j = json.loads(body)
            d = j.get("data") or j
            u = d.get("url") or d.get("pic_url") or ""
            if u:
                out.append('{"url":"%s","width":%s,"height":%s}' % (
                    u, d.get("width", 0), d.get("height", 0)))
        except Exception:
            continue
    return "[%s]" % ",".join(out) if out else ""


def delete(tid):
    """删一条说说。"""
    if not tid:
        return {"ok": False, "error": "没有 tid"}
    ck = _cookies()
    if not ck:
        return {"ok": False, "error": "拿不到 cookie"}
    sk = _skey(ck)
    gtk = _gtk(sk)
    info = _login_info()
    uin = info.get("uin") or ""
    url = ("https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/"
           "cgi-bin/emotion_cgi_delete_v6?g_tk=%d" % gtk)
    data = urllib.parse.urlencode({"hostuin": uin, "tid": tid, "t1": "",
                                   "qzreferrer": "https://user.qzone.qq.com/%s" % uin}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Cookie": ck, "Referer": "https://user.qzone.qq.com/%s" % uin,
        "User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
    code, body = _http(req)
    log("删除返回 code=%s body=%s" % (code, body[:200]))
    # 删除接口返回的是 JSONP 包在 <script> 里，不能直接 json.loads
    m = re.search(r"callback\((\{[\s\S]*?\})\)\s*;?\s*(?:</script>|$)", body)
    payload = m.group(1) if m else body
    try:
        j = json.loads(payload)
        if int(j.get("code", j.get("ret", -1))) == 0:
            return {"ok": True}
        return {"ok": False, "error": str(j.get("message") or j.get("msg") or payload[:120])}
    except Exception:
        return {"ok": False, "error": (body[:120] or "无响应")}


# ---------------- 文案生成 ----------------
def _sticker_names(n=18):
    try:
        d = json.load(open(STICKER_INDEX, encoding="utf-8-sig"))
        items = d.get("items") or []
        names = [str(i.get("name") or "") for i in items if i.get("name")]
        random.shuffle(names)
        return names[:n]
    except Exception:
        return []


def pick_sticker(hint=""):
    """从 322 张图库里挑一张，返回 (name, path)。"""
    try:
        d = json.load(open(STICKER_INDEX, encoding="utf-8-sig"))
    except Exception:
        return "", ""
    items = [i for i in (d.get("items") or []) if i.get("name")]
    if not items:
        return "", ""
    pick = None
    hint = (hint or "").strip()
    if hint:
        for it in items:
            if hint in str(it.get("name") or "") or str(it.get("code") or "") == hint:
                pick = it
                break
    if not pick:
        pick = random.choice(items)
    fn = str(pick.get("file") or pick.get("gif") or "")
    path = ""
    if fn:
        p = fn if os.path.isabs(fn) else os.path.join(GIF_DIR, fn)
        if os.path.isfile(p):
            path = p
    if not path:
        cand = [os.path.join(GIF_DIR, x) for x in os.listdir(GIF_DIR)[:200]] \
            if os.path.isdir(GIF_DIR) else []
        cand = [c for c in cand if os.path.isfile(c)]
        path = random.choice(cand) if cand else ""
    return str(pick.get("name") or ""), path


def _moe_tune(model):
    """22G 级 MoE 的 runner 参数（与 qqbot/llm_params 保持一致）。"""
    low = str(model or "").lower()
    if "35b" in low or "30b" in low or "a3b" in low:
        return {"num_batch": 512, "num_thread": 8, "num_gpu": 99, "num_keep": 48}
    return {}


DOODLE_DIR = r"D://大肥鱼缓存//doodles"


def gen_doodle(hint="", model=None):
    """让模型亲笔画一幅简笔画：模型生成 SVG → resvg 转 PNG → 返回本地路径。

    用于「发空间想配图但没有合适的表情包」时的兜底（用户要求）。
    失败返回空串，调用方降级为纯文字。
    """
    try:
        import resvg_py
    except Exception:
        return ""
    prompt = (
        "你是一只叫大肥鱼的可爱AI，现在要给这条说说配一幅黑白简笔画。"
        "主题：%s\n"
        "要求：只输出一个完整的 <svg>…</svg> 代码，不要任何解释文字；"
        "viewBox='0 0 400 400'，白色背景，黑色线条（stroke=#333），"
        "线条圆润用 stroke-linecap='round'，图案简洁呆萌，主体大而居中，"
        "可以用简单形状拼出表情/小动物/食物，不要写文字。"
    ) % (hint or "随便画一只开心的小鱼")
    try:
        # 用 8b 画：35B 常被聊天占住 runner，排队会超时；SVG 简笔画小模型够用
        svg = _llm(prompt, model=model or "qwen3:8b", num_predict=1500)
    except Exception:
        return ""
    m = re.search(r"<svg[\s\S]*?</svg>", svg, re.I)
    if not m:
        return ""
    svg = m.group(0)
    try:
        os.makedirs(DOODLE_DIR, exist_ok=True)
        path = os.path.join(DOODLE_DIR, "doodle_%d.png" % int(time.time() * 1000))
        png = resvg_py.svg_to_bytes(svg_string=svg, width=480)
        with open(path, "wb") as f:
            f.write(bytes(png))
        return path
    except Exception:
        return ""


def _llm(prompt, model=None, timeout=300, num_predict=300):
    d = _settings()
    host = str(d.get("ollama_host") or "http://127.0.0.1:11434").rstrip("/")
    model = model or str(d.get("model") or "qwen3.6:35b")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "keep_alive": "30m",
        "options": {"temperature": 0.95, "num_predict": num_predict,
                    "num_ctx": int(d.get("ctx_size", 3072) or 3072),
                    **(_moe_tune(model))},
    }
    req = urllib.request.Request(host + "/api/chat",
                                 data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with _NO_PROXY.open(req, timeout=timeout) as r:
        out = json.loads(r.read().decode("utf-8"))
    txt = ((out.get("message") or {}).get("content") or "").strip()
    txt = re.sub(r"<think[^>]*>[\s\S]*?</think[^>]*>", "", txt).strip()
    # 有的模型（9B 蒸馏）开着思考时会把「Thinking Process: 1. **Analyze ...」
    # 整段当成正文返回，这种不能发，判空让上层跳过。
    if re.search(r"(?i)thinking\s+process|here'?s\s+(a|my|the)\s+thinking"
                 r"|分析(过程|请求)|思考过程|推理过程", txt[:200]):
        return ""
    if len(txt) > 140 and len(re.findall(r"(?m)^\s*\d+[\.、\)]\s+", txt)) >= 2:
        return ""
    return txt


def ai_write(hint="", want_image=True):
    """让模型写一条说说。返回 {ok, text, image, image_path}。"""
    cfg = _cfg()
    d = _settings()
    persona = str(d.get("persona_global") or "").strip()
    style = cfg["风格"]
    hour = time.localtime().tm_hour
    tod = ("凌晨" if hour < 5 else "早上" if hour < 9 else "上午" if hour < 12
           else "中午" if hour < 14 else "下午" if hour < 18 else "晚上" if hour < 23 else "深夜")
    names = _sticker_names(10)
    prompt = (
        "你是「大肥鱼」，一个在 QQ 上生活的 AI，现在要发一条 QQ 空间的说说。\n"
        "%s\n"
        "现在是%s（%d 点）。\n"
        "要求：\n"
        "1. 像真人发朋友圈/说说，60 字以内，口语，不要标题、不要引号、不要 hashtag；\n"
        "2. 不要提自己是 AI、不要解释、不要写“这条说说”这种元信息；\n"
        "3. 可以有一点情绪和小牢骚，具体到某个物件/场景，别空泛；\n"
        "%s"
        "%s"
        "%s\n"
        "直接输出说说正文。"
        % (("你的人设：" + persona + "\n") if persona else "",
           tod, hour,
           ("4. 文风要求：" + style + "\n") if style else "",
           ("5. 想表达的方向：" + hint + "\n") if hint else "",
           ("可选配图表情（不用写进正文）：" + "、".join(names)) if names else "")
    )
    try:
        text = _llm(prompt)
    except Exception as e:
        return {"ok": False, "error": "模型调用失败：%r" % e}
    text = text.strip().strip('"“”').replace("\n", " ").strip()
    text = re.sub(r"^(说说|正文)[：:]\s*", "", text)
    if not text:
        return {"ok": False, "error": "模型没写出内容"}
    img_name, img_path = ("", "")
    if want_image and cfg["自动配图"]:
        img_name, img_path = pick_sticker(text)
    return {"ok": True, "text": text[:200], "image": img_name, "image_path": img_path}


# ---------------- 状态 ----------------
def status():
    global _last_error, _next_auto_at
    cfg = _cfg()
    info = _login_info()
    st = _state()
    today = _today()
    count = st.get("count", 0) if st.get("day") == today else 0
    try:
        n = len(json.load(open(STICKER_INDEX, encoding="utf-8-sig")).get("items") or [])
    except Exception:
        n = 0
    return {
        "ok": True,
        "online": bool(info.get("uin")),
        "uin": info.get("uin", ""),
        "nick": info.get("nick", ""),
        "sticker_count": n,
        "today": count,
        "daily_limit": cfg["每日上限"],
        "auto": cfg["自动发"],
        "dry_run": cfg["试运行"],
        "next_auto_at": _next_auto_at,
        "last_error": _last_error,
        "history": _hist()[:20],
        "cfg": cfg,
    }


def bump_today():
    st = _state()
    if st.get("day") != _today():
        st = {"day": _today(), "count": 1, "last_at": time.time()}
    else:
        st["count"] = int(st.get("count", 0)) + 1
        st["last_at"] = time.time()
    _save_state(st)


def _in_quiet(cfg):
    a, b = cfg["安静起"], cfg["安静止"]
    if a == b:
        return False
    h = time.localtime().tm_hour
    return (a <= h < b) if a < b else (h >= a or h < b)


# ---------------- 定时器 ----------------
def start_scheduler():
    global _sched_started
    if _sched_started:
        return
    _sched_started = True
    threading.Thread(target=_loop, daemon=True, name="qzone-auto").start()
    log("空间定时器已启动")


def _loop():
    global _next_auto_at, _last_error
    time.sleep(20)          # 让主程序先起来，别抢 QQ 登录
    while True:
        try:
            cfg = _cfg()
            if not cfg["自动发"]:
                _next_auto_at = 0
                time.sleep(60)
                continue
            st = _state()
            done_today = st.get("count", 0) if st.get("day") == _today() else 0
            if done_today >= cfg["每日上限"]:
                _next_auto_at = 0
                time.sleep(300)
                continue
            if _in_quiet(cfg):
                _next_auto_at = 0
                time.sleep(300)
                continue
            wait = int(cfg["间隔分钟"] * 60) - (time.time() - float(st.get("last_at") or 0))
            if wait > 0:
                _next_auto_at = time.time() + wait
                time.sleep(min(wait, 60))
                continue
            # 到点：写一条
            r = ai_write("", want_image=cfg["自动配图"])
            if not r.get("ok"):
                _last_error = r.get("error", "")
                log("自动写说说失败：%s" % _last_error, "WARN")
                time.sleep(300)
                continue
            text = r["text"]
            imgs = [r["image_path"]] if r.get("image_path") else []
            if cfg["试运行"]:
                _add_hist({"time": time.strftime("%m-%d %H:%M"), "mode": "auto",
                           "ok": True, "text": text, "images": imgs,
                           "err": "试运行：只生成没发布", "tid": ""})
                bump_today()
                log("试运行生成：%s" % text)
                _next_auto_at = time.time() + cfg["间隔分钟"] * 60
                time.sleep(min(cfg["间隔分钟"] * 60, 60))
                continue
            pub = publish(text, ugc_right=cfg["查看权限"], images=imgs)
            bump_today()
            _add_hist({"time": time.strftime("%m-%d %H:%M"), "mode": "auto",
                       "ok": bool(pub.get("ok")), "text": text, "images": imgs,
                       "err": "" if pub.get("ok") else pub.get("error", ""),
                       "tid": pub.get("tid", "")})
            if pub.get("ok"):
                log("自动发说说成功：%s" % text)
            else:
                _last_error = pub.get("error", "")
                log("自动发说说失败：%s" % _last_error, "WARN")
            _next_auto_at = time.time() + cfg["间隔分钟"] * 60
            time.sleep(30)
        except Exception as e:
            log("定时器异常：%r" % e, "ERROR")
            time.sleep(120)
