# -*- coding: utf-8 -*-
"""DSH 原生界面托管：按需启动、返回带 token 的地址、关闭即释放。

大肥鱼里点「DSH 原生界面」，实际打开的就是 DSH 自己的 Web 前端（插件市场 + 插件管理）。
它 boot 的是 dfyweb profile —— 和大肥鱼宿主共用同一个 DSH home 与同一份 node_modules，
所以在 DSH 界面里装/卸的插件，宿主重启后能看到同一批。

性能考量：
- 只在用户点开时启动，不做常驻；关掉页面就结束进程，不在后台留 node
- 启动输出写文件，不占用主进程的 stdout（主进程 stdio 被重定向到 _crash.log）
- 复用已有实例：先探端口 + 读日志里的 token 地址，能复用就不重启
"""
import os
import re
import subprocess
import sys
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = r"D:\大肥鱼缓存"
LOG = os.path.join(CACHE, "_dshui.log")
# 注意：不要读环境里的 DSH_HOME —— 这台机器上它被设成了旧的 E:\DSH-home，
# 而大肥鱼宿主读到的是 D 盘这份，跟着它走就会又变成"两个 home、两份插件清单"。
DSH_HOME = os.environ.get("DFY_DSH_UI_HOME") or r"D:\本地部署大肥鱼环境\dsh"
NODE = r"D:\本地部署大肥鱼环境\dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
CLI = os.path.join(BASE, "dsh_web", "node_modules", "@deepseek-ai", "dsh", "lib", "bin.js")
CWD = os.path.join(BASE, "dsh_web")
PROFILE = "dfyweb"
PORT = 6811
# 内置修改版前端：node 进程托管这份（已改品牌），而不是官方 dsh-web-frontend。
# 由 dfy_dsh_build.py 生成；dsh-web-app 补丁读这个环境变量定位 dist。
DFY_DIST = os.path.join(BASE, "dsh_host", "dfy-dsh", "dist")
# doctor 生成的 pnpm 垫片目录：必须前置到 PATH，否则 DSH 子进程会抓到
# 别处的 pnpm（例如 12.x），主版本一漂 node_modules 就会被重写。
SHIM_DIR = os.path.join(BASE, "dsh_host", "bin")

_lock = threading.Lock()
_proc = None
_url = ""


def _doctor_fix():
    """启动前自愈：只修配置类（不替用户开代理），失败也不阻塞拉起。"""
    try:
        subprocess.run(
            [sys.executable, os.path.join(BASE, "dfy_dsh_doctor.py"), "fix", "--safe"],
            capture_output=True, text=True, timeout=60,
            creationflags=(0x08000000 if sys.platform.startswith("win") else 0))
    except Exception:
        pass


def _child_env():
    env = dict(os.environ)
    env["DSH_HOME"] = DSH_HOME
    # 让 dsh-web-app 托管修改版 dist（大肥鱼品牌），不再返回官方前端
    if os.path.isdir(DFY_DIST):
        env["DFY_DSH_DIST"] = DFY_DIST
    # 垫片前置：保证 dsh plugin / 市场子进程用的是运行时那版 pnpm
    if os.path.isdir(SHIM_DIR):
        env["PATH"] = SHIM_DIR + os.pathsep + env.get("PATH", "")
        env["PNPM_HOME"] = SHIM_DIR
    env.pop("REMOTE_DEBUGGING_PORT", None)
    return env


def _port_alive():
    import socket
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", PORT))
        return True
    except Exception:
        return False
    finally:
        s.close()


def _read_url_from_log():
    try:
        with open(LOG, "r", encoding="utf-8", errors="ignore") as f:
            txt = f.read()
    except Exception:
        return ""
    m = re.findall(r"dsh web: (http://127\.0\.0\.1:%d/?\?token=[A-Za-z0-9_\-]+)" % PORT, txt)
    return m[-1] if m else ""


def _log_tail(n=8):
    """报错时带几句真话，别只说"进程已退出"。"""
    try:
        with open(LOG, "r", encoding="utf-8", errors="ignore") as f:
            lines = [x.rstrip() for x in f.readlines() if x.strip()]
        return " | ".join(lines[-n:])[:400]
    except Exception:
        return ""


def _port_pids():
    """谁在监听这个端口（只查 PID，不猜）。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
             "Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue | "
             "Select-Object -ExpandProperty OwningProcess | Sort-Object -Unique" % PORT],
            capture_output=True, text=True, timeout=15)
        return [int(t) for t in (out.stdout or "").split() if t.strip().isdigit()]
    except Exception:
        return []


def _cmdline_of(pid):
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
             "(Get-CimInstance Win32_Process -Filter 'ProcessId=%d').CommandLine" % pid],
            capture_output=True, text=True, timeout=15)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def _is_our_dsh(cmd):
    """只认我们自己起的 DSH Web：命令行里带 dfyweb 或 dsh_web 的 bin.js。
    别的程序占了 6811 就绝不乱杀 —— 宁可直接报错让用户改端口。"""
    c = (cmd or "").lower()
    return ("dfyweb" in c) or ("dsh_web" in c and "bin.js" in c)


def _kill_port():
    """结束占用端口的 DSH Web 实例（含 DSH CLI 派生出来的子 node），并等端口真正释放。"""
    killed = False
    # 1) 端口占用者（DSH CLI 会 fork 出真正 listen 的子进程，所以必须按端口反查）
    for pid in _port_pids():
        cmd = _cmdline_of(pid)
        if not _is_our_dsh(cmd):
            continue
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, text=True, timeout=15)
            killed = True
        except Exception:
            pass
    # 2) 兜底：命令行带 dfyweb 的残留（端口已释放但进程还没退干净的情况）
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
             "Where-Object { $_.CommandLine -like '*dfyweb*' } | "
             "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
            capture_output=True, text=True, timeout=20)
    except Exception:
        pass
    # 3) 等端口真的空出来，否则紧接着 Popen 又会 EADDRINUSE
    deadline = time.time() + 6
    while time.time() < deadline and _port_alive():
        time.sleep(0.3)
    return killed


def status():
    return {"running": bool(_proc and _proc.poll() is None) or _port_alive(),
            "url": _url if _url else _read_url_from_log(),
            "port": PORT}


def _probe_market():
    """用市场自己的探针判一次 pnpm 是否可用，返回 (ok, 明细)。

    市场里的 probePnpm 结果按**进程常驻缓存**：第一次失败后，这个进程就永远认为
    pnpm 不可用，界面会一直顶着"安装软件前需要先配置 pnpm 环境"的横幅。
    只有重启 6811 才会重探 —— 所以拉起后必须验一次，验不过就自动重启一遍。
    """
    try:
        p = subprocess.run(
            [sys.executable, os.path.join(BASE, "dfy_dsh_doctor.py"), "probe", "--json"],
            capture_output=True, text=True, timeout=120, env=_child_env(),
            creationflags=(0x08000000 if sys.platform.startswith("win") else 0))
        out = p.stdout or ""
        import json
        data = json.loads(out[out.find("{"):]) if "{" in out else {}
        return bool(data.get("ok")), data
    except Exception as e:
        return False, {"error": repr(e)}


def _start_once():
    """拉起一次 6811（假定端口已清干净），返回 {ok,url,reused} 或 {ok:False,error}。"""
    global _proc, _url
    if not os.path.isfile(CLI) or not os.path.isfile(NODE):
        return {"ok": False, "error": "DSH Web 组件缺失，路径不对"}

    os.makedirs(CACHE, exist_ok=True)
    try:
        with open(LOG, "w", encoding="utf-8") as f:
            f.write("")
    except Exception:
        pass

    _doctor_fix()
    env = _child_env()

    creation = 0
    if sys.platform.startswith("win"):
        creation = 0x08000000  # CREATE_NO_WINDOW

    logf = open(LOG, "a", encoding="utf-8", buffering=1)
    try:
        _proc = subprocess.Popen(
            [NODE, CLI, "--profile", PROFILE, "--port", str(PORT), "--no-open"],
            cwd=CWD, env=env, stdout=logf, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=creation,
        )
    except Exception as e:
        return {"ok": False, "error": "启动失败: %r" % (e,)}

    deadline = time.time() + 45
    while time.time() < deadline:
        if _proc.poll() is not None:
            return {"ok": False, "error": "进程已退出（%s），日志：%s" % (_proc.returncode, _log_tail())}
        u = _read_url_from_log()
        if u and _port_alive():
            _url = u
            return {"ok": True, "url": u, "reused": False}
        time.sleep(0.5)
    return {"ok": False, "error": "45 秒内没拿到访问地址，日志：%s" % _log_tail()}


def _stop_own():
    """只停自己拉起的实例，绝不杀别人的 node。"""
    global _proc, _url
    if _proc is not None:
        try:
            if _proc.poll() is None:
                _proc.terminate()
                try:
                    _proc.wait(timeout=5)
                except Exception:
                    _proc.kill()
        except Exception:
            pass
        _proc = None
    if _port_alive():
        _kill_port()
    _url = ""


def open_ui():
    """启动（或复用）DSH Web 前端，并验一次市场 pnpm 探针。"""
    global _proc, _url
    with _lock:
        # ① 已有实例能复用就复用（带着 token 的地址就够用，不必重启）
        if _port_alive():
            u = _url or _read_url_from_log()
            if u:
                _url = u
                okp, info = _probe_market()
                if okp:
                    return {"ok": True, "url": u, "reused": True, "marketPnpm": True, "probe": info}
                # 复用到的实例探针是死的：缓存只有重启才清，停掉重起
                _stop_own()
            # 端口被占但拿不到 token（日志被清过/是别人的实例）→ 只清自己的实例
        if _port_alive():
            _kill_port()
            if _port_alive():
                return {"ok": False, "error": "端口 %d 被其它程序占用，先关掉占用它的程序再试" % PORT}

        res = _start_once()
        if not res.get("ok"):
            return res

        # ② 拉起后必须验市场探针：false 说明这次启动探针就挂了（缓存不会自愈）
        okp, info = _probe_market()
        if not okp:
            _stop_own()
            res = _start_once()
            if not res.get("ok"):
                return res
            okp, info = _probe_market()
        res["marketPnpm"] = okp
        res["probe"] = info
        return res


def close_ui():
    """结束 DSH Web 进程，把内存还给系统，并把界面里装过的插件同步回宿主。"""
    global _proc, _url
    with _lock:
        stopped = False
        if _proc is not None:
            try:
                if _proc.poll() is None:
                    _proc.terminate()
                    try:
                        _proc.wait(timeout=5)
                    except Exception:
                        _proc.kill()
                    stopped = True
            except Exception:
                pass
            _proc = None
        if _port_alive():
            stopped = _kill_port() or stopped
        _url = ""
        synced = _sync_back()
        return {"ok": True, "stopped": stopped, "synced": synced}


def _sync_back():
    """把 dfyweb 里新增的依赖合并回 desktop profile，并刷新宿主名单。

    两边 node_modules 是同一个目录，所以包本身早就落地了，缺的只是"登记"。
    不同步的话，在 DSH 界面里装完插件，宿主重启也认不出来——这正是历史上
    「装了十来个这里也不同步」的第二个来源。
    """
    import json
    prof_pkg = os.path.join(DSH_HOME, "profiles", PROFILE, "package.json")
    desk_pkg = os.path.join(DSH_HOME, "profiles", "desktop", "package.json")
    added = []
    try:
        with open(prof_pkg, encoding="utf-8") as f:
            p = json.load(f)
        with open(desk_pkg, encoding="utf-8") as f:
            d = json.load(f)
        pd = p.get("dependencies") or {}
        dd = d.setdefault("dependencies", {})
        for name, ver in pd.items():
            if name not in dd:
                dd[name] = ver
                added.append(name)
        if added:
            with open(desk_pkg, "w", encoding="utf-8", newline="\n") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
                f.write("\n")
    except Exception:
        return []

    try:
        import dfy_market as m
        if hasattr(m, "sync_host_manifest"):
            m.sync_host_manifest()
    except Exception:
        pass
    return added
