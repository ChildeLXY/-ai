# -*- coding: utf-8 -*-
"""AutoClaw 式改造的验证（真模块，不再 regex 抠函数体 + exec 伪造命名空间）。"""
import os
import re
import sys
import threading
import time

sys.path.insert(0, r"D:\DeepBuddy")
_out, _err = sys.stdout, sys.stderr
import deepbuddy as db          # noqa: E402
sys.stdout, sys.stderr = _out, _err

os.makedirs(db.WORK_DIR, exist_ok=True)

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name)


print("== 凭证保护 ==")
check("id_rsa 被拦", db._is_credential_path(r"C:\Users\x\.ssh\id_rsa"))
check(".env 被拦", db._is_credential_path(r"D:\proj\.env"))
check(".aws/config 被拦", db._is_credential_path(r"C:\Users\x\.aws\config"))
check(".gnupg/pubring 被拦", db._is_credential_path(r"C:\Users\x\.gnupg\pubring.gpg"))
check("apikey.txt 被拦", db._is_credential_path(r"D:\proj\apikey.txt"))
check("普通 txt 放行", not db._is_credential_path(r"D:\proj\readme.txt"))
check("tokenizer.py 不误伤", not db._is_credential_path(r"D:\proj\tokenizer.py"))
r = db.tool_read_file({"path": r"D:\proj\.env"})
check("read_file 返回拦截文案", "安全策略拦截" in r)
r = db.tool_write_file({"path": r"C:\Users\x\secrets.pem", "content": "x"})
check("write_file 返回拦截文案", "安全策略拦截" in r)
r = db.tool_list_dir({"path": r"C:\Users\x\.ssh"})
check("list_dir 也过凭证门", "安全策略拦截" in r)

print("== 上下文注入 ==")
ctx = db._system_context()
check("含日期", "2026-" in ctx)
check("含星期", "星期" in ctx)
check("含 Windows", "Windows" in ctx)
check("含工作目录", "workspace" in ctx)

print("== 后台进程 ==")
PY = sys.executable
r = db.tool_start_process({"command": '"%s" -c "import time;[print(\'tick\',i,flush=True) or time.sleep(0.4) for i in range(20)]"' % PY})
check("启动返回 id", "进程 id=" in r)
m = re.search(r"id=([0-9a-f]+)", r)
check("id 可解析", bool(m))
pid = m.group(1) if m else ""
time.sleep(1.2)
r = db.tool_poll_process({"id": pid})
check("poll 到输出", "tick" in r)
check("进程存活", "存活" in r)
r = db.tool_stop_process({"id": pid})
check("stop 成功", "已停止" in r)
r = db.tool_poll_process({"id": pid})
check("stop 后 poll 报不存在", "不存在" in r)
r = db.tool_poll_process({"id": "deadbeef"})
check("假 id 报错", "不存在" in r)

print("== 后台进程拒绝危险命令 ==")
r = db.tool_start_process({"command": "rm -rf /"})
check("危险命令被拦", "拦截" in r)

print("== 危险命令确认门 ==")
tid = db._new_task()
res = {}


def ask():
    res["ok"] = db._ask_confirm(tid, "测试：format c:")


th = threading.Thread(target=ask)
th.start()
time.sleep(0.5)
with db._LOCK:
    ev = db.TASKS[tid]["confirm_event"]
    db.TASKS[tid]["confirm_result"] = True
    ev.set()
th.join(5)
check("允许后放行", res.get("ok") is True)

tid2 = db._new_task()
res2 = {}


def ask2():
    res2["ok"] = db._ask_confirm(tid2, "测试")


th2 = threading.Thread(target=ask2)
th2.start()
time.sleep(0.5)
with db._LOCK:
    db.TASKS[tid2]["confirm_event"].set()
    db.TASKS[tid2]["confirm_result"] = False
th2.join(5)
check("拒绝后不放行", res2.get("ok") is False)
check("确认事件发出来了",
      any(e["kind"] == "confirm" for e in db.TASKS[tid]["events"]))

print()
print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
