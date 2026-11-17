# -*- coding: utf-8 -*-
"""精确复测智谱候选模型：看图能力 + 思维链是否真实输出。

第一轮探测有两个问题：
  - glm-4.7-flash / glm-4.6v-flash 撞上 1305 限流，结论不可靠
  - 靠 reasoning_tokens 判断"是否思考型"会误判（有时只有 0~1 个 reasoning token）
所以这一轮：
  - 每个模型重试到成功为止（最多 4 次，间隔递增）
  - 思维链看**响应正文**里有没有 reasoning_content 字段，而不是 usage 计数
  - 看图用一张纯红 PNG，问"什么颜色"，答出"红"才算真看图
"""
import base64
import io
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib

sys.path.insert(0, r"D:\大肥鱼AI")
import api_base

CFG = api_base.config()
KEY = (CFG.get("api_key") or "").strip()
URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

CANDIDATES = [
    "glm-4.7-flash",     # 官方免费 · 文本 200K
    "glm-4.6v-flash",    # 官方免费 · 视觉
    "glm-4.5-flash",     # 免费 · 文本 128K
    "glm-4-flash",       # 老牌免费
    "glm-4-flashx",      # 老牌免费
]


def red_png():
    w = h = 64
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw))
    png += chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


IMG = red_png()


def call(model, msgs, max_tokens=120, timeout=45):
    payload = {"model": model, "messages": msgs, "stream": False, "max_tokens": max_tokens}
    req = urllib.request.Request(
        URL, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY})
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return 200, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            j = json.loads(body)
            err = j.get("error") or {}
            return e.code, {"_err": "%s|%s" % (err.get("code"), err.get("message"))}
        except Exception:
            return e.code, {"_err": body[:120]}
    except Exception as e:
        return 0, {"_err": str(e)[:120]}


def call_retry(model, msgs, tries=4, **kw):
    """限流(1305/429)是常态，重试到出结果为止。"""
    last = None
    for i in range(tries):
        st, d = call(model, msgs, **kw)
        if st == 200:
            return d
        last = d.get("_err")
        if st not in (429, 1305) and not (isinstance(st, int) and st == 429):
            # 非限流错误也重试一次（网络抖动），但别死磕
            if i >= 1:
                break
        time.sleep(3 + i * 3)
    return {"_err": last, "_failed": True}


def probe(model):
    r = {"model": model}

    d = call_retry(model, [{"role": "user", "content": "简单算一下 17*23 等于多少，只给结果。"}])
    if d.get("_failed"):
        r["text"] = "FAIL " + str(d.get("_err"))[:80]
        return r
    msg = ((d.get("choices") or [{}])[0]).get("message") or {}
    r["text"] = "OK"
    r["think"] = bool((msg.get("reasoning_content") or msg.get("reasoning")
                       or msg.get("thinking")))
    r["answer"] = (msg.get("content") or "").strip()[:40]

    dv = call_retry(model, [{"role": "user", "content": [
        {"type": "text", "text": "这张图是什么颜色？只回答颜色本身。"},
        {"type": "image_url", "image_url": {"url": IMG}}]}])
    if dv.get("_failed"):
        r["vision"] = "NO " + str(dv.get("_err"))[:60]
    else:
        mv = ((dv.get("choices") or [{}])[0]).get("message") or {}
        ans = (mv.get("content") or "").strip()
        r["vision"] = "YES" if ("红" in ans) else ("MAYBE(%s)" % ans[:20])
    return r


rows = []
for m in CANDIDATES:
    r = probe(m)
    rows.append(r)
    print("%-16s text=%-6s think=%-6s vision=%-16s answer=%r"
          % (m, r.get("text"), r.get("think"), r.get("vision"), r.get("answer")),
          flush=True)

with io.open(r"D:\大肥鱼AI\_zhipu_free_probe.json", "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, indent=2)
print("\nsaved -> _zhipu_free_probe.json")
