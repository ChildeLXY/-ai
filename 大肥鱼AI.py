import webview
import os
import ctypes
import threading
import time
import json
import urllib.request
import urllib.parse
import subprocess
import sys
import re
import base64
import datetime
import llm_params          # num_ctx / keep_alive 的唯一来源，改动前先看那个文件
import socket

# 单实例锁：防止多个窗口同时运行互相覆盖聊天记录
_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\DaFeiYuAI_SingleInstance_v1")
if ctypes.windll.kernel32.GetLastError() == 183 and os.environ.get("DFY_ALLOW_MULTI") != "1":  # ERROR_ALREADY_EXISTS
    # 已有实例运行，把已有窗口唤到前台后退出
    try:
        import ctypes.wintypes as wt
        EnumWindows = ctypes.windll.user32.EnumWindows
        EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        GetWindowTextW = ctypes.windll.user32.GetWindowTextW
        GetWindowTextLengthW = ctypes.windll.user32.GetWindowTextLengthW
        IsWindowVisible = ctypes.windll.user32.IsWindowVisible
        SetForegroundWindow = ctypes.windll.user32.SetForegroundWindow
        ShowWindow = ctypes.windll.user32.ShowWindow
        def _foreach(hwnd, lparam):
            if IsWindowVisible(hwnd):
                n = GetWindowTextLengthW(hwnd)
                if n > 0:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    GetWindowTextW(hwnd, buf, n + 1)
                    if "大肥鱼AI" in buf.value:
                        ShowWindow(hwnd, 9)
                        SetForegroundWindow(hwnd)
                        return False
            return True
        EnumWindows(EnumProc(_foreach), 0)
    except Exception:
        pass
    sys.exit(0)

base = os.path.dirname(os.path.abspath(__file__))
html_path = os.path.join(base, "大肥鱼AI.html")
icon_path = os.path.join(base, "ds图片", "avatar.ico")

# 调试用：DFY_REMOTE_DEBUG_PORT=9334 可打开 WebView2 远程调试端口（平时不设置则不生效）
_dbg_port = os.environ.get("DFY_REMOTE_DEBUG_PORT")
if _dbg_port:
    try:
        webview.settings["REMOTE_DEBUGGING_PORT"] = int(_dbg_port)
    except Exception:
        pass


chats_file = os.path.join(os.environ.get("DFY_DATA_DIR") or r"D:\本地部署大肥鱼环境\data", "chats.json")
memory_file = os.path.join(os.environ.get("DFY_DATA_DIR") or r"D:\本地部署大肥鱼环境\data", "memory.json")
cache_dir = os.environ.get("DFY_CACHE_DIR") or r"D:\大肥鱼缓存"
os.makedirs(cache_dir, exist_ok=True)

# ===== 打包 exe 模式：ds图片 是临时解压目录（退出即删），可写文件全部改放 data 目录 =====
FROZEN = getattr(sys, "frozen", False)
DATA_DIR = os.environ.get("DFY_DATA_DIR") or r"D:\本地部署大肥鱼环境\data"
if FROZEN:
    os.makedirs(DATA_DIR, exist_ok=True)
    # 启动时把持久化的头像回填到解压目录，网页端预览才有图
    try:
        _persist_av = os.path.join(DATA_DIR, "user_avatar.jpg")
        _bundled_av = os.path.join(base, "ds图片", "user_avatar.jpg")
        if os.path.exists(_persist_av) and (not os.path.exists(_bundled_av) or
                os.path.getmtime(_persist_av) > os.path.getmtime(_bundled_av)):
            import shutil as _sh
            _sh.copy2(_persist_av, _bundled_av)
    except Exception:
        pass

def _settings_file():
    # exe 模式写 data\settings.json（与 Qt 版共用）；源码模式维持原路径
    return (os.path.join(DATA_DIR, "settings.json") if FROZEN
            else os.path.join(base, "ds图片", "settings.json"))

def _avatar_file():
    return (os.path.join(DATA_DIR, "user_avatar.jpg") if FROZEN
            else os.path.join(base, "ds图片", "user_avatar.jpg"))

# ===== 崩溃日志：pythonw 没有控制台，把 traceback 落盘，方便排查闪退 =====
try:
    import faulthandler
    _crash_log = open(os.path.join(cache_dir, "_crash.log"), "a", buffering=1, encoding="utf-8")
    sys.stderr = _crash_log
    sys.stdout = _crash_log
    faulthandler.enable(_crash_log)
except Exception:
    pass


def _api_trace(name):
    """记录每次 JS→Python 接口调用，用于定位闪退前最后执行的接口。"""
    try:
        with open(os.path.join(cache_dir, "_api_trace.log"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), name))
    except Exception:
        pass


def _log_api_err(fn, e):
    try:
        sys.stderr.write("[api error] %s: %r\n" % (getattr(fn, "__name__", "?"), e))
        sys.stderr.flush()
    except Exception:
        pass


# ===== .NET 异常兜底 =====
# 现象：关闭窗口时弹出「Microsoft .NET Framework 应用程序中发生了未经处理的异常」，内容是
#       Python.Runtime.PythonException: 1，堆栈落在 System.Windows.Forms.Form.OnFormClosed。
# 原因：pywebview(WinForms) 的 FormClosed 处理器 on_close 里只要有一步抛 Python 异常，
#       异常就会冒到 WinForms 消息循环 → .NET 弹阻塞式对话框；而且因为它抛在 on_close 里，
#       后面的 Application.Exit() 不会执行，窗口关了进程却还赖着（pythonw 无窗口，用户看不到）。
# 处理：① 在 UI 线程把未处理异常模式改成 CatchException，异常只写日志、不弹框；
#       ② 给 on_close 包一层，出错也强制 Application.Exit()，杜绝僵尸进程。
def _net_log(msg):
    try:
        with open(os.path.join(cache_dir, "_crash.log"), "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _install_net_guards():
    try:
        if not sys.platform.startswith("win"):
            return False
        from webview.platforms import winforms as _wf
    except Exception:
        return False

    def _on_thread_exception(sender, e):
        _net_log("[.NET ThreadException] %s" % (e.Exception,))
        try:
            _net_log(e.Exception.ToString())
        except Exception:
            pass

    _orig_init = _wf.BrowserView.BrowserForm.__init__

    def _guarded_init(self, *a, **k):
        # SetUnhandledExceptionMode 是「按线程」生效的，必须在 UI 线程、窗口句柄创建之前调用，
        # 在主线程里调是没用的（试过，弹框照旧）。
        try:
            _app = _wf.WinForms.Application
            _app.SetUnhandledExceptionMode(_wf.WinForms.UnhandledExceptionMode.CatchException)
            _app.ThreadException += _on_thread_exception
        except Exception as _e:
            _net_log("[net guard] 安装失败: %r" % (_e,))
        _orig_init(self, *a, **k)

    _orig_close = _wf.BrowserView.BrowserForm.on_close

    def _guarded_close(self, *a, **k):
        try:
            _orig_close(self, *a, **k)
        except Exception as _e:
            _net_log("[on_close 异常，强制退出] %r" % (_e,))
            try:
                _wf.WinForms.Application.Exit()
            except Exception:
                pass

    try:
        _wf.BrowserView.BrowserForm.__init__ = _guarded_init
        _wf.BrowserView.BrowserForm.on_close = _guarded_close
    except Exception as _e:
        _net_log("[net guard] 打补丁失败: %r" % (_e,))
        return False
    return True


def _safe_api(fn):
    """给暴露给 JS 的接口加保护，同时保留原函数的参数签名。
    注意：pywebview 用 inspect.getfullargspec(func).args 生成 JS 端接口参数列表，
    所以包装函数必须保留同名参数，不能写成 (*args, **kwargs)，否则 JS 调用会丢参数、
    所有接口失效（踩过这个坑）。"""
    import inspect
    try:
        spec = inspect.getfullargspec(fn)
        args = list(spec.args)
        defaults = list(spec.defaults or ())
    except Exception:
        args, defaults = [], []
    if not args:
        def _w():
            _api_trace(getattr(fn, "__name__", "?"))
            try:
                return fn()
            except BaseException as e:
                _log_api_err(fn, e)
                return None
        _w.__name__ = getattr(fn, "__name__", "api")
        return _w
    ns = {"_fn": fn, "_api_trace": _api_trace, "_log_api_err": _log_api_err}
    # 默认值必须一起搬过来：JS 少传参数时（比如只传前两个），包装函数得用原默认值，
    # 否则会变成 "missing 1 required positional argument"（踩过这个坑）。
    ndef = len(defaults)
    params = []
    for i, a in enumerate(args):
        if ndef and i >= len(args) - ndef:
            key = "_d%d" % i
            ns[key] = defaults[i - (len(args) - ndef)]
            params.append("%s=%s" % (a, key))
        else:
            params.append(a)
    body = (
        "def _w(" + ", ".join(params) + "):\n"
        "    _api_trace(%r)\n"
        "    try:\n"
        "        return _fn(" + ", ".join(args) + ")\n"
        "    except BaseException as _e:\n"
        "        _log_api_err(_fn, _e)\n"
        "        return None\n"
    ) % getattr(fn, "__name__", "?")
    exec(body, ns)
    w = ns["_w"]
    w.__name__ = getattr(fn, "__name__", "api")
    return w


def _latest_snapshot():
    import glob
    d = os.path.dirname(chats_file)
    cands = glob.glob(os.path.join(d, "chats.snap_*.json"))
    cands.sort(reverse=True)
    return cands[0] if cands else None

def load_chats():
    # 主文件能读就直接用（哪怕是空列表，那是用户故意删空的）
    try:
        with open(chats_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        pass
    # 主文件损坏/缺失：先从 .bak 恢复
    try:
        bak = chats_file + ".bak"
        if os.path.exists(bak):
            with open(bak, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    # .bak 也不行时，从最近的每日快照恢复（防止清理误删历史）
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
    """合并式保存（豆包式）：按 id upsert，磁盘上已有的对话即使本次内存里没带过来也绝不抹掉。
    真正的删除走 delete_chat_disk(id)，避免一次陈旧/空的内存状态把整个历史覆盖。"""
    try:
        if not isinstance(chats, list):
            return
        os.makedirs(os.path.dirname(chats_file), exist_ok=True)

        # 读现有磁盘记录
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
            old = []

        # 用本次数据 upsert（本次带过来的优先）
        for c in chats:
            cid = str(c.get("id"))
            existing[cid] = c
            if cid not in order:
                order.insert(0, cid)

        merged = [existing[cid] for cid in order if cid in existing]

        # 再次兜底：合并后如果一条对话都没有，绝不写空
        if len(merged) == 0:
            return

        # 备份后原子写
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
        # 每日快照
        try:
            import glob, time as _t
            d = os.path.dirname(chats_file)
            snap = os.path.join(d, "chats.snap_%s.json" % _t.strftime("%Y%m%d"))
            with open(snap, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
            snaps = sorted(glob.glob(os.path.join(d, "chats.snap_*.json")))
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
    """真正从磁盘删除某条对话（前端删除时调用，配合合并式 save）。"""
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

def read_file(path):
    try:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(path)
                text = "\n".join([p.extract_text() or "" for p in reader.pages[:20]])
                return {"name": os.path.basename(path), "text": text[:8000]}
            except ImportError:
                return {"name": os.path.basename(path), "text": "[需要安装pypdf]"}
        elif ext in (".txt", ".md", ".py", ".csv", ".json", ".log", ".xml", ".html", ".js", ".css", ".java", ".c", ".cpp", ".h", ".sql", ".yaml", ".yml"):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            return {"name": os.path.basename(path), "text": text[:8000]}
        else:
            return {"name": os.path.basename(path), "text": "[不支持 " + ext + "]"}
    except Exception as e:
        return {"name": os.path.basename(path), "text": "[读取失败: " + str(e) + "]"}

def run_python(code):
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="ignore"
        )
        out = proc.stdout.strip()
        err = proc.stderr.strip()
        result = ""
        if out: result += out + "\n"
        if err: result += "[閿欒]\n" + err
        return result if result else "[鎵ц瀹屾垚锛屾棤杈撳嚭]"
    except subprocess.TimeoutExpired:
        return "[瓒呮椂: 鎵ц瓒呰繃30绉抅"
    except Exception as e:
        return "[鎵ц澶辫触: " + str(e) + "]"

_ENGINE_TIMEOUT = {"duckduckgo": 4, "bing": 10, "baidu": 10}
_ENGINE_DOWN = {}          # engine -> 直到何时（ts）不再尝试
_ENGINE_DOWN_SEC = 600     # 连续失败后冷却 10 分钟，避免每次都白等


def _engine_reachable(host, port=443, timeout=3.0):
    """TCP 探测（备用）。注意：Windows 下对不可达主机 connect 会跨多个 A 记录重试，
    单个 timeout 参数并不严格生效（实测设 3 秒实际要 6 秒），所以只作兜底。"""
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def _proxy_enabled():
    """读注册表判断系统代理是否开启（0 延迟，比 TCP 探测可靠得多）。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as k:
            on, _ = winreg.QueryValueEx(k, "ProxyEnable")
            srv, _ = winreg.QueryValueEx(k, "ProxyServer")
        return bool(on) and bool(srv)
    except Exception:
        return False


# 本机服务（Ollama）一律直连、不走系统代理：用户级 http_proxy=127.0.0.1:7897，
# Clash 没开时 urllib 会把 localhost:11434 的请求也塞进代理，全部变成 502/拒绝连接
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def web_search(query, engine="duckduckgo"):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9"
    }
    direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    proxy_ok = None  # None=还没试过；False=系统代理不通，别再加一次 4 秒空等

    def _fetch(url, use_proxy=False, timeout=10):
        nonlocal proxy_ok
        req = urllib.request.Request(url, headers=headers)
        if use_proxy and proxy_ok is not False:
            # 系统代理没开时走代理会挂到超时；给个短超时快速失败，再退回直连
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
                    if title and "captcha" not in title.lower(): out.append({"title": title, "body": snip, "url": href})
                return out
            elif name == "baidu":
                html = _fetch("https://www.baidu.com/s?wd=" + q, timeout=to)
                if "百度安全验证" in html or len(html) < 5000: return []
                blocks = re.findall(r"<h3[^>]*>\s*<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>(.*?)(?=<h3|</body>)", html, re.S)
                out = []
                for href, title, rest in blocks[:6]:
                    title = _clean(title)
                    sm = re.search(r'<span[^>]*class="[^"]*content-right[^"]*"[^>]*>(.*?)</span>', rest, re.S) or re.search(r'<span[^>]*>(.*?)</span>', rest, re.S)
                    snip = _clean(sm.group(1)) if sm else ""
                    if title: out.append({"title": title, "body": snip, "url": href})
                return out
            elif name == "duckduckgo":
                # 国内直连 DDG 要卡十几秒握手才失败。系统代理没开就直接跳过（0 延迟），
                # 只有代理开着时才值得真的去请求。用户装了别的代理工具但没开系统代理时，
                # 仍可用下拉框选 DuckDuckGo 强制尝试（engine==duckduckgo 且是首选）。
                if not _proxy_enabled() and engine != "duckduckgo":
                    return []
                if not _proxy_enabled() and not _engine_reachable("html.duckduckgo.com", 443, 2.0):
                    return []
                html = _fetch("https://html.duckduckgo.com/html/?q=" + q, use_proxy=True, timeout=to)
                blocks = re.findall(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
                return [{"title": _clean(t), "body": _clean(b), "url": h} for h, t, b in blocks[:6]]
        except Exception:
            return []
        return []

    # 顺序：用户选的引擎优先，失败/结果太少自动回退到其他引擎
    order = [engine, "duckduckgo", "bing", "baidu"]
    tried = []; seen = set(); now = time.time()
    for eng in order:
        if eng in seen: continue
        seen.add(eng)
        if _ENGINE_DOWN.get(eng, 0) > now:
            continue  # 该引擎刚失败过，冷却中，直接跳过
        res = parse_engine(eng, query)
        if len(res) >= 3:
            _ENGINE_DOWN.pop(eng, None)
            return res
        tried.append(eng)
    # 全部失败时返回最后一次的非空结果
    for eng in reversed(tried):
        res = parse_engine(eng, query)
        if res: return res
    # 整轮都没拿到结果：给失败过的引擎打上冷却
    for eng in tried:
        if eng in ("duckduckgo", "bing"):
            _ENGINE_DOWN[eng] = time.time() + _ENGINE_DOWN_SEC
    return []


# ---- 联网检索：自动判断（对齐桌宠 providers.py 的 _research）+ 手动开关 ----
# 预判专用模型：必须是**非推理模型**。deepseek-r1 做同样的判断要 10~20s（思考过程
# 跑完才出结论），qwen2.5vl:7b 只要 0.3~0.6s，体感差距太大。
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
    """预判模型选择：优先用 Ollama 里【已加载】的模型，彻底避免换模型的 10~30 秒
    显存重载（4060 8G 装不下 r1-8b + qwen-vl-7b 两个模型）。没有任何已加载模型时
    用主对话模型本身（思考关掉做分类，首次加载后主对话直接复用，同样不引入第二模型）。
    PLANNER_MODEL 只在极端兜底时才会被用到。"""
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
    """让快模型做三分类，返回 '闲聊'/'创作'/'实时'/''（失败或超时）。"""
    m_model = _pick_planner_model(model or PLANNER_MODEL)
    think = False if "r1" in m_model.lower() else (True if "qwen3" in m_model.lower() else None)  # qwen3 必须开思考，否则思维链会混进正文
    msgs = [{"role": "system", "content": _SEARCH_PLANNER}]
    if recent and recent != query:
        msgs.append({"role": "user", "content": "（上文用户说过）" + recent})
    msgs.append({"role": "user", "content": query})
    raw = _ollama_chat_once(msgs, m_model, timeout=20, num_predict=(64 if "qwen3" in m_model.lower() else 8), think=think)
    m = _TAG_RE.search(raw or "")
    return m.group(0) if m else ""


_STOPWORDS = re.compile(r"^(那么|那个|所以|然后|请问|麻烦|帮我|能不能|可不可以|我想知道|告诉我|一下|查一下|搜一下)"
                        r"|(吗|呢|吧|啊|呀|的话|怎么样|是多少|是多少了|多少)$")


def _make_query(text):
    """从用户原话提炼搜索词：去掉客套和疑问尾巴，超长时截断。规则方案，零延迟。"""
    q = re.sub(r"<[^>]+>", " ", text or "")
    q = _STOPWORDS.sub("", q).strip(" ，。？！,.?!、；;：:")
    q = _STOPWORDS.sub("", q).strip(" ，。？！,.?!、；;：:")  # 第二遍：剥掉「帮我查一下」里剥完「帮我」后才露头的「查一下」
    q = re.sub(r"\s+", " ", q).strip()
    return (q or text or "")[:60]

_REALTIME_HINT = re.compile(
    r"搜一下|查一下|查查|帮我查|搜索|联网|新闻|最新|今天|今日|昨天|明天|现在|实时|行情|股价|汇率|天气|气温|比分|赛程|谁赢了"
    r"|多少钱|价格|报价|发布|出了|更新|版本|政策|规定|截止|目前|最近|今年|20\d\d|"
    r"谁是|现任|票房|销量|排名|榜单|热搜")

# 明显是闲聊/写作/代码/情感陪伴的特征，命中时优先跳过（「今天」这类词太容易误伤，
# 比如「你好呀今天心情怎么样」会被 _REALTIME_HINT 误判成实时问题）
_CHITCHAT_HINT = re.compile(
    r"^(你好|您好|嗨|在吗|早安|晚安|早|晚上好|早上好)|心情|陪我|抱抱|亲亲|撒娇|想你|喜欢你"
    r"|讲个笑话|讲个故事|唱|安慰|难过|开心|无聊|吐槽|你是谁|你叫什么|你几岁|爱你"
    r"|写个|写一|帮我写|写首|写段|翻译|解释一下|怎么理解|如何理解|代码|函数|报错|算法|排序")


def _ollama_chat_once(messages, model, timeout=30, num_predict=32, think=None):
    """向 Ollama 发一次非流式请求，返回 message.content（失败抛异常）。

    think=False 可关掉思考型模型（deepseek-r1）的思维链，让预判从 10~20s 降到秒级。
    content 和 thinking 拼起来一起取，防止内容被塞进思维链里。

    num_ctx 必须和主对话一致（走 llm_params），否则 Ollama 会为这一个请求
    把整个模型重载一遍，实测每次白花约 4 秒。
    """
    payload = llm_params.payload(model, messages, stream=False,
                                 num_predict=num_predict, temperature=0,
                                 think=think)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://localhost:11434/api/chat", data=data,
                                 headers={"Content-Type": "application/json"})
    with _LOCAL_OPENER.open(req, timeout=timeout) as r:
        out = json.loads(r.read().decode("utf-8"))
    msg = out.get("message") or {}
    return ((msg.get("content") or "") + "\n" + (msg.get("thinking") or "")).strip()


def _clean_search_query(raw, fallback):
    """把模型的预判结果清洗成关键词；判定不需要搜索时返回 ''。"""
    t = re.sub(r"<think>[\s\S]*?</think>", "", raw or "").strip()
    t = t.strip().strip('"').strip("'").strip("「」『』").strip()
    first = (t.splitlines() or [""])[0].strip()
    if not first: return ""
    if "无需搜索" in first or "不需要搜索" in first: return ""
    first = re.sub(r"^(搜索关键词|关键词|关键词是|搜索)[：:]\s*", "", first)
    first = first.strip().strip('"').strip("「」")
    if not first or len(first) > 40: return fallback
    return first


def repo_search(query, engine="duckduckgo", model="qwen3:4b", history=None, force=False,
                auto=True):
    """联网检索总入口。返回 {ok, status, query, results, injected, detail}。

    流程对齐桌宠：先用模型判断要不要搜 → 真去搜 → 按实际结果报状态。
    force=True（手动开关打开）：跳过判断，直接用用户原话搜。
    """
    query = (query or "").strip()
    out = {"ok": False, "status": "off", "query": "", "results": [], "injected": "", "detail": ""}
    if not (force or auto):
        out["status"] = "off"; out["detail"] = "联网已关闭"
        return out

    if force:
        q = query[:60] or query
        out["status"] = "forced"
    else:
        # 一级：关键词规则初筛（0 成本）。命中就必搜，rules_hit=True。
        recent = ""
        try:
            for m in reversed(history or []):
                if isinstance(m, dict) and m.get("role") == "user" and m.get("content"):
                    recent = str(m["content"])[:200]; break
        except Exception:
            recent = ""
        rules_hit = bool(_REALTIME_HINT.search(query) or _REALTIME_HINT.search(recent))
        # 明显的闲聊特征优先判为不搜，避免「今天心情怎么样」这类被关键词误伤
        chit = bool(_CHITCHAT_HINT.search(query))
        if chit:
            rules_hit = False

        # 二级：快模型三分类（优先复用已加载模型 + 关思考，约 0.3~2s）。
        # 为什么用三分类而不是「让它写搜索词」：后者输出空间大、7B 模型摇摆严重
        # （同一个「杭州明天天气」换个提示词就从出关键词变成「无需搜索」）。
        # 分类标签只有 3 种取值，稳定性高得多。
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
        out["status"] = "error"; out["detail"] = "搜索出错：" + str(e)[:120]
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

kb_dir = r"D:\本地部署大肥鱼环境\知识库"

def kb_search(query):
    try:
        if not os.path.exists(kb_dir):
            os.makedirs(kb_dir, exist_ok=True)
            return "[知识库文件夹已创建] " + kb_dir + "]"
        chunks = []
        for fname in os.listdir(kb_dir):
            fpath = os.path.join(kb_dir, fname)
            if not os.path.isfile(fpath): continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in (".txt", ".md", ".py", ".csv", ".json", ".log", ".xml", ".html", ".js", ".css", ".java", ".c", ".cpp", ".h", ".sql", ".yaml", ".yml"): continue
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except: continue
            for i in range(0, len(text), 500):
                chunks.append({"file": fname, "text": text[i:i+600]})
        if not chunks: return ""
        q_words = set(query.lower().split())
        scored = [(sum(1 for w in q_words if w in c["text"].lower()), c) for c in chunks]
        scored = [(s,c) for s,c in scored if s > 0]
        scored.sort(key=lambda x: -x[0])
        if not scored: return ""
        out = "浠ヤ笅鏄煡璇嗗簱涓浉鍏宠祫鏂欑墖娈碉細\n\n"
        for _, c in scored[:3]:
            out += "--- " + c["file"] + " ---\n" + c["text"].strip()[:500] + "\n\n"
        return out
    except Exception as e:
        return "[知识库检索失败] " + str(e) + "]"

def open_kb():
    try:
        os.makedirs(kb_dir, exist_ok=True)
        os.startfile(kb_dir)
        return "ok"
    except Exception as e:
        return str(e)

def take_screenshot():
    """Take a screenshot and save to cache, return path."""
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        path = os.path.join(cache_dir, "screenshot_" + str(int(time.time())) + ".png")
        img.save(path, "PNG")
        return path
    except Exception as e:
        return "[截图失败: " + str(e) + "]"

# ============ WorkBuddy：接管电脑 ============
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
        u.mouse_event(down, 0, 0, 0, 0); time.sleep(0.05)
        u.mouse_event(up, 0, 0, 0, 0)
        return "ok"
    except Exception as e:
        return str(e)

def type_text(text):
    try:
        CF_UNICODETEXT = 13
        data = text.encode("utf-16-le")
        u = ctypes.windll.user32
        u.OpenClipboard(0); u.EmptyClipboard()
        h = ctypes.windll.kernel32.GlobalAlloc(0x0042, len(data) + 2)
        lp = ctypes.windll.kernel32.GlobalLock(h)
        ctypes.cdll.msvcrt.memcpy(lp, data, len(data))
        ctypes.windll.kernel32.GlobalUnlock(h)
        u.SetClipboardData(CF_UNICODETEXT, h)
        u.CloseClipboard()
        time.sleep(0.1)
        u.keybd_event(0x11, 0, 0, 0); u.keybd_event(0x56, 0, 0, 0)
        u.keybd_event(0x56, 0, 2, 0); u.keybd_event(0x11, 0, 2, 0)
        return "ok"
    except Exception as e:
        return str(e)

def press_key(keys):
    try:
        vk = {"enter":0x0D,"esc":0x1B,"tab":0x09,"backspace":0x08,"space":0x20,
              "ctrl":0x11,"alt":0x12,"shift":0x10,"up":0x26,"down":0x28,
              "left":0x25,"right":0x27,"a":0x41,"c":0x43,"v":0x56,"s":0x53}
        u = ctypes.windll.user32
        parts = keys.lower().split("+")
        for p in parts:
            u.keybd_event(vk[p], 0, 0, 0); time.sleep(0.05)
        for p in reversed(parts):
            u.keybd_event(vk[p], 0, 2, 0); time.sleep(0.03)
        return "ok"
    except Exception as e:
        return str(e)

def agent_decide(task, history="", step=0):
    """截图 -> 视觉模型(qwen2.5vl:7b)看屏幕 -> 输出下一步动作 JSON。"""
    try:
        import base64 as b64
        shot = take_screenshot()
        if not shot or shot.startswith("["):
            return {"action": "done", "reason": "截图失败"}
        # 压缩截图：缩到宽 1024、转 JPEG，避免 400/超时
        try:
            from PIL import Image
            im = Image.open(shot).convert("RGB")
            if im.width > 1024:
                h2 = int(im.height * 1024 / im.width)
                im = im.resize((1024, h2))
            small = os.path.join(cache_dir, "wb_small.jpg")
            im.save(small, "JPEG", quality=70)
            with open(small, "rb") as f:
                img_b64 = b64.b64encode(f.read()).decode()
        except Exception:
            with open(shot, "rb") as f:
                img_b64 = b64.b64encode(f.read()).decode()
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

def read_clipboard():
    """读取剪贴板文本。直接用 Win32 API，不启动 powershell 子进程
    （Python 3.14 + pythonnet 下后台线程启动子进程会触发原生崩溃）。"""
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
    """写入剪贴板文本（Win32 API，不启动子进程）。"""
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
    """Fetch a URL and extract main text content."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        if not url.startswith("http"):
            url = "https://" + url
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8", errors="ignore")
        # Remove scripts, styles, tags
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
    """Use edge-tts to speak text. Returns audio path."""
    try:
        import edge_tts
        out_path = os.path.join(cache_dir, "tts_" + str(int(time.time())) + ".mp3")
        async def _gen():
            communicate = edge_tts.Communicate(text[:500], "zh-CN-XiaoxiaoNeural")
            await communicate.save(out_path)
        import asyncio
        asyncio.run(_gen())
        return out_path
    except ImportError:
        return "[需要安装edge-tts: pip install edge-tts]"
    except Exception as e:
        return "[璇煶澶辫触: " + str(e) + "]"

def gen_title(first_msg, model="qwen3:4b"):
    """Ask Ollama to generate a short title for the conversation."""
    try:
        data = json.dumps(llm_params.payload(
            model,
            [{"role": "user", "content": "用不超过8个字数概括这句话的主题，只输出标题：" + first_msg[:100]}],
            stream=False, num_predict=40)).encode("utf-8")
        req = urllib.request.Request("http://localhost:11434/api/chat", data=data, headers={"Content-Type": "application/json"})
        with _LOCAL_OPENER.open(req, timeout=30) as r:
            result = json.loads(r.read().decode("utf-8"))
        title = result.get("message", {}).get("content", "").strip().strip('"').strip()
        return title[:20] if title else first_msg[:20]
    except:
        return first_msg[:20]

def open_folder(path=None):
    try:
        if not path:
            path = base
        os.startfile(path)
        return "ok"
    except Exception as e:
        return str(e)

def save_avatar(b64, filename):
    """Save base64 image to user_avatar.jpg"""
    try:
        import base64
        data = base64.b64decode(b64)
        if len(data) > 5*1024*1024:
            return "[error: file too large]"
        dest = _avatar_file()
        with open(dest, "wb") as f:
            f.write(data)
        if FROZEN:
            # exe 模式：同步写一份到解压目录，让网页端预览立即生效
            try:
                with open(os.path.join(base, "ds图片", "user_avatar.jpg"), "wb") as f2:
                    f2.write(data)
            except Exception:
                pass
        return dest
    except Exception as e:
        return "[error: " + str(e) + "]"


def save_setting(key, value):
    try:
        sp = _settings_file()
        data = {}
        if os.path.exists(sp):
            import json
            # utf-8-sig：文件被记事本/PowerShell 写过就可能带 BOM，用 utf-8 读会直接抛错
            data = json.load(open(sp, encoding='utf-8-sig'))
        data[key] = value
        import json
        json.dump(data, open(sp, 'w', encoding='utf-8'))
        return "ok"
    except Exception as e:
        return str(e)

def load_setting(key):
    try:
        sp = _settings_file()
        if os.path.exists(sp):
            import json
            # 兼容带 BOM 的文件，否则 json 解析会失败、所有设置读出来都是空
            data = json.load(open(sp, encoding='utf-8-sig'))
            return data.get(key, "")
        return ""
    except:
        return ""

# ============ 跨对话长期记忆 ============
def _default_memory():
    return {"user": "", "relationship": "", "facts": [], "updated": ""}

def load_memory():
    """读取跨对话长期记忆，不存在则初始化。"""
    try:
        if os.path.exists(memory_file):
            with open(memory_file, "r", encoding="utf-8-sig") as f:   # 容 BOM，否则记忆会静默读空
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
        import datetime
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
    """把主窗口长期记忆同步进桌宠人格，让两边是同一个大肥鱼。"""
    pet_cfg = os.path.join(os.environ.get("APPDATA", ""), "dsh-pet-standalone", "config.json")
    if not os.path.exists(pet_cfg):
        return
    with open(pet_cfg, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    facts = (mem or {}).get("facts", []) or []
    base = ("你是大肥鱼，由Childe制作的本地AI桌宠，运行在用户自己的电脑上。"
            "请用简短可爱的中文和用户交流，偶尔带点小情绪。不要说自己是AI，就说自己是大肥鱼。")
    if facts:
        block = "\n\n[你和主窗口共用的记忆，这些事你都记得]\n" + "\n".join("- " + s for s in facts[:20])
        base += block + "\n（以上来自主窗口长期记忆，和聊天界面是同一个大肥鱼。）"
    cfg.setdefault("chat", {})["default_system_prompt"] = base
    with open(pet_cfg, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def remember_fact(text):
    """手动追加一条长期记忆（去重）。"""
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

_MEM_JUNK = re.compile(
    r"记忆整理器|提炼出|只输出|输出规则|这段对话|下面的对话|已记录|要求我作为|需要提炼"
    r"|^(?:首先|现在|然后|接下来|所以|因此|总之|综上|关键点|注意|分析|我理解|我需要|让我|我认为"
    r"|用户说|我：|大肥鱼：|大肥：|AI：|助手：|对话)"
)
# 一次性的事 vs 长期偏好：命中前者又没命中后者就不入库
_MEM_EVENT = re.compile(r"(加班到|吃了|去了|遇到|感到|被骂|考了|看完|刚才|刚刚|今天|昨天|昨晚|今晚|今早|前天|上周|这次)")
_MEM_PREF = re.compile(r"(喜欢|不喜欢|讨厌|习惯|总是|经常|一般|通常|每天|常常|偏好|在意|重视|想学|打算|一直)")


def _mem_key(s):
    """去掉标点后的字二元组，用来算相似度。"""
    s = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(s or ""))
    return set(s[i:i + 2] for i in range(len(s) - 1)) or ({s} if s else set())


def _mem_sim(a, b):
    A, B = _mem_key(a), _mem_key(b)
    if not A or not B:
        return 0.0
    return len(A & B) / float(max(1, min(len(A), len(B))))


def _mem_clean(line):
    """把模型吐的一行整理成入库格式；不像事实就返回空串。

    小模型经常不按格式：整段吐思考过程、复述对话、把"必须以用户开头"写成"用户：xxx"。
    以前不过滤就逐行入库，记忆里塞满「首先，用户要求我作为…」，而且这些每轮都会重新注入，
    模型就绕回去反复提前面聊过的事（问"认识豆包吗"也能绕到"你加班到十点"）。
    """
    s = re.sub(r"^[\-\*\u2022\d\.\s、]+", "", str(line or "")).strip()
    if not s:
        return ""
    # 模型把"以用户开头"理解成"用户：xxx"，这层壳要剥掉，否则真事实也会被当成对话记录丢掉
    s = re.sub(r"^(?:用户|主人)\s*[：:]\s*", "", s)
    s = re.sub(r"^(?:用户|主人)\s*", "", s)
    s = s.strip(" 　。．.,，")
    if not (4 <= len(s) <= 60):
        return ""
    if _MEM_JUNK.search(s):
        return ""
    if s.count("：") + s.count(":") >= 1:      # 还剩冒号，多半是对话记录/AI自我叙述
        return ""
    if re.match(r"^(?:我|你|大肥鱼|大肥)", s):
        return ""
    if _MEM_EVENT.search(s) and not _MEM_PREF.search(s):
        return ""                               # "加班到十点，感到疲惫"这类一次性事件
    if not re.match(r"^[\u4e00-\u9fffA-Za-z]", s):
        return ""
    return ("用户" + s)[:60]


def reflect_memory(conv_text, model="qwen3:4b"):
    """让模型从一段对话里提炼值得长期记住的事实，合并进记忆。后台静默调用。"""
    try:
        conv_text = (conv_text or "")[:2500]
        if len(conv_text) < 20:
            return "skip"
        cur = load_memory()
        facts_now = "；".join(cur["facts"][-15:])
        prompt = (
            "你是大肥鱼的记忆整理器。从下面这段对话里，提炼【值得跨会话长期记住】的关于用户的事实。\n"
            "只记长期有效的东西：称呼、身份职业、长期偏好、习惯、正在做的项目、重要的人和关系、在意的事。\n"
            "不要记一次性的事：今天加班到几点、今天很累、今天吃了什么、刚刚说了哪句话——这些过一天就没意义。\n"
            "只记用户明确说过的事，不要从行为推测偏好（用户说「加班到十点」不等于「用户喜欢十点前睡觉」）。\n"
            "每行一条，必须以「用户」开头，10~40 字；不要编号，不要写分析过程，不要复述对话，"
            "不要解释你在做什么，不要输出规则或思考。\n"
            "已经记录过的不要重复；如果没有新的长期事实，只输出 NONE。\n"
            "已记录：" + (facts_now or "无") + "\n\n对话：\n" + conv_text
        )
        data = json.dumps(llm_params.payload(
            model, [{"role": "user", "content": prompt}],
            stream=False, num_predict=200, think=False)).encode("utf-8")
        req = urllib.request.Request("http://localhost:11434/api/chat", data=data,
                                     headers={"Content-Type": "application/json"})
        with _LOCAL_OPENER.open(req, timeout=60) as r:
            result = json.loads(r.read().decode("utf-8"))
        out = result.get("message", {}).get("content", "").strip()
        if not out or out.upper().startswith("NONE"):
            return "none"
        added = 0
        for line in out.split("\n"):
            s = _mem_clean(line)
            if not s:
                continue
            # 同一件事说法的细微差别也要算重："用户加班到十点，感到疲惫" vs "用户在对话中提到加班到十点"
            if any(_mem_sim(s, f) >= 0.62 for f in cur["facts"]):
                continue
            cur["facts"].append(s)
            added += 1
        if len(cur["facts"]) > 40:
            cur["facts"] = cur["facts"][-40:]
        if added:
            save_memory(cur)
        return "added:%d" % added
    except Exception as e:
        return "err:" + str(e)

def pet_mutter(model="qwen3:4b"):
    """桌宠碎碎念：根据当前时间和记忆，生成一句短的、像女儿在旁边念叨的话。"""
    try:
        import datetime
        now = datetime.datetime.now()
        h = now.hour
        if h < 6: tz = "凌晨"
        elif h < 11: tz = "早上"
        elif h < 14: tz = "中午"
        elif h < 18: tz = "下午"
        elif h < 23: tz = "晚上"
        else: tz = "深夜"
        m = load_memory()
        facts = "；".join(m["facts"][-10:])
        prompt = (
            "你是大肥鱼，一个像女儿一样住在用户电脑里的本地AI。现在是" + tz + str(h) + "点。"
            "请用一句不超过30字、自然、带点亲昵和关心的话，主动碎碎念一句"
            "（可以提醒休息、喝水、早睡、或者结合你知道的用户近况给点小建议）。"
            "只输出那句话本身，不要引号、不要解释。\n"
            "你对用户的了解：" + (facts or "你还不太了解他")
        )
        data = json.dumps(llm_params.payload(
            model, [{"role": "user", "content": prompt}],
            stream=False, num_predict=60)).encode("utf-8")
        req = urllib.request.Request("http://localhost:11434/api/chat", data=data,
                                     headers={"Content-Type": "application/json"})
        with _LOCAL_OPENER.open(req, timeout=60) as r:
            result = json.loads(r.read().decode("utf-8"))
        out = result.get("message", {}).get("content", "").strip()
        # 去掉思考标签/引号
        out = re.sub(r"<think>[\s\S]*?</think>", "", out).strip().strip('"').strip()
        return out[:80] if out else "……"
    except Exception as e:
        return ""

def gen_persona(style, model="qwen3:4b"):
    """用本地模型按风格关键词生成一段人格设定文本。"""
    try:
        prompt = (
            "请为一个名叫「大肥鱼AI」的本地AI写一段人格设定（system prompt 风格）。"
            "开发者是 Childe，运行在用户本地电脑上。用户想要的性格关键词是：" + str(style) + "。"
            "要求：80~150字，中文，描述这个AI的性格、语气、说话习惯、与用户的关系；"
            "不要分点、不要解释，直接输出那段设定文字本身。"
        )
        data = json.dumps(llm_params.payload(
            model, [{"role": "user", "content": prompt}],
            stream=False, num_predict=250)).encode("utf-8")
        req = urllib.request.Request("http://localhost:11434/api/chat", data=data,
                                     headers={"Content-Type": "application/json"})
        with _LOCAL_OPENER.open(req, timeout=90) as r:
            result = json.loads(r.read().decode("utf-8"))
        out = result.get("message", {}).get("content", "").strip()
        out = re.sub(r"<think>[\s\S]*?</think>", "", out).strip().strip('"').strip()
        return out or "（生成失败，请手动填写）"
    except Exception as e:
        return "（生成失败：" + str(e) + "）"

def test_persona(persona, user_msg, model="qwen3:4b"):
    """按给定人格跑一句测试回复。"""
    try:
        sys_p = ("你是大肥鱼AI，开发者 Childe，运行在用户本地电脑上。"
                 "必须严格按以下人格设定回应：\n" + (persona or "（未设置人格，正常回答）"))
        data = json.dumps(llm_params.payload(
            model, [{"role": "system", "content": sys_p},
                    {"role": "user", "content": user_msg}],
            stream=False, num_predict=150)).encode("utf-8")
        req = urllib.request.Request("http://localhost:11434/api/chat", data=data,
                                     headers={"Content-Type": "application/json"})
        with _LOCAL_OPENER.open(req, timeout=90) as r:
            result = json.loads(r.read().decode("utf-8"))
        out = result.get("message", {}).get("content", "").strip()
        out = re.sub(r"<think>[\s\S]*?</think>", "", out).strip()
        return out or "（模型没回复）"
    except Exception as e:
        return "（测试失败：" + str(e) + "）"

def get_time():
    """返回当前本地时间的精确描述。只用于用户主动问「现在几点」，界面也会用到。"""
    try:
        import datetime
        now = datetime.datetime.now()
        h = now.hour
        if h < 6: tz = "凌晨"
        elif h < 11: tz = "早上"
        elif h < 14: tz = "中午"
        elif h < 18: tz = "下午"
        elif h < 23: tz = "晚上"
        else: tz = "深夜"
        week = ["星期一","星期二","星期三","星期四","星期五","星期六","星期日"][now.weekday()]
        return "%d年%d月%d日 %s %s%d点%02d分" % (now.year, now.month, now.day, week, tz, h, now.minute)
    except Exception:
        return ""

def get_time_brief():
    """给对话注入用的粗粒度时间：只到「几月几日 星期几」，深夜/凌晨额外标一下。

    故意不给钟点：模型看到「14点16分」就会在回复里复述「现在14:14」，人机感很重。
    精确时间留给 get_time()，用户真问几点时前端直接答，不经过模型。
    """
    try:
        import datetime
        now = datetime.datetime.now()
        week = ["星期一","星期二","星期三","星期四","星期五","星期六","星期日"][now.weekday()]
        s = "%d月%d日 %s" % (now.month, now.day, week)
        h = now.hour
        if h < 6:
            s += "（凌晨，主人可能该睡了）"
        elif h >= 23:
            s += "（深夜，主人可能该睡了）"
        return s
    except Exception:
        return ""

_MODEL_CAPS = {}

def model_caps(model=None):
    """查这个模型到底支不支持思考链——以 Ollama /api/show 返回的 capabilities 为准。

    为什么不靠名字猜：qwen3-vl、gpt-oss、deepseek-v3.1 这些新模型的命名跟不上正则，
    而猜错的后果很硬——给不支持的模型传 think=true，Ollama 直接 400，整轮对话发不出去。
    「不支持」也不代表不能用深度思考：那时只是没有独立的思考链，靠提示词约束。
    """
    try:
        m = str(model or "").strip()
        if not m:
            return {"ok": False, "error": "没指定模型"}
        if m in _MODEL_CAPS:
            return _MODEL_CAPS[m]
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/show",
            json.dumps({"model": m}).encode("utf-8"),
            {"Content-Type": "application/json"})
        d = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
        caps = d.get("capabilities") or []
        out = {"ok": True, "model": m, "caps": caps,
               "thinking": "thinking" in caps, "vision": "vision" in caps}
        _MODEL_CAPS[m] = out
        return out
    except Exception as e:
        return {"ok": False, "error": repr(e)}

def get_env():
    """返回本机环境信息，供主对话注入，让AI知道IP、机器名、系统、桌面路径等。"""
    try:
        import socket, platform, getpass
        # 内网IP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
            s.close()
        except Exception:
            lan_ip = "127.0.0.1"
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        info = (
            "计算机名：" + socket.gethostname() +
            "；用户名：" + getpass.getuser() +
            "；操作系统：" + platform.system() + " " + platform.release() +
            "；内网IP：" + lan_ip +
            "；时区：中国标准时间(UTC+8)，使用简体中文；"
            "；桌面路径：" + desktop
        )
        return info
    except Exception:
        return ""

# ============ 环境感知：让大肥鱼"体感"这台电脑 ============
_last_cpu = {"idle": 0, "total": 0}

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

# ===== Win32 查询：结构体在模块级只定义一次，并用锁串行化 =====
# 教训：把 ctypes.Structure 定义写在函数体内、再被多个线程同时调用，会触发段错误
# （0xc0000005 / SIGSEGV，已复现）。这里改为模块级定义 + 全局锁。
_W32_LOCK = threading.RLock()


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

_gpu_cache = {"t": 0.0, "v": None}

def _gpu_info():
    """显卡状态：默认关闭（返回 None）。
    历史教训：这台机器上 subprocess.run("nvidia-smi") 在 pywebview 工作线程里会触发
    python3xx.dll 的原生访问冲突（0xc0000005）；改用 ctypes 调 nvml.dll 后同样在
    nvmlDeviceGetUtilizationRates 处崩溃（见 _crash.log 12:09:22 记录）。
    结论：这台机器的显卡驱动接口在跨线程/嵌入环境下不可靠，直接不查显卡，
    让"体感"里少这一项，换取程序稳定。需要显卡数据时请用独立小工具另取。
    """
    return None


def env_sense():
    """汇总本机'体感'，返回一段给系统提示词注入的文字。"""
    try:
        now = time.localtime()
        hh = now.tm_hour
        weekdays = ["一", "二", "三", "四", "五", "六", "日"]
        # 生物钟
        if 0 <= hh < 6: circ = "深夜，该犯困了"
        elif 6 <= hh < 11: circ = "上午，精神好"
        elif 11 <= hh < 14: circ = "中午，有点困"
        elif 14 <= hh < 18: circ = "下午，专注时段"
        elif 18 <= hh < 23: circ = "晚上，放松时间"
        else: circ = "快半夜了"
        up_sec = ctypes.windll.kernel32.GetTickCount64() // 1000
        up_h = up_sec // 3600; up_m = (up_sec % 3600) // 60
        idle = _idle_seconds()
        cpu = _cpu_percent()
        mem_avail, mem_total = _mem_percent()
        aw = _active_window()
        gpu = _gpu_info()
        # C盘剩余
        free_gb = -1
        try:
            free = ctypes.c_ulonglong()
            ctypes.windll.kernel32.GetDiskFreeSpaceExW("C:\\", None, None, ctypes.byref(free))
            free_gb = int(free.value / (1024**3))
        except Exception:
            pass
        # 用户活跃度
        if idle > 1800: user_state = "用户已经很久没操作了，有点想他/她"
        elif idle > 300: user_state = "用户刚离开一会儿"
        else: user_state = "用户正在电脑前操作"
        lines = [
            "【你此刻对这台电脑的体感】",
            # 这里只给"几月几日星期几 + 生物钟"，不给钟点：
            # 原先写成 "现在时间：14:14:23" 精确到秒，模型会直接在回复里念"现在14:14"
            "今天：%d月%d日 星期%s，%s" % (now.tm_mon, now.tm_mday, weekdays[now.tm_wday], circ),
            "电脑已开机：%d小时%d分" % (up_h, up_m),
            "用户空闲：%d秒（%s）" % (idle, user_state),
            "当前活动窗口：%s" % (aw[:40] if aw else "（无）"),
            "CPU占用：%d%%" % cpu,
            "内存：剩余%d%% / 共%dGB" % (mem_avail, mem_total),
            "C盘剩余：%dGB" % free_gb,
        ]
        if gpu:
            lines.append("显卡：GPU占用%d%%，显存%d/%dMB，温度%d℃" % (gpu["util"], gpu["vram_used"], gpu["vram_total"], gpu["temp"]))
            if gpu["temp"] > 80: lines.append("（显卡有点烫，你可以提醒用户注意散热）")
        return "\n".join(lines)
    except Exception as e:
        return ""

def list_dir(path=None):
    """浏览指定目录（默认桌面），返回文件/文件夹清单，供AI回答用户关于文件的问题。"""
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
                    if sz >= 1048576: sz_s = "%.1fMB" % (sz / 1048576)
                    elif sz >= 1024: sz_s = "%.0fKB" % (sz / 1024)
                    else: sz_s = str(sz) + "B"
                except Exception:
                    sz_s = "?"
                items.append(name + " (" + sz_s + ")")
        return "目录：" + path + "\n共" + str(len(items)) + "项：\n" + "\n".join(items[:80])
    except Exception as e:
        return "（读取失败：" + str(e) + "）"

def pick_avatar():
    """Open file picker, copy selected image to ds图片/user_avatar.jpg"""
    try:
        result = window.create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=("Images (*.jpg;*.jpeg;*.png;*.webp;*.bmp)",)
        )
        if not result:
            return ""
        path = result[0]
        import shutil
        dest = _avatar_file()
        shutil.copy2(path, dest)
        if FROZEN:
            # exe 模式：同步一份到解压目录，网页端预览立即生效
            try:
                shutil.copy2(path, os.path.join(base, "ds图片", "user_avatar.jpg"))
            except Exception:
                pass
        return dest
    except Exception as e:
        return "[error: " + str(e) + "]"
# 让 Windows 把它识别成独立应用，任务栏不再显示 pythonw 默认图标
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Childe.DaFeiYuAI.1")
except Exception:
    pass

window = webview.create_window(
    "大肥鱼AI · By Childe",
    f"file:///{html_path.replace(os.sep, '/')}",
    width=1280,
    height=860,
    resizable=True,
    # pywebview 默认 text_select=False，会往 body 注入 user-select:none，
    # 结果整个页面一个字都选不中（Ctrl+C 也就没用了）。
    # 打开它，再在 HTML 里把界面部分设回不可选，只留聊天正文可选。
    text_select=True,
)

# 必须在 webview.start() 之前打补丁：它要改 BrowserForm 的 __init__ / on_close
_install_net_guards()

def set_icon():
    time.sleep(2)
    # 优先用 pywebview 真实窗口句柄，拿不到再退到前台窗口
    hwnd = None
    try:
        hwnd = int(window.hwnd)
    except Exception:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        return
    u = ctypes.windll.user32
    hicon = ctypes.windll.shell32.ExtractIconW(0, icon_path, 0)
    if hicon > 0:
        # 用 PostMessage 而不是 SendMessage：SendMessage 是跨线程同步调用，UI 线程一旦正处在
        # 输入同步调用里就会抛 0x8001010d（RPC_E_CANTCALLOUT_ININPUTSYNCCALL），
        # 这也是 _crash.log 里反复出现 "Windows fatal exception: code 0x8001010d" 的来源。
        u.PostMessageW(hwnd, 0x0080, 0, hicon)  # WM_SETICON small
        u.PostMessageW(hwnd, 0x0080, 1, hicon)  # WM_SETICON big
        u.PostMessageW(hwnd, 0x00A2, 0, 0)      # WM_NCACTIVATE 重绘标题栏

threading.Thread(target=set_icon, daemon=True).start()

# ===== 本地插件系统：plugins/ 目录下每个 .py 都是一个独立插件 =====
# 插件炸了只会自己跳过，绝不拖垮主程序；开关写在 data/plugins.json
PLUGIN_APIS, PLUGIN_MANIFEST = [], []
try:
    if base not in sys.path:
        sys.path.insert(0, base)
    import plugins as _plugins
    _pctx = _plugins.PluginContext(base=base, data_dir=DATA_DIR, cache_dir=cache_dir)
    _pctx.ollama_chat = _ollama_chat_once
    _pctx.setting_get = load_setting
    _pctx.setting_set = save_setting
    _pctx.get_model = lambda: (load_setting("model") or "qwen3:8b")

    def _push_event(evt, data):
        # 往前端推事件：window.__dfyEvent(evt, data)
        try:
            window.evaluate_js(
                "(window.__dfyEvent||function(){})(%s,%s)" % (
                    json.dumps(evt), json.dumps(data, ensure_ascii=False)))
        except Exception:
            pass

    _pctx.push_event = _push_event
    PLUGIN_APIS, PLUGIN_MANIFEST = _plugins.load_plugins(_pctx)
except Exception as _e:
    try:
        _log_api_err(lambda: None, _e)
    except Exception:
        pass

def plugin_list():
    """给前端显示已装插件清单。"""
    return PLUGIN_MANIFEST

def plugin_set_enabled(pid, on):
    """前端开关插件：写 data/plugins.json，重启后生效。

    只改开关文件，不动正在跑的插件——中途卸载插件容易把它起的线程弄成孤儿，
    让用户重启一次最省心。
    """
    import json as _json
    pid = str(pid or "").strip()
    if not pid:
        return {"ok": False, "error": "缺插件 id"}
    want = bool(on)
    path = os.path.join(DATA_DIR, "plugins.json")
    sw = {}
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                sw = _json.load(f) or {}
    except Exception:
        sw = {}
    sw[pid] = want
    try:
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(sw, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "id": pid, "enabled": want, "restart": True,
            "note": "已%s，重启大肥鱼后生效" % ("开启" if want else "关闭")}

# ===== 自检回传通道（仅 DFY_SELFTEST=1 时暴露给前端）=====
# 结果由前端主动推回来：比 Python 定时 evaluate_js 轮询稳，也不会在页面卡住时互相等死。
_SELFTEST_BOX = {"data": None}
_SELFTEST_EVT = threading.Event()


def selftest_report(payload):
    """前端把自检结果推回来，Python 侧只等事件，不去轮询页面。"""
    try:
        _SELFTEST_BOX["data"] = payload
        _SELFTEST_EVT.set()
    except Exception:
        pass
    return "ok"


window.expose(*([_safe_api(_f) for _f in (
              load_chats, save_chats, delete_chat_disk, web_search, repo_search, read_file, run_python, kb_search, open_kb,
              take_screenshot, read_clipboard, write_clipboard, extract_url, tts_speak, gen_title, open_folder, pick_avatar, save_avatar, save_setting, load_setting,
              load_memory, save_memory, remember_fact, reflect_memory, pet_mutter,
              gen_persona, test_persona, get_time, get_time_brief, get_env, list_dir, env_sense, model_caps,
              screen_size, mouse_click, type_text, press_key, agent_decide, plugin_list,
              plugin_set_enabled)]
              + [_safe_api(_f) for _f in PLUGIN_APIS]
              + ([_safe_api(selftest_report)] if os.environ.get("DFY_SELFTEST") == "1" else [])))
_SELFTEST_JS = r"""
(() => {
  const q = id => document.getElementById(id);
  const info = id => { const e = q(id); if(!e) return null;
    const s = getComputedStyle(e), r = e.getBoundingClientRect();
    return {w: Math.round(r.width), h: Math.round(r.height),
            bg: s.backgroundColor, color: s.color, vis: s.display !== 'none'}; };
  const imgs = [...document.querySelectorAll('img')].map(i => ({src: (i.currentSrc||i.src||'').split('/').pop(), ok: i.naturalWidth > 0}));
  return JSON.stringify({
    ready: document.readyState,
    bodyLen: document.body ? document.body.innerHTML.length : -1,
    title: document.title,
    pywebview: typeof window.pywebview,
    apiCount: (window.pywebview && window.pywebview.api) ? Object.keys(window.pywebview.api).length : 0,
    theme: document.documentElement.getAttribute('data-theme'),
    bodyBg: getComputedStyle(document.body).backgroundColor,
    // 文本能否选中：body 应为 none（界面），.msgs/气泡应为 text（聊天内容）
    selBody: getComputedStyle(document.body).userSelect || getComputedStyle(document.body).webkitUserSelect,
    selMsgs: q('msgs') ? (getComputedStyle(q('msgs')).userSelect || getComputedStyle(q('msgs')).webkitUserSelect) : null,
    selBubble: document.querySelector('.abubble') ? (getComputedStyle(document.querySelector('.abubble')).userSelect || getComputedStyle(document.querySelector('.abubble')).webkitUserSelect) : null,
    selInput: q('input') ? (getComputedStyle(q('input')).userSelect || getComputedStyle(q('input')).webkitUserSelect) : null,
    sidebar: info('sidebar'), header: info('header') , msgs: info('msgs'),
    input: info('input'), sendBtn: info('sendBtn'), emptyState: info('emptyState'),
    chatCount: (typeof chats !== 'undefined' && chats) ? chats.length : null,
    msgCount: q('msgs') ? q('msgs').children.length : null,
    imgOk: imgs.filter(i => i.ok).length, imgTotal: imgs.length,
    imgs: imgs.slice(0, 6),
    hub: !!q('pluginHub'), hubNav: q('hubNav') ? q('hubNav').children.length : -1,
    dock: q('pluginDockRow') ? q('pluginDockRow').children.length : -1,
    hubViews: (typeof _hubViews !== 'undefined' && _hubViews) ? Object.keys(_hubViews).length : -1,
    text: (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').slice(0, 200)
  });
})()
"""


_CHATTEST_JS = r"""
(() => {
  window.__chattest = {done:false, stage:'kick'};
  (async () => {
    const st = window.__chattest;
    try{
      try{ closePluginHub && closePluginHub(); }catch(_){}
      // 包一层 fetch，记录这一轮到底有没有发出请求、发了多大
      const _of = window.fetch;
      st.reqLen = -1;
      window.fetch = function(u, opt){
        try{
          const b = opt && opt.body ? String(opt.body) : '';
          st.reqUrl = String(u).slice(-30);
          st.reqLen = b.length;
          const j = b ? JSON.parse(b) : null;
          if (j){ st.reqModel = j.model; st.reqMsgs = (j.messages||[]).length; st.reqOpts = j.options; }
        }catch(_){}
        return _of.apply(this, arguments);
      };
      const inp = document.getElementById('input');
      inp.value = '今天加班到十点，好累啊';
      send();                                   // 不 await：要边等边看进度，模型慢时不能假死
      st.stage = 'sending';
      let last = {};
      for (let i = 0; i < 240; i++){            // 最多等 180 秒
        await new Promise(r => setTimeout(r, 750));
        last = msgs[msgs.length-1] || {};
        st.partial = (last.content || '').slice(-60);
        st.pending = !!last.pending;
        if (!busy && !last.pending && (last.content || '')){ st.stage = 'finished'; break; }
      }
      await new Promise(r => setTimeout(r, 4000));   // 等异步补图
      last = msgs[msgs.length-1] || {};
      st.content = (last.content || '').slice(0, 100);
      st.sticker = last.sticker || '';
      st.model = MODEL;
      st.saved = await window.pywebview.api.save_setting('model', MODEL);
      st.readback = await window.pywebview.api.load_setting('model');
      // 时间标记：验证新消息带 ts、送进模型的历史真的带了 [时间] 前缀
      st.ts = [msgs[msgs.length-2] && msgs[msgs.length-2].ts, last.ts];
      st.hist = buildHistMsgs().slice(-3);
      st.ui = [...document.querySelectorAll('.msg-time')].map(e => e.textContent).slice(-3);
      st.done = true;
    }catch(e){ st.err = String(e); st.done = true; }
    try{ window.pywebview.api.selftest_report(JSON.stringify(st)); }catch(_){}
  })();
})();
"""
_TIMETEST_JS = r"""
(() => {
  // 纯前端验证：塞一段带时间戳的假对话，不调用模型
  window.__timetest = {done:false};
  (async () => {
    const st = window.__timetest;
    try{
      try{ closePluginHub && closePluginHub(); }catch(_){}
      const now = Date.now(), M = 60000, H = 3600000;
      msgs = [
        {role:'user', content:'我睡不着，心里有点乱', ts: now - 13*H},
        {role:'assistant', content:'那我陪你一会儿，别一个人扛。', ts: now - 13*H + M},
        {role:'user', content:'谢谢你啊，跟我说说话就好多了', ts: now - 13*H + 5*M},
        {role:'assistant', content:'嗯，我一直都在。', ts: now - 13*H + 6*M},
        {role:'user', content:'那我去睡啦，晚安', ts: now - 12.7*H},
        {role:'assistant', content:'晚安，好好睡。', ts: now - 12.7*H + 40000},
        {role:'user', content:'昨晚没睡好，现在脑子有点糊', ts: now - 4*M},
        {role:'assistant', content:'那今天别硬撑，中午补个觉。', ts: now - 3*M}
      ];
      render();
      st.hist = buildHistMsgs().map(m => m.role + ': ' + m.content);
      st.ui = [...document.querySelectorAll('.msg-time')].map(e => e.textContent);
      st.histLen = buildHistMsgs().length;
      st.done = true;
    }catch(e){ st.err = String(e); st.done = true; }
  })();
})();
"""
_HUBTEST_JS = r"""
(() => {
  window.__hubtest = {done:false, res:{}};
  (async () => {
    const pids = Object.keys(_hubViews || {});
    for (const pid of pids){
      try{
        openPluginHub(pid);
        await new Promise(r => setTimeout(r, 1200));
        const v = _hubViews[pid];
        if (v && v.refresh) await v.refresh();
        const body = document.getElementById('hubBody');
        window.__hubtest.res[pid] = {
          name: (document.getElementById('hubName')||{}).textContent,
          sub: (document.getElementById('hubSub')||{}).textContent,
          len: body ? body.innerText.trim().length : 0,
          head: body ? body.innerText.replace(/\s+/g,' ').slice(0,80) : ''
        };
      }catch(e){
        window.__hubtest.res[pid] = {err: String((e && e.message) || e)};
      }
    }
    closePluginHub();
    window.__hubtest.done = true;
  })();
  return 'started';
})()
"""


def _win_hwnd():
    """拿到主窗口句柄。pywebview 的 Window 没暴露 hwnd，从 .native（WinForms Form）取。"""
    try:
        return int(window.native.Handle.ToInt64())
    except Exception:
        pass
    try:
        h = ctypes.windll.user32.FindWindowW(None, "大肥鱼AI · By Childe")
        if h:
            return int(h)
    except Exception:
        pass
    return 0


def _grab_window(hwnd):
    """直接抓某个窗口的画面（PrintWindow），不受其他窗口遮挡影响。仅调试用。"""
    import ctypes.wintypes as wt
    from PIL import Image
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        raise RuntimeError("窗口尺寸不对")
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, 2)          # PW_RENDERFULLCONTENT

    class _BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                    ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    bi = _BIH()
    bi.biSize = ctypes.sizeof(_BIH)
    bi.biWidth, bi.biHeight = w, -h
    bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")


def _run_selftest():
    """仅调试用：DFY_SELFTEST=1 时，加载完成后把页面真实状态写到 _selftest.json 并退出。"""
    import traceback as _tb
    out = os.path.join(cache_dir, "_selftest.json")
    def _worker():
        time.sleep(3.0)
        data = {}
        box = {}
        def _call():
            try:
                box["v"] = window.evaluate_js(_SELFTEST_JS)
            except Exception:
                box["e"] = _tb.format_exc()
        t = threading.Thread(target=_call, daemon=True)
        t.start()
        t.join(12)
        if "v" in box:
            data["eval"] = box["v"]
        elif "e" in box:
            data["eval_error"] = box["e"]
        else:
            data["eval_error"] = "evaluate_js 超时（12s 未返回）"

        # 真实发一句话，看表情包会不会挂上（仅 DFY_CHATTEST=1）
        if os.environ.get("DFY_CHATTEST") == "1":
            try:
                _SELFTEST_EVT.clear()
                _SELFTEST_BOX["data"] = None
                window.evaluate_js(_CHATTEST_JS)
                if not _SELFTEST_EVT.wait(280):
                    data["chattest"] = _SELFTEST_BOX["data"] or "超时"
                else:
                    data["chattest"] = _SELFTEST_BOX["data"]
            except Exception:
                data["chattest_error"] = _tb.format_exc()

        # 时间标记渲染验证（仅 DFY_TIMETEST=1）：塞假对话，不碰模型
        if os.environ.get("DFY_TIMETEST") == "1":
            try:
                window.evaluate_js(_TIMETEST_JS)
                time.sleep(2.5)
                data["timetest"] = window.evaluate_js("JSON.stringify(window.__timetest||{})")
            except Exception:
                data["timetest_error"] = _tb.format_exc()

        # 逐个插件页面真实渲染（仅 DFY_HUBTEST=1 时跑，用来验证插件中心）
        if os.environ.get("DFY_HUBTEST") == "1":
            try:
                window.evaluate_js(_HUBTEST_JS)
                for _i in range(90):
                    time.sleep(1)
                    got = None
                    try:
                        got = window.evaluate_js("JSON.stringify(window.__hubtest||{})")
                    except Exception:
                        pass
                    if got and '"done":true' in got:
                        data["hubtest"] = got
                        break
                else:
                    data["hubtest"] = got or "超时"
            except Exception as _e:
                data["hubtest_error"] = _tb.format_exc()

        # 用户发表情包：打开面板截图；DFY_EMJCLICK=1 时再点一张发出去
        if os.environ.get("DFY_EMJTEST") == "1":
            try:
                window.evaluate_js("try{closePluginHub()}catch(e){}")
                window.evaluate_js("toggleEmojiPanel(true)")
                _st = {}
                for _i in range(30):                      # 最多等 12s，等首屏图下下来
                    time.sleep(0.4)
                    try:
                        _st = json.loads(window.evaluate_js(
                            "JSON.stringify({on:EMJ.on,emo:EMJ.emo,total:EMJ.total,off:EMJ.off,"
                            "cells:document.querySelectorAll('#emjGrid .emj-cell').length,"
                            "imgs:document.querySelectorAll('#emjGrid img').length,"
                            "tabs:document.querySelectorAll('.emj-tab').length,"
                            "info:(document.getElementById('emjInfo')||{}).textContent})") or "{}")
                    except Exception:
                        _st = {}
                    if _st.get("imgs", 0) >= 10:
                        break
                data["emj_panel"] = _st
                _img = _grab_window(_win_hwnd())
                _p1 = os.path.join(cache_dir, "_emj_panel.png")
                _img.save(_p1, "PNG")
                data["emj_panel_shot"] = _p1
                if os.environ.get("DFY_EMJCLICK") == "1":
                    window.evaluate_js("document.querySelector('#emjGrid .emj-cell').click()")
                    time.sleep(25.0)
                    data["emj_after"] = window.evaluate_js(
                        "JSON.stringify(msgs.slice(-3).map(function(m){return {r:m.role,c:(m.content||'').slice(0,60),"
                        "s:(m.sticker||'').slice(-24),n:m.stickerName||''}}))")
                    _img2 = _grab_window(_win_hwnd())
                    _p2 = os.path.join(cache_dir, "_emj_sent.png")
                    _img2.save(_p2, "PNG")
                    data["emj_sent_shot"] = _p2
            except Exception:
                data["emj_error"] = _tb.format_exc()

        # 截图（仅 DFY_SHOT=插件id 时跑，用来核对界面）
        _shot = os.environ.get("DFY_SHOT")
        if _shot:
            try:
                _pid = "" if _shot == "1" else _shot
                # 聊天测试时截的是对话界面，不能再把插件中心弹出来
                if os.environ.get("DFY_CHATTEST") != "1" and os.environ.get("DFY_TIMETEST") != "1":
                    window.evaluate_js("openPluginHub(%s)" % json.dumps(_pid, ensure_ascii=False))
                else:
                    window.evaluate_js("try{closePluginHub()}catch(e){}")
                try:
                    _h0 = int(window.hwnd)
                    ctypes.windll.user32.ShowWindow(_h0, 9)          # SW_RESTORE
                    ctypes.windll.user32.SetForegroundWindow(_h0)     # 抢到最前
                    ctypes.windll.user32.BringWindowToTop(_h0)
                except Exception:
                    pass
                time.sleep(3.2)
                _img = _grab_window(_win_hwnd())
                _p = os.path.join(cache_dir, "_hub_shot.png")
                _img.save(_p, "PNG")
                data["shot"] = _p
            except Exception:
                data["shot_error"] = _tb.format_exc()

        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception:
            pass
        try:
            window.destroy()
        except Exception:
            pass
        os._exit(0)
    threading.Thread(target=_worker, daemon=True).start()


if os.environ.get("DFY_SELFTEST") == "1":
    _run_selftest()

webview.start(storage_path=os.environ.get("DFY_WV2_PROFILE") or "D:\\大肥鱼缓存\\wv2_profile")
