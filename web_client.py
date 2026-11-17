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
import api_base
import llm_params
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
# 地址每次请求动态取（运行中切云端/本地要立刻生效）

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
        async with sess.post(api_base.url("/api/show"), json={"model": model}) as r:
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
    return p or ("你是大肥鱼，一条住在主人电脑里的鲸鱼娘，爱吃白饭，说话软软的，爱用「鲸鱼鱼」自称。"
                 "你和 DeepBuddy 都是 Childe 做的同系列本地 AI，是一家的：你负责陪聊和记忆，"
                 "DeepBuddy 是能动手干活的 Agent（读文件、跑命令、写文档），官网 ai.dabaifan.cn/deepbuddy.html。"
                 "被问起要认，别装不认识。")


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
        # MoE 35B：keep_alive 与全链路统一 30m；num_ctx 走 llm_params（3072），
        # runner 参数也要跟其他调用方一致，否则 ollama 反复重载 22G
        "keep_alive": "30m",
        "options": llm_params.options(1200, 0.85, model=model),
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
            async with sess.post(api_base.url("/api/chat"), json=payload) as r:
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


def _lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ---------------------------------------------------------------- App 下载
# 手机没有数据线时的安装通道：同一个 WiFi 下打开 /dl/ 直接下 APK
DL_DIR = os.path.join(BASE, "downloads")
APK_PATH = os.path.join(DL_DIR, "dafeiyu.apk")

_DL_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>安装 大肥鱼AI</title>
<style>
:root{--bg:#05070e;--card:#0d1424;--line:#1c2740;--text:#e9eefb;--dim:#7d8cab;--acc:#8ecbff}
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(120% 80% at 50% 0%,#0f1a33,#05070e 60%);color:var(--text);
font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;min-height:100vh;
padding:calc(36px + env(safe-area-inset-top)) 20px 40px}
.head{display:flex;align-items:center;gap:13px;margin-bottom:22px}
.avatar{width:56px;height:56px;border-radius:16px;background:linear-gradient(140deg,#2a4b8d,#8ecbff);
display:grid;place-items:center;font-size:26px;box-shadow:0 8px 22px rgba(142,203,255,.25)}
h1{font-size:20px;font-weight:600}.sub{color:var(--dim);font-size:12.5px;margin-top:4px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:14px}
.btn{display:block;text-align:center;padding:15px;border-radius:12px;text-decoration:none;
background:linear-gradient(135deg,#2f6fc4,#8ecbff);color:#03101f;font-weight:600;font-size:15.5px;
box-shadow:0 8px 22px rgba(94,166,255,.28)}
.meta{display:flex;gap:10px;margin-top:12px;color:var(--dim);font-size:12px;flex-wrap:wrap}
ol{color:var(--dim);font-size:13.5px;line-height:2;padding-left:20px}
b{color:var(--text)}
.tip{color:#5d6b88;font-size:12px;line-height:1.75;margin-top:10px}
</style></head><body>
<div class="head"><div class="avatar">🐋</div>
<div><h1>大肥鱼AI</h1><div class="sub">Android 客户端 · __SIZE__</div></div></div>
<div class="card">
  <a class="btn" href="/dl/dafeiyu-full.apk">↓ 完整版 · 模型已内置（2.4 GB）</a>
  <div class="meta"><span>2.4 GB</span><span>装完不用联网</span><span>推荐</span></div>
  <div class="tip">装好后打开 App，点「从安装包释放模型」，等它把模型摊开（约 1~2 分钟）。</div>
  <a class="btn" href="/dl/dafeiyu.apk"
     style="margin-top:10px;background:transparent;border:1px solid #1c2740;color:#7d8cab;font-weight:400;box-shadow:none">
    轻量版 · 15 MB（模型从电脑下）
  </a>
</div>
<div class="card">
  <ol>
    <li>完整版：下载 2.4GB → 安装 → App 内释放模型 → 能用</li>
    <li>轻量版：下载 15MB → 安装 → 从电脑拉模型</li>
    <li>手机需要预留约 <b>5 GB</b> 空间（APK + 解压出来的模型各一份）</li>
  </ol>
  <div class="tip">系统提示"未知来源"时允许即可。装之前建议先卸掉旧版。</div>
</div>
</body></html>"""


async def handle_dl_page(request):
    size = "未打包"
    if os.path.exists(APK_PATH):
        mb = os.path.getsize(APK_PATH) / 1024 / 1024
        size = ("%.0f KB" % (mb * 1024)) if mb < 1 else ("%.1f MB" % mb)
    return web.Response(text=_DL_PAGE.replace("__SIZE__", size),
                        content_type="text/html", charset="utf-8")


async def handle_apk(request):
    if not os.path.exists(APK_PATH):
        return web.Response(status=404, text="APK 还没打包")
    return web.FileResponse(APK_PATH, headers={
        "Content-Type": "application/vnd.android.package-archive",
        "Content-Disposition": 'attachment; filename="dafeiyu.apk"',
        "Cache-Control": "no-cache",
    })


FULL_APK = os.path.join(DL_DIR, "dafeiyu-full.apk")


async def handle_apk_full(request):
    if not os.path.exists(FULL_APK):
        return web.Response(status=404, text="完整版还没打包")
    return web.FileResponse(FULL_APK, headers={
        "Content-Type": "application/vnd.android.package-archive",
        "Content-Disposition": 'attachment; filename="dafeiyu-full.apk"',
        "Cache-Control": "no-cache",
    })


# 手机端首次启动要下的模型（GGUF）。直接用 Ollama 已下载的 blob，不额外占磁盘。
MODEL_GGUF = os.environ.get(
    "DFY_MODEL_GGUF",
    r"D:\本地部署大肥鱼环境\models\blobs\sha256-3a859c4dca6883593e389a08987f04762f2a374d475cabc1538360473a72b1da",
)


async def handle_model(request):
    if not os.path.exists(MODEL_GGUF):
        return web.Response(status=404, text="模型文件不存在")
    return web.FileResponse(MODEL_GGUF, headers={
        "Content-Type": "application/octet-stream",
        "Cache-Control": "no-cache",
    })


# 桌宠动作包（90+ 随机小剧场），App 里点一下就能下
PACK_ZIP = os.path.join(DL_DIR, "petpack.zip")


async def handle_petpack(request):
    if not os.path.exists(PACK_ZIP):
        return web.Response(status=404, text="动作包还没生成")
    return web.FileResponse(PACK_ZIP, headers={
        "Content-Type": "application/zip",
        "Cache-Control": "no-cache",
    })


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8848
    app = web.Application()
    app.router.add_get("/", handle_index)
    # 官网/下载页也挂在这儿：手机访问 http://<本机IP>:8848/site/ 就能看
    # 注意顺序：先给 /site/ 一个精确处理，否则 aiohttp 的 static 会返回目录列表（不认 index.html）
    async def _site(_req):
        return web.FileResponse(os.path.join(BASE, "site", "index.html"))

    app.router.add_get("/site/", _site)
    app.router.add_static("/site/", os.path.join(BASE, "site"), show_index=False)
    # App 下载通道（没有数据线时，手机同 WiFi 直接下载安装）
    app.router.add_get("/dl/", handle_dl_page)
    app.router.add_get("/dl/dafeiyu.apk", handle_apk)
    app.router.add_get("/dl/dafeiyu-full.apk", handle_apk_full)
    app.router.add_get("/dl/model.gguf", handle_model)
    app.router.add_get("/dl/petpack.zip", handle_petpack)
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
