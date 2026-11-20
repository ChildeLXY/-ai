import api_base
import webview
import os
import ctypes
import threading
import time
import json
import urllib.request
import urllib.error
import urllib.parse
import subprocess
import sys
import re
import base64
import datetime
import llm_params          # num_ctx / keep_alive 的唯一来源，改动前先看那个文件
import socket

# 桌宠/插件的工作区已从 C:\Users\<用户>\.dsh 搬到 D 盘。
# 这里兜底设一次：不管用 start.ps1、bat 还是直接跑 py，插件都能找对地方。
# 用户环境变量里也设了 DSH_HOME，但没有重新登录时不一定生效。
os.environ.setdefault("DSH_HOME", r"D:\本地部署大肥鱼环境\dsh")

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


# 接口调用日志改成「内存攒一批再落盘」：原来每调用一次就 open/append/close，
# 前端十来个定时器轮询时，这块小 IO 一直在抢磁盘。现在攒 2 秒或 200 行才写一次，
# 崩溃排查的用途不变（异常时由 atexit 兜底 flush）。
_TRACE_BUF = []
_TRACE_LAST = [0.0]
_TRACE_PATH = os.path.join(cache_dir, "_api_trace.log")


def _trace_flush():
    if not _TRACE_BUF:
        return
    try:
        try:
            if os.path.getsize(_TRACE_PATH) > 2 * 1024 * 1024:
                with open(_TRACE_PATH, "w", encoding="utf-8"):
                    pass
        except Exception:
            pass
        with open(_TRACE_PATH, "a", encoding="utf-8") as f:
            f.write("".join(_TRACE_BUF))
        _TRACE_BUF.clear()
    except Exception:
        pass


def _api_trace(name):
    """记录每次 JS→Python 接口调用，用于定位闪退前最后执行的接口。"""
    try:
        _TRACE_BUF.append("%s %s\n" % (time.strftime("%H:%M:%S"), name))
        now = time.time()
        if len(_TRACE_BUF) >= 200 or (now - _TRACE_LAST[0]) > 2:
            _TRACE_LAST[0] = now
            _trace_flush()
    except Exception:
        pass


try:
    import atexit
    atexit.register(_trace_flush)
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

def read_image(path):
    """把图片读成 base64 给模型"看"（Ollama 的 images 字段要的就是这个）。

    以前附件一律走 read_file() 当文字读，图片会变成"[不支持 .png]"，
    等于模型根本没看见图。这里单独做一个图片通道。
    顺手压一下：原图动辄 4K，直接塞进去既费 token 又慢。
    """
    try:
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"):
            return {"ok": False, "error": "不是图片: " + ext}
        data = open(path, "rb").read()
        try:
            import io
            from PIL import Image
            im = Image.open(io.BytesIO(data))
            # GIF 动图取第一帧；超过 1280 长的缩一下（1024~1280 对识别够用）
            im.seek(0)
            im = im.convert("RGB")
            if max(im.size) > 1280:
                im.thumbnail((1280, 1280), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
            data = buf.getvalue()
        except Exception:
            pass                      # 没有 PIL 或解不开就原样发
        if len(data) > 8 * 1024 * 1024:
            return {"ok": False, "error": "图片太大（压缩后仍超过 8MB）"}
        import base64
        return {"ok": True, "b64": base64.b64encode(data).decode("ascii"),
                "name": os.path.basename(path), "bytes": len(data)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
        req = urllib.request.Request(api_base.url("/api/ps"))
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
    """向 Ollama 发一次非流式请求，返回正文（失败抛异常）。

    思考型模型会显式开思考链，让推理走 thinking 通道，content 只留真正要说的话，
    这样 JSON / 命令这类结构化输出才不会被推理污染。

    num_ctx 必须和主对话一致（走 llm_params），否则 Ollama 会为这一个请求
    把整个模型重载一遍，实测每次白花约 4 秒。
    """
    # 思考型模型（qwen3/R1 等）：一律显式开思考链，让它在自己的通道里想。
    # 传 think=false 反而会把推理写进正文（实测 362 字），结构化输出（JSON）直接解析失败——
    # 心情判断插件之前就是这样一直报"解析失败"的。
    if _thinks(model):
        think = True
        # 思考和正文共用 num_predict：预算太小，思考吃完就只剩空正文（实测 200 时正文 0 字）。
        # 给思考额外留 1000 的额度，正文才有地方写。
        num_predict = min(max(int(num_predict or 0), 400) + 1000, 2200)
    payload = llm_params.payload(model, messages, stream=False,
                                 num_predict=num_predict, temperature=0,
                                 think=think)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(api_base.url("/api/chat"), data=data,
                                 headers={"Content-Type": "application/json"})
    with _LOCAL_OPENER.open(req, timeout=timeout) as r:
        out = json.loads(r.read().decode("utf-8"))
    msg = out.get("message") or {}
    content = (msg.get("content") or "").strip()
    thinking = (msg.get("thinking") or "").strip()
    # 正文优先。以前是"正文 + 思考"直接拼起来，思考一多就把 JSON/命令冲烂了；
    # 只有正文为空（有的模型把话全写进思考）时才拿思考兜底。
    return content or thinking


def _thinks(model):
    """这个模型有没有思考链。查不到就当没有（不传 think，免得 400）。"""
    try:
        return bool(model_caps(model).get("thinking"))
    except Exception:
        return False


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

# ============ 工作模式：可中断停止 + 可靠性 ============
_WB_CANCEL = threading.Event()

# 危险动作关键词：命中时强制标记，让前端二次确认（防止误点删除/支付/卸载等）
_DANGER_WORDS = ("删除", "卸载", "格式化", "清空", "支付", "转账", "关机", "重启",
                 "注销", "永久", "移除", "回收站", "覆盖", "确认删除", "关闭程序")


class _TaskCancelled(Exception):
    pass


def wb_reset():
    _WB_CANCEL.clear()
    return "ok"


def wb_cancel():
    _WB_CANCEL.set()
    return "ok"


def wb_cancelled():
    return _WB_CANCEL.is_set()


def _danger_hit(task, action, window_title, reason):
    blob = " ".join(str(x) for x in (task, action, window_title, reason))
    return any(w in blob for w in _DANGER_WORDS)


def _parse_action(content):
    content = re.sub(r"<think>[\s\S]*?</think>", "", content or "").strip()
    m = re.search(r"\{[\s\S]*\}", content)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _wb_chat_stream(sys_p, user_msg, img_b64, json_mode, deadline):
    """流式调用视觉模型，逐段检查取消标志与单步超时；返回拼接文本。"""
    body = llm_params.payload(
        "qwen3-vl:2b",
        [{"role": "system", "content": sys_p},
         {"role": "user", "content": user_msg, "images": [img_b64]}],
        stream=True, num_predict=1000, temperature=0.1)
    body["think"] = True
    if json_mode:
        body["format"] = "json"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(api_base.url("/api/chat"), data=data,
                                 headers={"Content-Type": "application/json"})
    parts = []
    with _LOCAL_OPENER.open(req, timeout=30) as r:
        for raw in r:
            if _WB_CANCEL.is_set():
                try:
                    r.close()
                except Exception:
                    pass
                raise _TaskCancelled()
            if time.time() > deadline:
                try:
                    r.close()
                except Exception:
                    pass
                return "".join(parts)
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                j = json.loads(line)
            except Exception:
                continue
            c = (j.get("message") or {}).get("content") or ""
            if c:
                parts.append(c)
            if j.get("done"):
                break
    return "".join(parts)


def agent_decide(task, history="", step=0):
    """截图 -> 视觉模型看屏幕 -> 输出下一步动作 JSON。
    可中断（wb_cancel）、JSON 强约束、解析失败重试、环境注入、危险动作标记。"""
    try:
        if _WB_CANCEL.is_set():
            return {"action": "done", "reason": "已停止", "_cancelled": True}
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
                img_b64 = base64.b64encode(f.read()).decode()
        except Exception:
            with open(shot, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
        sw = screen_size()
        win_title = _active_window() or ""
        env_ctx = (
            "【真实环境，唯一权威依据】当前时间：%s；当前激活窗口：%s；屏幕分辨率：%dx%d。"
            "禁止从聊天记录推算时间。" % (get_time(), win_title, sw["w"], sw["h"])
        )
        sys_p = (
            "你是一个能操控这台 Windows 电脑的智能助手（工作模式）。"
            "我会给你当前屏幕截图和用户任务。你只输出一个 JSON，不要任何解释文字。"
            '格式：{"reason":"一句话说明","action":"click|type|key|done",'
            '"x":0到1之间的数,"y":0到1之间的数,"text":"要输入的文字","key":"要按的键"}\n'
            "规则：click 时给归一化坐标 x/y（左上角0,0 右下角1,1）；type 时 text 为要输入的内容；"
            "key 时 key 如 enter/ctrl+s/alt+tab；任务完成或无法继续时 action=done。"
            "谨慎操作，不要点删除/确认删除/支付/卸载这类危险按钮。\n" + env_ctx
        )
        user_msg = "用户任务：" + task + "\n历史步骤：" + (history or "无") + "\n第%d步。现在截图如上，下一步做什么？只输出JSON。" % step

        try:
            deadline = time.time() + 120
            content = _wb_chat_stream(sys_p, user_msg, img_b64, True, deadline)
            action = _parse_action(content)
            if action is None and not _WB_CANCEL.is_set():
                # 解析失败重试一次：去掉 JSON 约束，避免个别模型被约束后行为异常
                deadline = time.time() + 90
                content = _wb_chat_stream(sys_p, user_msg, img_b64, False, deadline)
                action = _parse_action(content)
        except _TaskCancelled:
            return {"action": "done", "reason": "已停止", "_cancelled": True}

        if action is None:
            action = {"action": "done", "reason": "模型未输出有效动作"}
        action["_screenshot"] = shot
        action["_dangerous"] = bool(_danger_hit(task, action.get("action"),
                                                win_title, action.get("reason")))
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
    """朗读一句话。音色按设置走：
    - kurena：自己训的 Kiriziki Kurena 2.0（RVC 变声，走常驻进程，首句约 20 秒、之后 2~3 秒）
    - edge  ：微软晓伊（原来那套，免费、不用显卡）
    Kurena 出任何问题都自动退回 edge —— 念不出来比念得不像更难受。"""
    text = (text or "")
    try:
        engine = (load_setting("voice_engine") or "edge").strip().lower()
    except Exception:
        engine = "edge"
    if engine == "kurena":
        try:
            import dfy_voice
            r = dfy_voice.say(text)
            if r and not r.startswith("["):
                return r
            _voice_log("kurena 失败，退回 edge：" + str(r))
        except Exception as e:
            _voice_log("kurena 异常，退回 edge：" + str(e))
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
        return "[语音失败: " + str(e) + "]"


def _voice_log(msg):
    try:
        with open(os.path.join(cache_dir, "_voice.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def voice_status():
    """给设置面板看的：Kurena 能不能用、现在在不在显存里。"""
    try:
        import dfy_voice
        st = dfy_voice.status()
        st["available"] = dfy_voice.available()
        try:
            st["engine"] = (load_setting("voice_engine") or "edge")
        except Exception:
            st["engine"] = "edge"
        return st
    except Exception as e:
        return {"available": False, "ready": False, "error": str(e), "engine": "edge"}


def voice_stop():
    """立刻把她从显存里请出去（打游戏前想腾显卡就点这个）。"""
    try:
        import dfy_voice
        return dfy_voice.stop()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def voice_warm():
    """预热：把模型先载进显存，之后第一句就是 2~3 秒（约 20 秒一次）。"""
    try:
        import dfy_voice
        return {"ok": bool(dfy_voice.warm())}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def gen_title(first_msg, model="qwen3:4b"):
    """Ask Ollama to generate a short title for the conversation."""
    try:
        data = json.dumps(llm_params.payload(
            model,
            [{"role": "user", "content": "用不超过8个字数概括这句话的主题，只输出标题：" + first_msg[:100]}],
            stream=False, num_predict=40)).encode("utf-8")
        req = urllib.request.Request(api_base.url("/api/chat"), data=data, headers={"Content-Type": "application/json"})
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


def open_external_url(url):
    """前端 pywebview.api.open_external_url 的后端实现。

    之前前端调它、后端没暴露：点链接会静默失败（test_api_wiring 早就红着，
    只是没人当门禁用）。只放行 http/https，避免 file:// 或本地路径被当链接打开。
    """
    try:
        u = str(url or "").strip()
        low = u.lower()
        if not (low.startswith("http://") or low.startswith("https://")):
            return "[error: only http/https]"
        webbrowser.open(u)
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
        import json
        sp = _settings_file()
        data = {}
        if os.path.exists(sp):
            # utf-8-sig：文件被记事本/PowerShell 写过就可能带 BOM，用 utf-8 读会直接抛错
            try:
                raw = open(sp, encoding='utf-8-sig').read()
                data = json.JSONDecoder().raw_decode(raw.lstrip())[0]
            except Exception:
                data = {}      # 文件已经坏了就别在坏数据上继续写，整份重建
        data[key] = value
        # 先写同目录临时文件再 os.replace：主程序/网页端/桌宠/QQ 桥接是四个独立进程，
        # 直接往原文件 dump 会被并发写撕成"半截尾部重复"的坏 JSON（已经真实踩到过一次，
        # 结果模型悄悄退回默认、语音开关读成空值显示"语音关"）。
        tmp = sp + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, sp)
        return "ok"
    except Exception as e:
        return str(e)

def load_setting(key):
    try:
        sp = _settings_file()
        if os.path.exists(sp):
            import json
            # 兼容带 BOM 的文件，否则 json 解析会失败、所有设置读出来都是空
            try:
                data = json.load(open(sp, encoding='utf-8-sig'))
            except Exception:
                # 文件被写坏（并发写撕出重复尾巴）时，取最前面那段完整 JSON，别让所有设置一起变空
                raw = open(sp, encoding='utf-8-sig').read()
                data = json.JSONDecoder().raw_decode(raw.lstrip())[0]
            return data.get(key, "")
        return ""
    except:
        return ""

def set_ctx_size(n):
    """前端"上下文用量"面板的拨杆：设置上下文窗口大小。

    同时做两件事：
      1. 写 settings.json（ctx_size）——重启后仍然保留；
      2. 运行时 apply_ctx() 改 llm_params.NUM_CTX——主对话和所有插件立刻统一生效，
         不用重启，也不会因为主对话/插件 num_ctx 不一致让 Ollama 反复重载模型。
    """
    import llm_params as _lp
    val = _lp.apply_ctx(n)
    try:
        save_setting("ctx_size", val)
    except Exception:
        pass
    return {"ok": True, "ctx": val}


def get_ctx_size():
    """读当前生效的上下文窗口大小。"""
    import llm_params as _lp
    return {"ok": True, "ctx": _lp.current_ctx()}


# ============ QQ 桥接（内嵌在主进程，qq_bridge.py）============
# 生命周期跟着主程序，不再起独立进程（独立进程会被工具会话回收）。
_QQ = None                    # qq_bridge.Bridge 实例，window 建好后初始化
_QQ_PUSH = {"fn": None}       # push_event 延迟绑定（定义在后面 2057 行）


def _qq_event(evt, data):
    fn = _QQ_PUSH.get("fn")
    if fn:
        fn(evt, data)


def _qq_init():
    """创建内嵌桥接对象（只建对象，不起服务）。"""
    global _QQ
    if _QQ is not None:
        return _QQ
    try:
        import qq_bridge
        _QQ = qq_bridge.Bridge(load_setting, save_setting, _qq_event,
                               log_fn=(lambda m, lv="INFO": logging.info("QQ | " + str(m))))
    except Exception as e:
        logging.warning("QQ 桥接模块加载失败：%s", e)
    return _QQ


def qq_status():
    """QQ 桥接状态（给主界面 🐧 面板轮询）。"""
    b = _qq_init()
    if not b:
        return {"ok": True, "online": False, "error": "桥接模块不可用",
                "onebot_api": False, "recent": [], "sticker_count": 0}
    st = b.status()
    st["script_exists"] = True
    return st


def qq_start(mode="onebot"):
    """启动内嵌 QQ 桥接服务。"""
    b = _qq_init()
    if not b:
        return {"ok": False, "error": "桥接模块不可用"}
    return b.start()


def qq_stop():
    """停止内嵌 QQ 桥接服务。"""
    b = _qq_init()
    if not b:
        return {"ok": True, "note": "桥接模块不可用"}
    return b.stop()


def qq_send(target, text, kind="private", sticker=""):
    """手动发一条消息/表情包（🐧 面板用）。"""
    b = _qq_init()
    if not b:
        return {"ok": False, "error": "桥接模块不可用"}
    return b.send(target, text, kind, sticker)


def qq_reload():
    """重新加载人格/记忆/表情包（改了设置后用）。"""
    b = _qq_init()
    return b.refresh() if b else {"ok": False, "error": "桥接模块不可用"}


# ---------- QQ 客户端启动（界面一键：关 QQ → 注入 → 等登录 → 起桥接）----------
def _ql_cfg():
    """把设置里的 QQ 路径/端口拼成启动器要的配置。"""
    import qq_launcher
    return qq_launcher.cfg_from(load_setting)


def qq_paths():
    """检查 QQ / NapCat 文件是否齐，顺便回传当前配置。"""
    try:
        import qq_launcher
        c = _ql_cfg()
        r = qq_launcher.check_files(c)
        r["cfg"] = {k: c[k] for k in ("qq_exe", "napcat_dir", "qq_bot", "api_port", "bridge_port")}
        r["qq_running"] = qq_launcher.qq_running()
        return r
    except Exception as e:
        return {"ok": False, "error": str(e), "missing": []}


def qq_launch(force_all=False):
    """一键启动 QQ：后台跑完整条链，界面轮询 qq_launch_state 看进度。

    force_all=True 才关掉机器上所有 QQ（含用户自己的）；默认只重启机器人实例。
    """
    try:
        import qq_launcher

        def _ready():
            b = _qq_init()
            if not b:
                return {"ok": False, "error": "桥接模块不可用"}
            return b.start() if not b.running else {"ok": True, "already": True}

        flag = force_all in (True, 1, "1", "true", "True")
        return qq_launcher.launch(_ql_cfg(), on_ready=_ready, kill_all=bool(flag))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def qq_launch_state():
    """启动进度 + 端口/登录实时状态（带机器人号校验）。"""
    try:
        import qq_launcher
        want = str(load_setting("qq_bot") or "1834347962")
        return qq_launcher.state(bot_uin=want)
    except Exception as e:
        return {"ok": False, "error": str(e), "running": False}


def qq_login_shot():
    """把**机器人实例**的 QQ 窗口（含二维码）截给界面看。"""
    try:
        import qq_launcher
        return qq_launcher.login_shot()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def qq_scan_view(reveal=False):
    """插件页「扫码登录」用：要不要扫码、二维码长什么样，一次拿全。

    和 qq_launch_state 的区别：这里只读、不改启动流程，可以每 2~3 秒刷一次。
    reveal=True 会把藏在后台/托盘的机器人 QQ 登录窗拽到前台再截（只在进页面时给一次）。
    """
    try:
        import qq_launcher
        want = str(load_setting("qq_bot") or "1834347962")
        st = qq_launcher.state(bot_uin=want) or {}
        view = {
            "ok": True,
            "logged_in": bool(st.get("logged_in")),
            "onebot": bool(st.get("onebot_up")),
            "qq_running": bool(st.get("qq_running")),
            "bot_alive": bool(st.get("bot_alive")),
            "running": bool(st.get("running")),
            "step": st.get("step") or "",
            "qq": st.get("uin") or "",
            "nick": st.get("nick") or "",
            "qr": None,
            "need_start": False,
            "note": "",
        }
        if view["logged_in"]:
            view["note"] = "已登录 %s %s" % (view["nick"], view["qq"])
            return view
        if view["running"]:
            view["note"] = st.get("msg") or "正在拉起机器人 QQ…"
        # NapCat 走控制台扫码：QQ 客户端是无窗口进程组，截图永远截不到码，
        # 真正的二维码落在 <napcat_dir>\cache\qrcode.png，直接读文件最快也最准。
        nq = qq_launcher.napcat_qr()
        if nq.get("ok"):
            view["qr"] = nq.get("data")
            view["qr_src"] = "napcat"
            view["qr_time"] = nq.get("mtime") or ""
            url = qq_launcher.napcat_qr_url()
            if url:
                view["qr_url"] = url
            view["note"] = "二维码由 NapCat 生成（%s 刷新），手机 QQ 扫码即可" % (nq.get("mtime") or "刚刚")
            return view
        if nq.get("stale") or nq.get("error"):
            view["note"] = nq.get("error") or view["note"]
        shot = qq_launcher.login_shot(reveal=bool(reveal))
        if shot.get("ok"):
            view["qr"] = shot.get("data")
            view["note"] = "机器人 QQ 正在等扫码"
        else:
            view["need_start"] = bool(shot.get("no_bot") or shot.get("no_window"))
            view["note"] = shot.get("error") or "机器人 QQ 窗口还没出来"
        return view
    except Exception as e:
        return {"ok": False, "error": str(e), "logged_in": False, "qr": None}


def qq_bring():
    """把机器人 QQ 窗口提到最前面。"""
    try:
        import qq_launcher
        return qq_launcher.activate_window()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def qq_voice_say(text="", target="", kind="private"):
    """把一段文字念成语音条发到 QQ（界面上「试听」用）。"""
    try:
        b = _qq_init()
        if not b:
            return {"ok": False, "error": "桥接模块不可用"}
        if not getattr(b, "running", False):
            return {"ok": False, "error": "QQ 桥接没启动，先「启动 QQ 并登录」"}
        return b.voice_say(text, target, kind)
    except Exception as e:
        return {"ok": False, "error": str(e)}


def qq_voice_list():
    """可选音色清单（给设置页下拉用）。"""
    try:
        import qqbot as _qb
        return {"ok": True, "voices": _qb.Voice.VOICES}
    except Exception as e:
        return {"ok": False, "error": str(e), "voices": []}


# ---------------- DeepBuddy 执行层 ----------------
# DeepBuddy 是本地的一套 Agent（工具集 + 危险命令确认门 + 后台进程），
# 这里把它接进来：开大肥鱼AI 时一起拉起，QQ 里也能让它真动手。
def qq_buddy_status():
    try:
        import dfy_buddy
        return dfy_buddy.status()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def qq_buddy_launch():
    try:
        import dfy_buddy
        return dfy_buddy.launch()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def qq_buddy_tools():
    try:
        import dfy_buddy
        return {"ok": True, "tools": dfy_buddy.tools()}
    except Exception as e:
        return {"ok": False, "error": str(e), "tools": []}


# ---------------- DSH 插件转接口 ----------------
# 让 DSH 生态的插件（人设/记忆/心情/贴纸…）对本地大肥鱼生效。
# 底层是 dsh_host 那个兼容宿主，这里只做代理，方便前端调用。
def qq_dsh_status():
    try:
        import dfy_dsh
        return dfy_dsh.status()
    except Exception as e:
        return {"ok": False, "online": False, "error": str(e)}


def qq_dsh_start():
    try:
        import dfy_dsh
        return dfy_dsh.start()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def qq_dsh_restart():
    try:
        import dfy_dsh
        return dfy_dsh.restart_host()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def qq_dsh_search(q=""):
    try:
        import dfy_dsh
        return {"ok": True, "items": dfy_dsh.market_search(q)}
    except Exception as e:
        return {"ok": False, "error": str(e), "items": []}


def qq_dsh_install(name=""):
    """装插件：落点与 DSH 一致（profile/node_modules + package.json）。"""
    try:
        import dfy_dsh
        return dfy_dsh.market_install(name)
    except Exception as e:
        return {"ok": False, "error": str(e)}


def qq_dsh_toggle(name="", enabled=True):
    try:
        import dfy_dsh
        return dfy_dsh.toggle_plugin(name, bool(enabled))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def qq_dsh_context():
    try:
        import dfy_dsh
        return {"ok": True, "parts": dfy_dsh.context_parts(force=True),
                "text": dfy_dsh.context_text(True)[:8000]}
    except Exception as e:
        return {"ok": False, "error": str(e), "parts": [], "text": ""}


# ---------------- DSH 插件市场（独立弹窗） ----------------
def qq_market_open():
    try:
        import dfy_market
        return dfy_market.open_window()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def qq_market_status():
    out = {"ok": True, "pid": 0}
    try:
        import dfy_market
        out["pid"] = dfy_market.window_pid()
        out.update(dfy_market.status())
    except Exception as e:
        out["error"] = str(e)
    return out


def qq_market_close():
    try:
        import dfy_market
        return dfy_market.close_window()
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------- 插件中心内嵌市场（同一份 DSH 官方目录） ----------------
_MARKET_PRIMED = {"done": False}


def _market_mod():
    import dfy_market
    if not _MARKET_PRIMED["done"]:
        # 首次进来先在后台把目录拉一遍，界面立刻有东西看
        _MARKET_PRIMED["done"] = True
        try:
            dfy_market.prime_async()
        except Exception:
            pass
    return dfy_market


def market_status():
    try:
        m = _market_mod()
        out = m.status()
        try:
            out["installed"] = list(m.installed().values())
        except Exception:
            out["installed"] = []
        return out
    except Exception as e:
        return {"ok": False, "total": 0, "error": str(e)}


def market_categories():
    try:
        return _market_mod().categories()
    except Exception:
        return []


def market_search(q="", category="all", page=0, size=40, sort="stars"):
    """在官方目录里本地过滤（毫秒级），不打网络。"""
    try:
        def _i(v, d):
            try:
                return int(float(str(v)))
            except Exception:
                return d
        cat = "" if str(category) in ("", "all", "None") else str(category)
        return _market_mod().search(str(q or ""), cat, _i(page, 0),
                                    min(200, max(1, _i(size, 40))), str(sort or "stars"))
    except Exception as e:
        return {"ok": False, "total": 0, "rows": [], "error": str(e)}


def market_detail(key=""):
    try:
        return _market_mod().detail(str(key))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def market_install(spec=""):
    """装进 DSH 的 profile，装完两边都算已安装。"""
    try:
        if not str(spec).strip():
            return {"ok": False, "error": "没给包名"}
        return _market_mod().install(str(spec).strip())
    except Exception as e:
        return {"ok": False, "error": str(e)}


def market_uninstall(name=""):
    try:
        return _market_mod().uninstall(str(name).strip())
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------- 内置引擎：首次启动下载 ----------------
# 引擎不跟安装包一起走（381.7 MB / 1076.8 MB 解压后），第一次用时才下。
# 状态放进程级字典，前端轮询 engine_status() 拿进度，不阻塞主线程。
_ENGINE_BOOT = {
    "state": "idle",        # idle / running / done / error
    "phase": "",
    "percent": 0.0,
    "bytes_done": 0,
    "bytes_total": 0,
    "error": "",
    "version": "",
}


def _engine_boot_mod():
    import dfy_engine_bootstrap as eb
    return eb


def engine_status():
    """引擎是否就绪 + 下载进度。前端每 1–2 秒轮询一次即可。"""
    try:
        eb = _engine_boot_mod()
        installed = eb.is_engine_installed()
        out = {"ok": True, "installed": installed}
        out.update({k: _ENGINE_BOOT.get(k) for k in
                    ("state", "phase", "percent", "bytes_done", "bytes_total", "error")})
        if installed:
            try:
                out["version"] = eb.EngineBootstrap(eb.default_data_root()).installed_version()
            except Exception:
                out["version"] = ""
        else:
            try:
                out["manifest_url"] = (eb.EngineBootstrap(
                    eb.default_data_root()).manifest_url or "")[:300]
            except Exception:
                out["manifest_url"] = ""
        return out
    except Exception as e:
        return {"ok": False, "error": str(e), "installed": False}


def engine_install():
    """手动/自动触发下载。重复调用是安全的，已经在跑就直接返回。"""
    try:
        eb = _engine_boot_mod()
        if eb.is_engine_installed():
            return {"ok": True, "already": True}
        if _ENGINE_BOOT.get("state") == "running":
            return {"ok": True, "running": True}

        def _prog(phase, **kw):
            _ENGINE_BOOT["phase"] = phase
            if kw.get("percent") is not None:
                _ENGINE_BOOT["percent"] = round(float(kw["percent"]), 1)
            if kw.get("bytes_done"):
                _ENGINE_BOOT["bytes_done"] = int(kw["bytes_done"])
            if kw.get("bytes_total"):
                _ENGINE_BOOT["bytes_total"] = int(kw["bytes_total"])
            if phase == "error":
                _ENGINE_BOOT["error"] = str(kw.get("error") or "")
                _ENGINE_BOOT["state"] = "error"
            elif phase == "done":
                _ENGINE_BOOT["state"] = "done"
                _ENGINE_BOOT["percent"] = 100.0

        def _done(ok, r):
            r = r or {}
            if ok:
                _ENGINE_BOOT["state"] = "done"
                _ENGINE_BOOT["percent"] = 100.0
                _ENGINE_BOOT["version"] = str(r.get("version") or "")
                _push_engine_event("done", _ENGINE_BOOT["version"])
            else:
                _ENGINE_BOOT["state"] = "error"
                _ENGINE_BOOT["error"] = str(r.get("error") or "未知错误")
                _push_engine_event("error", _ENGINE_BOOT["error"])

        _ENGINE_BOOT["state"] = "running"
        _ENGINE_BOOT["phase"] = "checking"
        _ENGINE_BOOT["percent"] = 0.0
        _ENGINE_BOOT["error"] = ""
        bp = eb.EngineBootstrap(eb.default_data_root(), progress=_prog)
        bp.ensure_async(on_done=_done)
        _push_engine_event("start", "")
        return {"ok": True, "started": True}
    except Exception as e:
        _ENGINE_BOOT["state"] = "error"
        _ENGINE_BOOT["error"] = str(e)
        return {"ok": False, "error": str(e)}


def _push_engine_event(kind, detail=""):
    """把引擎状态变化推给前端；前端没挂回调也不影响。"""
    try:
        payload = json.dumps({"kind": kind, "detail": str(detail)[:200]},
                             ensure_ascii=False)
        window.evaluate_js(
            "(function(p){try{if(window.__dfyEngineEvent)window.__dfyEngineEvent(p);}catch(e){}})(%s)"
            % payload)
    except Exception:
        pass


def _auto_engine_bootstrap():
    """启动后自动补引擎。

    只在「没装 + 配了下载源」时自动跑，避免没配源时每台机器都弹错误。
    延迟 3 秒，让主界面先起来。
    """
    def worker():
        time.sleep(3)
        try:
            eb = _engine_boot_mod()
            if eb.is_engine_installed():
                return
            bp = eb.EngineBootstrap(eb.default_data_root())
            if not bp.manifest_url:
                _push_engine_event("no_source", "")
                return
            engine_install()
        except Exception as e:
            _push_engine_event("error", str(e))

    t = threading.Thread(target=worker, name="dfy-engine-auto", daemon=True)
    t.start()
    return t


def qq_zone_status():
    """空间面板状态：登录态、今日计数、下次自动时间、历史。"""
    try:
        import dfy_qzone as qq_zone
        return qq_zone.status()
    except Exception as e:
        return {"ok": False, "error": str(e), "history": []}


def qq_zone_cfg():
    """读空间配置（前端用中文键名）。"""
    try:
        import dfy_qzone as qq_zone
        return {"ok": True, "cfg": qq_zone._cfg()}
    except Exception as e:
        return {"ok": False, "error": str(e), "cfg": {}}


def qq_zone_set_cfg(cfg=None):
    """写空间配置。前端传的是中文键名字典。"""
    try:
        cfg = cfg or {}
        for k in _QZ_KEYS:
            if k not in cfg:
                continue
            v = cfg[k]
            if isinstance(v, bool):
                v = "1" if v else "0"
            save_setting("qzone_" + k, v)
        # 配置改了就重启定时器判断（线程里每轮都会重读，无需重启）
        try:
            import dfy_qzone as qq_zone
            qq_zone.start_scheduler()
        except Exception:
            pass
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def qq_zone_preview(hint=""):
    """让模型写一条说说（不发布）。"""
    try:
        import dfy_qzone as qq_zone
        r = qq_zone.ai_write(hint or "", want_image=True)
        if r.get("ok"):
            return {"ok": True, "text": r.get("text", ""), "image": r.get("image", ""),
                    "image_path": r.get("image_path", "")}
        return r
    except Exception as e:
        return {"ok": False, "error": str(e)}


def qq_zone_publish(text="", pick_name="", auto_img=False):
    """发一条说说。pick_name：指定表情名；auto_img：按文案自动挑图。"""
    try:
        import dfy_qzone as qq_zone
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "内容为空"}
        name, path = "", ""
        if pick_name:
            name, path = qq_zone.pick_sticker(pick_name)
            if not path:
                return {"ok": False, "error": "没找到表情「%s」" % pick_name}
        elif auto_img:
            name, path = qq_zone.pick_sticker(text)
        imgs = [path] if path else []
        r = qq_zone.publish(text, ugc_right=qq_zone._cfg()["查看权限"], images=imgs)
        qq_zone.bump_today()
        qq_zone._add_hist({
            "time": time.strftime("%m-%d %H:%M"), "mode": "manual",
            "ok": bool(r.get("ok")), "text": text,
            "images": imgs if r.get("images") else [],
            "err": "" if r.get("ok") else r.get("error", ""),
            "tid": r.get("tid", ""),
        })
        if r.get("ok"):
            out = {"ok": True, "tid": r.get("tid", ""),
                   "sticker": name if r.get("images") else ""}
            if r.get("warn"):
                out["warn"] = r["warn"]
            return out
        return r
    except Exception as e:
        return {"ok": False, "error": str(e)}


def qq_zone_delete(tid=""):
    """删一条说说。"""
    try:
        import dfy_qzone as qq_zone
        r = qq_zone.delete(tid)
        if r.get("ok"):
            h = [x for x in qq_zone._hist() if str(x.get("tid")) != str(tid)]
            try:
                json.dump(h, open(qq_zone.HIST_PATH, "w", encoding="utf-8"),
                          ensure_ascii=False)
            except Exception:
                pass
        return r
    except Exception as e:
        return {"ok": False, "error": str(e)}



def qq_fix_report():
    """自检并修复 NapCat 的上报配置（带 BOM 会导致整份配置读不到）。"""
    try:
        import qq_launcher

        def _g(k):
            try:
                return load_setting(k)
            except Exception:
                return None

        r = qq_launcher.ensure_report(qq_launcher.cfg_from(_g))
        if r.get("ok"):
            r["hint"] = ("已修正：%s。需要重启 QQ 才会生效，点「重启 QQ 并登录」。" % r.get("action")
                         if r.get("changed") else "上报配置本来就是对的，不用改。")
        return r
    except Exception as e:
        return {"ok": False, "error": str(e)}


def qq_kill(mode="bot"):
    """关 QQ：mode='bot' 只关机器人实例；mode='all' 全关（需用户在界面确认）。"""
    try:
        import qq_launcher
        if str(mode) == "all":
            return qq_launcher.stop_all()
        return qq_launcher.stop_bot()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def qq_cfg_save(payload):
    """保存 QQ 相关配置（QQ 号 / QQ.exe / NapCat 目录 / 端口）。"""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    ok_keys = ("qq_bot", "qq_exe", "qq_napcat_dir", "qq_api_port")
    saved = {}
    try:
        for k, v in (payload or {}).items():
            if k in ok_keys:
                save_setting(k, str(v))
                saved[k] = str(v)
        return {"ok": True, "saved": saved}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def qq_set_autostart(on):
    """写/删 QQ 自启标记文件，供 start.ps1 在下次启动大肥鱼时拉起 QQ 客户端。"""
    flag = r"D:\大肥鱼QQ\auto_start.flag"
    try:
        if on:
            with open(flag, "w", encoding="utf-8") as f:
                f.write("1")
        elif os.path.exists(flag):
            os.remove(flag)
        save_setting("qq_autostart", "1" if on else "0")
        return {"ok": True, "on": bool(on)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
            "请用简短可爱的中文和用户交流，偶尔带点小情绪。不要说自己是AI，就说自己是大肥鱼。"
            "你和 DeepBuddy 都是 Childe 做的同系列本地AI：你负责陪聊和记忆，DeepBuddy 是能动手干活的 Agent，官网 ai.dabaifan.cn/deepbuddy.html；被问起要认，别装不认识。")
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
        req = urllib.request.Request(api_base.url("/api/chat"), data=data,
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
        req = urllib.request.Request(api_base.url("/api/chat"), data=data,
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
        req = urllib.request.Request(api_base.url("/api/chat"), data=data,
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
                 "你和 DeepBuddy 是同系列本地AI，被问起要认。\n"
                 "必须严格按以下人格设定回应：\n" + (persona or "（未设置人格，正常回答）"))
        data = json.dumps(llm_params.payload(
            model, [{"role": "system", "content": sys_p},
                    {"role": "user", "content": user_msg}],
            stream=False, num_predict=150)).encode("utf-8")
        req = urllib.request.Request(api_base.url("/api/chat"), data=data,
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

def _git(*args, timeout=40):
    """跑一条 git 命令（不弹黑窗）。返回 (返回码, 输出)。"""
    try:
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        p = subprocess.run(["git", "-C", _repo_dir()] + list(args),
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace", creationflags=flags)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except Exception as e:
        return -1, str(e)


def _repo_dir():
    """仓库目录。优先用模块级 base（应用里一定有），兜底再算 __file__。"""
    try:
        return base
    except NameError:
        return os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- Gitee 自动更新
_UPDATE_STATE = {"checked": 0, "behind": 0, "log": "", "error": "",
                 "local": "", "remote": "", "busy": False}
_UPDATE_LOCK = threading.Lock()
_UPDATE_TTL = 300          # 5 分钟内复用上次结果，避免频繁 fetch
_AUTO_INTERVAL = 1800      # 后台每 30 分钟复查一次


def _git_dirty_count():
    """已跟踪文件的改动数；未跟踪文件（聊天记录/workspace）不算，免得天天假报警。"""
    rc, out = _git("status", "--porcelain", "--untracked-files=no", timeout=20)
    if rc != 0:
        return -1
    return len([ln for ln in out.splitlines() if ln.strip()])


def _update_snapshot(cached=False):
    with _UPDATE_LOCK:
        s = dict(_UPDATE_STATE)
    s.pop("busy", None)
    s["ok"] = not bool(s.get("error"))
    s["cached"] = bool(cached)
    s["dirty"] = _git_dirty_count()
    s["auto"] = (str(load_setting("autoUpdate") or "") == "1")
    return s


def check_update(force=False):
    """检查 Gitee 有无新提交：只 fetch，不改工作区。后台与手动按钮共用。"""
    import time as _t
    now = _t.time()
    with _UPDATE_LOCK:
        if _UPDATE_STATE["busy"]:
            return _update_snapshot(cached=True)
        if (not force and _UPDATE_STATE["checked"]
                and now - _UPDATE_STATE["checked"] < _UPDATE_TTL):
            return _update_snapshot(cached=True)
        _UPDATE_STATE["busy"] = True
    try:
        rc, out = _git("fetch", "--quiet", "origin", timeout=45)
        if rc != 0:
            with _UPDATE_LOCK:
                _UPDATE_STATE.update({"checked": now,
                                      "error": "拉取失败（网络？）: " + out[-180:]})
            return _update_snapshot()
        rc2, cnt = _git("rev-list", "--count", "HEAD..origin/master")
        if rc2 != 0:
            with _UPDATE_LOCK:
                _UPDATE_STATE.update({"checked": now,
                                      "error": "读不到远端分支: " + (cnt or "")[-180:]})
            return _update_snapshot()
        try:
            n = int((cnt or "0").split()[0])
        except Exception:
            n = 0
        log = _git("log", "--oneline", "HEAD..origin/master", "-8")[1] if n else ""
        local = _git("rev-parse", "--short", "HEAD")[1].strip()
        remote = _git("rev-parse", "--short", "origin/master")[1].strip()
        with _UPDATE_LOCK:
            _UPDATE_STATE.update({"checked": now, "behind": n, "log": log,
                                  "error": "", "local": local, "remote": remote})
        return _update_snapshot()
    finally:
        with _UPDATE_LOCK:
            _UPDATE_STATE["busy"] = False


def update_status():
    """前端启动时读缓存状态，不触发网络请求。"""
    return _update_snapshot(cached=True)


def do_update():
    """快进到 Gitee 最新。工作区有未提交改动时拒绝，绝不覆盖本地代码。"""
    dirty = _git_dirty_count()
    if dirty != 0:
        tip = ("本地有 %d 处未提交改动，已跳过更新（不会覆盖你的代码）。\n" % dirty
               if dirty > 0 else "读不到工作区状态，已跳过更新。\n")
        return {"ok": False, "out": tip + "请先提交或撤销改动，再点更新。"}
    rc, out = _git("pull", "--ff-only", "origin", "master", timeout=150)
    if rc == 0:
        import time as _t
        with _UPDATE_LOCK:
            _UPDATE_STATE.update({"behind": 0, "log": "", "checked": _t.time()})
    return {"ok": rc == 0, "out": out[-600:]}


def auto_update_loop():
    """后台：启动后延迟检查，之后每 30 分钟复查；自动安装仅在工作区干净时发生。"""
    import time as _t
    _t.sleep(10)
    while True:
        try:
            if str(load_setting("autoCheck") or "1") != "0":
                r = check_update(force=True)
                if (r.get("ok") and r.get("behind", 0) > 0
                        and str(load_setting("autoUpdate") or "") == "1"
                        and r.get("dirty") == 0):
                    do_update()
        except Exception:
            pass
        _t.sleep(_AUTO_INTERVAL)


# ---------------------------------------------------------------- 云端模型
def _proxy_alive():
    import socket
    s = socket.socket()
    s.settimeout(0.6)
    try:
        s.connect(("127.0.0.1", 11435))
        return True
    except Exception:
        return False
    finally:
        s.close()


def _ensure_proxy():
    """配置改成云端时，顺手把兼容代理拉起来；不用用户手动开窗口。"""
    if _proxy_alive():
        return True
    try:
        import subprocess
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloud_proxy.py")
        subprocess.Popen([sys.executable, p],
                         cwd=os.path.dirname(p),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(20):
            time.sleep(0.2)
            if _proxy_alive():
                return True
    except Exception:
        pass
    return _proxy_alive()


def get_api_base():
    """前端拿这个决定 fetch 往哪打。"""
    return api_base.base()


def cloud_config_get():
    c = api_base.config()
    c["api_key"] = "*" * 8 if c.get("api_key") else ""   # 不回显完整 key
    c["base"] = api_base.base()
    c["proxy_alive"] = _proxy_alive()
    c["models"] = api_base.cloud_models(c)
    return c


def cloud_fetch_models(base_url="", api_key=""):
    """问厂商"这个 Key 能用哪些模型"。

    智谱是 /api/paas/v4/models，OpenAI 兼容那批是 /v1/models，形状一样。
    Key 可以从参数传（用户填了还没保存），也可以留空用配置里存着的。
    """
    base = (base_url or "").strip().rstrip("/")
    key = (api_key or "").strip()
    if not base:
        base = (api_base.config().get("base_url") or "").strip().rstrip("/")
    if not key:
        key = (api_base.config().get("api_key") or "").strip()
    if not base:
        return {"ok": False, "msg": "没填接口地址"}

    root = base
    for suffix in ("/chat/completions", "/completions"):
        if root.endswith(suffix):
            root = root[:-len(suffix)]
            break
    if not root.rstrip("/").split("/")[-1].startswith("v"):
        root += "/v1"
    url = root + "/models"

    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    # 出公网这一跳：国内 API 直连，走配置里的代理就带上
    px = (api_base.config().get("proxy") or "").strip()
    if px:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": px, "https": px}))
    else:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        req = urllib.request.Request(url, headers=headers)
        with opener.open(req, timeout=25) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
            j = json.loads(body)
            err = j.get("error") or {}
            msg = "%s %s" % (err.get("code"), err.get("message"))
        except Exception:
            msg = str(e)
        return {"ok": False, "msg": "厂商返回 %s：%s" % (e.code, msg[:200]), "url": url}
    except Exception as e:
        return {"ok": False, "msg": str(e)[:200], "url": url}

    items = []
    for it in (data.get("data") or []):
        if isinstance(it, dict):
            mid = str(it.get("id") or "").strip()
            if mid:
                items.append(mid)
    # 厂商的 /models 接口常常不列免费模型（智谱实测 12 个里一个免费的都没有），
    # 所以必须并上内置的免费目录，否则用户永远看不到那批模型。
    free = api_base.free_models_for(base)
    have = set(items)
    for f in free:
        if f["id"] not in have:
            items.append(f["id"])
    items = sorted(set(items))
    return {"ok": True, "url": url, "models": items, "count": len(items),
            "free": [f for f in free]}


def cloud_config_save(cfg_json):
    """设置页保存。开启云端时自动启动代理，关闭时不必停（无害，纯透传）。"""
    try:
        c = json.loads(cfg_json) if isinstance(cfg_json, str) else dict(cfg_json or {})
    except Exception as e:
        return {"ok": False, "msg": "配置格式不对：%s" % e}
    if c.get("api_key") == "*" * 8:
        c.pop("api_key")           # 页面没改 key
    saved = api_base.save_config(c)
    alive = _ensure_proxy() if saved.get("enabled") else True
    return {"ok": True, "enabled": saved.get("enabled"),
            "base": api_base.base(), "proxy_alive": alive,
            "msg": ("已开启，兼容代理已就绪" if alive else "配置已存，但代理没起来，请手动运行 cloud_proxy.py")
            if saved.get("enabled") else "已关闭，恢复纯本地"}


def cloud_test():
    """从前端点「测试连接」：走完整链路发一句 hi。"""
    c = api_base.config()
    if not c.get("enabled"):
        return {"ok": False, "msg": "云端未开启"}
    _ensure_proxy()
    body = llm_params.payload(c.get("model"),
                              [{"role": "user", "content": "hi"}],
                              stream=False, num_predict=8)
    try:
        req = urllib.request.Request(
            api_base.url("/api/chat"), json.dumps(body).encode("utf-8"),
            {"Content-Type": "application/json"})
        with api_base.opener().open(req, timeout=45) as r:
            d = json.loads(r.read().decode("utf-8"))
        reply = (d.get("message") or {}).get("content", "")
        return {"ok": True, "reply": reply[:120], "model": c.get("model")}
    except urllib.error.HTTPError as e:
        # 代理已经把错误翻成人话了，直接从响应体里取，别只显示 "HTTP Error 502"
        try:
            j = json.loads(e.read().decode("utf-8", errors="replace"))
            msg = j.get("error") or str(e)
        except Exception:
            msg = str(e)
        return {"ok": False, "msg": str(msg)[:400]}
    except Exception as e:
        return {"ok": False, "msg": str(e)[:300]}


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
            api_base.url("/api/show"),
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

# 界面必须走 http 起源，不能是 file://。
# 官方 DSH 前端的入口栅栏会拒绝 file:// 起源（Origin: null）的 iframe 请求，
# 直接报 "dsh web: authentication required"。改用 pywebview 自带 http 服务后，
# 大肥鱼和 DSH 都在 127.0.0.1 上（同站），iframe 才拿得到授权。
window = webview.create_window(
    "大肥鱼AI · By Childe",
    html_path,
    width=1280,
    height=860,
    resizable=True,
    # pywebview 默认 text_select=False，会往 body 注入 user-select:none，
    # 结果整个页面一个字都选不中（Ctrl+C 也就没用了）。
    # 打开它，再在 HTML 里把界面部分设回不可选，只留聊天正文可选。
    text_select=True,
)

# DFY_START_TAB=cloud：窗口一打开就停在指定设置页（截图、复现问题时用）
def _jump_start_tab():
    tab = os.environ.get("DFY_START_TAB")
    if tab:
        try:
            # 云端那类页面在设置弹窗里，得先 openSettings 再切 tab。
            # 延迟交给页面自己做：loaded 时脚本可能还没执行完。
            window.evaluate_js(
                "setTimeout(function(){try{openSettings();switchTab(%r)}catch(e){}},1500)" % tab)
        except Exception:
            pass
    # DFY_AUTO_MARKET=1：自检用——启动后自动点一次"插件市场"，把前端到后端的链路跑通
    if os.environ.get("DFY_AUTO_MARKET") == "1":
        try:
            window.evaluate_js(
                "setTimeout(function(){try{hubMarketOpen()}catch(e){"
                "document.title='ERR '+e.message}},2500)")
        except Exception:
            pass


try:
    window.events.loaded += _jump_start_tab
except Exception:
    pass




# 首次启动时引擎没装就后台自动拉取；界面先起来，进度走 engine_status 轮询。
# 没配置下载源时只推一个 no_source 事件，不弹错误。
try:
    _auto_engine_bootstrap()
except Exception:
    pass

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
    # 让内嵌 QQ 桥接也能用同一条事件通道推消息到前端
    _QQ_PUSH["fn"] = _push_event
    PLUGIN_APIS, PLUGIN_MANIFEST = _plugins.load_plugins(_pctx)
except Exception as _e:
    try:
        _log_api_err(lambda: None, _e)
    except Exception:
        pass

def plugin_list():
    """给前端显示已装插件清单。

    以前这里只返回 plugins/*.py 的 Python 插件，市场装的 DSH 插件
    （node_modules 里的 npm 包）永远进不来，用户会以为"装了没同步"。
    现在把两边合成一份，用 kind 区分：Python 插件没有 kind，DSH 插件 kind='dsh'。
    """
    out = list(PLUGIN_MANIFEST)
    try:
        import dfy_market as _mk
        rows = _mk.installed() or {}
        for name, v in rows.items():
            on = bool(v.get("host_enabled"))
            loaded = bool(v.get("loaded"))
            err = (v.get("load_error") or "").strip()
            present = bool(v.get("present"))
            if not present:
                note = ("包目录丢了（package.json 里还记着），点一下重新安装就能修好"
                        if on else "包目录丢了，重新安装后才会生效")
            elif on and not loaded:
                note = err[:160] or "已启用但宿主没加载起来"
            elif on:
                note = ""
            else:
                note = "未加入宿主加载名单，打开开关后重启生效"
            out.append({
                "id": "dsh:" + name,
                "pkg": name,
                "name": name,
                "icon": "🐋",
                "version": v.get("version") or "",
                "enabled": on,
                # 目录都没了肯定算异常；没启用不算异常；启用了却加载失败才算
                "ok": bool(present) and (loaded if on else True),
                "present": present,
                "note": note,
                "kind": "dsh",
                "desc": (v.get("description") or "")[:160],
                "apis": [],
            })
    except Exception as e:
        try:
            _log_api_err(lambda: None, e)
        except Exception:
            pass
    return out

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

# ===== 自己给自己写插件 =====
# 模型按插件契约生成一个 plugins/<id>.py，先 py_compile 验一遍再落盘，
# 落盘即写 plugins.json 打开开关；插件线程在重启后才真正起（同 plugin_set_enabled 的取舍）。
_PLUGIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")
_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

_PLUGIN_CONTRACT = """你在大肥鱼AI里写一个插件。大肥鱼的插件就是 plugins 目录下一个 .py 文件，契约如下：

文件头必须有这四个模块级常量：
PLUGIN_ID = "英文小写下划线 id"
NAME = "中文名字"
ICON = "一个 emoji"
VERSION = "1.0.0"

必须提供 setup(ctx)：模块被加载时调用一次，里面挂线程必须用 daemon=True，别阻塞主线程。
可选提供 apis()：返回 {函数名: 函数}，这些函数会直接挂到前端 window.pywebview.api 上，前端可以 await 调用，参数和返回值都要能 JSON 序列化。
可选提供 shutdown()：停插件时清理。
可选提供 on_event(event, data)：接收主程序广播的事件。

ctx 能用的东西：
ctx.plugin_data(PLUGIN_ID, "data.json")  → 该插件专属数据文件路径（在 data/plugins/<id>/ 下，别写别的地方）
ctx.log(msg)                            → 写日志到缓存目录 _plugins.log
ctx.chat(text)                          → 让大肥鱼本体回一句话（会带上人格和记忆）
ctx.ollama_chat(messages, model=None)   → 直接调本地模型，返回文本
ctx.get_model()                         → 当前模型名

硬约束：
1. 只用一个文件，不要新增依赖包；只能用 Python 标准库 + 项目里已有的 requests/urllib。
2. 禁止访问 C 盘任何路径、禁止写注册表、禁止弹窗、禁止 subprocess 起 GUI。
3. 所有网络请求必须 try/except 兜住，异常只 log 不抛。
4. 不要 import 大肥鱼主程序的模块，只能通过 ctx 拿能力。
5. 只输出一个 ```python 代码块，不要解释。"""


def _plugin_dir():
    d = _PLUGIN_DIR
    if not os.path.isdir(d):
        try:
            alt = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "plugins")
            if os.path.isdir(alt):
                return alt
        except Exception:
            pass
    return d


def _strip_code_fence(text):
    """模型爱在代码外面包 ```，剥掉围栏并截到第一个 PLUGIN_ID 开始。"""
    t = text or ""
    if "```" in t:
        parts = t.split("```")
        # 取最长的那个围栏块
        blocks = [p for p in parts if "PLUGIN_ID" in p]
        if blocks:
            t = max(blocks, key=len)
        else:
            t = parts[1] if len(parts) > 1 else t
    if t.lower().startswith("python"):
        t = t[6:]
    idx = t.find("PLUGIN_ID")
    if idx > 0:
        t = t[idx:]
    return t.strip() + "\n"


def plugin_forge_prompt():
    """前端展示用：告诉用户模型会按什么规矩写。"""
    return {"ok": True, "contract": _PLUGIN_CONTRACT,
            "note": "生成后先编译校验，通过才写进 plugins 目录"}


def plugin_forge_generate(desc, model=None):
    """让模型照插件契约写一个插件，只返回代码，不落盘。"""
    desc = (desc or "").strip()
    if not desc:
        return {"ok": False, "error": "先说清楚这个插件要干什么"}
    if len(desc) > 2000:
        desc = desc[:2000]
    mdl = (model or load_setting("model") or "qwen3:8b")
    msgs = [
        {"role": "system", "content": _PLUGIN_CONTRACT},
        {"role": "user", "content": "我要一个这样的插件：%s\n\n直接给完整代码。" % desc},
    ]
    try:
        raw = _ollama_chat_once(msgs, mdl, timeout=120, num_predict=1400)
    except Exception as e:
        return {"ok": False, "error": "模型没写出来：%s" % e}
    code = _strip_code_fence(raw)
    if "PLUGIN_ID" not in code or "def setup" not in code:
        return {"ok": False, "error": "模型这次没按插件契约写（缺 PLUGIN_ID 或 setup），再试一次",
                "raw": code[:400]}
    pid = ""
    m = re.search(r'PLUGIN_ID\s*=\s*["\']([^"\']+)["\']', code)
    if m:
        pid = m.group(1).strip()
    name = ""
    m2 = re.search(r'NAME\s*=\s*["\']([^"\']+)["\']', code)
    if m2:
        name = m2.group(1).strip()
    # 先做语法体检，省得把坏文件写进插件目录
    ok, err = _forge_check(pid, code)
    if not ok:
        return {"ok": False, "error": err, "code": code, "id": pid}
    return {"ok": True, "code": code, "id": pid, "name": name or pid, "model": mdl}


def _forge_check(pid, code):
    """id 合法 + 能编译 + 能在独立子进程里真加载一次。

    最后这步是照 DSH 的 forge 做法搬过来的：插件代码在子进程里 exec_module 一遍，
    缺 PLUGIN_ID/setup 或者一 import 就炸的，绝不会进主进程、也不会写进 plugins 目录。
    """
    if not pid or not _PLUGIN_ID_RE.match(pid):
        return False, "PLUGIN_ID 只能用英文小写字母开头 + 小写字母/数字/下划线（2-32 位）"
    import py_compile
    import tempfile
    import subprocess
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".py", prefix="dfyforge_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)
        py_compile.compile(tmp, doraise=True, cfile=tmp + "c")
        probe = (
            "import importlib.util,sys\n"
            "spec=importlib.util.spec_from_file_location('dfy_probe',sys.argv[1])\n"
            "m=importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\n"
            "assert getattr(m,'PLUGIN_ID',None),'no PLUGIN_ID'\n"
            "assert getattr(m,'NAME',None),'no NAME'\n"
            "assert callable(getattr(m,'setup',None)),'no setup'\n"
            "print('ok')\n"
        )
        r = subprocess.run([sys.executable, "-c", probe, tmp],
                           capture_output=True, text=True, timeout=25)
        if not (r.returncode == 0 and "ok" in (r.stdout or "")):
            tail = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()
            return False, "代码能编译但加载不起来：" + (tail[-1] if tail else "未知错误")
        return True, ""
    except Exception as e:
        return False, "代码体检没过：%s" % e
    finally:
        for p in (tmp, (tmp + "c") if tmp else None):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


def plugin_forge_save(code, pid=None):
    """把模型写好的插件落盘 + 打开开关。"""
    code = _strip_code_fence(code or "")
    if not code:
        return {"ok": False, "error": "代码是空的"}
    m = re.search(r'PLUGIN_ID\s*=\s*["\']([^"\']+)["\']', code)
    real_id = (pid or (m.group(1) if m else "")).strip()
    if not real_id and m is None:
        return {"ok": False, "error": "代码里没有 PLUGIN_ID"}
    ok, err = _forge_check(real_id, code)
    if not ok:
        return {"ok": False, "error": err}
    d = _plugin_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as e:
        return {"ok": False, "error": "插件目录建不了：%s" % e}
    path = os.path.join(d, real_id + ".py")
    if not os.path.abspath(path).startswith(os.path.abspath(d) + os.sep):
        return {"ok": False, "error": "插件 id 不合法"}
    try:
        # 覆盖前留一份旧版，免得手滑把已有插件写坏
        if os.path.exists(path):
            bak = path + ".bak"
            try:
                import shutil as _sh
                _sh.copy2(path, bak)
            except Exception:
                pass
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
    except Exception as e:
        return {"ok": False, "error": "写文件失败：%s" % e}
    try:
        plugin_set_enabled(real_id, True)
    except Exception:
        pass
    try:
        _net_log("插件生成：%s → %s" % (real_id, path))
    except Exception:
        pass
    return {"ok": True, "id": real_id, "path": path, "restart": True,
            "note": "已写进 plugins 目录并打开开关，重启大肥鱼后它就上岗"}


def plugin_forge_reveal(pid):
    """在资源管理器里定位刚写好的插件文件（方便用户自己改）。"""
    try:
        path = os.path.join(_plugin_dir(), str(pid or "").strip().lstrip("._") + ".py")
        if os.path.exists(path):
            import subprocess as _sp
            _sp.Popen(["explorer", "/select,", path])
            return {"ok": True, "path": path}
        return {"ok": False, "error": "文件不在"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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


# 启动时把上次的上下文窗口大小应用上（前端滑块调过的值）。必须在插件加载前，
# 这样插件第一次调用 llm_params 时拿到的就是用户设定的 num_ctx。
try:
    import llm_params as _lp0
    _saved_ctx = load_setting("ctx_size")
    if _saved_ctx:
        _lp0.apply_ctx(_saved_ctx)
except Exception:
    pass

def read_mood():
    """读取情绪引擎当前状态（由 mood_watcher 维护的 mood.json），供主对话注入。"""
    try:
        mp = os.path.join(os.path.dirname(memory_file), "mood.json")
        if not os.path.exists(mp):
            return {}
        with open(mp, "r", encoding="utf-8-sig") as f:
            st = json.load(f)
        return {"label": st.get("label", ""), "mood": st.get("mood", {}), "updated": st.get("updated", "")}
    except Exception:
        return {}

# ===== DSH 原生界面（插件市场 / 插件管理直接用 DSH 自己的前端）=====
# 按需启动、关闭即释放，避免多挂一个常驻 node 进程拖慢整机。
try:
    import dfy_dshui as _dshui
except Exception as _e:
    _dshui = None
    try:
        sys.stderr.write("[dshui] import failed: %r\n" % (_e,))
    except Exception:
        pass


def dsh_ui_open():
    """打开 DSH 原生界面：返回 {ok, url}，url 自带一次性 token。"""
    if _dshui is None:
        return {"ok": False, "error": "dfy_dshui 模块没加载"}
    return _dshui.open_ui()


def dsh_ui_close():
    """关掉 DSH 界面进程，把内存还回去。"""
    if _dshui is None:
        return {"ok": False}
    return _dshui.close_ui()


def dsh_ui_status():
    if _dshui is None:
        return {"running": False}
    return _dshui.status()


window.expose(*([_safe_api(_f) for _f in (
              dsh_ui_open, dsh_ui_close, dsh_ui_status,
              load_chats, save_chats, delete_chat_disk, web_search, repo_search, read_file, read_image, run_python, kb_search, open_kb,
              take_screenshot, read_clipboard, write_clipboard, extract_url, tts_speak, voice_status, voice_stop, voice_warm, gen_title, open_folder, open_external_url, pick_avatar, save_avatar, save_setting, load_setting,
              load_memory, save_memory, remember_fact, reflect_memory, pet_mutter, read_mood,
              gen_persona, test_persona, get_time, get_time_brief, get_env, list_dir, env_sense, model_caps,
                check_update, do_update, update_status,
              get_api_base, cloud_config_get, cloud_config_save, cloud_test,
              cloud_fetch_models, set_ctx_size, get_ctx_size,
              qq_status, qq_start, qq_stop, qq_send, qq_reload, qq_set_autostart,
              qq_launch, qq_launch_state, qq_login_shot, qq_bring, qq_kill, qq_paths, qq_cfg_save,
              qq_scan_view,
              qq_fix_report, qq_voice_say, qq_voice_list,
              qq_dsh_status, qq_dsh_start, qq_dsh_restart, qq_dsh_search,
              qq_dsh_install, qq_dsh_toggle, qq_dsh_context,
              qq_market_open, qq_market_status, qq_market_close,
              market_status, market_categories, market_search, market_detail,
              market_install, market_uninstall,
              engine_status, engine_install,
              qq_buddy_status, qq_buddy_launch, qq_buddy_tools,
              qq_zone_status, qq_zone_cfg, qq_zone_set_cfg, qq_zone_preview,
              qq_zone_publish, qq_zone_delete,
                screen_size, mouse_click, type_text, press_key, agent_decide,
                wb_cancel, wb_reset, wb_cancelled, plugin_list,
              plugin_set_enabled, plugin_forge_prompt, plugin_forge_generate,
              plugin_forge_save, plugin_forge_reveal)]
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

# 云端模式在开机自启时，代理窗口可能根本没被拉起（以前只在"保存配置"那一刻启动），
# 结果整个程序一个请求都发不出去，还看起来像退不回本地。启动时补一次，后台线程不拖界面。
try:
    if api_base.enabled():
        threading.Thread(target=_ensure_proxy, daemon=True).start()
except Exception:
    pass

# Gitee 更新：后台线程延迟 10 秒做首次检测，之后每 30 分钟复查；
# 默认只提示不安装，自动安装要在设置里单独打开（且工作区干净才执行）。
try:
    if str(load_setting("autoCheck") or "1") != "0":
        threading.Thread(target=auto_update_loop, daemon=True, name="gitee-update").start()
except Exception:
    pass

# QQ 桥接自启：设置里 qq_autostart=1 时随主程序一起起来（内嵌线程，跟着本进程活）
def _qq_boot():
    """开机就绪：起桥接；如果机器上一个 QQ 都没开，顺手把机器人 QQ 拉起来。"""
    try:
        r = qq_start("onebot")
    except Exception as e:
        r = {"ok": False, "error": str(e)}
    try:
        if str(load_setting("qq_autostart") or "0") not in ("1", "true", "True"):
            return
        import time as _t

        _t.sleep(2)
        import qq_launcher
        # 已经登录就不用管；有 QQ 在跑（大概率是你自己的）也绝不擅自杀
        try:
            cur = _QQ.status() if _QQ else {}
        except Exception:
            cur = {}
        if cur.get("logged_in"):
            return
        if qq_launcher.qq_running():
            print("[QQ] 机器上已有 QQ 在运行，跳过自动拉起（避免关掉你自己的号）")
            return
        print("[QQ] 没有 QQ 在运行，自动拉起机器人实例…")
        qq_launcher.launch(qq_launcher.cfg_from(load_setting), kill_all=True)
    except Exception as e:
        print("[QQ] 自动拉起失败：%r" % e)


try:
    if str(load_setting("qq_autostart") or "0") in ("1", "true", "True"):
        threading.Thread(target=_qq_boot, daemon=True, name="qq-autostart").start()
except Exception:
    pass

# 空间定时发说说：线程里每轮都重读配置，界面上开关改了立刻生效
try:
    import dfy_qzone as _qzone
    _qzone.start_scheduler()
    print("[Qzone] 定时器已启动")
except Exception as _e:
    import traceback as _tb
    try:
        with open(r"D:\大肥鱼缓存\qzone.log", "a", encoding="utf-8") as _f:
            _f.write("[%s][ERROR] 定时器启动失败：%r\n%s\n" % (
                time.strftime("%H:%M:%S"), _e, _tb.format_exc()))
    except Exception:
        pass
    print("[Qzone] 定时器启动失败：%r" % _e)

# 内存看护：模型进程提优先级、浏览器降级，内存紧张时修剪浏览器工作集，
# 优先保证大肥鱼（35B 模型）不换页、思维在线
try:
    import dfy_mem_guard as _guard
    _guard.start(interval=60, min_avail_gb=8.0)
    print("[MemGuard] 内存看护已启动")
except Exception as _e:
    print("[MemGuard] 启动失败：%r" % _e)

# DeepBuddy：开大肥鱼AI 的时候把它也带起来（已经在跑就不重复起）。
# 它是一整套 Agent 执行层（工具集 + 危险命令确认门 + 后台进程），
# QQ 那边要用它的能力，所以两件事一起做：拉起窗口、预热模块。
def _boot_buddy():
    try:
        import dfy_buddy
        r = dfy_buddy.launch()
        print("[DeepBuddy] %s" % r)
        dfy_buddy.module()
    except Exception as _e:
        print("[DeepBuddy] 启动失败：%r" % _e)


try:
    threading.Thread(target=_boot_buddy, daemon=True, name="deepbuddy").start()
except Exception:
    pass

# DSH 插件转接口：后台把兼容宿主拉起来（不阻塞界面）。
# 宿主是 D:/大肥鱼AI/dsh_host/host.mjs，负责加载 DSH 插件并把
# 「插件注入的提示词 + 注册的工具」开成 HTTP 给机器人和界面用。
def _boot_dsh_host():
    try:
        import dfy_dsh
        if not dfy_dsh.enabled():
            print("[DSH] 转接口已在设置里关闭")
            return
        r = dfy_dsh.ensure()
        print("[DSH] 宿主：%s" % r)
    except Exception as _e:
        print("[DSH] 启动失败：%r" % _e)


try:
    threading.Thread(target=_boot_dsh_host, daemon=True, name="dsh-host").start()
except Exception:
    pass

# 托盘：点关闭不退出，缩到系统托盘；真要退从托盘菜单选「退出」
try:
    import dfy_tray
    dfy_tray.install(window, icon_path)
    print("[托盘] 已挂载")
except Exception as _e:
    print("[托盘] 挂载失败：%r" % _e)

# http_server=True：把本地页面用 127.0.0.1 上的内置服务发出去（见上方注释）。
# 万一端口/服务出问题，退回原来的起法，保证界面一定打得开。
_fallback_file = "file:///" + html_path.replace(os.sep, "/")
try:
    webview.start(http_server=True, storage_path=os.environ.get("DFY_WV2_PROFILE") or "D:\\大肥鱼缓存\\wv2_profile")
except Exception as _e:
    print("[webview] http 模式启动失败，回退 file://：%r" % (_e,))
    try:
        window.load_url(_fallback_file)
    except Exception:
        pass
    webview.start(storage_path=os.environ.get("DFY_WV2_PROFILE") or "D:\\大肥鱼缓存\\wv2_profile")
