# -*- coding: utf-8 -*-
"""回归轮次执行器：跑一轮 13 项矩阵 → 记日志 → 失败自动 doctor fix 再跑一轮。

给值守用：`python dfy_regress_round.py`。退出码 0 = 最终全绿。
日志追加到 `docs\\dsh-regression-log.md`（只记失败明细，避免文件膨胀）。
"""
import datetime
import json
import os
import subprocess
import sys

BASE = r"D:\大肥鱼AI"
CACHE = r"D:\大肥鱼缓存"
NODE = r"D:\本地部署大肥鱼环境\dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
SCRIPT = os.path.join(CACHE, "pw_regress_all.mjs")
RESULT = os.path.join(CACHE, "regress_result.json")
LOGF = os.path.join(BASE, "docs", "dsh-regression-log.md")


def run_once():
    try:
        os.remove(RESULT)
    except Exception:
        pass
    p = subprocess.run([NODE, SCRIPT], cwd=CACHE, capture_output=True, text=True,
                       timeout=1200)
    out = (p.stdout or "") + (p.stderr or "")
    try:
        with open(RESULT, encoding="utf-8") as f:
            res = json.load(f)
    except Exception:
        return [], out
    return res, out


def doctor_fix():
    try:
        subprocess.run([sys.executable, os.path.join(BASE, "dfy_dsh_doctor.py"),
                        "fix", "--safe"], capture_output=True, text=True, timeout=180)
        return True
    except Exception:
        return False


def append_log(block):
    os.makedirs(os.path.dirname(LOGF), exist_ok=True)
    new = not os.path.exists(LOGF)
    with open(LOGF, "a", encoding="utf-8") as f:
        if new:
            f.write("# DSH 回归轮巡日志\n\n每轮由 `dfy_regress_round.py` 自动追加；"
                    "只记失败项，全绿时记一行汇总。\n\n")
        f.write(block + "\n")


def fmt(res, tag, elapsed):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    passed = sum(1 for r in res if r.get("ok"))
    total = len(res)
    head = "- `%s` %s — **%d/%d**（%.0fs）%s" % (ts, tag, passed, total, elapsed,
                                               "全绿 ✅" if passed == total else "有失败 ❌")
    lines = [head]
    for r in res:
        if not r.get("ok"):
            lines.append("  - ❌ %s — %s" % (r.get("name"), str(r.get("detail"))[:200]))
    return "\n".join(lines), passed, total


def main():
    import time
    t0 = time.time()
    res, out = run_once()
    if not res:
        append_log("- `%s` 首轮未产出结果（脚本崩溃）\n\n```\n%s\n```\n"
                   % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), out[-1200:]))
        print(out[-1500:])
        sys.exit(1)

    block, passed, total = fmt(res, "首轮", time.time() - t0)
    append_log(block)
    print(block)
    if passed == total:
        sys.exit(0)

    print("\n有失败，先 doctor fix 再跑一轮…")
    doctor_fix()
    t1 = time.time()
    res2, out2 = run_once()
    block2, passed2, total2 = fmt(res2, "fix 后复跑", time.time() - t1)
    append_log(block2)
    print(block2)
    sys.exit(0 if passed2 == total2 else 1)


if __name__ == "__main__":
    main()
