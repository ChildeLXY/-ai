# -*- coding: utf-8 -*-
"""内存看护：优先保证大肥鱼（模型进程）的运行资源。

策略（每 60 秒一轮）：
  1) ollama / llama-server / 大肥鱼AI 主程序 → 优先级提为 AboveNormal
     （"思维在线"：让模型进程在系统调度里永远排在前面）
  2) 浏览器（Edge/Chrome/Firefox 等）→ 优先级降为 BelowNormal
  3) 可用内存 < 阈值（默认 8G）时，修剪浏览器的工作集（EmptyWorkingSet）：
     不杀进程、不丢标签页，只是把它的内存压回系统/页面文件，腾给模型。
"""
import ctypes
import os
import threading
import time
import traceback

_BIGFISH_MAIN = "大肥鱼AI.py"
_MODEL_NAMES = ("ollama.exe", "ollama app.exe", "llama-server.exe", "ollama.exe")
_BROWSER_KEYS = ("chrome", "msedge", "firefox", "360se", "360chrome", "qqbrowser",
                 "brave", "vivaldi", "opera", "sogouexplorer")
_PRIO_ABOVE = 0x00008000   # ABOVE_NORMAL_PRIORITY_CLASS
_PRIO_BELOW = 0x00004000   # BELOW_NORMAL_PRIORITY_CLASS

_psapi = ctypes.WinDLL("psapi")
_kernel32 = ctypes.WinDLL("kernel32")


def _trim_ws(pid):
    """EmptyWorkingSet：把进程工作集压出去（温和，不杀进程）。"""
    try:
        h = _kernel32.OpenProcess(0x1F0FFF, False, pid)  # PROCESS_ALL_ACCESS
        if not h:
            return False
        try:
            return bool(_psapi.EmptyWorkingSet(h))
        finally:
            _kernel32.CloseHandle(h)
    except Exception:
        return False


def _prio(pid, cls):
    try:
        h = _kernel32.OpenProcess(0x0200, False, pid)  # PROCESS_SET_INFORMATION
        if not h:
            return False
        try:
            return bool(_kernel32.SetPriorityClass(h, cls))
        finally:
            _kernel32.CloseHandle(h)
    except Exception:
        return False


def sweep(min_avail_gb=8.0, log=None):
    """跑一轮看护，返回统计 dict。"""
    import psutil
    stat = {"model": 0, "browser": 0, "trimmed": 0}
    for p in psutil.process_iter(["name", "pid", "cmdline"]):
        try:
            n = (p.info["name"] or "").lower()
            cl = " ".join(p.info["cmdline"] or [])
            if any(k == n for k in _MODEL_NAMES) or _BIGFISH_MAIN in cl:
                if _prio(p.info["pid"], _PRIO_ABOVE):
                    stat["model"] += 1
            elif any(k in n for k in _BROWSER_KEYS) and "webview" not in n:
                if _prio(p.info["pid"], _PRIO_BELOW):
                    stat["browser"] += 1
        except Exception:
            continue
    # 内存紧张时修剪浏览器工作集，优先保证模型不换页
    try:
        avail = psutil.virtual_memory().available / 2**30
        if avail < min_avail_gb:
            for p in psutil.process_iter(["name", "pid", "memory_info"]):
                try:
                    n = (p.info["name"] or "").lower()
                    if any(k in n for k in _BROWSER_KEYS) and "webview" not in n:
                        if _trim_ws(p.info["pid"]):
                            stat["trimmed"] += 1
                except Exception:
                    continue
    except Exception:
        pass
    return stat


def start(interval=60, min_avail_gb=8.0, log=None):
    """在主程序里起一个常驻守护线程（daemon）。"""

    def _loop():
        while True:
            try:
                st = sweep(min_avail_gb, log)
                if log and (st["model"] or st["browser"] or st["trimmed"]):
                    log("内存看护：模型进程优先级×%d，浏览器降级×%d，修剪×%d" % (
                        st["model"], st["browser"], st["trimmed"]))
            except Exception:
                if log:
                    log("内存看护异常：%s" % traceback.format_exc(limit=1), "WARN")
            time.sleep(interval)

    threading.Thread(target=_loop, daemon=True, name="dfy-mem-guard").start()


if __name__ == "__main__":
    print(sweep(8.0))
