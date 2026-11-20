# -*- coding: utf-8 -*-
r"""DSH 值守看门狗：定期体检 6800 / 6811 / doctor 六项，异常自动修。

设计取舍：
- **不改用户的进程归属**：6800（host.mjs）由主程序管，本脚本发现它挂了只记录 + 尝试一次
  `doctor fix`，不会去抢着起第二个 host（两个 host 抢 6800 会把插件加载搞乱）。
- **只重启自己的 6811**：通过 dfy_dshui.open_ui()，它会清掉旧实例、跑 doctor、验市场探针。
- 日志按天切，保留 7 天：`logs\dsh-watchdog-YYYYMMDD.ndjson`

用法：
  python dfy_dsh_watchdog.py --once        # 跑一轮（给自动化/验收用）
  python dfy_dsh_watchdog.py               # 常驻，默认 10 分钟一轮
  python dfy_dsh_watchdog.py --interval 120
"""
import argparse
import datetime
import json
import os
import socket
import subprocess
import sys
import time

BASE = r"D:\大肥鱼AI"
LOGDIR = os.path.join(BASE, "logs")
sys.path.insert(0, BASE)


def _port(port, host="127.0.0.1", timeout=0.4):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def _doctor(fix=False):
    cmd = [sys.executable, os.path.join(BASE, "dfy_dsh_doctor.py"),
           "fix" if fix else "check", "--safe", "--json"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        d = json.loads((p.stdout or "")[p.stdout.find("{"):]) if "{" in (p.stdout or "") else {}
        return p.returncode, d
    except Exception as e:
        return -1, {"error": repr(e)}


def _host_plugins():
    try:
        import urllib.request as u
        with u.urlopen("http://127.0.0.1:6800/health", timeout=2) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        p = j.get("plugins")
        return len(p) if isinstance(p, dict) else 0
    except Exception:
        return -1


def _log(rec):
    os.makedirs(LOGDIR, exist_ok=True)
    day = datetime.date.today().strftime("%Y%m%d")
    path = os.path.join(LOGDIR, "dsh-watchdog-%s.ndjson" % day)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # 只留 7 天
    cutoff = datetime.date.today() - datetime.timedelta(days=7)
    for fn in os.listdir(LOGDIR):
        if not fn.startswith("dsh-watchdog-") or not fn.endswith(".ndjson"):
            continue
        stamp = fn[len("dsh-watchdog-"):-len(".ndjson")]
        try:
            if datetime.datetime.strptime(stamp, "%Y%m%d").date() < cutoff:
                os.remove(os.path.join(LOGDIR, fn))
        except Exception:
            pass
    return path


def one_round(auto_fix=True, ensure_ui=False):
    """ensure_ui=True 时才把 6811 当「必须在跑」；否则按需启停的设计下
    6811 空闲只是状态，不是故障（旧版把它算进 ok，等于逼看门狗常驻拉起 DSH）。"""
    rec = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "actions": []}

    # 1) 6800 宿主
    host_up = _port(6800)
    rec["host6800"] = host_up
    rec["hostPlugins"] = _host_plugins() if host_up else 0

    # 2) 6811 DSH Web（状态记录，不默认要求在线）
    ui_up = _port(6811)
    rec["ui6811"] = ui_up
    rec["uiRequired"] = bool(ensure_ui)

    # 3) doctor（现在是七项，含内置产物）
    rc, d = _doctor()
    rec["doctor"] = "OK" if rc == 0 else "FAIL"
    if rc != 0:
        bad = [c.get("id") for c in (d.get("checks") or []) if not c.get("ok")]
        rec["doctorBad"] = bad
        if auto_fix:
            rc2, d2 = _doctor(fix=True)
            rec["actions"].append("doctor fix -> %s" % ("OK" if rc2 == 0 else "FAIL"))

    # 4) 只有显式要求时，6811 挂了才拉起来
    if ensure_ui and not ui_up and auto_fix:
        try:
            import dfy_dshui
            r = dfy_dshui.open_ui()
            rec["actions"].append("open_ui -> ok=%s pnpm=%s" % (r.get("ok"), r.get("marketPnpm")))
            rec["ui6811"] = bool(r.get("ok"))
        except Exception as e:
            rec["actions"].append("open_ui 异常: %r" % (e,))

    # 5) 6800 不归我们重启，只提示
    if not host_up:
        rec["actions"].append("6800 未监听：宿主由主程序负责，未重复拉起（避免抢端口）")

    ok = rec["host6800"] and rec["doctor"] == "OK"
    if ensure_ui:
        ok = ok and rec["ui6811"]
    rec["ok"] = bool(ok)
    path = _log(rec)
    rec["_log"] = path
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=600)
    ap.add_argument("--no-fix", action="store_true")
    ap.add_argument("--ensure-ui", action="store_true",
                    help="把 6811 当必须在跑：挂了就自动拉起（默认按需启停，不强制）")
    a = ap.parse_args()
    ensure = a.ensure_ui or os.environ.get("DSH_WATCH_ENSURE_UI") == "1"

    if a.once:
        r = one_round(auto_fix=not a.no_fix, ensure_ui=ensure)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.exit(0 if r["ok"] else 1)

    print("看门狗启动，每 %d 秒一轮，Ctrl+C 结束" % a.interval)
    while True:
        try:
            r = one_round(auto_fix=not a.no_fix, ensure_ui=ensure)
            print(r["t"], "OK" if r["ok"] else "FAIL",
                  "host=%s ui=%s doctor=%s" % (r["host6800"], r["ui6811"], r["doctor"]),
                  ("| " + "; ".join(r["actions"])) if r["actions"] else "")
        except Exception as e:
            print(r if 'r' in dir() else "", "看门狗异常:", e)
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
