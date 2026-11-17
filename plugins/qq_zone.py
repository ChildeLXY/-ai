# -*- coding: utf-8 -*-
"""
QQ 空间（说说）
================

让大肥鱼自己发 QQ 空间说说，像别人家养的号一样。

实现方式：直接调用 NapCat 自带的 OneBot 扩展接口 `send_qzone_msg`
（NapCat 原生支持，无需任何第三方服务）：

    POST http://127.0.0.1:3000/send_qzone_msg
    { "content": "正文", "images": ["D:/xx.gif"], "ugc_right": 1, "target_uins": [] }
    -> { "status":"ok", "data": { "tid": "..." } }

设计要点：
- **完全独立**：只走 NapCat 的 HTTP 接口，不写、不改 DSH 的任何文件，
  与 DSH 的 QQ 插件共用同一个 NapCat 登录态（同一个 QQ 号），互不干扰。
- **文案自己写**：复用大肥鱼的人格设定 + 长期记忆 + 当前时间，
  让说说像它本人在空间随口说的话，而不是一段 AI 文案。
- **配图从本地图库挑**：按文案关键词去匹配那 322 张表情包（tags/emotion/名字），
  挑最贴的一张一起发；嫌不合适可以关掉配图或手选。
- **自动发有护栏**：默认「试运行」——只写不发；还有每日上限、安静时段、
  最小间隔，避免半夜刷屏或被封号。
"""

import os
import re
import json
import time
import random
import threading
import datetime
import urllib.request
import urllib.error

PLUGIN_ID = "qq_zone"
PLUGIN_NAME = "QQ 空间"
PLUGIN_ICON = "🌌"
PLUGIN_VERSION = "1.0.0"

# NapCat 的 OneBot HTTP 接口。只用它，不碰 DSH。
NAPCAT = "http://127.0.0.1:3000"

# 表情包图库（大肥鱼本地那 322 张）
STICKER_DIR = r"D:\本地部署大肥鱼环境\data\plugins\nekowhale_stickers"
STICKER_INDEX = os.path.join(STICKER_DIR, "index.json")

# 长期记忆（和桌面版共用一份）
MEMORY_FILE = r"D:\本地部署大肥鱼环境\data\memory.json"

# ugc_right: 1 所有人 / 4 好友 / 16 部分好友 / 64 仅自己 / 128 部分好友不可见
DEFAULTS = {
    "总开关": True,
    "自动发": False,        # 到点自动发说说（默认关，先手动玩）
    "试运行": True,         # 自动时只生成、记录，不真发
    "间隔分钟": 180,        # 自动发的最小间隔
    "每日上限": 3,          # 一天最多几条
    "查看权限": 1,          # 默认所有人可见
    "自动配图": True,       # 从表情包库挑一张一起发
    "安静起": 1,            # 安静时段（起 == 止 表示不启用）
    "安静止": 8,
    "风格": "",             # 额外的文风要求，比如「少用感叹号」
}

_ctx = None
_lock = threading.RLock()
_state = None
_thread = None
_stop = threading.Event()
_cfg = dict(DEFAULTS)

_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ------------------------------------------------------------------ 小工具
def _now():
    return int(time.time())


def _today():
    return datetime.datetime.now().strftime("%Y-%m-%d")


def _num():
    return datetime.datetime.now().strftime("%H:%M")


def _log(msg):
    try:
        _ctx.log(PLUGIN_ID, msg)
    except Exception:
        pass


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _path():
    return _ctx.plugin_data(PLUGIN_ID, "state.json")


def _default_state():
    return {
        "历史": [],           # 最近发的说说
        "今日": {"date": "", "n": 0},
        "下次自动At": 0,
        "上次错误": "",
    }


def _load():
    global _state
    if _state is None:
        s = _ctx.read_json(_path(), None)
        if not isinstance(s, dict):
            s = _default_state()
        d = _default_state()
        for k, v in d.items():
            s.setdefault(k, v)
        if not isinstance(s.get("历史"), list):
            s["历史"] = []
        _state = s
    return _state


def _save():
    if _state is not None:
        try:
            _ctx.write_json(_path(), _state)
        except Exception:
            pass


# ------------------------------------------------------------------ NapCat
def _api(action, params, timeout=45):
    """调 NapCat 的 OneBot 扩展接口（绕过系统代理，直连本机）。"""
    url = NAPCAT.rstrip("/") + "/" + action
    body = json.dumps(params, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    with _NO_PROXY.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _login_info():
    """NapCat 是否在线、登录的是哪个号。"""
    try:
        d = _api("get_login_info", {}, timeout=6)
        data = d.get("data") or {}
        if data.get("user_id"):
            return {"online": True, "uin": str(data.get("user_id")),
                    "nick": data.get("nickname") or ""}
        return {"online": False, "uin": "", "nick": "", "note": "未登录"}
    except Exception as e:
        return {"online": False, "uin": "", "nick": "", "note": str(e)[:120]}


# ------------------------------------------------------------ 表情包图库
_stickers = None


def _load_stickers():
    """读本地表情包索引（322 张），带缓存。"""
    global _stickers
    if _stickers is not None:
        return _stickers
    out = []
    try:
        with open(STICKER_INDEX, encoding="utf-8") as f:
            d = json.load(f)
        items = d.get("items") if isinstance(d, dict) else d
        for it in (items or []):
            h = str(it.get("hash") or "").strip()
            if not h:
                continue
            p = os.path.join(STICKER_DIR, "gif", h + ".gif")
            if not os.path.exists(p):
                continue
            out.append({
                "name": it.get("name") or "",
                "emotion": it.get("emotion") or "",
                "tags": it.get("tags") or [],
                "use": it.get("use") or "",
                "desc": it.get("desc") or "",
                "path": p,
            })
    except Exception as e:
        _log("读表情包索引失败：%r" % (e,))
    _stickers = out
    return out


# 兜底选图时优先这些情绪：适合配日常说说，不会跟正文打架。
# （生气/无语/哭/难过/拒绝/嘲讽/疲惫 这类只在正文明确命中关键词时才用）
_SAFE_EMOTIONS = ("开心", "搞怪", "兴奋", "卖萌", "喜欢", "得意",
                  "打招呼", "害羞", "赞同", "其他")

# 口语里说「困/累」时，表情包索引里写的是「疲惫」，靠这张表对上。
_SYNONYMS = {
    "疲惫": ("困", "累", "睡", "乏", "没力气", "睁不开眼", "熬夜", "打哈欠"),
    "开心": ("高兴", "快乐", "爽", "愉快", "舒服", "开心", "哈哈", "笑"),
    "难过": ("伤心", "不开心", "失落", "低落", "委屈", "难过"),
    "生气": ("生气", "气死", "火大", "愤怒", "恼火", "气人"),
    "兴奋": ("激动", "期待", "迫不及待", "兴奋", "上头"),
    "喜欢": ("喜欢", "爱", "心动", "好可爱", "稀饭"),
    "疑惑": ("疑惑", "奇怪", "纳闷", "不懂", "为什么"),
    "惊讶": ("惊讶", "震惊", "没想到", "居然", "天呐"),
    "害怕": ("害怕", "怕", "恐怖", "吓"),
}


def _pick_image(text):
    """按文案关键词去匹配一张最贴的表情包。

    命中了就用命中的那张；没命中（文案没有明显情绪词）时，只在「友好情绪」
    里随机——不然会抽出「生气」「有脏东西」配一句温馨的话，非常违和。
    """
    lib = _load_stickers()
    if not lib:
        return None
    t = str(text or "")
    # 先根据同义词表判断这句文案大致是什么情绪
    mood = set()
    for emo, words in _SYNONYMS.items():
        if any(w in t for w in words):
            mood.add(emo)

    best, best_score = [], 0
    for it in lib:
        score = 0
        for tag in it["tags"]:
            tag = str(tag)
            if len(tag) >= 2 and tag in t:
                score += 3
        if it["emotion"] and it["emotion"] in t:
            score += 4
        elif it["emotion"] in mood:
            score += 4
        if it["name"] and len(it["name"]) >= 2 and it["name"] in t:
            score += 6
        # 名字里的双字词在文案里出现只是弱信号（「东西」「不要」这种太容易误伤），
        # 单独一个最多 +1，够不上命中门槛（见下面的 3 分线）。
        for part in re.findall(r"[\u4e00-\u9fa5]{2}", it["name"] or ""):
            if part in t:
                score += 1
        if score > best_score:
            best_score, best = score, [it]
        elif score == best_score and score > 0:
            best.append(it)
    # 3 分才算真命中（一个 tag 或一个情绪），避免被单个通用双字词带偏
    if best_score >= 3:
        return random.choice(best)
    safe = [it for it in lib if it["emotion"] in _SAFE_EMOTIONS]
    return random.choice(safe or lib)


# ------------------------------------------------------------ 人格与记忆
def _persona():
    try:
        g = _ctx.setting("persona_global", "") or ""
        return str(g).strip()
    except Exception:
        return ""


def _memory_brief():
    """从长期记忆里摘一小段当背景，别太长（说说不需要长篇铺垫）。"""
    try:
        with open(MEMORY_FILE, encoding="utf-8-sig") as f:
            d = json.load(f)
    except Exception:
        return ""
    bits = []
    u = str(d.get("user") or "").strip()
    if u:
        bits.append("关于主人：" + u[:80])
    facts = d.get("facts") or []
    if isinstance(facts, list):
        for x in facts[-3:]:
            s = str(x).strip()
            if s:
                bits.append("- " + s[:60])
    return "\n".join(bits)


def _recent_texts(n=4):
    s = _load()
    out = []
    for h in s.get("历史", [])[-n:]:
        t = str(h.get("text") or "").strip()
        if t:
            out.append(t)
    return out


def _gen_text(hint=""):
    """让模型按大肥鱼的人格写一条说说正文。"""
    model = _ctx.get_model()
    persona = _persona()
    mem = _memory_brief()
    now = datetime.datetime.now()
    week = "一二三四五六日"[now.weekday()]
    recent = _recent_texts()
    style = str(_cfg.get("风格") or "").strip()

    sys = ["你在给自己发 QQ 空间说说（就是朋友圈那种，只有你自己看得到也发得出去）。"]
    if persona:
        sys.append("你的人格设定：\n" + persona[:900])
    if mem:
        sys.append("你记得的事：\n" + mem)
    sys.append(
        "写说说要求：\n"
        "1. 第一人称，像真人在自己空间随口说一句，不要播音腔、不要总结陈词\n"
        "2. 一共 20~70 字，最多三句话，可以带 1 个表情符号\n"
        "3. 不要自称 AI、不要解释你在做什么、不要加「回复：」这类前缀\n"
        "4. 不要用引号把整句包起来，直接给正文\n"
        "5. 不要写标签、不要写日期编号"
    )
    if style:
        sys.append("额外文风要求：" + style)
    if recent:
        sys.append("最近发过这些，别重复或换汤不换药：\n" + "\n".join("- " + r for r in recent))

    user = "现在是 %s 星期%s %s。" % (now.strftime("%Y年%m月%d日"), week, now.strftime("%H:%M"))
    if hint:
        user += "\n这次想说的方向：" + str(hint)
    user += "\n直接写正文。"

    msgs = [{"role": "system", "content": "\n\n".join(sys)},
            {"role": "user", "content": user}]
    txt = _ctx.ollama_chat(msgs, model, timeout=90, num_predict=300)
    txt = str(txt or "").strip()
    # 清掉模型偶尔带的包装
    txt = re.sub(r"^(回复|说说|正文)[:：]\s*", "", txt).strip()
    txt = txt.strip("“”\"' ")
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt[:300]


# ------------------------------------------------------------------ 发布
def _publish(text, images=None, ugc_right=None):
    """真正调用 NapCat 发说说。返回 {ok, tid, error, sent_images}。"""
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "error": "正文不能为空"}
    imgs = [str(x) for x in (images or []) if x]
    # 只保留真实存在的本地文件，避免 NapCat 报「图片处理失败」
    imgs = [p for p in imgs if os.path.exists(p)]
    right = int(ugc_right if ugc_right is not None else _cfg.get("查看权限", 1))
    params = {"content": text, "images": imgs, "ugc_right": right, "target_uins": []}
    try:
        d = _api("send_qzone_msg", params, timeout=90)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        return {"ok": False, "error": "HTTP %s %s" % (e.code, detail)}
    except Exception as e:
        return {"ok": False, "error": "连不上 NapCat（3000 端口）：%s" % str(e)[:150]}

    if str(d.get("status")) != "ok" and int(d.get("retcode", -1)) != 0:
        return {"ok": False, "error": str(d.get("message") or d.get("wording") or d)[:200]}

    tid = ""
    data = d.get("data")
    if isinstance(data, dict):
        tid = str(data.get("tid") or "")
    elif isinstance(data, str):
        tid = data
    return {"ok": True, "tid": tid, "sent_images": imgs}


def _record(text, images, tid, mode, ok, err=""):
    """记一笔历史（含自动与手动）。"""
    s = _load()
    item = {
        "at": _now(),
        "time": datetime.datetime.now().strftime("%m-%d %H:%M"),
        "text": text,
        "images": [os.path.basename(p) for p in (images or [])],
        "tid": tid,
        "mode": mode,          # manual / ai / auto
        "ok": bool(ok),
        "err": err[:200] if err else "",
    }
    hist = s.get("历史") or []
    hist.append(item)
    s["历史"] = hist[-60:]
    if ok:
        t = s.get("今日") or {}
        if t.get("date") != _today():
            s["今日"] = {"date": _today(), "n": 1}
        else:
            s["今日"] = {"date": _today(), "n": int(t.get("n", 0)) + 1}
    if err:
        s["上次错误"] = err[:300]
    _save()
    try:
        _ctx.push_event("qzone_post", item)
    except Exception:
        pass
    return item


def _today_count():
    s = _load()
    t = s.get("今日") or {}
    return int(t.get("n", 0)) if t.get("date") == _today() else 0


def _in_quiet(dt=None):
    dt = dt or datetime.datetime.now()
    try:
        a = int(_cfg.get("安静起", 1))
        b = int(_cfg.get("安静止", 8))
    except Exception:
        return False
    if a == b:
        return False
    h = dt.hour
    return (a <= h < b) if a < b else (h >= a or h < b)


# ------------------------------------------------------------------ 对外接口
def qq_zone_status():
    """插件页面用：登录态、配置、今日条数、历史。"""
    if not _ctx:
        return {"ok": False, "error": "插件未就绪"}
    login = _login_info()
    s = _load()
    lib = _load_stickers()
    return {
        "ok": True,
        "online": login.get("online"),
        "uin": login.get("uin", ""),
        "nick": login.get("nick", ""),
        "note": login.get("note", ""),
        "napcat": NAPCAT,
        "sticker_count": len(lib),
        "today": _today_count(),
        "daily_limit": int(_cfg.get("每日上限", 3)),
        "auto": bool(_cfg.get("自动发", False)),
        "dry_run": bool(_cfg.get("试运行", True)),
        "next_auto_at": int(s.get("下次自动At", 0) or 0),
        "history": list(reversed(s.get("历史", [])[-30:])),
        "last_error": s.get("上次错误", ""),
        "cfg": dict(_cfg),
    }


def qq_zone_cfg():
    """读配置（前端回填表单用）。"""
    return {"ok": True, "cfg": dict(_cfg)}


def qq_zone_set_cfg(kw=None):
    """存配置。前端传一个对象 {中文配置名: 值} 即可。"""
    if not _ctx:
        return {"ok": False, "error": "插件未就绪"}
    changed = {}
    items = kw if isinstance(kw, dict) else {}
    for k, v in items.items():
        if k not in DEFAULTS:
            continue
        cv = _coerce(k, v)
        if cv is None:
            continue
        _cfg[k] = cv
        changed[k] = cv
        try:
            _ctx.setting_set("qzone_" + k, cv)
        except Exception:
            pass
    # 间隔改了就把下次时间顺延，避免立刻触发
    if "间隔分钟" in changed or "自动发" in changed:
        s = _load()
        if _cfg.get("自动发"):
            s["下次自动At"] = _now() + int(_cfg.get("间隔分钟", 180)) * 60
        else:
            s["下次自动At"] = 0
        _save()
    return {"ok": True, "cfg": dict(_cfg), "changed": changed}


def qq_zone_preview(hint=""):
    """只生成文案不发布（先看一眼效果）。"""
    if not _ctx:
        return {"ok": False, "error": "插件未就绪"}
    try:
        text = _gen_text(hint)
    except Exception as e:
        return {"ok": False, "error": "生成失败：%s" % str(e)[:180]}
    img = _pick_image(text) if _cfg.get("自动配图", True) else None
    return {
        "ok": True,
        "text": text,
        "image": (img or {}).get("name", ""),
        "image_path": (img or {}).get("path", ""),
    }


def qq_zone_publish(text, image="", use_ai_image=False):
    """发一条说说。

    text         正文
    image        表情包名字（按名字指定）
    use_ai_image true 时按正文自动挑一张

    两者都不给 = 纯文字说说，不配图。
    """
    if not _ctx:
        return {"ok": False, "error": "插件未就绪"}
    imgs = []
    name = ""
    if use_ai_image:
        it = _pick_image(text)
        if it:
            imgs = [it["path"]]
            name = it["name"]
    elif image:
        for it in _load_stickers():
            if it["name"] == image:
                imgs = [it["path"]]
                name = it["name"]
                break
        if not imgs:
            return {"ok": False, "error": "找不到表情包「%s」" % image}
    r = _publish(text, imgs)
    _record(text, imgs, r.get("tid", ""), "manual", r.get("ok"), r.get("error", ""))
    out = dict(r)
    out["sticker"] = name
    return out


def qq_zone_ai_publish(hint=""):
    """让 AI 自己写一条并发出去（手动点「让大肥鱼自己发一条」）。"""
    if not _ctx:
        return {"ok": False, "error": "插件未就绪"}
    try:
        text = _gen_text(hint)
    except Exception as e:
        return {"ok": False, "error": "生成失败：%s" % str(e)[:180]}
    if not text:
        return {"ok": False, "error": "模型没写出内容，换个方向试试"}
    imgs, name = [], ""
    if _cfg.get("自动配图", True):
        it = _pick_image(text)
        if it:
            imgs = [it["path"]]
            name = it["name"]
    r = _publish(text, imgs)
    _record(text, imgs, r.get("tid", ""), "ai", r.get("ok"), r.get("error", ""))
    out = dict(r)
    out["text"] = text
    out["sticker"] = name
    return out


def qq_zone_delete(tid):
    """按 tid 删一条说说。"""
    if not _ctx:
        return {"ok": False, "error": "插件未就绪"}
    tid = str(tid or "").strip()
    if not tid:
        return {"ok": False, "error": "缺少 tid"}
    try:
        d = _api("delete_qzone_msg", {"tid": tid}, timeout=30)
    except Exception as e:
        return {"ok": False, "error": str(e)[:180]}
    ok = str(d.get("status")) == "ok"
    return {"ok": ok, "raw": d if not ok else None}


def qq_zone_stickers(kw=""):
    """给前端挑图用：返回表情包名字列表（可按关键词过滤）。"""
    kw = str(kw or "").strip()
    out = []
    for it in _load_stickers():
        blob = (it["name"] + it["emotion"] + " ".join(it["tags"])).lower()
        if kw and kw.lower() not in blob:
            continue
        out.append({"name": it["name"], "emotion": it["emotion"],
                    "path": it["path"]})
    return {"ok": True, "count": len(out), "items": out[:400]}


# ------------------------------------------------------------------ 自动发
def _auto_once():
    """定时触发一次。带全部护栏：安静时段、日上限、试运行。"""
    now = datetime.datetime.now()
    if _in_quiet(now):
        _log("安静时段，跳过自动发说说")
        return
    if _today_count() >= int(_cfg.get("每日上限", 3)):
        _log("今日已达上限（%s 条），跳过" % _cfg.get("每日上限"))
        return
    try:
        text = _gen_text("")
    except Exception as e:
        _log("自动生成文案失败：%r" % (e,))
        return
    if not text:
        return
    dry = bool(_cfg.get("试运行", True))
    if dry:
        # 试运行：只记账不发，方便你先看几轮它想发什么
        _record(text, [], "", "auto", False, "[试运行] 未实际发布")
        _log("试运行生成：%s" % text[:60])
        return
    it = _pick_image(text) if _cfg.get("自动配图", True) else None
    imgs = [it["path"]] if it else []
    r = _publish(text, imgs)
    _record(text, imgs, r.get("tid", ""), "auto", r.get("ok"), r.get("error", ""))
    _log("自动发说说%s：%s" % ("成功" if r.get("ok") else "失败(%s)" % r.get("error", ""), text[:60]))


def _loop():
    while not _stop.is_set():
        try:
            if bool(_cfg.get("自动发", False)):
                s = _load()
                nxt = int(s.get("下次自动At", 0) or 0)
                now = _now()
                if nxt <= 0:
                    s["下次自动At"] = now + int(_cfg.get("间隔分钟", 180)) * 60
                    _save()
                elif nxt <= now:
                    _auto_once()
                    s = _load()
                    s["下次自动At"] = _now() + max(10, int(_cfg.get("间隔分钟", 180))) * 60
                    _save()
        except Exception as e:
            _log("自动发循环出错：%r" % (e,))
        for _ in range(20):
            if _stop.is_set():
                return
            time.sleep(0.5)


# ------------------------------------------------------------------ 生命周期
def _coerce(key, value):
    dv = DEFAULTS.get(key)
    if dv is None:
        return None
    if isinstance(dv, bool):
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("1", "true", "yes", "on", "是")
    if isinstance(dv, int):
        try:
            return int(value)
        except Exception:
            return None
    return value


def setup(ctx):
    global _ctx, _thread
    _ctx = ctx
    for k in DEFAULTS:
        got = ctx.setting("qzone_" + k, None)
        if got is None:
            continue
        cv = _coerce(k, got)
        if cv is not None:
            _cfg[k] = cv
    _load()
    if bool(_cfg.get("总开关", True)):
        _thread = threading.Thread(target=_loop, daemon=True)
        _thread.start()
    ctx.log(PLUGIN_ID, "QQ 空间已就绪（自动发=%s，试运行=%s，图库 %d 张）"
            % (_cfg.get("自动发"), _cfg.get("试运行"), len(_load_stickers())))


def shutdown():
    _stop.set()


def apis():
    return [
        qq_zone_status,
        qq_zone_cfg,
        qq_zone_set_cfg,
        qq_zone_preview,
        qq_zone_publish,
        qq_zone_ai_publish,
        qq_zone_delete,
        qq_zone_stickers,
    ]
