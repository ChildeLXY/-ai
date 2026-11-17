# -*- coding: utf-8 -*-
"""实测智谱各模型的：可用性 / 是否支持看图 / 是否输出思维链。

用现有 cloud_config.json 里的 Key，对每个候选模型发两次请求：
  1. 纯文本 "1+1=?"      —— 看有没有 reasoning_tokens（=思考型）
  2. 带一张红方块图       —— 看能不能读图（=多模态）
只打印结论，不打印 Key、不打印完整响应体。
"""
import io
import json
import sys
import urllib.request
import urllib.error
import base64
import os

sys.path.insert(0, r"D:\大肥鱼AI")
import api_base

CFG = api_base.config()
KEY = (CFG.get("api_key") or "").strip()
URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

MODELS = [
    "glm-4-flash", "glm-4-flashx", "glm-4.5-flash", "glm-4.7-flash",
    "glm-4.6v-flash", "glm-4.6v", "glm-4.5v", "glm-5v-turbo",
    "glm-5.3-flash", "glm-5.3-flashx", "glm-4-air", "glm-4-plus",
    "glm-4.5-air", "glm-z1-air", "glm-4.7", "glm-4.6", "glm-5.3",
]


def make_red_png():
    """造一张 64x64 纯红 PNG，用来测读图。"""
    import struct
    import zlib
    w = h = 64
    raw = b""
    for y in range(h):
        raw += b"\x00" + b"\xff\x00\x00" * w

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw))
    png += chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


IMG = make_red_png()


def call(model, msgs, timeout=40):
    payload = {"model": model, "messages": msgs, "stream": False, "max_tokens": 64}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        URL, data=data,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=timeout) as r:
            return 200, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            j = json.loads(body)
            err = j.get("error") or {}
            return e.code, {"_err": "%s %s" % (err.get("code"), err.get("message"))}
        except Exception:
            return e.code, {"_err": body[:150]}
    except Exception as e:
        return 0, {"_err": str(e)[:150]}


def probe(model):
    out = {"model": model}
    # 1. 文本 + 思考链
    st, d = call(model, [{"role": "user", "content": "1+1等于几？"}])
    if st != 200:
        out["text"] = "FAIL %s" % d.get("_err")
        return out
    u = d.get("usage") or {}
    ch = (d.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    out["text"] = "OK"
    out["think"] = bool((u.get("completion_tokens_details") or {}).get("reasoning_tokens")
                        or msg.get("reasoning_content"))
    out["cached"] = bool((u.get("prompt_tokens_details") or {}).get("cached_tokens"))
    # 2. 读图
    st2, d2 = call(model, [{"role": "user", "content": [
        {"type": "text", "text": "这张图是什么颜色？只回答颜色。"},
        {"type": "image_url", "image_url": {"url": IMG}}]}])
    if st2 != 200:
        out["vision"] = "FAIL %s" % str(d2.get("_err"))[:70]
    else:
        ch2 = (d2.get("choices") or [{}])[0]
        txt = ((ch2.get("message") or {}).get("content") or "").strip()
        out["vision"] = "OK(%s)" % txt[:20]
    return out


rows = []
for m in MODELS:
    r = probe(m)
    rows.append(r)
    print("%-18s text=%-12s think=%-5s cached=%-5s vision=%s"
          % (m, r.get("text"), r.get("think"), r.get("cached"), r.get("vision")),
          flush=True)

with io.open(r"D:\大肥鱼AI\_zhipu_probe.json", "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, indent=2)
print("\nsaved -> D:\\大肥鱼AI\\_zhipu_probe.json")
