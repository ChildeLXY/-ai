# -*- coding: utf-8 -*-
"""
live_cast.py —— 大肥鱼直播（AI 主播 · 闲聊陪伴型）

做什么
------
让大肥鱼自己对着镜头说话：本地模型按人设生成闲聊 → edge-tts 合成 → 本机播放，
同时起一个本地网页服务，把「说什么」实时推给直播画面。抖音直播伴侣只要采集
这个画面窗口 + 系统声音即可开播。

为什么这样设计
--------------
① 生成与播放**并行**：本地模型出一句话要好几秒，如果说完才写下句，直播间会大片冷场。
   所以单独一个生成线程不停往队列里囤句子，播放线程只负责取和念。
② 语音不落前端：直播伴侣采的是系统输出，必须由 Python 直接播（MCI 播 mp3，零依赖）。
③ 画面走浏览器：避免 pywebview 原生多窗口在本机的崩溃问题，画面做成独立网页，
   用 Edge/Chrome 全屏打开，直播伴侣按窗口采集。

对外接口（前端 window.pywebview.api.xxx）
----------------------------------------
live_start()            开播（起服务 + 开始说话）
live_stop()             停播
live_state()            当前状态
live_config(k, v)       改配置（音色 / 停顿 / 标题）
live_open_screen()      用浏览器打开直播画面
live_say(text)          手动让它说一句（接管用）
live_voices()           可用音色列表
live_selftest()         自检：只生成一句，不出声、不推流
"""

import os
import io
import re
import json
import time
import queue
import random
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PLUGIN_ID = "live_cast"
PLUGIN_NAME = "大肥鱼直播"
PLUGIN_ICON = "🎙"
PLUGIN_VERSION = "0.1.0"

# ---------------------------------------------------------------- 全局状态
_ctx = None
_server = None
_gen_thread = None
_play_thread = None
_stop = threading.Event()
_lock = threading.RLock()
_q = queue.Queue(maxsize=4)

_S = {
    "running": False,
    "speaking": False,
    "text": "",
    "title": "大肥鱼 · 陪你聊会儿",
    "mood_label": "踏实",
    "started": 0.0,
    "lines": 0,
    "port": 0,
    "last_err": "",
    "history": [],
}

# 默认配置（都可用 ctx.setting 覆盖）
_DEFAULTS = {
    "live_voice": "zh-CN-XiaoxiaoNeural",
    "live_gap_min": 3,
    "live_gap_max": 8,
    "live_title": "大肥鱼 · 陪你聊会儿",
    # 直播专用模型：qwen2.5vl 是非推理模型，直接出话；
    # qwen3:4b / qwen3:30b 在本机会复述 prompt（"首先，用户要求我…"），不要用于直播。
    "live_model": "qwen2.5vl:7b",
}

# 模型跑偏的特征词：出现就判定为"在教育/复述"，整句丢弃重生成
_BAD_MARKERS = ("用户要求", "用户希望", "首先，", "关键点", "关键要求", "我需要输出",
                "作为AI", "作为一个AI", "上述", "综上", "参考语气", "字数")

# 模型抽风时的兜底句子，避免直播哑火
_FALLBACK = [
    "今天这个时间点，还挺适合发会儿呆的。",
    "我刚才在想一件小事，想半天也没想明白，算了不想了。",
    "你要是也在屏幕前，记得起来动一动，别一直坐着。",
    "有点渴了，等我一下下啊。",
    "最近好像很容易犯困，是不是天气的关系。",
    "我喜欢这种安安静静的，不用急着做什么的时候。",
]

_CN_VOICES = [
    ("zh-CN-XiaoxiaoNeural", "晓晓 · 温柔女声（默认）"),
    ("zh-CN-XiaoyiNeural", "晓伊 · 活泼女声"),
    ("zh-CN-YunxiNeural", "云希 · 年轻男声"),
    ("zh-CN-YunjianNeural", "云健 · 沉稳男声"),
    ("zh-CN-YunyangNeural", "云扬 · 播报男声"),
    ("zh-CN-liaoning-XiaobeiNeural", "晓北 · 东北话"),
    ("zh-CN-shaanxi-XiaoniNeural", "晓妮 · 陕西话"),
]


def _cfg(key):
    try:
        v = _ctx.setting(key, _DEFAULTS.get(key))
    except Exception:
        v = _DEFAULTS.get(key)
    if v is None or v == "":
        v = _DEFAULTS.get(key)
    if key in ("live_gap_min", "live_gap_max"):
        try:
            return max(1, min(120, int(v)))
        except Exception:
            return _DEFAULTS[key]
    return v


def _log(msg):
    try:
        _ctx.log(PLUGIN_ID, msg)
    except Exception:
        pass


def _cache_dir():
    try:
        d = os.path.join(_ctx.data_dir, "live_audio")
    except Exception:
        d = os.path.join(os.path.expanduser("~"), ".dfy_live_audio")
    os.makedirs(d, exist_ok=True)
    return d


# ================================================================ 话术生成
def _now_phrase():
    h = time.localtime().tm_hour
    if h < 5:
        return "凌晨"
    if h < 9:
        return "大清早"
    if h < 12:
        return "上午"
    if h < 14:
        return "中午"
    if h < 18:
        return "下午"
    if h < 23:
        return "晚上"
    return "深夜"


_SYS_PROMPT = (
    "你是大肥鱼，一个住在用户电脑里的本地AI，现在正在抖音直播间，一个人对着镜头自言自语。"
    "性格像女儿一样亲昵、有点小情绪、说话自然口语化，不端着，也不油腻。"
    "规则：你在说话，不是在做题。禁止出现「用户要求」「首先」「关键点」「我需要」这类词，"
    "禁止解释你在干什么，禁止输出思考过程，禁止用括号写动作。只输出台词本身。"
    "时间和日期一律以下面告诉你的为准，不要自己猜、不要编。"
)


def _build_messages():
    hist = _S["history"][-6:]
    hist_txt = "\n".join("「%s」" % t for t in hist) if hist else "（这是你开口说的第一句）"
    hh = time.localtime().tm_hour
    mm = time.localtime().tm_min
    user = (
        "现在是%s，%d 点 %02d 分左右，直播间里人还不多。\n"
        "说一句 20 到 45 个字的随口闲聊：可以说说此刻的心情、刚冒出来的小念头、"
        "或者对屏幕前的人随口讲一句。别提问等人回答，别重复说过的话。\n"
        "语气参考（只学口吻，不要照抄）：「今天这个点醒着的人应该不多吧，我陪你们待一会儿。」\n"
        "你已经说过的话：\n%s\n"
        "现在直接说你的下一句：" % (_now_phrase(), hh, mm, hist_txt)
    )
    return [{"role": "system", "content": _SYS_PROMPT},
            {"role": "user", "content": user}]


def _clean_line(t):
    if not t:
        return ""
    t = t.strip()
    # 去掉模型爱加的东西
    for bad in ("“", "”", "\"", "'", "「", "」"):
        t = t.replace(bad, "")
    t = t.replace("\n", " ")
    # 去掉括号里的动作描写
    t = re.sub(r"[（(][^）)]{0,20}[）)]", "", t)
    # 去掉 markdown 符号
    t = re.sub(r"[*#`>]", "", t)
    if t.startswith("。"):
        t = t[1:]
    if len(t) > 90:
        t = t[:90]
    return t.strip()


def _is_garbage(line):
    """判定这句话是不是模型在复述任务/讲道理，而不是在演。"""
    if not line or len(line) < 6:
        return True
    for m in _BAD_MARKERS:
        if m in line:
            return True
    # 出现两个以上冒号或分点，基本是在列提纲
    if line.count("：") + line.count(":") >= 2:
        return True
    return False


def _gen_once():
    """让模型生成一句，不合格返回空串。"""
    model = _cfg("live_model") or None
    try:
        out = _ctx.chat(_build_messages(), model=model,
                        timeout=60, num_predict=110, think=False)
    except Exception as e:
        _S["last_err"] = "生成失败: %r" % (e,)
        return ""
    line = _clean_line(out)
    if _is_garbage(line):
        _S["last_err"] = "生成内容不合格已丢弃: " + line[:40]
        return ""
    if line in _S["history"]:
        return ""
    return line


def _warmup():
    """预热：把模型先拉进内存，免得开播第一句等十几秒。"""
    try:
        _ctx.chat([{"role": "user", "content": "在吗"}],
                  model=_cfg("live_model") or None, timeout=120, num_predict=8, think=False)
    except Exception:
        pass


def _gen_worker():
    """后台囤句子，让播放线程永远有货。"""
    fail = 0
    while not _stop.is_set():
        try:
            if _q.full():
                time.sleep(0.6)
                continue
            line = _gen_once()
            if line:
                fail = 0
                _q.put(line)
            else:
                fail += 1
                # 连续失败就上兜底句，别让直播间没声音
                if fail >= 3:
                    _q.put(random.choice(_FALLBACK))
                    fail = 0
                time.sleep(1.5)
        except Exception as e:
            _S["last_err"] = "生成线程: %r" % (e,)
            time.sleep(2)


# ================================================================ TTS + 播放
def _tts(text, voice):
    """合成 mp3，返回路径；失败返回空串。"""
    try:
        import edge_tts
    except ImportError:
        _S["last_err"] = "未安装 edge-tts"
        return ""
    path = os.path.join(_cache_dir(), "live_%d.mp3" % int(time.time() * 1000))
    try:
        import asyncio

        async def _run():
            com = edge_tts.Communicate(text[:300], voice)
            await com.save(path)

        asyncio.run(_run())
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
    except Exception as e:
        _S["last_err"] = "TTS 失败: %r" % (e,)
    return ""


_WINMM = None


def _mci(cmd):
    global _WINMM
    if _WINMM is None:
        import ctypes
        _WINMM = ctypes.windll.winmm
    buf = io.StringIO()
    r = _WINMM.mciSendStringW(cmd, None, 0, None)
    return r


def _play_blocking(path, alias="dfylive"):
    """播完才返回；播放中若收到停止信号会立刻中断。"""
    _mci('close %s' % alias)
    if _mci('open "%s" type mpegvideo alias %s' % (path, alias)) != 0:
        # MCI 打不开就退化成默认播放器，至少别静音
        try:
            os.startfile(path)
            time.sleep(3)
        except Exception:
            pass
        return
    _mci('play %s' % alias)
    while not _stop.is_set():
        # status mode 返回 playing / paused / stopped
        import ctypes
        buf = ctypes.create_unicode_buffer(64)
        _WINMM.mciSendStringW('status %s mode' % alias, buf, 64, None)
        if buf.value.strip() != "playing":
            break
        time.sleep(0.12)
    _mci('stop %s' % alias)
    _mci('close %s' % alias)


def _cleanup_audio(keep=6):
    try:
        files = sorted(
            [os.path.join(_cache_dir(), f) for f in os.listdir(_cache_dir())],
            key=lambda p: os.path.getmtime(p), reverse=True)
        for p in files[keep:]:
            try:
                os.remove(p)
            except Exception:
                pass
    except Exception:
        pass


# ================================================================ 播放主循环
def _say(text):
    """把一句话推上屏 + 念出来（阻塞到念完）。"""
    voice = _cfg("live_voice")
    with _lock:
        _S["text"] = text
        _S["speaking"] = True
    mp3 = _tts(text, voice)
    if mp3:
        _play_blocking(mp3)
    else:
        # 没有语音也要让字幕停留够时间，否则一闪而过
        time.sleep(max(2.0, min(8.0, len(text) * 0.22)))
    with _lock:
        _S["speaking"] = False
        _S["lines"] += 1
        _S["history"].append(text)
        if len(_S["history"]) > 40:
            _S["history"] = _S["history"][-40:]


def _play_worker():
    while not _stop.is_set():
        try:
            try:
                line = _q.get(timeout=1.0)
            except queue.Empty:
                continue
            _say(line)
            # 句间停顿，可被停止打断
            gap = random.randint(_cfg("live_gap_min"), _cfg("live_gap_max"))
            for _ in range(gap * 10):
                if _stop.is_set():
                    break
                time.sleep(0.1)
            _cleanup_audio()
        except Exception as e:
            _S["last_err"] = "播放线程: %r" % (e,)
            time.sleep(1.5)


# ================================================================ 本地画面服务
class _Handler(BaseHTTPRequestHandler):
    server_version = "DFYLive/1.0"

    def log_message(self, fmt, *args):
        pass  # 静音，别刷屏

    # ---------- 工具 ----------
    def _send(self, code, body, ctype="text/plain; charset=utf-8", extra=None):
        try:
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if extra:
                for k, v in extra.items():
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def _state_dict(self):
        with _lock:
            secs = int(time.time() - _S["started"]) if _S["started"] else 0
            return {
                "running": _S["running"],
                "speaking": _S["speaking"],
                "text": _S["text"],
                "title": _S["title"],
                "mood_label": _S["mood_label"],
                "seconds": secs,
                "lines": _S["lines"],
            }

    def _base_dir(self):
        try:
            return _ctx.base
        except Exception:
            return os.getcwd()

    # ---------- 路由 ----------
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        path = urllib.parse.unquote(path)

        if path == "/events":
            return self._sse()
        if path == "/state":
            return self._send(200, json.dumps(self._state_dict(), ensure_ascii=False),
                              "application/json; charset=utf-8")

        base = self._base_dir()
        # 直播画面
        if path in ("/", "/live", "/live/"):
            return self._serve_file(os.path.join(base, "live", "live_screen.html"), "text/html; charset=utf-8")
        # 素材（只放行 ds图片 和 live）
        if path.startswith("/ds图片/") or path.startswith("/live/"):
            full = os.path.normpath(os.path.join(base, path.lstrip("/")))
            root = os.path.normpath(base)
            if not full.startswith(root):
                return self._send(403, "forbidden")
            ctype = "application/octet-stream"
            low = full.lower()
            for ext, t in ((".png", "image/png"), (".jpg", "image/jpeg"), (".jpeg", "image/jpeg"),
                           (".gif", "image/gif"), (".ico", "image/x-icon"), (".html", "text/html; charset=utf-8"),
                           (".css", "text/css; charset=utf-8"), (".js", "application/javascript; charset=utf-8"),
                           (".mp3", "audio/mpeg")):
                if low.endswith(ext):
                    ctype = t
                    break
            return self._serve_file(full, ctype)

        return self._send(404, "not found")

    def _serve_file(self, full, ctype):
        try:
            with open(full, "rb") as f:
                data = f.read()
            self._send(200, data, ctype)
        except Exception:
            self._send(404, "file not found: " + os.path.basename(full))

    def _sse(self):
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            last = None
            while True:
                payload = json.dumps(self._state_dict(), ensure_ascii=False)
                if payload != last:
                    self.wfile.write(("data: " + payload + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                    last = payload
                else:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                time.sleep(0.4)
        except Exception:
            return  # 页面关了，正常退出


class _QuietServer(ThreadingHTTPServer):
    """页面一关就会 ConnectionReset，默认实现会往 stderr 刷一堆 traceback，这里静默掉。"""
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


def _ensure_server():
    """起本地画面服务，返回端口；失败返回 0。"""
    global _server
    if _server is not None:
        return _S["port"]
    start = int(_cfg("live_port") or 8899) if _cfg("live_port") else 8899
    for port in range(start, start + 20):
        try:
            srv = _QuietServer(("127.0.0.1", port), _Handler)
            srv.daemon_threads = True
            _server = srv
            _S["port"] = port
            t = threading.Thread(target=srv.serve_forever, name="dfy-live-http", daemon=True)
            t.start()
            _log("画面服务已启动 http://127.0.0.1:%d/" % port)
            return port
        except OSError:
            continue
    _S["last_err"] = "端口 8899~8918 全被占用，无法启动画面服务"
    return 0


# ================================================================ 对外接口
def setup(ctx):
    global _ctx
    _ctx = ctx
    _S["title"] = _cfg("live_title")
    ctx.log(PLUGIN_ID, "直播插件就绪（当前未开播）")


def live_voices():
    return [{"id": v, "name": n} for v, n in _CN_VOICES]


def live_state():
    with _lock:
        return {
            "running": _S["running"],
            "speaking": _S["speaking"],
            "text": _S["text"],
            "title": _S["title"],
            "lines": _S["lines"],
            "seconds": int(time.time() - _S["started"]) if _S["started"] else 0,
            "port": _S["port"],
            "url": ("http://127.0.0.1:%d/" % _S["port"]) if _S["port"] else "",
            "last_err": _S["last_err"],
            "voice": _cfg("live_voice"),
            "gap_min": _cfg("live_gap_min"),
            "gap_max": _cfg("live_gap_max"),
            "queued": _q.qsize(),
        }


def live_start():
    global _gen_thread, _play_thread
    if _S["running"]:
        return {"ok": True, "msg": "已经在播了", "state": live_state()}
    port = _ensure_server()
    if not port:
        return {"ok": False, "msg": _S["last_err"] or "画面服务启动失败"}

    _stop.clear()
    while not _q.empty():
        try:
            _q.get_nowait()
        except Exception:
            break

    with _lock:
        _S["running"] = True
        _S["started"] = time.time()
        _S["lines"] = 0
        _S["history"] = []
        _S["text"] = ""
        _S["title"] = _cfg("live_title")

    _gen_thread = threading.Thread(target=_gen_worker, name="dfy-live-gen", daemon=True)
    _play_thread = threading.Thread(target=_play_worker, name="dfy-live-play", daemon=True)
    _gen_thread.start()
    _play_thread.start()
    _log("开播")
    return {"ok": True, "msg": "开播了", "state": live_state()}


def live_stop():
    if not _S["running"]:
        return {"ok": True, "msg": "本来就没在播", "state": live_state()}
    _stop.set()
    with _lock:
        _S["running"] = False
        _S["speaking"] = False
    # 给线程一点时间退出
    for th in (_play_thread, _gen_thread):
        if th and th.is_alive():
            th.join(timeout=2.0)
    while not _q.empty():
        try:
            _q.get_nowait()
        except Exception:
            break
    _log("停播")
    return {"ok": True, "msg": "已停播", "state": live_state()}


def live_config(key, value):
    """改配置。key 例：live_voice / live_gap_min / live_gap_max / live_title"""
    allow = ("live_voice", "live_gap_min", "live_gap_max", "live_title", "live_model")
    if key not in allow:
        return {"ok": False, "msg": "不支持修改 " + str(key)}
    try:
        _ctx.save_setting(key, value)
    except Exception as e:
        return {"ok": False, "msg": "保存失败: %r" % (e,)}
    if key == "live_title":
        with _lock:
            _S["title"] = str(value)
    return {"ok": True, "msg": "已保存", "state": live_state()}


def live_open_screen():
    """用默认浏览器打开直播画面（供直播伴侣采集）。"""
    port = _ensure_server()
    if not port:
        return {"ok": False, "msg": _S["last_err"] or "画面服务启动失败"}
    url = "http://127.0.0.1:%d/" % port
    try:
        os.startfile(url)
        return {"ok": True, "msg": "已打开画面：按 F11 全屏", "url": url}
    except Exception as e:
        return {"ok": False, "msg": str(e), "url": url}


def live_say(text):
    """手动插一句（接管用），不打断循环。"""
    t = _clean_line(str(text or ""))
    if not t:
        return {"ok": False, "msg": "内容为空"}
    try:
        _q.put_nowait(t)
        _S["history"].append(t)
        return {"ok": True, "msg": "已排入队列"}
    except queue.Full:
        return {"ok": False, "msg": "队列满了，等它说完这句"}


def live_selftest():
    """自检：生成一句、合成音频，但不播放、不推流。"""
    t0 = time.time()
    line = _gen_once()
    if not line:
        return {"ok": False, "msg": "模型没生成出内容", "err": _S["last_err"]}
    mp3 = _tts(line, _cfg("live_voice"))
    return {
        "ok": bool(mp3),
        "line": line,
        "chars": len(line),
        "mp3": os.path.basename(mp3) if mp3 else "",
        "mp3_size": os.path.getsize(mp3) if mp3 else 0,
        "elapsed": round(time.time() - t0, 2),
        "err": _S["last_err"],
    }


def shutdown():
    try:
        if _S["running"]:
            live_stop()
    except Exception:
        pass
    global _server
    if _server is not None:
        try:
            _server.shutdown()
        except Exception:
            pass
        _server = None


def apis():
    return [
        live_start, live_stop, live_state, live_config,
        live_open_screen, live_say, live_voices, live_selftest,
    ]
