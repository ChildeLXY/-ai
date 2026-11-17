# -*- coding: utf-8 -*-
"""三家模型（DeepSeek/Qwen/GLM）特化适配验证。

两层：
1. 单元级 —— _split_think / _model_family / 代理的 format:json 映射与豁免
2. 实测级 —— 本地 Ollama json 模式 + 云端 GLM json 模式真实出活
"""
import os, re, sys, json, time, threading, subprocess, urllib.request

DB = r"D:\DeepBuddy\deepbuddy.py"
CP = r"D:\大肥鱼AI\cloud_proxy.py"
src_db = open(DB, encoding="utf-8").read()
src_cp = open(CP, encoding="utf-8").read()

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name)

ns = {}
exec("import os,re,sys,json,time,uuid,shutil,threading,platform,subprocess,base64,io,"
     "urllib.parse,urllib.request,webbrowser", ns)
ns["APP_DIR"] = r"D:\DeepBuddy"
ns["WORK_DIR"] = os.path.join(ns["APP_DIR"], "workspace")
ns["TASKS"] = {}
ns["_LOCK"] = threading.RLock()
ns["CONFIRM_TIMEOUT"] = 120
ns["TASK_TIMEOUT"] = 1200
ns["BG_DIR"] = os.path.join(ns["WORK_DIR"], "_bg")
ns["_BG_PROCS"] = {}
ns["_BG_LOCK"] = threading.Lock()
ns["OLLAMA"] = "http://localhost:11434"
ns["LLAMA_CPP"] = "http://localhost:8080"
ns["CLOUD_PROXY"] = "http://127.0.0.1:11435"
ns["CLOUD_SUFFIX"] = "#cloud"
ns["CPP_SUFFIX"] = "#cpp"
ns["DEFAULT_MODEL"] = "qwen2.5vl:7b"
ns["_LOCAL"] = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class _TaskCancelled(Exception):
    pass


ns["TaskCancelled"] = _TaskCancelled

def grab(src, name):
    m = re.search(r"^def %s\(.*?(?=^def |^class |^# -*-|^if __name__)" % name, src, re.S | re.M)
    return m.group(0) if m else None

for n in ["_split_think", "_model_family", "_family_params", "ollama_chat", "_cloud_chat",
          "_cpp_chat", "chat", "_extract_json"]:
    g = grab(src_db, n)
    assert g, "抓不到 " + n
    exec(g, ns)

print("== 模型家族识别 ==")
check("glm → glm", ns["_model_family"]("glm-4.7-flash") == "glm")
check("chatglm → glm", ns["_model_family"]("chatglm3-6b") == "glm")
check("deepseek-r1 → deepseek", ns["_model_family"]("deepseek-r1:8b") == "deepseek")
check("qwen → qwen", ns["_model_family"]("qwen2.5vl:7b") == "qwen")
check("qwq → qwen", ns["_model_family"]("qwq:32b") == "qwen")
check("llama → other", ns["_model_family"]("llama3:8b") == "other")

print("== 部署矩阵特化参数 ==")
fp = ns["_family_params"]("deepseek-r1:8b")
check("deepseek 本地：不压 think + 预算 6000", fp == {"num_predict": 6000, "think": None})
fp = ns["_family_params"]("deepseek-r1:14b")
check("deepseek-r1:14b 同策略", fp["num_predict"] == 6000)
fp = ns["_family_params"]("qwen3:8b")
check("qwen 本地：关思考 + 预算 3000", fp == {"num_predict": 3000, "think": False})
fp = ns["_family_params"]("qwen2.5vl:7b")
check("qwen2.5vl 同策略", fp["think"] is False)
fp = ns["_family_params"]("glm-4.7-flash#cloud")
check("glm 云端：常规参数", fp == {"num_predict": 3000, "think": False})

print("== think 剥离 ==")
c, t = ns["_split_think"]("<think>我要先列目录</think>{\"tool\":\"list_dir\"}", "")
check("Qwen3 风格 think 混正文 → 剥离", c == '{"tool":"list_dir"}' and t == "我要先列目录")
c, t = ns["_split_think"]("<think>未闭合被截断的输出", "")
check("未闭合 think 也剥", not c.startswith("<think>"))
c, t = ns["_split_think"]("正文", "已有思维链")
check("正常正文不动", c == "正文" and t == "已有思维链")

print("== JSON 解析兼容 think 残留 ==")
r = ns["_extract_json"](' blah blah {"thought":"x","tool":"finish","args":{"answer":"ok"}} trailing')
check("前后废话中抠 JSON", r and r["tool"] == "finish")

print("== 代理 format:json 映射 ==")
nscp = {}
exec("import re,json,os", nscp)
for n in ["to_openai_payload", "resolve_model", "model_caps_for",
          "to_openai_messages"]:
    g = grab(src_cp, n)
    assert g, "代理抓不到 " + n
    exec(g, nscp)
nscp["api_base"] = type("M", (), {})()
nscp["api_base"].cloud_models = lambda c=None: []
nscp["api_base"].FREE_MODEL_CATALOG = {}
nscp["api_base"].free_models_for = lambda b: []

cbase = {"enabled": True, "api_key": "x", "base_url": "https://open.bigmodel.cn/api/paas/v4",
         "model": "glm-4.7-flash", "models": [{"id": "glm-4.7-flash", "vision": False, "thinking": True},
                                              {"id": "glm-4-flash", "vision": False, "thinking": False}]}
body = {"model": "glm-4-flash", "messages": [{"role": "user", "content": "hi"}],
        "stream": False, "format": "json",
        "options": {"temperature": 0.2, "num_predict": 100}}
p = nscp["to_openai_payload"](body, cbase)
check("format:json → response_format json_object",
      p.get("response_format") == {"type": "json_object"})
body2 = dict(body); body2.pop("format")
p2 = nscp["to_openai_payload"](body2, cbase)
check("不带 format 时不加 response_format", "response_format" not in p2)
# reasoner 豁免
creas = {"enabled": True, "api_key": "sk-x", "base_url": "https://api.deepseek.com/v1",
         "model": "deepseek-reasoner",
         "models": [{"id": "deepseek-reasoner", "vision": False, "thinking": True}]}
body3 = {"model": "deepseek-reasoner", "messages": [{"role": "user", "content": "hi"}],
         "stream": False, "format": "json",
         "options": {"temperature": 0.2, "num_predict": 100}}
p3 = nscp["to_openai_payload"](body3, creas)
check("deepseek-reasoner 豁免 response_format", "response_format" not in p3)
check("deepseek-reasoner 同时豁免 temperature", "temperature" not in p3)

print()
print("== 实测：本地 Ollama json 模式 ==")
try:
    r = ns["ollama_chat"]([{"role": "user", "content":
        '输出一个JSON：{"thought":"好的","tool":"finish","args":{"answer":"测试通过"}}'}],
        model="qwen2.5vl:7b", num_predict=200, json_mode=True)
    obj = ns["_extract_json"](r["content"])
    check("本地 json 模式出合法 JSON", obj is not None and obj.get("tool") == "finish")
except Exception as e:
    print("  SKIP 本地 Ollama:", e)

print("== 实测：云端 GLM json 模式 ==")
try:
    t0 = time.time()
    r = ns["_cloud_chat"]([{"role": "user", "content":
        '输出一个JSON对象，形如 {"thought":"...","tool":"finish","args":{"answer":"一句话自我介绍"}}'}],
        model="glm-4-flash", num_predict=500, json_mode=True)
    obj = ns["_extract_json"](r["content"])
    ok = obj is not None and obj.get("tool") == "finish"
    check("GLM json 模式出合法 JSON (%.1fs)" % (time.time() - t0), ok)
    if not ok:
        print("  原始输出:", repr(r["content"][:300]))
except Exception as e:
    print("  SKIP 云端 GLM:", e)

print()
print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    sys.exit(1)
