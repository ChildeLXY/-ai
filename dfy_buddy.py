"""把 DeepBuddy 接成大肥鱼AI 的一部分。

DeepBuddy（D:/DeepBuddy/deepbuddy.py）本身就是一套 AutoClaw 风格的 Agent：
    工具集（列目录 / 读写文件 / 跑 Python / 跑命令 / 后台进程 / 抓网页）、
    危险命令确认门、凭证文件保护、任务墙钟超时、最大步数保护。

它原本只服务自己的窗口。这里做两件事：
    1. 拉起 —— 开大肥鱼AI 时把 DeepBuddy 窗口也带起来（去重，已经在跑就不重复起）
    2. 借能力 —— QQ 里可以让它跑一个真正的 Agent 任务，工具执行、多步循环都由它负责

注意：QQ 场景下没人点「允许执行」，所以危险命令一律按拒绝处理（安全默认）。
"""
import os
import subprocess
import sys
import threading
import time

BUDDY_DIR = r"D:\DeepBuddy"
BUDDY_PY = os.path.join(BUDDY_DIR, "deepbuddy.py")
# DeepBuddy 跑在 venv312（pywebview 装在那儿）
BUDDY_PYTHONW = r"D:\本地部署大肥鱼环境\venv312\Scripts\pythonw.exe"
_CACHE_DIR = r"D:\大肥鱼缓存"

_MOD = {"m": None, "err": ""}
_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()      # 同一时刻只跑一个 Agent 任务，防止互相抢模型


def log(msg, level="INFO"):
    line = "[%s][BUDDY][%s] %s" % (time.strftime("%H:%M:%S"), level, msg)
    try:
        with open(os.path.join(_CACHE_DIR, "dsh_bridge.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- 进程
def running():
    """DeepBuddy 窗口是否已经开着（按命令行匹配，不依赖窗口标题）。"""
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cl = " ".join(p.info.get("cmdline") or []).lower()
                if "deepbuddy.py" in cl:
                    return True
            except Exception:
                continue
    except Exception:
        # psutil 不在时退回 tasklist（看不到命令行，只能按进程数粗判，宁可不起）
        try:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq pythonw.exe"],
                                 capture_output=True, text=True, timeout=10,
                                 encoding="utf-8", errors="replace")
            return (out.stdout or "").count("pythonw.exe") >= 2
        except Exception:
            pass
    return False


def launch(wait=0):
    """拉起 DeepBuddy。已在跑则直接返回。"""
    if running():
        return {"ok": True, "already": True}
    if not os.path.isfile(BUDDY_PY):
        return {"ok": False, "error": "找不到 %s" % BUDDY_PY}
    py = BUDDY_PYTHONW if os.path.isfile(BUDDY_PYTHONW) else (sys.executable or "pythonw")
    try:
        # 用 pythonw 起，不带控制台；cwd 设成它自己目录（它按相对路径读 ui.html）
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen([py, BUDDY_PY], cwd=BUDDY_DIR, close_fds=True, creationflags=flags)
        log("已拉起 DeepBuddy")
        if wait:
            t0 = time.time()
            while time.time() - t0 < wait:
                if running():
                    return {"ok": True, "elapsed": round(time.time() - t0, 1)}
                time.sleep(0.5)
        return {"ok": True}
    except Exception as e:
        log("拉起 DeepBuddy 失败：%r" % e, "ERROR")
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------- 借能力
def module():
    """按需 import DeepBuddy 的模块（有 __main__ 保护，import 不会弹窗）。"""
    with _LOCK:
        if _MOD["m"] is not None:
            return _MOD["m"]
        if _MOD["err"] and time.time() - _MOD.get("at", 0) < 60:
            return None
        try:
            if BUDDY_DIR not in sys.path:
                sys.path.insert(0, BUDDY_DIR)
            import deepbuddy as db  # noqa: E402
            # QQ 里没有界面可以点「允许执行」——危险命令一律拒绝，这是安全默认
            db._ask_confirm = lambda *a, **k: False
            _MOD["m"] = db
            _MOD["err"] = ""
            log("DeepBuddy 模块已加载（工具 %d 个）" % len(getattr(db, "TOOLS", {}) or {}))
            return db
        except Exception as e:
            _MOD["err"] = repr(e)
            _MOD["at"] = time.time()
            log("加载 DeepBuddy 模块失败：%r" % e, "ERROR")
            return None


def tools():
    db = module()
    if not db:
        return []
    out = []
    for name, spec in (getattr(db, "TOOLS", {}) or {}).items():
        desc = ""
        if isinstance(spec, dict):
            desc = spec.get("desc") or spec.get("description") or ""
        else:
            desc = (getattr(spec, "__doc__", "") or "")
        out.append({"name": name, "description": str(desc).strip()[:160]})
    return out


def run(prompt, model="", timeout=240, on_event=None):
    """跑一个真正的 Agent 任务，返回最终答复。

    on_event(event) 会收到过程中每一步（工具调用、状态、错误），
    这样 QQ 里可以边跑边报进度。
    """
    db = module()
    if not db:
        return {"ok": False, "error": "DeepBuddy 模块加载失败：%s" % _MOD["err"], "text": ""}
    if not _RUN_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "上一个任务还在跑，等它结束", "text": ""}
    tid = None
    try:
        tid = db._new_task()
        used_model = model or db.DEFAULT_MODEL

        def _worker():
            try:
                db.run_task(tid, prompt, used_model)
            except Exception as e:
                log("任务异常：%r" % e, "ERROR")

        th = threading.Thread(target=_worker, daemon=True, name="buddy-task")
        th.start()
        t0 = time.time()
        cursor = 0
        while time.time() - t0 < timeout:
            time.sleep(0.35)
            task = db.TASKS.get(tid) or {}
            evs = task.get("events") or []
            if on_event:
                for e in evs[cursor:]:
                    try:
                        on_event(e)
                    except Exception:
                        pass
            cursor = len(evs)
            if task.get("done") or not th.is_alive():
                break
        task = db.TASKS.get(tid) or {}
        evs = task.get("events") or []
        if on_event:
            for e in evs[cursor:]:
                try:
                    on_event(e)
                except Exception:
                    pass
        finals = [e for e in evs if e.get("kind") == "final"]
        errors = [e for e in evs if e.get("kind") == "error"]
        if finals:
            return {"ok": True, "text": str(finals[-1].get("text") or ""), "events": evs,
                    "elapsed": round(time.time() - t0, 1)}
        if errors:
            return {"ok": False, "text": str(errors[-1].get("text") or ""),
                    "error": str(errors[-1].get("text") or "")[:300], "events": evs}
        steps = [e for e in evs if e.get("kind") in ("tool", "assistant")]
        last = ""
        for e in reversed(steps):
            if e.get("text"):
                last = e["text"]
                break
        return {"ok": False, "text": last or "任务没有产出结果",
                "error": "超时或没有 final", "events": evs}
    finally:
        try:
            if tid:
                db.TASKS.pop(tid, None)
        except Exception:
            pass
        _RUN_LOCK.release()


def status():
    db = module()
    return {
        "ok": True,
        "dir": BUDDY_DIR,
        "exists": os.path.isfile(BUDDY_PY),
        "running": running(),
        "loaded": db is not None,
        "tools": len(tools()),
        "error": _MOD["err"],
    }
