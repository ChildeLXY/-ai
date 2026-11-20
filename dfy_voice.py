# -*- coding: utf-8 -*-
"""RVC 音色（Kiriziki Kurena 2.0）接入：给大肥鱼一个「用自己的音色说话」的声音。

链路：文字 → edge-tts（晓伊/云希等）→ 常驻 RVC 进程变声 → wav。
为什么常驻：Applio 冷启动要 import torch + 载 hubert + 载模型，实测 21.7 秒；
模型驻留之后同一句 7 秒音频只要 0.7 秒。常驻进程由本模块拉起，空闲超时自动退出释放显存。

对外只暴露三件事：
- say(text)        → 返回 wav 路径（失败返回 "[...]" 字符串，跟现有 tts_speak 契约一致）
- status()         → 进程状态 / 显存是否占用 / 音色信息
- stop()           → 立刻结束进程、释放显存
"""
import hashlib
import json
import os
import queue
import subprocess
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = r"D:\大肥鱼缓存"
VOICE_DIR = os.path.join(CACHE, "voice")
WORKER = os.path.join(BASE, "dfy_rvc_worker.py")

# Applio 自带环境（torch 2.11+cu128，实测可用 GPU）
PYTHON = r"E:\Applio\env\python.exe"
APPLIO_ROOT = r"E:\Applio"
PTH = os.path.join(APPLIO_ROOT, "logs", "Kiriziki Kurena 2.0", "Kiriziki Kurena 2.0.pth")
IDX = os.path.join(APPLIO_ROOT, "logs", "Kiriziki Kurena 2.0", "Kiriziki Kurena 2.0.index")
VOICE_NAME = "Kiriziki Kurena 2.0"

# 默认参数：fcpe 比 rmvpe 快且够用；index_rate 0.6 在相似度和清晰度之间比较稳
DEFAULTS = {"voice": "zh-CN-XiaoxiaoNeural", "pitch": 0, "index_rate": 0.6,
            "protect": 0.33, "f0": "fcpe", "rate": 0}
IDLE_SEC = int(os.environ.get("DFY_RVC_IDLE_SEC") or 300)   # 5 分钟没人用就卸模型、退进程

_lock = threading.Lock()
_proc = None
_last_use = 0.0
_reaper = None
_stats = {"calls": 0, "miss_ms": 0, "last_ms": 0}


# ---------------------------------------------------------------- 进程管理
def _spawn():
    global _proc
    creation = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW：别弹黑框
    _proc = subprocess.Popen(
        [PYTHON, WORKER],
        cwd=APPLIO_ROOT,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", bufsize=1, creationflags=creation,
    )
    _proc.stdin.write(json.dumps({"op": "ping"}, ensure_ascii=False) + "\n")
    _proc.stdin.flush()
    hello = _read_json(_proc, timeout=45)          # 启动横幅 {"hello": ...}
    if hello is None:
        _kill()
        raise RuntimeError("RVC 工作进程没起来（检查 %s 是否存在）" % PYTHON)
    pong = _read_json(_proc, timeout=45)           # ping 的回复，必须读掉，不然会串到下一句
    if pong is None or not pong.get("ok"):
        _kill()
        raise RuntimeError("RVC 工作进程握手失败（%s）" % (pong or {}).get("error", "无回应"))


def _read_json(proc, timeout=90):
    """从 worker 的 stdout 读一行 JSON（stderr 已经单独走了，不会被污染）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except Exception:
            continue
    return None


def _kill():
    global _proc
    if _proc is None:
        return
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


def _ensure():
    global _proc, _last_use, _reaper
    if _proc is not None and _proc.poll() is None:
        _last_use = time.time()
        return _proc
    _kill()
    _spawn()
    _last_use = time.time()
    if _reaper is None:
        _reaper = threading.Thread(target=_reap_loop, daemon=True)
        _reaper.start()
    return _proc


def _reap_loop():
    """空闲到点就卸模型退进程 —— 你打游戏的时候，显存不吃着。"""
    while True:
        time.sleep(20)
        with _lock:
            if _proc is None or _proc.poll() is not None:
                return
            if time.time() - _last_use > IDLE_SEC:
                _kill()
                return


# ---------------------------------------------------------------- 对外接口
def _cache_key(text, params):
    raw = json.dumps([text, params, VOICE_NAME], ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:20]


def _cache_path(text, params):
    return os.path.join(VOICE_DIR, "kurena_%s.wav" % _cache_key(text, params))


def say(text, **over):
    """合成一句带 Kurena 音色的语音，返回 wav 路径；出错返回 "[...]"（和 tts_speak 契约一致）。"""
    text = (text or "").strip()
    if not text:
        return "[空文本]"
    # 太长的一段一次变声会占住显存好几秒，截到 500 字（和现有 tts_speak 上限一致）
    text = text[:500]
    params = dict(DEFAULTS, **over)
    os.makedirs(VOICE_DIR, exist_ok=True)
    out = _cache_path(text, params)
    if os.path.exists(out) and os.path.getsize(out) > 0:
        _stats["calls"] += 1
        return out                        # 同样的句子，直接复用，零等待

    with _lock:
        try:
            proc = _ensure()
            req = {"op": "say", "text": text, "out": out, **params}
            proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            t0 = time.time()
            resp = _read_json(proc, timeout=180)
            if resp is None or not resp.get("ok"):
                err = (resp or {}).get("error", "工作进程无响应")
                _kill()
                return "[Kurena 语音失败: %s]" % err
            _stats["calls"] += 1
            _stats["last_ms"] = int((time.time() - t0) * 1000)
            _stats["miss_ms"] = resp.get("ms", _stats["last_ms"])
            return resp["out"]
        except Exception as e:
            _kill()
            return "[Kurena 语音失败: %s]" % e


def status():
    alive = _proc is not None and _proc.poll() is None
    return {
        "engine": "kurena-rvc",
        "ready": alive,
        "voice": VOICE_NAME,
        "python": PYTHON,
        "pth": PTH,
        "idle_sec": IDLE_SEC,
        "calls": _stats["calls"],
        "last_ms": _stats["last_ms"],
        "cache_files": len([f for f in os.listdir(VOICE_DIR) if f.endswith(".wav")]) if os.path.isdir(VOICE_DIR) else 0,
    }


def warm():
    """提前把模型载进显存（约 20 秒）。想让她"张口就来"就调它。"""
    with _lock:
        try:
            proc = _ensure()
            out = os.path.join(VOICE_DIR, "_warm.wav")
            req = {"op": "say", "text": "嗯。", "out": out, **DEFAULTS}
            proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            resp = _read_json(proc, timeout=180)
            return bool(resp and resp.get("ok"))
        except Exception:
            _kill()
            return False


def stop():
    global _proc
    with _lock:
        had = _proc is not None
        _kill()
    return {"ok": True, "stopped": had}


def available():
    return os.path.isfile(PYTHON) and os.path.isfile(PTH) and os.path.isfile(IDX)
