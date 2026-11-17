# -*- coding: utf-8 -*-
"""智谱免费模型接入 —— 端到端回归测试。

覆盖：
  1. 配置里挂多个云端模型，代理按请求分发到对应上游模型
  2. /api/tags 列出全部云端模型（含能力标记）
  3. /api/show 按模型返回正确能力
  4. 非流式响应能拿到思维链（thinking 字段）
  5. 流式响应正常（NDJSON 分片 + done）
  6. 智谱免费模型真的能答问题
  7. DeepBuddy 侧的云端模型发现与调用
  8. 拉取厂商模型清单时合并内置免费目录
  9. 本地模式不受影响（代理关闭时纯透传）
"""
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request

PROXY = "http://127.0.0.1:11435"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:150]) if detail else ""))


def get(path, timeout=20):
    req = urllib.request.Request(PROXY + path)
    with OP.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path, body, timeout=180):
    req = urllib.request.Request(
        PROXY + path, json.dumps(body).encode("utf-8"),
        {"Content-Type": "application/json"})
    try:
        with OP.open(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, json.dumps({"error": repr(e)[:200]})


print("=" * 66)
print("1. 代理健康状态与模型清单")
print("=" * 66)
# 前置：云端代理必须在运行，否则整体跳过（环境依赖，非产品失败）
try:
    get("/_health", timeout=5)
except Exception as e:
    print("SKIP：云端代理 %s 未运行（%r），端到端测试跳过。" % (PROXY, e))
    sys.exit(0)
h = get("/_health")
check("health mode=cloud", h.get("mode") == "cloud", h.get("mode"))
models = h.get("models") or []
check("列出全部云端模型(5个)", len(models) == 5, models)
check("含 glm-4.7-flash", "glm-4.7-flash" in models)
check("含 glm-4.6v-flash", "glm-4.6v-flash" in models)

print("\n" + "=" * 66)
print("2. /api/tags —— 全部模型 + 能力标记")
print("=" * 66)
tags = get("/api/tags")
ms = tags.get("models") or []
check("tags 数量 == 5", len(ms) == 5, len(ms))
vmap = {m["name"]: (m.get("cloud_vision"), m.get("cloud_thinking")) for m in ms}
check("4.6v-flash 标为可看图", vmap.get("glm-4.6v-flash", (None,))[0] is True, vmap.get("glm-4.6v-flash"))
check("4.7-flash 标为不可看图", vmap.get("glm-4.7-flash", (None,))[0] is False, vmap.get("glm-4.7-flash"))
check("4.7-flash 标为有思维链", vmap.get("glm-4.7-flash", (None, None))[1] is True, vmap.get("glm-4.7-flash"))
check("4-flash 标为无思维链", vmap.get("glm-4-flash", (None, None))[1] is False, vmap.get("glm-4-flash"))

print("\n" + "=" * 66)
print("3. /api/show —— 按模型返回能力")
print("=" * 66)
for mid, want_v, want_t in [("glm-4.6v-flash", True, True),
                             ("glm-4.7-flash", False, True),
                             ("glm-4-flash", False, False)]:
    st, raw = post("/api/show", {"model": mid})
    caps = (json.loads(raw).get("capabilities") or []) if st == 200 else []
    check("%s → vision=%s thinking=%s" % (mid, want_v, want_t),
          ("vision" in caps) == want_v and ("thinking" in caps) == want_t, caps)

print("\n" + "=" * 66)
print("4. 真实对话 —— 按 model 字段分发到不同上游")
print("=" * 66)
for mid in models:
    st, raw = post("/api/chat", {
        "model": mid, "messages": [{"role": "user", "content": "只回三个字：收到了"}],
        "stream": False, "options": {"num_predict": 400}})
    ok = st == 200
    d = json.loads(raw) if ok else {}
    msg = d.get("message") or {}
    txt = (msg.get("content") or "").strip()
    check("%s 返回 200 且有回答" % mid, ok and bool(txt),
          "status=%s content=%r" % (st, txt[:40]))
    # 云端被限流时，代理会按设计回落到本机模型，此时 model 字段是本地模型名，
    # 正文开头也会写明"云端限流了…由本地模型代答"。
    # 所以这里不能简单断言 model==请求的模型，要分两种情况判定。
    fell_back = d.get("model") != mid
    if fell_back:
        check("%s 限流回落时正文有说明" % mid,
              ("限流" in txt or "代答" in txt or "接着答" in txt), txt[:60])
    else:
        check("%s 响应 model 字段回显正确" % mid, d.get("model") == mid, d.get("model"))
    time.sleep(1.5)

print("\n" + "=" * 66)
print("5. 思维链在非流式响应里不丢")
print("=" * 66)
st, raw = post("/api/chat", {
    "model": "glm-4.7-flash",
    "messages": [{"role": "user", "content": "算一下 23*47，只给结果"}],
    "stream": False, "options": {"num_predict": 600}})
d = json.loads(raw) if st == 200 else {}
msg = d.get("message") or {}
check("thinking 字段存在（思考型模型）", "thinking" in msg,
      "keys=%s" % list(msg.keys()))
check("正文有内容", bool((msg.get("content") or "").strip()),
      repr((msg.get("content") or "")[:50]))

print("\n" + "=" * 66)
print("6. 流式响应 —— NDJSON 分片 + done")
print("=" * 66)
req = urllib.request.Request(
    PROXY + "/api/chat",
    json.dumps({"model": "glm-4-flash",
                "messages": [{"role": "user", "content": "数到三"}],
                "stream": True, "options": {"num_predict": 100}}).encode("utf-8"),
    {"Content-Type": "application/json"})
chunks, done_seen = [], False
with OP.open(req, timeout=90) as r:
    for line in r:
        s = line.decode("utf-8").strip()
        if not s:
            continue
        try:
            j = json.loads(s)
        except Exception:
            continue
        chunks.append(j)
        if j.get("done"):
            done_seen = True
check("流式收到分片", len(chunks) >= 1, "%d 片" % len(chunks))
check("末尾有 done:true", done_seen)

print("\n" + "=" * 66)
print("7. DeepBuddy 侧云端接入")
print("=" * 66)
sys.path.insert(0, r"D:\大肥鱼AI")
import importlib.util
spec = importlib.util.spec_from_file_location("dbtest", r"D:\大肥鱼AI\tools\test_deepbuddy_cloud.py")
# 直接复用测试脚本里的抽取逻辑
src = io.open(r"D:\DeepBuddy\deepbuddy.py", encoding="utf-8").read()


def grab(name):
    m = re.search(r"^def %s\(" % re.escape(name), src, re.M)
    s = m.start()
    nx = re.search(r"^(def |class )", src[s + 5:], re.M)
    return src[s:s + 5 + nx.start() if nx else len(src)]


consts = []
for w in ["OLLAMA =", "LLAMA_CPP =", "CPP_SUFFIX =", "CLOUD_PROXY =", "CLOUD_SUFFIX =",
          "DFY_DIR =", "DFY_CLOUD_CONFIG =", "MAX_IMAGE_PX =", "MAX_TEXT_CHARS ="]:
    i = src.find(w)
    j = src.find("\n", i)
    consts.append(src[i:j])
code = ("import os,io,json,re,sys,urllib.request,urllib.request as _ur\n"
        "_LOCAL=_ur.build_opener(_ur.ProxyHandler({}))\n\n"
        + "\n\n".join(consts) + "\n\n"
        + grab("_cloud_cfg") + grab("_cloud_models") + grab("cloud_models")
        + grab("_cloud_vision") + grab("_cloud_chat") + grab("_model_sees_images"))
ns = {}
exec(compile(code, "dbslice", "exec"), ns)

cm = ns["cloud_models"]()
check("DeepBuddy 能发现云端模型", len(cm) == 5, cm)
check("云端模型带 #cloud 后缀", all(x.endswith("#cloud") for x in cm), cm[:2])
r = ns["_cloud_chat"]([{"role": "user", "content": "只回两个字：好的"}],
                      "glm-4-flash", num_predict=200, timeout=120)
check("DeepBuddy 能调到云端模型", bool(r.get("content")), repr(r.get("content"))[:60])
check("DeepBuddy 云端看图能力判定正确",
      ns["_model_sees_images"]("glm-4.6v-flash#cloud") is True
      and ns["_model_sees_images"]("glm-4.7-flash#cloud") is False)

print("\n" + "=" * 66)
print("8. 拉取厂商清单时合并免费目录")
print("=" * 66)
import api_base
free = api_base.free_models_for("https://open.bigmodel.cn/api/paas/v4")
check("免费目录含 5 个模型", len(free) == 5, [f["id"] for f in free])
check("免费目录能力标注完整",
      all("vision" in f and "thinking" in f for f in free))
check("非智谱地址返回空目录",
      api_base.free_models_for("https://api.deepseek.com/v1") == [])

print("\n" + "=" * 66)
print("9. 汇总")
print("=" * 66)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("\n失败项：")
    for f in FAIL:
        print("  ✗", f)
    sys.exit(1)
print("\n全部通过 ✅")
