# -*- coding: utf-8 -*-
"""三家模型（DeepSeek/Qwen/GLM）特化适配验证。

改动要点：
- 不再用 regex 从源码抠函数体 + exec 进手工命名空间（那套全局是伪造的，
  函数改一行就可能静默测到错片段），改为**直接 import 真模块**。
- 实测段失败不再 `SKIP` 静默放行：服务在跑却没出活 = FAIL（退出码 1）；
  服务没跑 = 未验证（退出码 2），两者都不再算「通过」。
"""
import os
import sys
import threading
import time
import urllib.request

sys.path.insert(0, r"D:\DeepBuddy")
sys.path.insert(0, r"D:\大肥鱼AI")
_out, _err = sys.stdout, sys.stderr
import deepbuddy as db            # noqa: E402
import cloud_proxy as cp          # noqa: E402
sys.stdout, sys.stderr = _out, _err

PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name
          + (("  ← " + detail) if detail and not cond else ""))


def skip(name, why):
    SKIP.append(name)
    print("  SKIP " + name + "  ← " + why)


print("== 模型家族识别 ==")
check("glm → glm", db._model_family("glm-4.7-flash") == "glm")
check("chatglm → glm", db._model_family("chatglm3-6b") == "glm")
check("deepseek-r1 → deepseek", db._model_family("deepseek-r1:8b") == "deepseek")
check("qwen → qwen", db._model_family("qwen2.5vl:7b") == "qwen")
check("qwq → qwen", db._model_family("qwq:32b") == "qwen")
check("llama → other", db._model_family("llama3:8b") == "other")

print("== 部署矩阵特化参数 ==")
fp = db._family_params("deepseek-r1:8b")
check("deepseek 本地：不压 think + 预算 6000", fp == {"num_predict": 6000, "think": None})
fp = db._family_params("deepseek-r1:14b")
check("deepseek-r1:14b 同策略", fp["num_predict"] == 6000)
fp = db._family_params("qwen3:8b")
check("qwen 本地：关思考 + 预算 3000", fp == {"num_predict": 3000, "think": False})
fp = db._family_params("qwen2.5vl:7b")
check("qwen2.5vl 同策略", fp["think"] is False)
fp = db._family_params("glm-4.7-flash#cloud")
check("glm 云端：常规参数", fp == {"num_predict": 3000, "think": False})

print("== think 剥离 ==")
c, t = db._split_think("<think>我要先列目录</think>{\"tool\":\"list_dir\"}", "")
check("Qwen3 风格 think 混正文 → 剥离", c == '{"tool":"list_dir"}' and t == "我要先列目录")
c, t = db._split_think("<think>未闭合被截断的输出", "")
check("未闭合 think 也剥", not c.startswith("<think>"))
c, t = db._split_think("正文", "已有思维链")
check("正常正文不动", c == "正文" and t == "已有思维链")

print("== JSON 解析兼容 think 残留 ==")
r = db._extract_json(' blah blah {"thought":"x","tool":"finish","args":{"answer":"ok"}} trailing')
check("前后废话中抠 JSON", bool(r) and r.get("tool") == "finish")

print("== 代理 format:json 映射 ==")


class _FakeApiBase(object):
    FREE_MODEL_CATALOG = {}

    def cloud_models(self, c=None):
        return []

    def free_models_for(self, b):
        return []


cp.api_base = _FakeApiBase()

cbase = {"enabled": True, "api_key": "x", "base_url": "https://open.bigmodel.cn/api/paas/v4",
         "model": "glm-4.7-flash",
         "models": [{"id": "glm-4.7-flash", "vision": False, "thinking": True},
                    {"id": "glm-4-flash", "vision": False, "thinking": False}]}
body = {"model": "glm-4-flash", "messages": [{"role": "user", "content": "hi"}],
        "stream": False, "format": "json",
        "options": {"temperature": 0.2, "num_predict": 100}}
p = cp.to_openai_payload(body, cbase)
check("format:json → response_format json_object",
      p.get("response_format") == {"type": "json_object"})
body2 = dict(body)
body2.pop("format")
p2 = cp.to_openai_payload(body2, cbase)
check("不带 format 时不加 response_format", "response_format" not in p2)
creas = {"enabled": True, "api_key": "sk-x", "base_url": "https://api.deepseek.com/v1",
         "model": "deepseek-reasoner",
         "models": [{"id": "deepseek-reasoner", "vision": False, "thinking": True}]}
body3 = {"model": "deepseek-reasoner", "messages": [{"role": "user", "content": "hi"}],
         "stream": False, "format": "json",
         "options": {"temperature": 0.2, "num_predict": 100}}
p3 = cp.to_openai_payload(body3, creas)
check("deepseek-reasoner 豁免 response_format", "response_format" not in p3)
check("deepseek-reasoner 同时豁免 temperature", "temperature" not in p3)


def _reachable(url):
    try:
        urllib.request.urlopen(url, timeout=4).read()
        return True
    except Exception:
        return False


print()
print("== 实测：本地 Ollama json 模式 ==")


def call_with_deadline(fn, secs):
    """流式读取可能长时间不返回：用线程 + join 兜住，避免测试整体挂死。"""
    box = {}
    def _run():
        try:
            box["v"] = fn()
        except Exception as e:
            box["e"] = e
    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(secs)
    if th.is_alive():
        raise TimeoutError("超过 %d 秒未返回" % secs)
    if "e" in box:
        raise box["e"]
    return box["v"]


TEXT_MODEL = "ds-v4pro-q35-9b-text:latest"
if not _reachable("http://localhost:11434/api/tags"):
    skip("本地 Ollama 实测", "11434 未运行，本次未验证")
else:
    try:
        r = call_with_deadline(lambda: db.ollama_chat(
            [{"role": "user", "content":
                '输出一个JSON：{"thought":"好的","tool":"finish","args":{"answer":"测试通过"}}'}],
            model=TEXT_MODEL, num_predict=200, json_mode=True, timeout=120), 150)
        obj = db._extract_json(r["content"])
        check("本地 json 模式出合法 JSON (%s)" % TEXT_MODEL,
              bool(obj) and obj.get("tool") == "finish",
              "原始输出：" + repr((r or {}).get("content", "")[:120]))
    except Exception as e:
        check("本地 Ollama 真实出活", False, repr(e)[:120])

print("== 实测：云端 GLM json 模式 ==")
if not _reachable(db.CLOUD_PROXY + "/api/tags"):
    skip("云端 GLM 实测", "兼容代理 11435 未运行，本次未验证")
else:
    try:
        t0 = time.time()
        r = db._cloud_chat([{"role": "user", "content":
            '输出一个JSON对象，形如 {"thought":"...","tool":"finish","args":{"answer":"一句话自我介绍"}}'}],
            model="glm-4-flash", num_predict=500, json_mode=True)
        obj = db._extract_json(r["content"])
        check("GLM json 模式出合法 JSON (%.1fs)" % (time.time() - t0),
              bool(obj) and obj.get("tool") == "finish",
              "原始输出：" + repr((r or {}).get("content", "")[:200]))
    except Exception as e:
        check("云端 GLM 真实出活", False, repr(e)[:120])

print()
print("=" * 60)
print("通过 %d / 失败 %d / 未验证 %d" % (len(PASS), len(FAIL), len(SKIP)))
for f in FAIL:
    print("  - FAIL " + f)
for s in SKIP:
    print("  - SKIP " + s)
if FAIL:
    sys.exit(1)
if SKIP:
    sys.exit(2)
