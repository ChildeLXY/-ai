# -*- coding: utf-8 -*-
"""内置 Agent 引擎（DaFeiyuEngine）管理层。

引擎是**随大肥鱼一起分发**的完整 Agent 运行时（基于 DeepSeek Harness，MIT），
用户在别的电脑上装上大肥鱼就能直接用插件市场的一万多个插件，
不需要那台电脑预先装过 DSH。

本模块只做四件事：
  1. 定位引擎（可移植路径，不写死任何人的盘符）
  2. 插件安装 / 卸载 / 列表（走引擎原生命令 `dsh plugin`，与 DSH 本体同链路）
  3. 起 / 停 / 查 web 服务（带 token 鉴权，供大肥鱼的 DSH 模式嵌入）
  4. 引擎版本与健康信息

不做的事：不自建插件加载器、不复制 DSH 的 UI、不改引擎 asar。
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import http.cookiejar

# ---------------------------------------------------------------- 路径

_LEGACY_ROOT = r"D:\本地部署大肥鱼环境"
_LEGACY_ENGINE = os.path.join(_LEGACY_ROOT, "engine")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))


def _data_root():
    """数据根：与 dfy_paths 保持同一套规则。"""
    env = (os.environ.get("DFY_DATA_DIR") or "").strip()
    if env:
        return env
    legacy = os.path.join(_LEGACY_ROOT, "data")
    if os.path.isdir(legacy):
        return legacy
    appdata = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(appdata, "大肥鱼AI", "data")


DATA = _data_root()
CACHE = (os.environ.get("DFY_CACHE_DIR") or "").strip() or (
    r"D:\大肥鱼缓存" if os.path.isdir(r"D:\大肥鱼缓存")
    else os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "大肥鱼AI", "cache")
)


def engine_candidates():
    """引擎可能所在的位置，按优先级排列。

    这套顺序必须和 dfy_engine_bootstrap 保持一致，否则会出现
    「下载器装到 A、程序去 B 找」的分裂。最后一项是可写的数据目录，
    给没有管理员权限、装不进 Program Files 的普通用户用。
    """
    out = []
    env = (os.environ.get("DFY_ENGINE_DIR") or "").strip()
    if env:
        out.append(env)
    out.append(os.path.join(_THIS_DIR, "engine"))        # 安装包形态（随包分发时）
    out.append(_LEGACY_ENGINE)                           # 老机器 D 盘
    out.append(os.path.join(DATA, "engine"))             # 首次启动下载装到这里
    return out


def engine_root():
    """引擎根目录：先找已装的，都没有则返回预期的安装位置（不要求存在）。"""
    for c in engine_candidates():
        if os.path.isdir(c):
            return c
    return os.path.join(DATA, "engine")


ENGINE = engine_root()
CLI = os.path.join(ENGINE, "resources", "runtime", "cli", "bin", "dsh.cmd")
EXE = os.path.join(ENGINE, "DaFeiyuEngine.exe")
DSH_HOME = (os.environ.get("DSH_HOME") or "").strip() or os.path.join(DATA, "dsh")

LOG_PATH = os.path.join(CACHE, "engine.log")
STATE_PATH = os.path.join(CACHE, "engine_web.json")

_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_LOCK = threading.Lock()
_PROC = None


def log(msg, level="INFO"):
    line = "[%s][ENGINE][%s] %s" % (time.strftime("%m-%d %H:%M:%S"), level, msg)
    try:
        os.makedirs(CACHE, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


NOISE = ("crashpad", "registration_protocol_win")


def _clean(text):
    return "\n".join(l for l in (text or "").splitlines()
                     if not any(n in l for n in NOISE)).strip()


def _env():
    e = dict(os.environ)
    e["DSH_HOME"] = DSH_HOME
    return e


# ---------------------------------------------------------------- 状态

def available():
    """引擎是否就位（CLI + 主程序都在）。"""
    return {"available": os.path.isfile(CLI) and os.path.isfile(EXE),
            "engine": ENGINE, "cli": CLI, "exe": EXE, "dsh_home": DSH_HOME}


def version(timeout=90):
    if not os.path.isfile(CLI):
        return {"ok": False, "error": "引擎未就位"}
    try:
        r = subprocess.run(["cmd", "/c", CLI, "--version"], capture_output=True,
                           text=True, timeout=timeout, env=_env(),
                           encoding="utf-8", errors="replace")
    except Exception as e:
        return {"ok": False, "error": "执行失败：%s" % e}
    out = _clean((r.stdout or "") + "\n" + (r.stderr or ""))
    if r.returncode != 0:
        return {"ok": False, "error": out[-400:] or "引擎返回非零"}
    return {"ok": True, "version": out.splitlines()[-1].strip() if out else ""}


# ---------------------------------------------------------------- 插件

def list_plugins(profile="desktop", timeout=180):
    if not os.path.isfile(CLI):
        return {"ok": False, "error": "引擎未就位"}
    try:
        r = subprocess.run(["cmd", "/c", CLI, "plugin", "--profile", profile,
                            "list", "--depth", "0"],
                           capture_output=True, text=True, timeout=timeout, env=_env(),
                           encoding="utf-8", errors="replace")
    except Exception as e:
        return {"ok": False, "error": "执行失败：%s" % e}
    out = _clean((r.stdout or "") + "\n" + (r.stderr or ""))
    if r.returncode != 0:
        return {"ok": False, "error": out[-600:] or "引擎返回非零"}

    names = []
    for line in out.splitlines():
        m = re.search(r"(?:├──|└──)\s+(\S+?)@(\S+)", line)
        if m:
            names.append({"name": m.group(1), "version": m.group(2)})
    return {"ok": True, "profile": profile, "plugins": names, "raw": out}


def add_plugin(spec, profile="desktop", timeout=1200):
    """走引擎原生安装。返回 ok/name/version/tail。"""
    if not os.path.isfile(CLI):
        return {"ok": False, "error": "引擎未就位"}
    log("安装插件 %s -> profile %s" % (spec, profile))
    t0 = time.time()
    try:
        r = subprocess.run(["cmd", "/c", CLI, "plugin", "--profile", profile, "add", spec],
                           capture_output=True, text=True, timeout=timeout, env=_env(),
                           encoding="utf-8", errors="replace")
    except Exception as e:
        return {"ok": False, "error": "执行失败：%s" % e}
    out = _clean((r.stdout or "") + "\n" + (r.stderr or ""))
    if r.returncode != 0:
        log("安装失败 %s：%s" % (spec, out[-300:]), "ERROR")
        return {"ok": False, "error": out[-800:] or "引擎返回非零", "tail": out[-400:]}

    name, ver = "", ""
    for line in out.splitlines():
        m = re.match(r"^\+\s+(@?[^\s@]+(?:/[^\s@]+)?)\s+(\S+)$", line.strip())
        if m:
            name, ver = m.group(1), m.group(2)
            break
    if not name:
        bare = spec.split("@")[0] if not spec.startswith("@") else "@" + spec[1:].split("@")[0]
        name = bare
    log("安装完成 %s v%s（%.1fs）" % (name, ver, time.time() - t0))
    return {"ok": True, "name": name, "version": ver,
            "elapsed": round(time.time() - t0, 1), "tail": out[-400:]}


def remove_plugin(name, profile="desktop", timeout=600):
    if not os.path.isfile(CLI):
        return {"ok": False, "error": "引擎未就位"}
    if not re.match(r"^(@[a-z0-9-._~]+/)?[a-z0-9-._~]+$", name or "", re.I):
        return {"ok": False, "error": "包名不合法"}
    try:
        r = subprocess.run(["cmd", "/c", CLI, "plugin", "--profile", profile, "remove", name],
                           capture_output=True, text=True, timeout=timeout, env=_env(),
                           encoding="utf-8", errors="replace")
    except Exception as e:
        return {"ok": False, "error": "执行失败：%s" % e}
    out = _clean((r.stdout or "") + "\n" + (r.stderr or ""))
    if r.returncode != 0:
        return {"ok": False, "error": out[-600:] or "引擎返回非零"}
    return {"ok": True, "name": name, "tail": out[-300:]}


# ---------------------------------------------------------------- web 服务

def _read_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_state(d):
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, STATE_PATH)
    except Exception as e:
        log("写状态失败：%r" % e, "WARN")


def web_start(port=6801, profile="web", wait=40):
    """起引擎的 web 服务，返回 {ok, port, token, url, pid}。

    引擎会把带 token 的访问地址打在 stdout 第一行，形如：
        dsh web: http://127.0.0.1:6801/?token=XXXX
    """
    global _PROC
    if not os.path.isfile(CLI):
        return {"ok": False, "error": "引擎未就位"}

    with _LOCK:
        st = _read_state()
        if st.get("pid") and _pid_alive(st["pid"]):
            # 已在跑：直接确认端口是否仍可访问
            probe = _probe(st.get("port"), st.get("token"))
            if probe.get("ok"):
                return {"ok": True, "port": st["port"], "token": st.get("token", ""),
                        "url": st.get("url", ""), "pid": st["pid"], "reused": True}
            _kill_pid(st["pid"])

        log("启动引擎 web 服务（port=%s, profile=%s）" % (port, profile))
        cmd = ["cmd", "/c", CLI, "--profile", profile, "--port", str(port), "--no-open"]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    env=_env(), text=True, encoding="utf-8",
                                    errors="replace", cwd=ENGINE,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            return {"ok": False, "error": "启动失败：%s" % e}

        url, token, real_port = "", "", port
        deadline = time.time() + wait
        while time.time() < deadline:
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.2)
                continue
            line = _clean(line)
            if not line:
                continue
            m = re.search(r"(http://127\.0\.0\.1:(\d+)/?\?token=([A-Za-z0-9_\-]+))", line)
            if m:
                url, real_port, token = m.group(1), int(m.group(2)), m.group(3)
                break
            log("引擎输出：%s" % line[:200], "DEBUG")

        if not token:
            try:
                proc.kill()
            except Exception:
                pass
            return {"ok": False, "error": "没等到带 token 的地址（引擎未正常起服务）"}

        _PROC = proc
        state = {"pid": proc.pid, "port": real_port, "token": token, "url": url,
                 "profile": profile, "started_at": int(time.time())}
        _write_state(state)

        # 拿到 token 只代表进程已打印地址，端口未必已监听；必须轮询到真正可访问
        probe = {"ok": False, "status": "not_ready"}
        for _ in range(30):
            probe = _probe(real_port, token, timeout=4)
            if probe.get("ok"):
                break
            time.sleep(0.5)
        log("web 服务已起：port=%s probe=%s" % (real_port, probe))
        return {"ok": bool(probe.get("ok")), "port": real_port, "token": token,
                "url": url, "pid": proc.pid, "status": probe.get("status"),
                "error": probe.get("error", "")}


def _pid_alive(pid):
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % int(pid), "/NH"],
                             capture_output=True, text=True, timeout=15,
                             encoding="utf-8", errors="replace")
        return str(int(pid)) in (out.stdout or "")
    except Exception:
        return False


def _kill_pid(pid):
    try:
        subprocess.run(["taskkill", "/PID", str(int(pid)), "/F", "/T"],
                       capture_output=True, timeout=20)
        return True
    except Exception:
        return False


def _probe(port, token, timeout=15):
    """探测引擎 web 是否可用。

    引擎的认证流程是：GET /?token=XXX → 303 See Other（Location: ./）
    + Set-Cookie: dsh-auth-xxx（HttpOnly, SameSite=Strict, 30 天）
    浏览器会自动跟随并带上 cookie 拿到 200。
    所以这里必须用 CookieJar，否则会停在 401。
    """
    if not port:
        return {"ok": False, "status": "no_port"}
    url = "http://127.0.0.1:%d/?token=%s" % (int(port), token or "")
    try:
        jar = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(jar),
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with op.open(req, timeout=timeout) as r:
            body = r.read(2000)
            return {"ok": r.status == 200, "status": r.status,
                    "bytes": len(body), "cookies": len(jar)}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "error": str(e)[:200]}
    except Exception as e:
        return {"ok": False, "status": "error", "error": str(e)[:200]}


def web_status():
    st = _read_state()
    if not st:
        return {"ok": False, "running": False}
    alive = _pid_alive(st.get("pid"))
    probe = _probe(st.get("port"), st.get("token"), timeout=6) if alive else {"ok": False}
    return {"ok": bool(alive and probe.get("ok")), "running": alive,
            "port": st.get("port"), "token": st.get("token"),
            "url": st.get("url"), "pid": st.get("pid"),
            "probe": probe.get("status")}


def web_stop():
    global _PROC
    st = _read_state()
    pid = st.get("pid")
    if pid and _pid_alive(pid):
        _kill_pid(pid)
    _PROC = None
    _write_state({})
    return {"ok": True, "stopped": bool(pid)}


def embed_url():
    """给前端嵌入用的地址（带 token）。"""
    st = _read_state()
    if not st.get("url"):
        return ""
    return st["url"]


# ---------------------------------------------------------------- CLI

if __name__ == "__main__":
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
    if cmd == "status":
        print(json.dumps({"available": available(), "version": version()},
                         ensure_ascii=False, indent=2))
    elif cmd == "plugins":
        print(json.dumps(list_plugins(), ensure_ascii=False, indent=2)[:4000])
    elif cmd == "web-start":
        print(json.dumps(web_start(), ensure_ascii=False, indent=2))
    elif cmd == "web-status":
        print(json.dumps(web_status(), ensure_ascii=False, indent=2))
    elif cmd == "web-stop":
        print(json.dumps(web_stop(), ensure_ascii=False, indent=2))
    else:
        print("用法: dfy_engine.py [status|plugins|web-start|web-status|web-stop]")
