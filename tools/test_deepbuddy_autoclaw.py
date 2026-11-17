# -*- coding: utf-8 -*-
"""AutoClaw 式改造的单元级验证（不起 GUI，直接抠函数跑）。"""
import os, re, sys, time, threading, json, base64, io, uuid

SRC = r"D:\DeepBuddy\deepbuddy.py"
src = open(SRC, encoding="utf-8").read()

def grab(name):
    m = re.search(r"^def %s\(.*?(?=^def |^class |^# -*-|^if __name__)" % name, src, re.S | re.M)
    return m.group(0) if m else None

wanted = ["_is_dangerous", "_is_credential_path", "tool_start_process",
          "tool_poll_process", "tool_stop_process", "_bg_read",
          "tool_run_shell", "tool_read_file", "tool_write_file", "tool_append_file",
          "_abs", "_system_context", "_ask_confirm", "_emit", "_new_task",
          "_bare_start", "_wait_confirm"]
consts = ["import os", "import re", "import sys", "import json", "import time",
          "import uuid", "import shutil", "import threading",
          "DANGER_PATTERNS", "CREDENTIAL_NAME_PATTERNS", "BG_DIR", "_BG_PROCS",
          "_BG_LOCK", "WORK_DIR", "APP_DIR", "TASKS", "_LOCK",
          "CONFIRM_TIMEOUT", "TASK_TIMEOUT"]
ns = {}
exec("import os,re,sys,json,time,uuid,shutil,threading,platform,subprocess", ns)
for c in ["DANGER_PATTERNS", "CREDENTIAL_NAME_PATTERNS"]:
    m = re.search(r"^%s = \[.*?^\]" % c, src, re.S | re.M)
    exec(m.group(0), ns)
ns["APP_DIR"] = r"D:\DeepBuddy"
ns["WORK_DIR"] = os.path.join(ns["APP_DIR"], "workspace")
ns["TASKS"] = {}
ns["_LOCK"] = threading.RLock()
ns["CONFIRM_TIMEOUT"] = 120
ns["TASK_TIMEOUT"] = 1200
ns["BG_DIR"] = os.path.join(ns["WORK_DIR"], "_bg")
ns["_BG_PROCS"] = {}
ns["_BG_LOCK"] = threading.Lock()
ns["uuid"] = uuid
for n in wanted:
    g = grab(n)
    assert g, "抓不到函数 " + n
    exec(g, ns)

os.makedirs(ns["WORK_DIR"], exist_ok=True)
PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name)

print("== 凭证保护 ==")
check("id_rsa 被拦读", ns["_is_credential_path"](r"C:\Users\x\.ssh\id_rsa"))
check(".env 被拦读", ns["_is_credential_path"](r"D:\proj\.env"))
check("apikey.txt 被拦写", ns["_is_credential_path"](r"D:\proj\apikey.txt"))
check("普通 txt 放行", not ns["_is_credential_path"](r"D:\proj\readme.txt"))
r = ns["tool_read_file"]({"path": r"D:\proj\.env"})
check("read_file 返回拦截文案", "安全策略拦截" in r)
r = ns["tool_write_file"]({"path": r"C:\Users\x\secrets.pem", "content": "x"})
check("write_file 返回拦截文案", "安全策略拦截" in r)

print("== 上下文注入 ==")
ctx = ns["_system_context"]()
check("含日期", "2026-" in ctx)
check("含星期", "星期" in ctx)
check("含 Windows", "Windows" in ctx)
check("含工作目录", "workspace" in ctx)

print("== 后台进程 ==")
r = ns["tool_start_process"]({"command": "python -c \"import time;[print('tick',i,flush=True) or time.sleep(0.4) for i in range(20)]\""})
check("启动返回 id", "进程 id=" in r)
pid = re.search(r"id=([0-9a-f]+)", r).group(1)
time.sleep(1.2)
r = ns["tool_poll_process"]({"id": pid})
check("poll 到输出", "tick" in r)
check("进程存活", "存活" in r)
r = ns["tool_stop_process"]({"id": pid})
check("stop 成功", "已停止" in r)
r = ns["tool_poll_process"]({"id": pid})
check("stop 后 poll 报不存在", "不存在" in r)
r = ns["tool_poll_process"]({"id": "deadbeef"})
check("假 id 报错", "不存在" in r)

print("== 危险命令确认门 ==")
tid = ns["_new_task"]()
res = {}
def ask():
    res["ok"] = ns["_ask_confirm"](tid, "测试：format c:")
th = threading.Thread(target=ask); th.start()
time.sleep(0.5)
with ns["_LOCK"]:
    ev = ns["TASKS"][tid]["confirm_event"]
    ns["TASKS"][tid]["confirm_result"] = True
    ev.set()
th.join(5)
check("允许后放行", res.get("ok") is True)

tid2 = ns["_new_task"]()
res2 = {}
def ask2():
    res2["ok"] = ns["_ask_confirm"](tid2, "测试")
th2 = threading.Thread(target=ask2); th2.start()
time.sleep(0.5)
with ns["_LOCK"]:
    ns["TASKS"][tid2]["confirm_event"].set()
    ns["TASKS"][tid2]["confirm_result"] = False
th2.join(5)
check("拒绝后不放行", res2.get("ok") is False)
check("确认事件发出来了", any(e["kind"] == "confirm" for e in ns["TASKS"][tid]["events"]))

print()
print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    sys.exit(1)
