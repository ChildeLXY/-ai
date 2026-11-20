# -*- coding: utf-8 -*-
"""实测 DeepBuddy 的云端模型接入（真模块，不再 regex 抠源码拼命名空间）。

退出码语义（改前：只 print，恒 0，云端为空/对话失败都算「成功」）：
  0 = 真验证通过
  1 = 环境具备但功能坏了（云端已配置/代理已起，却拿不到清单或对话空响应）
  2 = 环境前置不满足（云端代理没运行，本次「未验证」—— 不是通过）
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, r"D:\DeepBuddy")
_out, _err = sys.stdout, sys.stderr
import deepbuddy as db          # noqa: E402
sys.stdout, sys.stderr = _out, _err

PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name
          + (("  ← " + detail) if detail and not cond else ""))


def proxy_up():
    try:
        urllib.request.urlopen(db.CLOUD_PROXY + "/api/tags", timeout=4).read()
        return True
    except Exception:
        return False


print("=== 0. 环境前置 ===")
cfg = db._cloud_cfg()
has_cfg = bool(cfg.get("providers") or cfg.get("cloud") or cfg)
up = proxy_up()
print("  云端配置存在:", has_cfg, " 代理可达:", up)
if not up:
    print("  SKIP 云端代理 %s 未运行 —— 本次未验证（退出码 2，不算通过）" % db.CLOUD_PROXY)
    sys.exit(2)
check("云端配置存在", has_cfg, "cloud_config.json 为空")

print()
print("=== 1. 云端模型清单 ===")
t0 = time.time()
cm = db.cloud_models()
print("  下拉框里的云端模型 %d 个（%.1fs）" % (len(cm), time.time() - t0))
for x in cm[:12]:
    print("   ", x)
check("云端模型清单非空", bool(cm), "代理在跑但清单为空 —— 云端没接上")

print()
print("=== 2. 云端模型实际对话 ===")
if cm:
    real = cm[0][: -len(db.CLOUD_SUFFIX)]
    try:
        r = db._cloud_chat([{"role": "user", "content": "只回答三个字：收到了"}],
                           real, num_predict=200, timeout=60)
        content = (r or {}).get("content") or ""
        print("  模型:", real)
        print("  content :", repr(content[:60]))
        check("云端对话返回非空内容", bool(content.strip()), "空响应")
        check("云端对话内容符合要求", "收到" in content, "模型没按要求回答：" + repr(content[:40]))
    except Exception as e:
        print("  调用失败:", repr(e)[:160])
        check("云端对话可执行", False, repr(e)[:120])
else:
    check("云端对话可执行", False, "无可用云端模型，未能验证")

print()
print("=== 3. 云端模型看图能力 ===")
models = db._cloud_models()
if models:
    for x in models[:8]:
        mid = x["id"]
        v = db._cloud_vision(mid)
        sees = db._model_sees_images(mid + db.CLOUD_SUFFIX)
        print("  %-18s 清单 vision=%-5s  /api/show=%s  sees_images=%s" % (mid, x["vision"], v, sees))
        check("vision 判定为布尔（%s）" % mid,
              isinstance(v, bool) and isinstance(sees, bool),
              "判定未返回布尔：%r/%r" % (v, sees))
else:
    check("看图能力可判定", False, "无云端模型可测")

print()
print("=" * 60)
print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
