# -*- coding: utf-8 -*-
"""
dfy_core —— 大肥鱼AI 的后端核心（无 UI 依赖）。
从原 大肥鱼AI.py 抽取全部业务函数，供 Qt 版前端调用。
原 pywebview 版的 js 接口层不再需要，这里直接被 Python 调用。

路径约定（打包成 exe 后也成立）：
- DATA_DIR  = D:\\本地部署大肥鱼环境\\data     聊天记录/长期记忆/设置（跨版本共用）
- ASSET_DIR = 打包内 ds图片（avatar.jpg 等静态素材）或源码目录 ds图片
- 旧版设置/头像在 桌面\\大肥鱼AI\\ds图片\\，首次运行自动迁移到 DATA_DIR
"""
import os
import re
import sys
import json
import time
import ctypes
import base64
import socket
import datetime
import threading
import subprocess
import urllib.parse
import urllib.request
import llm_params          # num_ctx / keep_alive 的唯一来源

FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
# 数据目录：默认沿用本机 D 盘布局，其他机器可用环境变量覆盖
DATA_DIR = os.environ.get("DFY_DATA_DIR") or r"D:\本地部署大肥鱼环境\data"
CACHE_DIR = os.environ.get("DFY_CACHE_DIR") or r"D:\大肥鱼缓存"
# 旧版素材目录：跟随源码/exe 自身位置，不写死用户目录
OLD_ASSET_DIR = os.path.join(APP_DIR, "ds图片")
# 打包后静态素材在 _MEIPASS/ds图片；开发时用源码目录
if FROZEN and getattr(sys, "_MEIPASS", None):
    ASSET_DIR = os.path.join(sys._MEIPASS, "ds图片")
else:
    ASSET_DIR = OLD_ASSET_DIR

chats_file = os.path.join(DATA_DIR, "chats.json")
memory_file = os.path.join(DATA_DIR, "memory.json")
settings_file = os.path.join(DATA_DIR, "settings.json")
user_avatar_file = os.path.join(DATA_DIR, "user_avatar.jpg")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)


def _migrate():
    """旧版（桌面 pywebview 版）的设置和用户头像迁到 DATA_DIR，只迁一次。"""
    try:
        old_settings = os.path.join(OLD_ASSET_DIR, "settings.json")
        if not os.path.exists(settings_file) and os.path.exists(old_settings):
            shutil_copy(old_settings, settings_file)
    except Exception:
        pass
    try:
        old_avatar = os.path.join(OLD_ASSET_DIR, "user_avatar.jpg")
        if not os.path.exists(user_avatar_file) and os.path.exists(old_avatar):
            shutil_copy(old_avatar, user_avatar_file)
    except Exception:
        pass


def shutil_copy(a, b):
    import shutil
    shutil.copy2(a, b)


_migrate()


def asset_path(name):
    """静态素材（avatar.jpg 等）：打包内 → 源码目录 → 数据目录。"""
    p = os.path.join(ASSET_DIR, name)
    if os.path.exists(p):
        return p
    p2 = os.path.join(DATA_DIR, name)
    return p2 if os.path.exists(p2) else p


# ============ 设置（utf-8-sig：旧文件带 BOM） ============
def load_setting(key):
    try:
        if os.path.exists(settings_file):
            with open(settings_file, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            return data.get(key, "")
    except Exception:
        pass
    return ""


def load_all_settings():
    try:
        if os.path.exists(settings_file):
            with open(settings_file, "r", encoding="utf-8-sig") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}
    return {}


def save_setting(key, value):
    try:
        data = load_all_settings()
        data[key] = value
        tmp = settings_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, settings_file)
        return "ok"
    except Exception as e:
        return str(e)


# ============ 聊天记录（合并式保存，与旧版磁盘格式完全兼容） ============
def _latest_snapshot():
    import glob
    d = os.path.dirname(chats_file)
    cands = glob.glob(os.path.join(d, "chats.snap_*.json"))
    cands.sort(reverse=True)
    return cands[0] if cands else None


def load_chats():
    try:
        with open(chats_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        pass
    try:
        bak = chats_file + ".bak"
        if os.path.exists(bak):
            with open(bak, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    try:
        sp = _latest_snapshot()
        if sp:
            with open(sp, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def save_chats(chats):
    """合并式保存（按 id upsert）；真正的删除走 delete_chat_disk。"""
    try:
        if not isinstance(chats, list):
            return
        os.makedirs(os.path.dirname(chats_file), exist_ok=True)
        existing = {}
        order = []
        try:
            if os.path.exists(chats_file):
                with open(chats_file, "r", encoding="utf-8") as f:
                    old = json.load(f)
                if isinstance(old, list):
                    for c in old:
                        cid = str(c.get("id"))
                        existing[cid] = c
                        order.append(cid)
        except Exception:
            pass
        for c in chats:
            cid = str(c.get("id"))
            existing[cid] = c
            if cid not in order:
                order.insert(0, cid)
        merged = [existing[cid] for cid in order if cid in existing]
        if len(merged) == 0:
            return
        try:
            if os.path.exists(chats_file):
                import shutil
                shutil.copy2(chats_file, chats_file + ".bak")
        except Exception:
            pass
        tmp = chats_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        os.replace(tmp, chats_file)
        try:
            import glob
            snap = os.path.join(os.path.dirname(chats_file),
                                "chats.snap_%s.json" % time.strftime("%Y%m%d"))
            with open(snap, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
            snaps = sorted(glob.glob(os.path.join(os.path.dirname(chats_file), "chats.snap_*.json")))
            for old in snaps[:-10]:
                try:
                    os.remove(old)
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass


def delete_chat_disk(chat_id):
    try:
        cid = str(chat_id)
        if not os.path.exists(chats_file):
            return "ok"
        with open(chats_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return "ok"
        data = [c for c in data if str(c.get("id")) != cid]
        tmp = chats_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, chats_file)
        return "ok"
    except Exception as e:
        return str(e)


# ============ 文件读取（附件） ============
_TEXT_EXT = (".txt", ".md", ".py", ".csv", ".json", ".log", ".xml", ".html",
             ".js", ".css", ".java", ".c", ".cpp", ".h", ".sql", ".yaml", ".yml")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")


def read_file(path):
    """读附件：文本截 8000 字；图片单独给 base64（走视觉模型）。"""
    try:
        ext = os.path.splitext(path)[1].lower()
        if ext in IMAGE_EXT:
            with open(path, "rb") as f:
                raw = f.read()
            return {"name": os.path.basename(path), "text": "",
                    "image": _image_b64(path)}
        if ext == ".pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(path)
                text = "\n".join([p.extract_text() or "" for p in reader.pages[:20]])
                return {"name": os.path.basename(path), "text": text[:8000]}
            except ImportError:
                return {"name": os.path.basename(path), "text": "[需要安装pypdf]"}
        if ext in _TEXT_EXT:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            return {"name": os.path.basename(path), "text": text[:8000]}
        return {"name": os.path.basename(path), "text": "[不支持 " + ext + "]"}
    except Exception as e:
        return {"name": os.path.basename(path), "text": "[读取失败: " + str(e) + "]"}


def _image_b64(path, max_px=1024):
    """图片缩到最长边 max_px 转 JPEG base64，省显存也更快。"""
    with open(path, "rb") as f:
        raw = f.read()
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(raw))
        im.load()
        if max(im.size) > max_px:
            r = max_px / float(max(im.size))
            im = im.resize((max(1, int(im.width * r)), max(1, int(im.height * r))), Image.LANCZOS)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return base64.b64encode(raw).decode("ascii")


def _py_exe():
    """冻结后 sys.executable 是 exe 自身，不能跑 -c 代码；找真实 python。"""
    if not FROZEN:
        return sys.executable
    for c in (r"D:\本地部署大肥鱼环境\venv312\Scripts\python.exe",
              r"D:\本地部署大肥鱼环境\venv312\Scripts\pythonw.exe"):
        if os.path.exists(c):
            return c
    return sys.executable


def run_python(code):
    try:
        proc = subprocess.run(
            [_py_exe(), "-c", code],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="ignore"
        )
        out = proc.stdout.strip()
        err = proc.stderr.strip()
        result = ""
        if out:
            result += out + "\n"
        if err:
            result += "[错误]\n" + err
        return result if result else "[执行完成，无输出]"
    except subprocess.TimeoutExpired:
        return "[超时: 执行超过30秒]"
    except Exception as e:
        return "[执行失败: " + str(e) + "]"


# ============ 联网检索 ============
_ENGINE_TIMEOUT = {"duckduckgo": 4, "bing": 10, "baidu": 10}
_ENGINE_DOWN = {}
_ENGINE_DOWN_SEC = 600

_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _engine_reachable(host, port=443, timeout=3.0):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def _proxy_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as k:
            on, _ = winreg.QueryValueEx(k, "ProxyEnable")
            srv, _ = winreg.QueryValueEx(k, "ProxyServer")
        return bool(on) and bool(srv)
    except Exception:
        return False


def web_search(query, engine="duckduckgo"):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9"
    }
    direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    proxy_ok = None

    def _fetch(url, use_proxy=False, timeout=10):
        nonlocal proxy_ok
        req = urllib.request.Request(url, headers=headers)
        if use_proxy and proxy_ok is not False:
            try:
                with urllib.request.urlopen(req, timeout=4) as r:
                    proxy_ok = True
                    return r.read().decode("utf-8", errors="ignore")
            except Exception:
                proxy_ok = False
        with direct_opener.open(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")

    def _clean(t):
        return re.sub(r"<[^>]+>", "", t or "").strip()

    def parse_engine(name, query):
        q = urllib.parse.quote(query)
        to = _ENGINE_TIMEOUT.get(name, 10)
        try:
            if name == "bing":
                html = _fetch("https://www.bing.com/search?q=" + q + "&setlang=zh-CN", timeout=to)
                blocks = re.findall(r"<h2[^>]*>\s*<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>(.*?)(?=<h2|</li>)", html, re.S)
                out = []
                for href, title, rest in blocks[:6]:
                    title = _clean(title)
                    sm = re.search(r"<p[^>]*>(.*?)</p>", rest, re.S)
                    snip = _clean(sm.group(1)) if sm else ""
                    if title and "captcha" not in title.lower():
                        out.append({"title": title, "body": snip, "url": href})
                return out
            elif name == "baidu":
                html = _fetch("https://www.baidu.com/s?wd=" + q, timeout=to)
                if "百度安全验证" in html or len(html) < 5000:
                    return []
                blocks = re.findall(r"<h3[^>]*>\s*<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>(.*?)(?=<h3|</body>)", html, re.S)
                out = []
                for href, title, rest in blocks[:6]:
                    title = _clean(title)
                    sm = re.search(r'<span[^>]*class="[^"]*content-right[^"]*"[^>]*>(.*?)</span>', rest, re.S) or re.search(r'<span[^>]*>(.*?)</span>', rest, re.S)
                    snip = _clean(sm.group(1)) if sm else ""
                    if title:
                        out.append({"title": title, "body": snip, "url": href})
                return out
            elif name == "duckduckgo":
                if not _proxy_enabled() and engine != "duckduckgo":
                    return []
                if not _proxy_enabled() and not _engine_reachable("html.duckduckgo.com", 443, 2.0):
                    return []
                html = _fetch("https://html.duckduckgo.com/html/?q=" + q, use_proxy=True, timeout=to)
                blocks = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
                return [{"title": _clean(t), "body": _clean(b), "url": h} for h, t, b in blocks[:6]]
        except Exception:
            return []
        return []

    order = [engine, "duckduckgo", "bing", "baidu"]
    tried = []
    seen = set()
    now = time.time()
    for eng in order:
        if eng in seen:
            continue
        seen.add(eng)
        if _ENGINE_DOWN.get(eng, 0) > now:
            continue
        res = parse_engine(eng, query)
        if len(res) >= 3:
            _ENGINE_DOWN.pop(eng, None)
            return res
        tried.append(eng)
    for eng in reversed(tried):
        res = parse_engine(eng, query)
        if res:
            return res
    for eng in tried:
        if eng in ("duckduckgo", "bing"):
            _ENGINE_DOWN[eng] = time.time() + _ENGINE_DOWN_SEC
    return []


PLANNER_MODEL = "qwen3:4b"

_SEARCH_PLANNER = (
    "你是分类器。判断用户这句话属于哪类，只输出一个标签，不要解释：\n"
    "闲聊 — 打招呼、问候、聊天、情感、撒娇、讲笑话、讲故事、问你是谁\n"
    "创作 — 写作、翻译、改写、起名、写代码、解释概念\n"
    "实时 — 需要最新/外部信息才能答准：新闻、天气、行情股价、汇率、比分赛程、"
    "价格、版本更新、政策法规、某产品某人某事的现状\n"
    "格式：只输出 闲聊 或 创作 或 实时"
)
_TAG_RE = re.compile(r"闲聊|创作|实时")


def _pick_planner_model(default_model):
    try:
        req = urllib.request.Request("http://localhost:11434/api/ps")
        with _LOCAL_OPENER.open(req, timeout=3) as r:
            models = json.loads(r.read().decode("utf-8")).get("models", [])
        if models:
            name = str(models[0].get("name") or "").strip()
            if name:
                return name
    except Exception:
        pass
    return default_model


def _classify_need_search(query, recent="", model=None):
    m_model = _pick_planner_model(model or PLANNER_MODEL)
    think = False if ("r1" in m_model.lower() or "qwen3" in m_model.lower()) else None
    msgs = [{"role": "system", "content": _SEARCH_PLANNER}]
    if recent and recent != query:
        msgs.append({"role": "user", "content": "（上文用户说过）" + recent})
    msgs.append({"role": "user", "content": query})
    raw = _ollama_chat_once(msgs, m_model, timeout=20,
                            num_predict=(64 if "qwen3" in m_model.lower() else 8), think=think)
    m = _TAG_RE.search(raw or "")
    return m.group(0) if m else ""


_STOPWORDS = re.compile(r"^(那么|那个|所以|然后|请问|麻烦|帮我|能不能|可不可以|我想知道|告诉我|一下|查一下|搜一下)"
                        r"|(吗|呢|吧|啊|呀|的话|怎么样|是多少|是多少了|多少)$")

_REALTIME_HINT = re.compile(
    r"搜一下|查一下|查查|帮我查|搜索|联网|新闻|最新|今天|今日|昨天|明天|现在|实时|行情|股价|汇率|天气|气温|比分|赛程|谁赢了"
    r"|多少钱|价格|报价|发布|出了|更新|版本|政策|规定|截止|目前|最近|今年|20\d\d|"
    r"谁是|现任|票房|销量|排名|榜单|热搜")

_CHITCHAT_HINT = re.compile(
    r"^(你好|您好|嗨|在吗|早安|晚安|早|晚上好|早上好)|心情|陪我|抱抱|亲亲|撒娇|想你|喜欢你"
    r"|讲个笑话|讲个故事|唱|安慰|难过|开心|无聊|吐槽|你是谁|你叫什么|你几岁|爱你"
    r"|写个|写一|帮我写|写首|写段|翻译|解释一下|怎么理解|如何理解|代码|函数|报错|算法|排序")


def _ollama_chat_once(messages, model, timeout=30, num_predict=32, think=None, include_think=True):
    # num_ctx 必须和主对话一致（走 llm_params），否则 Ollama 会为这一个请求
    # 把整个模型重载一遍，实测每次白花约 4 秒
    payload = llm_params.payload(model, messages, stream=False,
                                 num_predict=num_predict, temperature=0,
                                 think=think)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://localhost:11434/api/chat", data=data,
                                 headers={"Content-Type": "application/json"})
    with _LOCAL_OPENER.open(req, timeout=timeout) as r:
        out = json.loads(r.read().decode("utf-8"))
    msg = out.get("message") or {}
    content = msg.get("content") or ""
    # 有些模型把思考直接写进 content
    content = re.sub(r"<think>[\s\S]*?</think>", "", content).strip()
    if include_think and (msg.get("thinking") or ""):
        content = (content + "\n" + msg.get("thinking")).strip()
    return content.strip()


def _make_query(text):
    q = re.sub(r"<[^>]+>", " ", text or "")
    q = _STOPWORDS.sub("", q).strip(" ，。？！,.?!、；;：:")
    q = _STOPWORDS.sub("", q).strip(" ，。？！,.?!、；;：:")
    q = re.sub(r"\s+", " ", q).strip()
    return (q or text or "")[:60]


def repo_search(query, engine="duckduckgo", model="qwen3:4b", history=None, force=False, auto=True):
    """联网检索总入口。返回 {ok, status, query, results, injected, detail}。"""
    query = (query or "").strip()
    out = {"ok": False, "status": "off", "query": "", "results": [], "injected": "", "detail": ""}
    if not (force or auto):
        out["status"] = "off"
        out["detail"] = "联网已关闭"
        return out
    if force:
        q = query[:60] or query
        out["status"] = "forced"
    else:
        recent = ""
        try:
            for m in reversed(history or []):
                if isinstance(m, dict) and m.get("role") == "user" and m.get("content"):
                    recent = str(m["content"])[:200]
                    break
        except Exception:
            recent = ""
        rules_hit = bool(_REALTIME_HINT.search(query) or _REALTIME_HINT.search(recent))
        chit = bool(_CHITCHAT_HINT.search(query))
        if chit:
            rules_hit = False
        tag = ""
        try:
            tag = _classify_need_search(query, recent, model)
        except Exception as e:
            out["detail"] = "判断跳过：" + str(e)[:60]
        want = (tag == "实时") or (rules_hit and tag != "闲聊" and tag != "创作")
        if chit and tag != "实时":
            want = False
        if not want:
            out["status"] = "skipped"
            out["detail"] = "无需联网，直接回答" + ("（%s）" % tag if tag else "")
            return out
        q = _make_query(query)
        out["status"] = "auto"
    out["query"] = q
    try:
        res = web_search(q, engine)
    except Exception as e:
        out["status"] = "error"
        out["detail"] = "搜索出错：" + str(e)[:120]
        return out
    if not res:
        out["status"] = "error"
        out["detail"] = "搜索「%s」没有取到结果（可能是网络或代理问题）" % q
        return out
    out["ok"] = True
    out["results"] = res
    lines = ["以下是刚刚联网搜索到的资料（关键词：%s），请优先采用其中的最新信息；"
             "资料与你已有知识冲突时以资料为准；资料不足时如实说明。" % q]
    for i, r in enumerate(res, 1):
        lines.append("[%d] %s\n%s\n%s" % (i, r.get("title", ""), r.get("body", ""), r.get("url", "")))
    out["injected"] = "\n\n".join(lines)
    out["detail"] = "找到 %d 条结果" % len(res)
    return out


# ============ 流式对话 ============
def ollama_stream(messages, model, num_ctx=None, keep_alive=None, timeout=600):
    """逐行产出 {content, thinking, done}。迭代器被中途关闭即中止生成。"""
    payload = {"model": model, "messages": messages, "stream": True,
               "keep_alive": keep_alive or llm_params.KEEP_ALIVE,
               "options": {"num_ctx": num_ctx or llm_params.NUM_CTX}}
    # qwen3 / deepseek-r1 默认会先输出思维链；2026-10-28 按主人要求关掉，直接说话
    m = (model or "").lower()
    if "qwen3" in m or "r1" in m:
        payload["think"] = False
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://localhost:11434/api/chat", data=data,
                                 headers={"Content-Type": "application/json"})
    with _LOCAL_OPENER.open(req, timeout=timeout) as r:
        for raw in r:
            if raw is None:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                j = json.loads(line)
            except Exception:
                continue
            msg = j.get("message") or {}
            yield {"content": msg.get("content") or "",
                   "thinking": msg.get("thinking") or "",
                   "done": bool(j.get("done"))}


def ollama_alive():
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with _LOCAL_OPENER.open(req, timeout=4) as r:
            json.loads(r.read().decode("utf-8"))
        return True
    except Exception:
        return False


def model_loaded(model):
    try:
        req = urllib.request.Request("http://localhost:11434/api/ps")
        with _LOCAL_OPENER.open(req, timeout=4) as r:
            data = json.loads(r.read().decode("utf-8"))
        return any(str(m.get("name") or "") == model for m in data.get("models", []))
    except Exception:
        return False


def preload_model(model):
    """空转一次把模型拉进显存。"""
    try:
        _ollama_chat_once([{"role": "user", "content": "."}], model, timeout=120, num_predict=1)
    except Exception:
        pass


# ============ 知识库 ============
kb_dir = r"D:\本地部署大肥鱼环境\知识库"


def kb_search(query):
    try:
        if not os.path.exists(kb_dir):
            os.makedirs(kb_dir, exist_ok=True)
            return "[知识库文件夹已创建] " + kb_dir
        chunks = []
        for fname in os.listdir(kb_dir):
            fpath = os.path.join(kb_dir, fname)
            if not os.path.isfile(fpath):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in _TEXT_EXT:
                continue
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except Exception:
                continue
            for i in range(0, len(text), 500):
                chunks.append({"file": fname, "text": text[i:i + 600]})
        if not chunks:
            return ""
        q_words = set(query.lower().split())
        scored = [(sum(1 for w in q_words if w in c["text"].lower()), c) for c in chunks]
        scored = [(s, c) for s, c in scored if s > 0]
        scored.sort(key=lambda x: -x[0])
        if not scored:
            return ""
        out = "以下是知识库中相关资料片段：\n\n"
        for _, c in scored[:3]:
            out += "--- " + c["file"] + " ---\n" + c["text"].strip()[:500] + "\n\n"
        return out
    except Exception as e:
        return "[知识库检索失败] " + str(e)


def open_kb():
    try:
        os.makedirs(kb_dir, exist_ok=True)
        os.startfile(kb_dir)
        return "ok"
    except Exception as e:
        return str(e)


# ============ 工具 ============
def take_screenshot():
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        path = os.path.join(CACHE_DIR, "screenshot_" + str(int(time.time())) + ".png")
        img.save(path, "PNG")
        return path
    except Exception as e:
        return "[截图失败: " + str(e) + "]"


def read_clipboard():
    try:
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        u.GetClipboardData.restype = ctypes.c_void_p
        k.GlobalLock.restype = ctypes.c_void_p
        CF_UNICODETEXT = 13
        if not u.OpenClipboard(None):
            return ""
        try:
            h = u.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return ""
            p = k.GlobalLock(ctypes.c_void_p(h))
            if not p:
                return ""
            try:
                return ctypes.wstring_at(p)
            finally:
                k.GlobalUnlock(ctypes.c_void_p(h))
        finally:
            u.CloseClipboard()
    except Exception as e:
        return "[剪贴板读取失败: " + str(e) + "]"


def write_clipboard(text):
    try:
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        k.GlobalAlloc.restype = ctypes.c_void_p
        k.GlobalLock.restype = ctypes.c_void_p
        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002
        buf = ctypes.create_unicode_buffer(str(text))
        size = ctypes.sizeof(buf)
        if not u.OpenClipboard(None):
            return False
        try:
            u.EmptyClipboard()
            h = k.GlobalAlloc(GMEM_MOVEABLE, size)
            if not h:
                return False
            p = k.GlobalLock(ctypes.c_void_p(h))
            if not p:
                return False
            ctypes.memmove(p, ctypes.cast(buf, ctypes.c_void_p), size)
            k.GlobalUnlock(ctypes.c_void_p(h))
            u.SetClipboardData(CF_UNICODETEXT, ctypes.c_void_p(h))
        finally:
            u.CloseClipboard()
        return True
    except Exception:
        return False


def extract_url(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        if not url.startswith("http"):
            url = "https://" + url
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8", errors="ignore")
        html = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', html, flags=re.I)
        html = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', html, flags=re.I)
        html = re.sub(r'<nav[^>]*>[\s\S]*?</nav>', '', html, flags=re.I)
        html = re.sub(r'<footer[^>]*>[\s\S]*?</footer>', '', html, flags=re.I)
        html = re.sub(r'<header[^>]*>[\s\S]*?</header>', '', html, flags=re.I)
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text).strip()
        title = re.search(r'<title[^>]*>(.*?)</title>', html, re.S | re.I)
        title = title.group(1).strip() if title else url
        return {"title": title, "text": text[:6000]}
    except Exception as e:
        return {"title": "抓取失败", "text": str(e)}


def tts_speak(text):
    try:
        import edge_tts
        out_path = os.path.join(CACHE_DIR, "tts_" + str(int(time.time())) + ".mp3")
        import asyncio

        async def _gen():
            communicate = edge_tts.Communicate(text[:500], "zh-CN-XiaoxiaoNeural")
            await communicate.save(out_path)
        asyncio.run(_gen())
        return out_path
    except ImportError:
        return "[需要安装edge-tts]"
    except Exception as e:
        return "[语音失败: " + str(e) + "]"


def gen_title(first_msg, model="qwen3:4b"):
    try:
        # 思考型模型的思考会吃掉 num_predict 预算导致 content 为空，故不限长（温度 0、任务简单，风险低）
        out = _ollama_chat_once(
            [{"role": "user", "content": "用不超过8个字数概括这句话的主题，只输出标题，不要任何解释：" + first_msg[:100]}],
            model, timeout=90, num_predict=-1, include_think=False)
        title = out.strip().strip('"').strip()
        return title[:20] if title else first_msg[:20]
    except Exception:
        return first_msg[:20]


def open_folder(path=None):
    try:
        os.startfile(path or DATA_DIR)
        return "ok"
    except Exception as e:
        return str(e)


def save_avatar(b64):
    try:
        data = base64.b64decode(b64)
        if len(data) > 5 * 1024 * 1024:
            return "[error: file too large]"
        with open(user_avatar_file, "wb") as f:
            f.write(data)
        return user_avatar_file
    except Exception as e:
        return "[error: " + str(e) + "]"


# ============ 跨对话长期记忆 ============
def _default_memory():
    return {"user": "", "relationship": "", "facts": [], "updated": ""}


def load_memory():
    try:
        if os.path.exists(memory_file):
            with open(memory_file, "r", encoding="utf-8") as f:
                m = json.load(f)
            if isinstance(m, dict):
                m.setdefault("user", "")
                m.setdefault("relationship", "")
                m.setdefault("facts", [])
                return m
        return _default_memory()
    except Exception:
        return _default_memory()


def save_memory(mem):
    try:
        os.makedirs(os.path.dirname(memory_file), exist_ok=True)
        mem["updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        tmp = memory_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(mem, f, ensure_ascii=False, indent=2)
        os.replace(tmp, memory_file)
        try:
            _sync_pet_memory(mem)
        except Exception:
            pass
        return "ok"
    except Exception as e:
        return str(e)


def _sync_pet_memory(mem):
    pet_cfg = os.path.join(os.environ.get("APPDATA", ""), "dsh-pet-standalone", "config.json")
    if not os.path.exists(pet_cfg):
        return
    with open(pet_cfg, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    facts = (mem or {}).get("facts", []) or []
    basep = ("你是大肥鱼，由Childe制作的本地AI桌宠，运行在用户自己的电脑上。"
             "请用简短可爱的中文和用户交流，偶尔带点小情绪。不要说自己是AI，就说自己是大肥鱼。")
    if facts:
        block = "\n\n[你和主窗口共用的记忆，这些事你都记得]\n" + "\n".join("- " + s for s in facts[:20])
        basep += block + "\n（以上来自主窗口长期记忆，和聊天界面是同一个大肥鱼。）"
    cfg.setdefault("chat", {})["default_system_prompt"] = basep
    with open(pet_cfg, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def remember_fact(text):
    text = (text or "").strip()
    if not text:
        return "empty"
    m = load_memory()
    for f in m["facts"]:
        if text in f or f in text:
            return "dup"
    m["facts"].append(text[:120])
    if len(m["facts"]) > 60:
        m["facts"] = m["facts"][-60:]
    save_memory(m)
    return "ok"


def reflect_memory(conv_text, model="qwen3:4b"):
    try:
        conv_text = (conv_text or "")[:2500]
        if len(conv_text) < 20:
            return "skip"
        cur = load_memory()
        facts_now = "；".join(cur["facts"][-15:])
        prompt = (
            "你是大肥鱼的记忆整理器。从下面这段用户和AI的对话里，提炼【值得跨会话长期记住】的关于用户的事实"
            "（偏好、习惯、名字、项目、关系、在意的事）。只输出事实条目，每条一行，10~40字；"
            "已经记录过的不要重复；如果没有新事实，只输出 NONE。\n"
            "已记录：" + (facts_now or "无") + "\n\n对话：\n" + conv_text
        )
        out = _ollama_chat_once([{"role": "user", "content": prompt}], model,
                                timeout=60, num_predict=200)
        if not out or out.upper().startswith("NONE"):
            return "none"
        added = 0
        for line in out.split("\n"):
            line = re.sub(r"^[\-\*\d\.\s]+", "", line).strip()
            if not line or line.upper().startswith("NONE") or len(line) < 6:
                continue
            dup = False
            for f in cur["facts"]:
                if line in f or f in line:
                    dup = True
                    break
            if not dup:
                cur["facts"].append(line[:120])
                added += 1
        if len(cur["facts"]) > 60:
            cur["facts"] = cur["facts"][-60:]
        if added:
            save_memory(cur)
        return "added:%d" % added
    except Exception as e:
        return "err:" + str(e)


def pet_mutter(model="qwen3:4b"):
    try:
        now = datetime.datetime.now()
        h = now.hour
        if h < 6:
            tz = "凌晨"
        elif h < 11:
            tz = "早上"
        elif h < 14:
            tz = "中午"
        elif h < 18:
            tz = "下午"
        elif h < 23:
            tz = "晚上"
        else:
            tz = "深夜"
        m = load_memory()
        facts = "；".join(m["facts"][-10:])
        prompt = (
            "你是大肥鱼，一个像女儿一样住在用户电脑里的本地AI。现在是" + tz + str(h) + "点。"
            "请用一句不超过30字、自然、带点亲昵和关心的话，主动碎碎念一句"
            "（可以提醒休息、喝水、早睡、或者结合你知道的用户近况给点小建议）。"
            "只输出那句话本身，不要引号、不要解释。\n"
            "你对用户的了解：" + (facts or "你还不太了解他")
        )
        out = _ollama_chat_once([{"role": "user", "content": prompt}], model, timeout=60, num_predict=60)
        out = re.sub(r"<think>[\s\S]*?</think>", "", out).strip().strip('"').strip()
        return out[:80] if out else "……"
    except Exception:
        return ""


def gen_persona(style, model="qwen3:4b"):
    try:
        prompt = (
            "请为一个名叫「大肥鱼AI」的本地AI写一段人格设定（system prompt 风格）。"
            "开发者是 Childe，运行在用户本地电脑上。用户想要的性格关键词是：" + str(style) + "。"
            "要求：80~150字，中文，描述这个AI的性格、语气、说话习惯、与用户的关系；"
            "不要分点、不要解释，直接输出那段设定文字本身。"
        )
        out = _ollama_chat_once([{"role": "user", "content": prompt}], model, timeout=90, num_predict=250)
        out = re.sub(r"<think>[\s\S]*?</think>", "", out).strip().strip('"').strip()
        return out or "（生成失败，请手动填写）"
    except Exception as e:
        return "（生成失败：" + str(e) + "）"


def test_persona(persona, user_msg, model="qwen3:4b"):
    try:
        sys_p = ("你是大肥鱼AI，开发者 Childe，运行在用户本地电脑上。"
                 "必须严格按以下人格设定回应：\n" + (persona or "（未设置人格，正常回答）"))
        out = _ollama_chat_once([{"role": "system", "content": sys_p},
                                 {"role": "user", "content": user_msg}],
                                model, timeout=90, num_predict=150)
        out = re.sub(r"<think>[\s\S]*?</think>", "", out).strip()
        return out or "（模型没回复）"
    except Exception as e:
        return "（测试失败：" + str(e) + "）"


def get_time():
    try:
        now = datetime.datetime.now()
        h = now.hour
        if h < 6:
            tz = "凌晨"
        elif h < 11:
            tz = "早上"
        elif h < 14:
            tz = "中午"
        elif h < 18:
            tz = "下午"
        elif h < 23:
            tz = "晚上"
        else:
            tz = "深夜"
        week = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now.weekday()]
        return "%d年%d月%d日 %s %s%d点%02d分" % (now.year, now.month, now.day, week, tz, h, now.minute)
    except Exception:
        return ""


def get_env():
    try:
        import platform
        import getpass
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
            s.close()
        except Exception:
            lan_ip = "127.0.0.1"
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        return ("计算机名：" + socket.gethostname() +
                "；用户名：" + getpass.getuser() +
                "；操作系统：" + platform.system() + " " + platform.release() +
                "；内网IP：" + lan_ip +
                "；时区：中国标准时间(UTC+8)，使用简体中文；"
                "；桌面路径：" + desktop)
    except Exception:
        return ""


# ============ 环境感知 ============
_last_cpu = {"idle": 0, "total": 0}
_W32_LOCK = threading.RLock()


def _cpu_percent():
    try:
        class FT(ctypes.Structure):
            _fields_ = [("idle", ctypes.c_ulonglong), ("kernel", ctypes.c_ulonglong), ("user", ctypes.c_ulonglong)]
        ft = FT()
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(ft), ctypes.byref(ft), ctypes.byref(ft))
        idle = ft.idle
        total = ft.kernel + ft.user
        d_idle = idle - _last_cpu["idle"]
        d_total = total - _last_cpu["total"]
        _last_cpu["idle"] = idle
        _last_cpu["total"] = total
        if d_total <= 0:
            return 0
        return max(0, min(100, int(100 * (1 - d_idle / d_total))))
    except Exception:
        return -1


class _MEMSTATUSEX(ctypes.Structure):
    _fields_ = [("l", ctypes.c_ulong), ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                ("t3", ctypes.c_ulonglong), ("t4", ctypes.c_ulonglong)]


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_uint), ("dw", ctypes.c_uint)]


_MS = _MEMSTATUSEX()
_LI = _LASTINPUTINFO()


def _mem_percent():
    try:
        with _W32_LOCK:
            _MS.l = ctypes.sizeof(_MEMSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(_MS))
            return int(_MS.avail * 100 / _MS.total), int(_MS.total / (1024 ** 3))
    except Exception:
        return -1, 0


def _idle_seconds():
    try:
        with _W32_LOCK:
            _LI.cb = ctypes.sizeof(_LASTINPUTINFO)
            ctypes.windll.user32.GetLastInputInfo(ctypes.byref(_LI))
            millis = ctypes.windll.kernel32.GetTickCount64() - _LI.dw
            return int(millis / 1000)
    except Exception:
        return 0


def _active_window():
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
        return buf.value.strip()
    except Exception:
        return ""


def env_sense():
    try:
        now = time.localtime()
        hh = now.tm_hour
        weekdays = ["一", "二", "三", "四", "五", "六", "日"]
        if 0 <= hh < 6:
            circ = "深夜，该犯困了"
        elif 6 <= hh < 11:
            circ = "上午，精神好"
        elif 11 <= hh < 14:
            circ = "中午，有点困"
        elif 14 <= hh < 18:
            circ = "下午，专注时段"
        elif 18 <= hh < 23:
            circ = "晚上，放松时间"
        else:
            circ = "快半夜了"
        up_sec = ctypes.windll.kernel32.GetTickCount64() // 1000
        up_h = up_sec // 3600
        up_m = (up_sec % 3600) // 60
        idle = _idle_seconds()
        cpu = _cpu_percent()
        mem_avail, mem_total = _mem_percent()
        aw = _active_window()
        free_gb = -1
        try:
            free = ctypes.c_ulonglong()
            ctypes.windll.kernel32.GetDiskFreeSpaceExW("C:\\", None, None, ctypes.byref(free))
            free_gb = int(free.value / (1024 ** 3))
        except Exception:
            pass
        if idle > 1800:
            user_state = "用户已经很久没操作了，有点想他/她"
        elif idle > 300:
            user_state = "用户刚离开一会儿"
        else:
            user_state = "用户正在电脑前操作"
        lines = [
            "【你此刻对这台电脑的体感】",
            "今天：%d月%d日 星期%s，%s" % (now.tm_mon, now.tm_mday, weekdays[now.tm_wday], circ),
            "电脑已开机：%d小时%d分" % (up_h, up_m),
            "用户空闲：%d秒（%s）" % (idle, user_state),
            "当前活动窗口：%s" % (aw[:40] if aw else "（无）"),
            "CPU占用：%d%%" % cpu,
            "内存：剩余%d%% / 共%dGB" % (mem_avail, mem_total),
            "C盘剩余：%dGB" % free_gb,
        ]
        return "\n".join(lines)
    except Exception:
        return ""


def list_dir(path=None):
    try:
        if not path:
            path = os.path.join(os.path.expanduser("~"), "Desktop")
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            return "（路径不存在：" + path + "）"
        items = []
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            if os.path.isdir(full):
                items.append("[文件夹] " + name)
            else:
                try:
                    sz = os.path.getsize(full)
                    if sz >= 1048576:
                        sz_s = "%.1fMB" % (sz / 1048576)
                    elif sz >= 1024:
                        sz_s = "%.0fKB" % (sz / 1024)
                    else:
                        sz_s = str(sz) + "B"
                except Exception:
                    sz_s = "?"
                items.append(name + " (" + sz_s + ")")
        return "目录：" + path + "\n共" + str(len(items)) + "项：\n" + "\n".join(items[:80])
    except Exception as e:
        return "（读取失败：" + str(e) + "）"


# ============ WorkBuddy 工作模式 ============
def screen_size():
    try:
        w = ctypes.windll.user32.GetSystemMetrics(0)
        h = ctypes.windll.user32.GetSystemMetrics(1)
        return {"w": w, "h": h}
    except Exception:
        return {"w": 1920, "h": 1080}


def mouse_click(x, y, button="left"):
    try:
        u = ctypes.windll.user32
        u.SetCursorPos(int(x), int(y))
        time.sleep(0.15)
        down, up = (0x0002, 0x0004) if button == "left" else (0x0008, 0x0010)
        u.mouse_event(down, 0, 0, 0, 0)
        time.sleep(0.05)
        u.mouse_event(up, 0, 0, 0, 0)
        return "ok"
    except Exception as e:
        return str(e)


def type_text(text):
    try:
        CF_UNICODETEXT = 13
        data = str(text).encode("utf-16-le")
        u = ctypes.windll.user32
        u.OpenClipboard(0)
        u.EmptyClipboard()
        h = ctypes.windll.kernel32.GlobalAlloc(0x0042, len(data) + 2)
        lp = ctypes.windll.kernel32.GlobalLock(h)
        ctypes.cdll.msvcrt.memcpy(lp, data, len(data))
        ctypes.windll.kernel32.GlobalUnlock(h)
        u.SetClipboardData(CF_UNICODETEXT, h)
        u.CloseClipboard()
        time.sleep(0.1)
        u.keybd_event(0x11, 0, 0, 0)
        u.keybd_event(0x56, 0, 0, 0)
        u.keybd_event(0x56, 0, 2, 0)
        u.keybd_event(0x11, 0, 2, 0)
        return "ok"
    except Exception as e:
        return str(e)


def press_key(keys):
    try:
        vk = {"enter": 0x0D, "esc": 0x1B, "tab": 0x09, "backspace": 0x08, "space": 0x20,
              "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "up": 0x26, "down": 0x28,
              "left": 0x25, "right": 0x27, "a": 0x41, "c": 0x43, "v": 0x56, "s": 0x53}
        u = ctypes.windll.user32
        parts = keys.lower().split("+")
        for p in parts:
            u.keybd_event(vk[p], 0, 0, 0)
            time.sleep(0.05)
        for p in reversed(parts):
            u.keybd_event(vk[p], 0, 2, 0)
            time.sleep(0.03)
        return "ok"
    except Exception as e:
        return str(e)


def agent_decide(task, history="", step=0):
    """截图 -> 视觉模型看屏幕 -> 输出下一步动作 JSON。"""
    try:
        shot = take_screenshot()
        if not shot or shot.startswith("["):
            return {"action": "done", "reason": "截图失败"}
        try:
            img_b64 = _image_b64(shot, max_px=1024)
        except Exception:
            with open(shot, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
        sw = screen_size()
        sys_p = (
            "你是一个能操控这台 Windows 电脑的智能助手（WorkBuddy模式）。"
            "我会给你当前屏幕截图和用户任务。你只输出一个 JSON，不要任何解释文字。"
            '格式：{"reason":"一句话说明","action":"click|type|key|done",'
            '"x":0到1之间的数,"y":0到1之间的数,"text":"要输入的文字","key":"要按的键"}\n'
            "规则：click 时给归一化坐标 x/y（左上角0,0 右下角1,1）；type 时 text 为要输入的内容；"
            "key 时 key 如 enter/ctrl+s/alt+tab；任务完成或无法继续时 action=done。"
            "屏幕分辨率 %dx%d。谨慎操作，不要点删除/确认删除这类危险按钮。" % (sw["w"], sw["h"])
        )
        user_msg = "用户任务：" + task + "\n历史步骤：" + (history or "无") + "\n第%d步。现在截图如上，下一步做什么？只输出JSON。" % step
        payload = llm_params.payload(
            "qwen2.5vl:7b",
            [{"role": "system", "content": sys_p},
             {"role": "user", "content": user_msg, "images": [img_b64]}],
            stream=False, num_predict=300, temperature=0.1)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request("http://localhost:11434/api/chat", data=data,
                                     headers={"Content-Type": "application/json"})
        with _LOCAL_OPENER.open(req, timeout=120) as r:
            result = json.loads(r.read().decode("utf-8"))
        content = result.get("message", {}).get("content", "").strip()
        content = re.sub(r"<think>[\s\S]*?</think>", "", content).strip()
        m = re.search(r"\{[\s\S]*\}", content)
        action = json.loads(m.group(0)) if m else {"action": "done", "reason": "模型未输出动作"}
        action["_screenshot"] = shot
        return action
    except Exception as e:
        return {"action": "done", "reason": "决策出错：" + str(e)}
