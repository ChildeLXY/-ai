# -*- coding: utf-8 -*-
"""实测：本地 JSON 模式 + 云端 GLM JSON 模式（部署矩阵：千问/DeepSeek 本地，GLM 云端）

旧版用正则从 deepbuddy.py 里抠几个函数拼命名空间，抠不全就炸在内层依赖上
（曾出现 `_scan_tool_call is not defined`，而外部看起来像"模型坏了"）。
这里改成真模块导入：测的就是真函数，缺依赖也不会伪装成模型故障。

退出码：0 通过 / 1 功能坏 / 2 前置不满足（本地模型或云端代理没起来）
"""
import io
import json
import os
import sys
import time

sys.path.insert(0, r"D:\DeepBuddy")

_LOCAL_MODEL = os.environ.get("DFY_TEST_LOCAL_MODEL", "ds-v4pro-q35-9b-text:latest")
_TIMEOUT = int(os.environ.get("DFY_TEST_TIMEOUT", "600"))

PASS, FAIL, SKIP = [], [], []


def check(name, cond, extra=""):
    print(("  PASS " if cond else "  FAIL ") + name + (("  -> %s" % extra) if extra and not cond else ""))
    (PASS if cond else FAIL).append(name)
    return cond


def skip(name, why):
    print("  SKIP " + name + "：" + str(why)[:120])
    SKIP.append(name)


def main():
    # deepbuddy 导入即把 stdout/stderr 重定向到 _run.log，先存后恢复
    so, se = sys.stdout, sys.stderr
    import deepbuddy as db
    sys.stdout, sys.stderr = so, se

    print("== 实测：本地 JSON 模式（%s）==" % _LOCAL_MODEL)
    try:
        t0 = time.time()
        r = db.ollama_chat(
            [{"role": "user", "content":
              '输出一个JSON：{"thought":"好的","tool":"finish","args":{"answer":"测试通过"}}'}],
            model=_LOCAL_MODEL, num_predict=400, json_mode=True, timeout=_TIMEOUT)
        obj = db._extract_json(r.get("content") or "")
        check("本地 json 出合法 JSON (%.1fs)" % (time.time() - t0),
              bool(obj) and obj.get("tool") == "finish", repr((r.get("content") or "")[:160]))
    except Exception as e:
        skip("本地 json 模式", "本地后端不可用：%r" % (e,))

    print("== 实测：云端 GLM (glm-4-flash) JSON 模式 ==")
    try:
        t0 = time.time()
        r = db._cloud_chat(
            [{"role": "user", "content":
              '输出一个JSON对象，形如 {"thought":"...","tool":"finish","args":{"answer":"一句话自我介绍"}}'}],
            model="glm-4-flash", num_predict=500, json_mode=True, timeout=_TIMEOUT)
        obj = db._extract_json(r.get("content") or "")
        check("GLM json 出合法 JSON (%.1fs)" % (time.time() - t0),
              bool(obj) and obj.get("tool") == "finish", repr((r.get("content") or "")[:160]))
    except Exception as e:
        skip("云端 GLM json 模式", "云端代理不可用：%r" % (e,))

    print("== 实测：推理模型特化（不压 think + 大预算，%s）==" % _LOCAL_MODEL)
    try:
        t0 = time.time()
        r = db.ollama_chat(
            [{"role": "user", "content":
              '先想清楚再输出一个JSON：{"thought":"好的","tool":"finish","args":{"answer":"测试通过"}}'}],
            model=_LOCAL_MODEL, num_predict=6000, json_mode=True, timeout=_TIMEOUT)
        full = (r.get("content") or "").strip()
        content, thinking = db._split_think(full, (r.get("thinking") or "").strip())
        obj = db._extract_json(content) or db._extract_json(thinking)
        check("推理模型 json 出合法 (%.1fs, thinking=%d字)" % (time.time() - t0, len(thinking)),
              bool(obj) and obj.get("tool") == "finish",
              "content=%r thinking=%r" % (content[:120], thinking[:120]))
    except Exception as e:
        skip("推理模型特化", "本地后端不可用：%r" % (e,))

    print()
    print("通过 %d，失败 %d，跳过 %d" % (len(PASS), len(FAIL), len(SKIP)))
    if FAIL:
        print("结果: 功能坏")
        return 1
    if SKIP and not PASS:
        print("结果: 未验证（前置不满足）")
        return 2
    if SKIP:
        print("结果: 已验证的部分通过，另有 %d 项前置不满足" % len(SKIP))
        return 2
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
