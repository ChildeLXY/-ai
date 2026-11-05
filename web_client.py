# -*- coding: utf-8 -*-
"""大肥鱼AI 网页客户端。

为什么要单独一个文件：桌面端是 pywebview 壳，手机用不了。
这里起一个轻量 HTTP 服务（aiohttp，环境里已有，不用装东西），
浏览器打开就能聊，复用同一套 Ollama、同一份人格设定。

数据独立存在 data/web_chats.json——**不和桌面端的 chats.json 混用**，
避免两边同时写把聊天记录写坏。想合并的话另说。

用法：
    python web_client.py            # 默认 0.0.0.0:8848
    python web_client.py 8899       # 换端口
"""
import asyncio
import json
import os
import socket
import sys
import time
import urllib.parse

from aiohttp import web, ClientSession, ClientTimeout

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DFY_DATA_DIR") or r"D:\本地部署大肥鱼环境\data"
WEB_CHATS = os.path.join(DATA_DIR, "web_chats.json")
SETTINGS = os.path.join(DATA_DIR, "settings.json")
OLLAMA = "http://127.0.0.1:11434"

MODELS = [
    {"id": "qwen2.5vl:7b", "short": "灵动", "note": "说话自然、会挑表情包"},
    {"id": "qwen3-vl:4b-instruct", "short": "轻快", "note": "省显存、更快"},
    {"id": "deepseek-r1:8b", "short": "思考", "note": "爱推理，看不了图"},
    {"id": "qwen3.6:35b", "short": "深度ProMax", "note": "最强，较慢"},
]
DEFAULT_MODEL = "qwen2.5vl:7b"
HIST_TURNS = 16

_lock = asyncio.Lock()


_caps_cache = {}


async def caps(sess, model):
    """问 Ollama 这个模型支不支持思考链（别凭名字猜，qwen2.5vl 传了会直接 400）。"""
    if model in _caps_cache:
        return _caps_cache[model]
    try:
        async with sess.post(OLLAMA + "/api/show", json={"model": model}) as r:
            d = await r.json()
        c = d.get("capabilities") or []
        v = {"thinking": "thinking" in c, "vision": "vision" in c}
    except Exception:
        v = {"thinking": False, "vision": False}
    _caps_cache[model] = v
    return v


# ------------------------------------------------------------------ 存储
def _load_json(p, default):
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def load_chats():
    d = _load_json(WEB_CHATS, {"chats": []})
    if isinstance(d, list):
        d = {"chats": d}
    d.setdefault("chats", [])
    return d


def save_chats(d):
    os.makedirs(os.path.dirname(WEB_CHATS), exist_ok=True)
    tmp = WEB_CHATS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, WEB_CHATS)          # 原子替换，断电也不会写坏


def persona():
    s = _load_json(SETTINGS, {})
    p = (s.get("persona_global") or "").strip()
    return p or "你是大肥鱼，一条住在主人电脑里的鲸鱼娘，爱吃白饭，说话软软的，爱用「鲸鱼鱼」自称。"


def time_brief():
    n = time.localtime()
    wd = "一二三四五六日"[n.tm_wday]
    return "%d年%d月%d日 星期%s" % (n.tm_year, n.tm_mon, n.tm_mday, wd)


def sys_prompt():
    return (persona()
            + "\n\n【今天】" + time_brief() + "。这只用来判断事情是今天还是昨天，不是聊天话题。"
            + "\n\n【这一轮】\n- 用户问什么就答什么，别把话题拐回以前的事。"
              "\n- 像发微信：一两句口语，不分行、不用括号写动作、不报时间。")


# ------------------------------------------------------------------ 页面
async def handle_index(request):
    return web.FileResponse(os.path.join(BASE, "web_ui.html"))


async def handle_state(request):
    d = load_chats()
    chats = [{"id": c["id"], "title": c.get("title") or "新对话",
              "ts": c.get("ts") or 0, "n": len(c.get("msgs") or [])}
             for c in d["chats"]]
    chats.sort(key=lambda c: c["ts"], reverse=True)
    return web.json_response({"chats": chats, "models": MODELS, "model": DEFAULT_MODEL})


async def handle_chat(request):
    cid = request.query.get("id")
    d = load_chats()
    for c in d["chats"]:
        if c["id"] == cid:
            return web.json_response({"ok": True, "chat": c})
    return web.json_response({"ok": False, "error": "没有这个对话"})


async def handle_new(request):
    body = await request.json() if request.can_read_body else {}
    cid = "w%d" % int(time.time() * 1000)
    d = load_chats()
    d["chats"].append({"id": cid, "title": body.get("title") or "新对话",
                       "ts": int(time.time() * 1000), "msgs": []})
    async with _lock:
        save_chats(d)
    return web.json_response({"ok": True, "id": cid})


async def handle_del(request):
    body = await request.json()
    cid = body.get("id")
    d = load_chats()
    d["chats"] = [c for c in d["chats"] if c["id"] != cid]
    async with _lock:
        save_chats(d)
    return web.json_response({"ok": True})


def _hist(msgs):
    out = []
    for m in msgs[-(HIST_TURNS * 2):]:
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip():
            out.append({"role": m["role"], "content": m["content"]})
    return out


async def handle_send(request):
    """流式回复：把 Ollama 的 NDJSON 转成 SSE 推给浏览器。"""
    body = await request.json()
    cid = body.get("id")
    text = (body.get("text") or "").strip()
    model = body.get("model") or DEFAULT_MODEL
    if not text:
        return web.json_response({"ok": False, "error": "空消息"})

    d = load_chats()
    chat = next((c for c in d["chats"] if c["id"] == cid), None)
    if chat is None:
        return web.json_response({"ok": False, "error": "对话不存在"})

    chat.setdefault("msgs", []).append({"role": "user", "content": text, "ts": int(time.time() * 1000)})
    if chat.get("title") in (None, "", "新对话"):
        chat["title"] = text[:18]
    chat["ts"] = int(time.time() * 1000)
    async with _lock:
        save_chats(d)

    payload = {
        "model": model,
        "messages": [{"role": "system", "content": sys_prompt()}] + _hist(chat["msgs"]),
        "stream": True,
        "keep_alive": "15m" if ("35b" in model or "30b" in model) else "60m",
        "options": {"num_ctx": 8192, "temperature": 0.85, "num_predict": 1200},
    }
    async with ClientSession(timeout=ClientTimeout(total=30)) as _cs:
        c = await caps(_cs, model)
    if c["thinking"]:
        # qwen3 初代 / R1：关掉思考会把推理写进正文，必须 true
        # qwen3.6 及以后：传 false 很干净而且快得多（实测 0.7s vs 23s）
        # 注意 "qwen3.6" 是点，"qwen3:8b" 是冒号，所以这里用点来判断代际
        payload["think"] = not model.startswith("qwen3.")

    resp = web.StreamResponse(headers={
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })
    await resp.prepare(request)

    reply = ""
    try:
        timeout = ClientTimeout(total=900)
        async with ClientSession(timeout=timeout) as sess:
            async with sess.post(OLLAMA + "/api/chat", json=payload) as r:
                if r.status != 200:
                    err = (await r.text())[:300]
                    await resp.write(("data: " + json.dumps({"error": err}) + "\n\n").encode())
                    await resp.write(b"data: [DONE]\n\n")
                    return resp
                buf = b""
                async for chunk in r.content.iter_any():
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            j = json.loads(line.decode("utf-8", "ignore"))
                        except Exception:
                            continue
                        msg = j.get("message") or {}
                        piece = msg.get("content") or ""
                        if piece:
                            reply += piece
                            await resp.write(("data: " + json.dumps({"t": piece}, ensure_ascii=False) + "\n\n").encode())
    except Exception as e:
        await resp.write(("data: " + json.dumps({"error": repr(e)}) + "\n\n").encode())
    finally:
        if reply:
            d2 = load_chats()
            c2 = next((c for c in d2["chats"] if c["id"] == cid), None)
            if c2 is not None:
                c2.setdefault("msgs", []).append(
                    {"role": "assistant", "content": reply, "ts": int(time.time() * 1000)})
                c2["ts"] = int(time.time() * 1000)
                async with _lock:
                    save_chats(d2)
        await resp.write(b"data: [DONE]\n\n")
    return resp


# ------------------------------------------------------------------ 启动
def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8848
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/state", handle_state)
    app.router.add_get("/api/chat", handle_chat)
    app.router.add_post("/api/new", handle_new)
    app.router.add_post("/api/del", handle_del)
    app.router.add_post("/api/send", handle_send)

    ip = lan_ip()
    print("=" * 52)
    print(" 大肥鱼AI 网页客户端")
    print(" 本机:  http://127.0.0.1:%d" % port)
    print(" 手机:  http://%s:%d   （需在同一个 WiFi 下）" % (ip, port))
    print(" 数据:  %s" % WEB_CHATS)
    print("=" * 52, flush=True)
    web.run_app(app, host="0.0.0.0", port=port, print=None)


if __name__ == "__main__":
    main()
