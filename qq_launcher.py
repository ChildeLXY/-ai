# -*- coding: utf-8 -*-
"""QQ 客户端启动器 —— 把原来只能在后台双击 .bat 才能干的事，搬到界面上。

之前要发消息得先手动跑 D:/大肥鱼QQ/启动QQ机器人完整.bat：
    杀掉残留 QQ → NapCat 注入拉起 QQ → 等 OneBot 3000 → 再回界面点启动
现在界面点一下「启动 QQ 并登录」，这条链全自动跑，进度实时显示；
二维码也不用切窗口找，直接把 QQ 窗口截回界面里看。

设计约束：
  · 不 import 主程序，配置由调用方以 dict 传入（避免循环依赖）。
  · 所有阻塞动作都在后台线程里跑，界面只读 _STATE，不会卡窗口。
"""
import os
import io
import time
import json
import base64
import socket
import ctypes
import ctypes.wintypes as wt
import subprocess
import threading

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
# 拉起 QQ 时必须脱离父进程：否则父进程一退出（关控制台、脚本跑完）QQ 会被连带杀掉
SPAWN_FLAGS = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# NapCat 控制台输出重定向后的文件句柄（必须留着引用，否则句柄被回收、子进程写不进去）
_CONSOLE_FHS = []

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

# 64 位句柄必须显式声明，否则返回值被截成 32 位、再传回去时又被截一次，直接抓图失败
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, wt.DWORD]
gdi32.BitBlt.restype = wt.BOOL
user32.GetWindowDC.restype = ctypes.c_void_p
user32.GetWindowDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, ctypes.c_void_p]
user32.PrintWindow.argtypes = [wt.HWND, ctypes.c_void_p, wt.UINT]
user32.PrintWindow.restype = wt.BOOL
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

DEFAULTS = {
    "qq_exe": r"D:\QQ\QQ.exe",
    "napcat_dir": r"D:\NapCat",
    "qq_bot": "1834347962",
    "api_port": 3000,
    "bridge_port": 6700,
    "login_wait": 100,
}


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [_WNDPROC, wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(_RECT)]
user32.GetWindowRect.restype = wt.BOOL
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wt.HWND]
kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, wt.DWORD, wt.LPWSTR,
                                                ctypes.POINTER(wt.DWORD)]


class _BMIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class _BMI(ctypes.Structure):
    _fields_ = [("bmiHeader", _BMIH), ("bmiColors", wt.DWORD * 3)]


gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.UINT, wt.UINT,
                            ctypes.c_void_p, ctypes.POINTER(_BMI), wt.UINT]
gdi32.GetDIBits.restype = ctypes.c_int


# ---------------------------------------------------------------- 配置
def cfg_from(get):
    """get(key) -> 设置值；缺省回落到 DEFAULTS。"""
    c = dict(DEFAULTS)
    try:
        m = {
            "qq_exe": "qq_exe", "napcat_dir": "qq_napcat_dir", "qq_bot": "qq_bot",
            "api_port": "qq_api_port", "bridge_port": "qq_port", "login_wait": "qq_login_wait",
        }
        for k, sk in m.items():
            v = get(sk)
            if v not in (None, ""):
                c[k] = int(v) if isinstance(DEFAULTS[k], int) else str(v)
    except Exception:
        pass
    c["qq_exe"] = os.path.normpath(c["qq_exe"])
    c["napcat_dir"] = os.path.normpath(c["napcat_dir"])
    return c


def check_files(c):
    d = c["napcat_dir"]
    need = {
        "QQ 客户端": c["qq_exe"],
        "NapCat 启动器": os.path.join(d, "NapCatWinBootMain.exe"),
        "NapCat 注入 DLL": os.path.join(d, "NapCatWinBootHook.dll"),
        "NapCat 主程序": os.path.join(d, "napcat.mjs"),
    }
    missing = [k for k, v in need.items() if not os.path.exists(v)]
    return {"ok": not missing, "missing": missing, "paths": need}


def ensure_report(cfg):
    """确保 NapCat 会把消息上报给桥接。

    踩过的坑：PowerShell 写出来的 JSON 带 UTF-8 BOM，Node 的 JSON.parse 直接抛
    「Unexpected token」，NapCat 于是整套 network 配置都没读到 —— QQ 能登录、能发消息，
    但收消息永远不推给桥接。所以每次启动前都把这份文件校验/修正一遍，并且不写 BOM。
    """
    uin = str(cfg.get("qq_bot") or "").strip()
    p = os.path.join(cfg["napcat_dir"], "config", "onebot11_%s.json" % (uin or ""))
    want_url = "http://127.0.0.1:%d/onebot" % int(cfg["bridge_port"])
    api_port = int(cfg["api_port"])
    fixed = {"action": "", "path": p}
    try:
        d = {"network": {}}
        if os.path.exists(p):
            raw = open(p, "rb").read()
            if raw[:3] == b"\xef\xbb\xbf":
                raw = raw[3:]
                fixed["action"] = "去掉 BOM"
            try:
                d = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                fixed["action"] = "重建（原文件解析失败）"
                d = {"network": {}}
        else:
            fixed["action"] = "新建"
        net = d.setdefault("network", {})
        kept, has = [], False
        for x in (net.get("httpClients") or []):
            if not isinstance(x, dict):
                continue
            if str(x.get("url") or "") == want_url:
                if has:            # 去重
                    continue
                has = True
                x = dict(x, enable=True)
            kept.append(x)
        if not has:
            kept.append({"name": "大肥鱼AI", "enable": True, "url": want_url,
                         "messagePostFormat": "array", "reportSelfMessage": False,
                         "token": "", "debug": False})
            fixed["action"] = fixed["action"] or "补上报地址"
        net["httpClients"] = kept
        # API 端口（3000）保留；没配就补上，桥接靠它发消息 / 查登录信息
        servers = net.get("httpServers") or []
        if not any(int(x.get("port") or 0) == api_port for x in servers if isinstance(x, dict)):
            servers.append({"name": "API", "enable": True, "host": "127.0.0.1",
                            "port": api_port, "enableCors": True,
                            "enableWebsocket": False, "messagePostFormat": "array",
                            "token": "", "debug": False})
            fixed["action"] = fixed["action"] or "补 API 端口"
        net["httpServers"] = servers
        net.setdefault("websocketServers", [])   # DSH 用的 3001，原样留着
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(d, ensure_ascii=False, indent=2))
        fixed["ok"] = True
        fixed["url"] = want_url
        fixed["changed"] = bool(fixed["action"])
        return fixed
    except Exception as e:
        return {"ok": False, "error": str(e), "path": p}


# ---------------------------------------------------------------- 进程
def _tasklist(exe="QQ.exe"):
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + exe, "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=12,
                             creationflags=CREATE_NO_WINDOW, encoding="utf-8", errors="ignore").stdout or ""
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not line.lower().startswith('"'):
            continue
        parts = [p.strip('"') for p in line.split('","')]
        if parts and parts[0].lower() == exe.lower():
            rows.append({"pid": parts[1] if len(parts) > 1 else "", "name": parts[0]})
    return rows


def qq_running():
    return len(_tasklist("QQ.exe")) > 0


def all_qq_pids():
    return set(int(x["pid"]) for x in _tasklist("QQ.exe") if str(x.get("pid", "")).isdigit())


# ---------------------------------------------------------------- 机器人实例的进程记录
# 用户可能同时登着自己的 QQ（甚至两个），所以绝不能靠「最大的 QQ 窗口」猜。
# 我们只认「这次由 NapCat 启动器拉起来的那些 pid」，记到文件里，重启程序也认得出。
PID_FILE = os.path.join(r"D:\大肥鱼缓存", "qq_bot_pids.json")


def save_bot_pids(pids, uin=""):
    try:
        os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
        with open(PID_FILE, "w", encoding="utf-8") as f:
            json.dump({"pids": sorted(int(x) for x in pids), "uin": str(uin or ""),
                       "ts": time.strftime("%Y-%m-%d %H:%M:%S")}, f, ensure_ascii=False)
    except Exception:
        pass


def load_bot_pids():
    try:
        with open(PID_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return set(int(x) for x in (d.get("pids") or []))
    except Exception:
        return set()


def bot_pids():
    """记录过、且现在还活着的机器人实例进程。"""
    alive = all_qq_pids()
    return set(p for p in load_bot_pids() if p in alive)


def kill_pids(pids, tree=True):
    """只杀指定进程（机器人实例），不碰用户自己的 QQ。"""
    for pid in list(pids):
        try:
            cmd = ["taskkill", "/PID", str(int(pid)), "/F"]
            if tree:
                cmd.insert(3, "/T")
            subprocess.run(cmd, capture_output=True, timeout=12, creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass
    time.sleep(1.0)
    return bot_pids()


def kill_all_qq(timeout=20):
    """强杀所有 QQ（只在用户明确同意时用；会连自己日常的号一起关）。"""
    end = time.time() + timeout
    for _ in range(5):
        if not qq_running():
            return True
        try:
            subprocess.run(["taskkill", "/IM", "QQ.exe", "/F"], capture_output=True,
                           timeout=12, creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass
        time.sleep(1.2)
        if time.time() > end:
            break
    return not qq_running()


def kill_bot():
    """关掉机器人实例（用户的号不受影响）。"""
    pids = bot_pids()
    if not pids:
        return {"ok": True, "killed": 0, "note": "没有记录到机器人实例"}
    left = kill_pids(pids)
    if not left:
        save_bot_pids(set())
    return {"ok": not left, "killed": len(pids) - len(left), "left": sorted(left)}


# 兼容旧名字
kill_qq = kill_all_qq


# ---------------------------------------------------------------- 端口
def port_up(port, host="127.0.0.1", t=0.4):
    try:
        with socket.create_connection((host, int(port)), timeout=t):
            return True
    except Exception:
        return False


def _http_json(port, path, timeout=3):
    try:
        import urllib.request
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open("http://127.0.0.1:%d%s" % (int(port), path), timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return None


def login_info(port):
    """OneBot 的登录信息；没连上返回 {}。"""
    d = _http_json(port, "/get_login_info")
    if not isinstance(d, dict):
        return {}
    return d.get("data") or {}


# ---------------------------------------------------------------- 窗口
def _pid_of(hwnd):
    p = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
    return int(p.value)


def _exe_of(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def qq_windows(pids=None):
    """所有可见的 QQ 顶层窗口，按面积从大到小。

    pids 给定时只认这些进程——用来排除用户自己日常用的那个 QQ，
    避免把「大肥鱼」的二维码位置显示成别人的聊天列表。
    """
    out = []
    want = set(int(x) for x in pids) if pids else None

    @_WNDPROC
    def cb(hwnd, lp):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            title = ""
            if n > 0:
                b = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, b, n + 1)
                title = b.value
            pid = _pid_of(hwnd)
            if want is not None and pid not in want:
                return True
            exe = _exe_of(pid)
            if not exe.lower().endswith("qq.exe"):
                return True
            r = _RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            if w < 120 or h < 120:
                return True
            out.append({"hwnd": int(hwnd), "title": title, "pid": pid,
                        "w": w, "h": h, "area": w * h})
        except Exception:
            pass
        return True

    user32.EnumWindows(cb, 0)
    out.sort(key=lambda x: -x["area"])
    return out


def best_window(pids=None):
    ws = qq_windows(pids)
    if not ws:
        return None
    # 有标题的（登录窗/主窗都有标题）优先，其次面积大的
    titled = [w for w in ws if w["title"]]
    return (titled or ws)[0]


def _all_qq_windows(pids=None):
    """把这些进程的所有顶层窗口都列出来——包括隐藏的、没标题的。

    qq_windows() 只认「看得见」的窗口；QQ 拉到托盘/隐藏的情况下就会一个都找不到，
    登录二维码也就截不出来。这里专门用来把那种藏起来的窗口拽出来。
    """
    out = []
    want = set(int(x) for x in pids) if pids else None

    @_WNDPROC
    def cb(hwnd, lp):
        try:
            pid = _pid_of(hwnd)
            if want is not None and pid not in want:
                return True
            exe = _exe_of(pid)
            if not exe.lower().endswith("qq.exe"):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            title = ""
            if n > 0:
                b = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, b, n + 1)
                title = b.value
            r = _RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            if w < 120 or h < 120:
                return True
            out.append({"hwnd": int(hwnd), "title": title, "pid": pid,
                        "w": w, "h": h, "area": w * h,
                        "visible": bool(user32.IsWindowVisible(hwnd))})
        except Exception:
            pass
        return True

    user32.EnumWindows(cb, 0)
    out.sort(key=lambda x: -x["area"])
    return out


def reveal_window(pids=None):
    """把机器人 QQ 的登录窗口强行显示到前台（藏托盘、隐藏都拽出来）。

    只动机器人实例自己的 pid，绝不碰用户日常用的 QQ。
    """
    pids = set(pids or bot_pids())
    if not pids:
        return {"ok": False, "no_window": True,
                "error": "机器人 QQ 还没启动（点「启动 QQ 并登录」）"}
    ws = _all_qq_windows(pids)
    if not ws:
        return {"ok": False, "no_window": True, "error": "机器人 QQ 还没出登录窗口"}
    w = ws[0]
    try:
        user32.ShowWindow(w["hwnd"], 9)        # SW_RESTORE：还原 + 显示
        user32.SetForegroundWindow(w["hwnd"])
        time.sleep(0.4)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "hwnd": w["hwnd"], "title": w.get("title") or "QQ",
            "visible": bool(user32.IsWindowVisible(w["hwnd"]))}




def activate_window(hwnd=None):
    """把机器人 QQ 窗口拿到最前面（需要用户直接点二维码时用）。"""
    w = None
    if hwnd:
        w = {"hwnd": int(hwnd)}
    else:
        w = best_window(bot_pids() or None)
        if not w:
            # 窗口藏在托盘/没显示出来时，先把它拽出来再取
            reveal_window()
            w = best_window(bot_pids() or None)
    if not w:
        return {"ok": False, "error": "机器人 QQ 窗口没找到（先启动一次登录）"}
    try:
        h = w["hwnd"]
        user32.ShowWindow(h, 9)          # SW_RESTORE
        user32.SetForegroundWindow(h)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _capture(hwnd):
    """PrintWindow 抓窗口（Electron 窗口要 PW_RENDERFULLCONTENT=2，否则黑图）。"""
    from PIL import Image
    r = _RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w < 20 or h < 20:
        return None
    wdc = user32.GetWindowDC(hwnd)
    if not wdc:
        return None
    cdc = mfc = save = None
    try:
        cdc = gdi32.CreateCompatibleDC(wdc)
        save = gdi32.CreateCompatibleBitmap(wdc, w, h)
        gdi32.SelectObject(cdc, save)
        ok = user32.PrintWindow(hwnd, cdc, 2)
        if not ok:
            gdi32.BitBlt(cdc, 0, 0, w, h, wdc, 0, 0, 0x00CC0020)
        bmi = _BMI()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BMIH)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h          # 负数 = 自上而下，省一次翻转
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        got = gdi32.GetDIBits(cdc, save, 0, h, buf, ctypes.byref(bmi), 0)
        if not got:
            return None
        img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
        return img.convert("RGB")
    except Exception:
        return None
    finally:
        try:
            if save:
                gdi32.DeleteObject(save)
            if cdc:
                gdi32.DeleteDC(cdc)
            user32.ReleaseDC(hwnd, wdc)
        except Exception:
            pass


def napcat_console_log():
    """NapCat 的 stdout/stderr 落地文件（_spawn 里重定向过来）。"""
    cache = os.environ.get("DFY_CACHE_DIR") or r"D:\大肥鱼缓存"
    return os.path.join(cache, "napcat_console.log")


def _read_tail(path, max_bytes=30000):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            return f.read().decode("utf-8", "ignore")
    except Exception:
        return ""


def napcat_qr(cfg=None, max_age=300):
    """直接读 NapCat 自己落盘的二维码（<napcat_dir>\\cache\\qrcode.png）。

    为什么不再截窗口：NapCat 走的是控制台扫码登录，QQ 客户端是**无窗口**的
    进程组（MainWindowHandle 全是 0），根本没有登录窗可截。二维码只会出现在
    NapCat 的 stdout 和 cache/qrcode.png 里，这里直接读文件，每次都拿最新的。
    """
    c = cfg or cfg_from(lambda k: None)
    p = os.path.join(c["napcat_dir"], "cache", "qrcode.png")
    try:
        st = os.stat(p)
    except Exception:
        return {"ok": False, "error": "NapCat 还没生成二维码", "path": p}
    age = int(time.time() - st.st_mtime)
    if age > max_age:
        return {"ok": False, "stale": True, "path": p,
                "error": "二维码已过期（%d 秒没刷新）" % age}
    try:
        with open(p, "rb") as f:
            raw = f.read()
        if not raw:
            return {"ok": False, "error": "二维码图片是空的", "path": p}
        return {"ok": True, "data": "data:image/png;base64," + base64.b64encode(raw).decode("ascii"),
                "age": age, "mtime": time.strftime("%H:%M:%S", time.localtime(st.st_mtime)),
                "path": p}
    except Exception as e:
        return {"ok": False, "error": "读二维码失败：%s" % e, "path": p}


def napcat_qr_url():
    """从 NapCat 控制台输出里捞最近一次「二维码解码URL」，控制台码扫不出时备用。"""
    txt = _read_tail(napcat_console_log())
    url = ""
    for line in txt.splitlines():
        if "二维码解码URL" in line:
            i = line.find("http")
            if i >= 0:
                url = line[i:].strip()
    return url


def login_shot(max_w=440, reveal=False):
    """把**机器人实例**的 QQ 窗口截给界面看（二维码就在这里）。

    只认自己记录过的 pid——用户可能同时开着自己的 QQ，绝不能截错。
    reveal=True 时，窗口藏在托盘/不可见会先拽到前台再截（插件页自动弹码用）。
    """
    pids = bot_pids() or set(_STATE.get("bot_pids") or [])
    if not pids:
        return {"ok": False, "error": "机器人 QQ 还没启动（点「启动 QQ 并登录」）", "no_bot": True}
    if reveal:
        reveal_window(pids)
    win = best_window(pids)
    if not win:
        # 窗口可能被收起在托盘：找到进程但没窗口，先尝试还原一次
        for w in qq_windows(pids):
            user32.ShowWindow(w["hwnd"], 9)
        win = best_window(pids)
    if not win:
        if reveal:
            rv = reveal_window(pids)
            win = best_window(pids)
            if not win:
                return {"ok": False, "no_window": True,
                        "error": rv.get("error") or "机器人 QQ 窗口还没显示出来"}
        if not win:
            return {"ok": False, "no_window": True,
                    "error": "机器人 QQ 窗口还没显示出来"}
    img = _capture(win["hwnd"])
    if img is None:
        return {"ok": False, "error": "窗口截图失败"}
    w, h = img.size
    if w > max_w:
        img = img.resize((max_w, max(1, int(h * max_w / float(w)))))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return {"ok": True, "data": "data:image/png;base64," + b64,
            "title": win.get("title") or "QQ", "w": w, "h": h, "pid": win["pid"]}


# ---------------------------------------------------------------- 启动流程
_LOCK = threading.Lock()
_STATE = {
    "running": False, "finished": False, "step": "", "msg": "", "log": [],
    "onebot_up": False, "logged_in": False, "uin": "", "nick": "",
    "need_qr": False, "error": "", "started_at": 0, "elapsed": 0,
    "bot_pids": [], "account_wrong": False, "force_hint": False,
}


def _set(**kw):
    with _LOCK:
        _STATE.update(kw)


def _log(msg, level="info"):
    try:
        with _LOCK:
            _STATE["log"].append({"t": time.strftime("%H:%M:%S"), "m": msg, "lv": level})
            _STATE["log"] = _STATE["log"][-40:]
    except Exception:
        pass


def state(bot_uin=""):
    with _LOCK:
        s = dict(_STATE)
        s["log"] = list(_STATE["log"])
    if s["started_at"]:
        s["elapsed"] = int(time.time() - s["started_at"]) if s["running"] else s.get("elapsed", 0)
    # 端口/登录态实时探测，界面不用自己算
    api = s.get("_api_port") or DEFAULTS["api_port"]
    s["onebot_up"] = port_up(api)
    if s["onebot_up"]:
        info = login_info(api)
        if info.get("user_id"):
            s["logged_in"] = True
            s["uin"] = str(info.get("user_id"))
            s["nick"] = str(info.get("nickname") or "")
    if bot_uin and s["logged_in"]:
        s["account_wrong"] = str(bot_uin) != str(s["uin"])
    s.pop("_api_port", None)
    s["bridge_up"] = port_up(s.get("_bridge_port") or DEFAULTS["bridge_port"])
    s.pop("_bridge_port", None)
    bp = bot_pids() or set(s.get("bot_pids") or [])
    s["bot_pids"] = sorted(bp)
    s["bot_alive"] = bool(bp & all_qq_pids())
    s["qq_running"] = qq_running()
    s["qq_count"] = len(all_qq_pids())
    return s


def main_window_count():
    """算一下「看得见的 QQ 窗口」有几个（用来提示用户会被关掉几个号）。"""
    try:
        return len([w for w in qq_windows() if w["w"] >= 200])
    except Exception:
        return 0


def launch(cfg, on_ready=None, kill_all=False):
    """后台线程跑完整条链：拉起注入 → 等 OneBot → 起桥接。

    NapCat 注入必须独占 QQ 客户端（客户端已开着时，新进程会直接握手退出、注入不上），
    所以检测到「别人的 QQ 还开着」时先返回 need_confirm，让界面问一句再决定要不要关。
    """
    if _STATE.get("running"):
        return {"ok": True, "already": True}
    chk = check_files(cfg)
    if not chk["ok"]:
        return {"ok": False, "error": "缺少文件：" + "、".join(chk["missing"])}
    if not kill_all:
        others = all_qq_pids() - bot_pids()
        wins = main_window_count()
        if others:
            return {"ok": False, "need_confirm": True, "count": max(wins, 1),
                    "error": "现在开着 QQ（%d 个窗口）；机器人要独占客户端才能注入 NapCat" % max(wins, 1)}
    with _LOCK:
        _STATE.update({"running": True, "finished": False, "step": "准备",
                       "msg": "开始…", "log": [], "error": "", "need_qr": False,
                       "logged_in": False, "uin": "", "nick": "", "account_wrong": False,
                       "force_hint": False,
                       "started_at": time.time(), "elapsed": 0, "bot_pids": [],
                       "_api_port": cfg["api_port"], "_bridge_port": cfg["bridge_port"]})
    t = threading.Thread(target=_worker, args=(cfg, on_ready, bool(kill_all)),
                         daemon=True, name="qq-launcher")
    t.start()
    return {"ok": True, "started": True}


def _spawn(cfg):
    """用 NapCat 启动器拉起一个注入了 NapCat 的 QQ 实例。"""
    d = cfg["napcat_dir"]
    env = dict(os.environ)
    for k in ("ELECTRON_RUN_AS_NODE", "NODE_OPTIONS", "NODE_CHANNEL_FD", "VM_OPTIONS"):
        env.pop(k, None)
    hook = os.path.join(d, "NapCatWinBootHook.dll")
    env.update({
        "NAPCAT_PATCH_PACKAGE": os.path.join(d, "qqnt.json"),
        "NAPCAT_LOAD_PATH": os.path.join(d, "loadNapCat.js"),
        "NAPCAT_INJECT_PATH": hook,
        "NAPCAT_LAUNCHER_PATH": os.path.join(d, "NapCatWinBootMain.exe"),
        "NAPCAT_MAIN_PATH": os.path.join(d, "napcat.mjs"),
    })
    args = [os.path.join(d, "NapCatWinBootMain.exe"), cfg["qq_exe"], hook]
    uin = str(cfg.get("qq_bot") or "").strip()
    if uin:
        args += ["-q", uin]
    # NapCat 的二维码只打在控制台上（同时也写一份 cache/qrcode.png），
    # 以前把 stdout 丢进 DEVNULL，出问题就完全看不到原因，二维码 URL 也拿不到。
    # 现在落到 D:\大肥鱼缓存\napcat_console.log，既能排错，也能解析解码 URL。
    out = err = subprocess.DEVNULL
    try:
        fh = open(napcat_console_log(), "ab", buffering=0)
        out = err = fh
        _CONSOLE_FHS.append(fh)
    except Exception:
        pass
    subprocess.Popen(args, cwd=d, env=env, close_fds=True, creationflags=SPAWN_FLAGS,
                     stdin=subprocess.DEVNULL, stdout=out, stderr=err)
    return args


def _worker(cfg, on_ready, kill_all=False):
    api = int(cfg["api_port"])
    uin_want = str(cfg.get("qq_bot") or "").strip()
    t0 = _STATE.get("started_at") or time.time()
    try:
        # 1. 处理旧实例：默认只关「我们自己的」机器人实例
        before = all_qq_pids()
        if kill_all:
            _set(step="关闭旧 QQ", msg="按你的确认，关掉所有 QQ 再重新注入…")
            _log("关闭全部 QQ 进程（用户已确认）")
            if not kill_all_qq():
                _set(running=False, finished=True, error="QQ 进程关不掉，先手动结束再试")
                return
            before = set()
        else:
            old = bot_pids()
            if old:
                _set(step="关闭旧 QQ", msg="先关掉上一次的机器人实例（你自己的 QQ 不动）…")
                _log("关闭机器人实例：%s" % sorted(old))
                kill_pids(old)
                save_bot_pids(set())
            elif before:
                _log("检测到 %d 个 QQ 进程在跑（当作你自己的号，不动它）" % len(before))

        # 2. NapCat 注入 + 拉起
        _set(step="检查上报配置", msg="确认 NapCat 会把消息推给大肥鱼…")
        rep = ensure_report(cfg)
        if rep.get("ok"):
            if rep.get("changed"):
                _log("已修正 NapCat 上报配置：%s → %s" % (rep["action"], rep["url"]), "warn")
            else:
                _log("NapCat 上报配置正常：%s" % rep["url"])
        else:
            _log("上报配置检查失败：%s" % rep.get("error"), "err")

        _set(step="注入 NapCat", msg="正在拉起机器人 QQ（注入模式）…")
        args = _spawn(cfg)
        _log("注入器已启动：%s" % " ".join(args[1:]))

        # 3. 认领新进程 = 机器人实例（多开时靠这个区分，绝不靠窗口大小猜）
        _set(step="等待 OneBot", msg="等机器人 QQ 起来…")
        new_pids = set()
        claim_end = time.time() + 20
        while time.time() < claim_end:
            time.sleep(1.0)
            now = all_qq_pids()
            new_pids |= (now - before)
            if new_pids and (qq_windows(new_pids) or port_up(api)):
                break
        alive_new = new_pids & all_qq_pids()
        if alive_new:
            new_pids = alive_new
            save_bot_pids(new_pids, uin_want)
            _set(bot_pids=sorted(new_pids))
            _log("机器人实例 pid：%s" % sorted(new_pids))
        else:
            _log("注入没成功：新起的 QQ 进程直接退出了（客户端已开着就会这样）", "warn")
            _set(force_hint=True, step="注入失败",
                 msg="注入没成功 —— QQ 已经开着的时候注入不进去。点下面「重启 QQ 并登录」，会先关掉现有 QQ 再来一次。",
                 finished=True, elapsed=int(time.time() - t0))
            return

        # 4. 等登录
        deadline = time.time() + int(cfg["login_wait"])
        qr_hinted = False
        while time.time() < deadline:
            time.sleep(1.2)
            if port_up(api):
                info = login_info(api)
                if info.get("user_id"):
                    _set(logged_in=True, uin=str(info.get("user_id")),
                         nick=str(info.get("nickname") or ""), need_qr=False)
                    _log("QQ 已登录：%s(%s)" % (info.get("nickname") or "", info.get("user_id")))
                    break
            if not qr_hinted and time.time() - t0 > 8:
                qr_hinted = True
                if best_window(bot_pids() or new_pids or None):
                    _set(need_qr=True, msg="机器人的 QQ 窗口出来了 —— 在下面扫码登录")
                    _log("等待扫码登录")
            # 顺手把后续新出现的子进程也纳入机器人 pid
            if new_pids:
                more = all_qq_pids() - before - new_pids
                if more:
                    new_pids |= more
                    save_bot_pids(new_pids, uin_want)
                    _set(bot_pids=sorted(new_pids))

        if not _STATE.get("logged_in"):
            _set(step="未登录", msg="还没登录上：如果 QQ 弹出登录窗，扫一下码；没有窗口就点「重新登录」。",
                 need_qr=bool(best_window(bot_pids() or new_pids or None)), finished=True,
                 elapsed=int(time.time() - t0))
            _log("等待超时：OneBot 未就绪", "warn")
            return

        # 5. 校验收到的号是不是配置的机器人号
        got = str(_STATE.get("uin") or "")
        if uin_want and got and got != uin_want:
            _set(account_wrong=True,
                 msg="⚠ 登录的是 %s(%s)，不是配置的机器人 %s。点「换号重登」重新来。" %
                     (_STATE.get("nick") or "", got, uin_want), finished=True)
            _log("账号不匹配：%s ≠ %s" % (got, uin_want), "warn")
            return

        # 6. 起桥接
        _set(step="启动桥接", msg="登录成功，正在接上大肥鱼…")
        if on_ready:
            try:
                r = on_ready() or {}
                if isinstance(r, dict) and r.get("ok") is False:
                    _log("桥接启动返回：%s" % r.get("error"), "warn")
            except Exception as e:
                _log("桥接启动异常：%r" % e, "err")
        _set(step="完成", msg="✅ 大肥鱼(%s) 已上线，可以聊了" % (_STATE.get("nick") or got),
             finished=True, need_qr=False, elapsed=int(time.time() - t0))
        _log("全链路就绪")
    except Exception as e:
        _set(error=str(e), msg="出错了：" + str(e), finished=True)
        _log("异常：%r" % e, "err")
    finally:
        _set(running=False)


def reset():
    """清掉上一次的进度，方便重来。"""
    with _LOCK:
        _STATE.update({"running": False, "finished": False, "step": "", "msg": "",
                       "log": [], "error": "", "need_qr": False, "account_wrong": False,
                       "logged_in": False, "uin": "", "nick": "", "elapsed": 0})
    return {"ok": True}


def stop_bot():
    """只关机器人那个 QQ，用户自己的号不受影响。"""
    r = kill_bot()
    _log("手动关闭机器人 QQ：%s" % ("成功" if r.get("ok") else "失败"))
    r["qq_running"] = qq_running()
    r["qq_count"] = len(all_qq_pids())
    return r


def stop_all():
    """关掉所有 QQ（含用户自己的），需要用户明确确认。"""
    ok = kill_all_qq()
    save_bot_pids(set())
    _log("关闭全部 QQ：%s" % ("成功" if ok else "失败"))
    return {"ok": ok, "qq_running": qq_running(), "all": True}
