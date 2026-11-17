# -*- coding: utf-8 -*-
"""实测 DeepBuddy 的云端模型接入。

不 import deepbuddy.py（那会拉起 pywebview），而是把它的源码抠出来单独跑，
验证三个关键点：
  1. 云端模型能出现在下拉列表里
  2. 选云端模型能真的拿到回答
  3. 云端模型支不支持看图，问对了
"""
import io
import json
import os
import re
import sys
import urllib.request

DB = r"D:\DeepBuddy\deepbuddy.py"
src = io.open(DB, encoding="utf-8").read()

# 抠出需要的常量和函数（都是自包含的，不依赖 webview）
wanted = [
    "OLLAMA =", "LLAMA_CPP =", "CPP_SUFFIX =", "CLOUD_PROXY =", "CLOUD_SUFFIX =",
    "DFY_DIR =", "DFY_CLOUD_CONFIG =", "MAX_IMAGE_PX =", "MAX_TEXT_CHARS =",
]
parts = []
for w in wanted:
    i = src.find(w)
    assert i >= 0, w
    j = src.find("\n", i)
    parts.append(src[i:j])

# 函数体：按 def 名抓（抓到下一个顶层 def/class 为止）
def grab(name):
    m = re.search(r"^def %s\(" % re.escape(name), src, re.M)
    assert m, name
    start = m.start()
    nxt = re.search(r"^(def |class )", src[start + 5:], re.M)
    end = start + 5 + nxt.start() if nxt else len(src)
    return src[start:end]

for fn in ["_cloud_cfg", "_cloud_models", "cloud_models", "_cloud_vision",
           "_cloud_chat", "_model_sees_images", "_compose_user_turn",
           "_image_b64", "_file_kind", "_read_attach_text", "_hsize"]:
    parts.append(grab(fn))

code = ("import os, io, json, re, sys\n"
        "import urllib.request\n"
        "import urllib.request as _ur\n"
        "_LOCAL = _ur.build_opener(_ur.ProxyHandler({}))\n\n") + "\n\n".join(parts)
code += """
"""
ns = {}
exec(compile(code, "dbslice", "exec"), ns)

print("=== 1. 云端模型清单 ===")
cm = ns["cloud_models"]()
print("下拉框里的云端模型：")
for x in cm:
    print("  ", x)
if not cm:
    print("  （空 —— 兼容代理没跑或云端没开）")

print("\n=== 2. 云端模型实际对话 ===")
if cm:
    real = cm[0][: -len(ns["CLOUD_SUFFIX"])]
    try:
        r = ns["_cloud_chat"](
            [{"role": "user", "content": "只回答三个字：收到了"}],
            real, num_predict=200, timeout=60)
        print("  模型:", real)
        print("  content  :", repr(r["content"][:60]))
        print("  thinking :", repr(r["thinking"][:60]))
    except Exception as e:
        print("  调用失败:", repr(e)[:200])

print("\n=== 3. 云端模型看图能力 ===")
for x in ns["_cloud_models"]():
    mid = x["id"]
    print("  %-18s 清单标 vision=%-5s  代理 /api/show 问出来=%s"
          % (mid, x["vision"], ns["_cloud_vision"](mid)))
    print("     _model_sees_images 判定 =", ns["_model_sees_images"](mid + ns["CLOUD_SUFFIX"]))
