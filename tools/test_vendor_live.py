# -*- coding: utf-8 -*-
"""实测：本地 Qwen json 模式 + 云端 GLM json 模式（部署矩阵：千问/DeepSeek 本地，GLM 云端）"""
import os, re, sys, json, time, threading, urllib.request

ns = {}
exec("import os,re,sys,json,time,uuid,shutil,threading,platform,subprocess,"
     "urllib.parse,urllib.request", ns)
ns["OLLAMA"] = "http://localhost:11434"
ns["LLAMA_CPP"] = "http://localhost:8080"
ns["CLOUD_PROXY"] = "http://127.0.0.1:11435"
ns["DEFAULT_MODEL"] = "qwen2.5vl:7b"
ns["_LOCAL"] = urllib.request.build_opener(urllib.request.ProxyHandler({}))
src = open(r"D:\DeepBuddy\deepbuddy.py", encoding="utf-8").read()
for n in ["_split_think", "_cloud_chat", "ollama_chat", "_extract_json"]:
    m = re.search(r"^def %s\(.*?(?=^def |^class |^# -*-|^if __name__)" % n, src, re.S | re.M)
    exec(m.group(0), ns)

def check(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    return cond

ok = True
print("== 实测：本地 Qwen (qwen2.5vl:7b) json 模式 ==")
try:
    t0 = time.time()
    r = ns["ollama_chat"]([{"role": "user", "content":
        '输出一个JSON：{"thought":"好的","tool":"finish","args":{"answer":"测试通过"}}'}],
        model="qwen2.5vl:7b", num_predict=200, json_mode=True, timeout=120)
    obj = ns["_extract_json"](r["content"])
    ok &= check("本地 json 出合法 JSON (%.1fs)" % (time.time() - t0),
                obj is not None and obj.get("tool") == "finish")
    if not obj:
        print("   原始:", repr(r["content"][:200]))
except Exception as e:
    print("  FAIL 本地:", e); ok = False

print("== 实测：云端 GLM (glm-4-flash) json 模式 ==")
try:
    t0 = time.time()
    r = ns["_cloud_chat"]([{"role": "user", "content":
        '输出一个JSON对象，形如 {"thought":"...","tool":"finish","args":{"answer":"一句话自我介绍"}}'}],
        model="glm-4-flash", num_predict=500, json_mode=True, timeout=120)
    obj = ns["_extract_json"](r["content"])
    ok &= check("GLM json 出合法 JSON (%.1fs)" % (time.time() - t0),
                obj is not None and obj.get("tool") == "finish")
    if not obj:
        print("   原始:", repr(r["content"][:200]))
except Exception as e:
    print("  FAIL 云端:", e); ok = False

print("== 实测：本地 DeepSeek-R1（推理模型特化：不压 think + 大预算） ==")
try:
    t0 = time.time()
    payload = {"model": "deepseek-r1:8b", "messages": [{"role": "user", "content":
        '输出一个JSON：{"thought":"好的","tool":"finish","args":{"answer":"测试通过"}}'}],
        "stream": False, "format": "json",
        "options": {"temperature": 0.2, "num_predict": 6000}}
    req = urllib.request.Request(ns["OLLAMA"] + "/api/chat",
        json.dumps(payload, ensure_ascii=False).encode(),
        {"Content-Type": "application/json"})
    with ns["_LOCAL"].open(req, timeout=300) as r:
        out = json.loads(r.read().decode())
    msg = out.get("message") or {}
    full = (msg.get("content") or "")
    content, thinking = ns["_split_think"](full.strip(), (msg.get("thinking") or "").strip())
    obj = ns["_extract_json"](content) or ns["_extract_json"](thinking)
    ok &= check("deepseek-r1 json 出合法 (%.1fs, thinking=%d字)" % (time.time() - t0, len(thinking)),
                obj is not None and obj.get("tool") == "finish")
    if not obj:
        print("   content:", repr(content[:200])); print("   thinking:", repr(thinking[:200]))
except Exception as e:
    print("  FAIL deepseek-r1:", e); ok = False

print()
print("结果:", "全部通过" if ok else "有失败")
sys.exit(0 if ok else 1)
