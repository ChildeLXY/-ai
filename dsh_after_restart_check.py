# -*- coding: utf-8 -*-
"""真机重启大肥鱼之后的一键自检。

用途：改完 create_window(http_server=True) 与开机直进之后，主程序要**重启一次**才生效。
重启完跑这个脚本，它会把「能不能进官方 DSH 前端」从头到尾验一遍，不用凭感觉。

顺序：doctor 六项 → 拉起 6811 并验市场探针 → 开一个真实窗口跑入口 e2e → 汇总。
"""
import json
import os
import subprocess
import sys

PY = sys.executable
BASE = r"D:\大肥鱼AI"
CACHE = r"D:\大肥鱼缓存"
sys.path.insert(0, BASE)
os.chdir(BASE)

report = {}


def step(name, fn):
    try:
        r = fn()
        report[name] = r
        print("[%s] %s" % ("OK  " if r.get("ok") else "FAIL", name))
        if not r.get("ok"):
            print("      " + str(r.get("detail") or r)[:400])
        return r.get("ok")
    except Exception as e:
        report[name] = {"ok": False, "detail": repr(e)}
        print("[FAIL] %s — %r" % (name, e))
        return False


def doctor():
    p = subprocess.run([PY, os.path.join(BASE, "dfy_dsh_doctor.py"), "check", "--safe", "--json"],
                       capture_output=True, text=True, timeout=180)
    try:
        d = json.loads(p.stdout[p.stdout.find("{"):])
    except Exception:
        return {"ok": False, "detail": (p.stdout or p.stderr)[:400]}
    bad = [c["id"] for c in d.get("checks", []) if not c.get("ok")]
    return {"ok": p.returncode == 0, "detail": ("未通过项: %s" % bad) if bad else "六项全绿",
            "checks": d.get("checks")}


def open_ui():
    import dfy_dshui
    r = dfy_dshui.open_ui()
    ok = bool(r.get("ok")) and bool(r.get("marketPnpm"))
    return {"ok": ok, "detail": json.dumps({k: v for k, v in r.items() if k != "probe"},
                                          ensure_ascii=False), "probe": r.get("probe")}


def entry_e2e():
    p = subprocess.run([PY, os.path.join(CACHE, "dsh_entry_e2e.py")],
                       capture_output=True, text=True, timeout=300, cwd=BASE)
    try:
        d = json.loads((p.stdout or "")[p.stdout.find("{"):])
    except Exception:
        return {"ok": False, "detail": (p.stdout or p.stderr)[-400:]}
    return {"ok": d.get("verdict") == "OK", "detail": json.dumps(d.get("checks"), ensure_ascii=False)}


print("=" * 60)
print("大肥鱼 · DSH 原生入口 重启后自检")
print("=" * 60)
step("1 doctor 六项", doctor)
step("2 拉起 6811 + 市场 pnpm 探针", open_ui)
step("3 入口 e2e（真实窗口）", entry_e2e)

json_path = os.path.join(CACHE, "after_restart_check.json")
with open(json_path, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

ok = all(v.get("ok") for v in report.values())
print("=" * 60)
print("结论：%s（明细 %s）" % ("全部通过" if ok else "有未通过项", json_path))
print("人工只需看一眼：点主界面里的「◈ DSH 原生」，应直接出官方界面而不是 authentication required。")
sys.exit(0 if ok else 1)
