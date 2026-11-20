# -*- coding: utf-8 -*-
"""DSH 原生界面托管（dfy_dshui）生命周期测试。

覆盖四个真实会遇到的路径：
  1. 首次打开 → 拿到带 token 地址
  2. 再点一次 → 复用（不重启，省一次 6 秒 + 150MB 内存）
  3. 关闭 → 进程结束、端口释放
  4. 端口上有「残留实例、但日志里没有 token」 → 能自动清掉并重新拉起
     （上一版这里会直接报"进程已退出"，是真机上踩到过的坑）

跑法：python tools\test_dsh_ui_lifecycle.py
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, r"D:\大肥鱼AI")
import dfy_dshui as d  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (("  " + str(extra)) if extra else ""))


def boot_outside():
    """绕开模块自己起一个实例，模拟"残留"。"""
    env = dict(os.environ)
    env["DSH_HOME"] = d.DSH_HOME
    env.pop("REMOTE_DEBUGGING_PORT", None)
    creation = 0x08000000 if sys.platform.startswith("win") else 0
    return subprocess.Popen(
        [d.NODE, d.CLI, "--profile", d.PROFILE, "--port", str(d.PORT), "--no-open"],
        cwd=d.CWD, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, creationflags=creation)


print("== 0) 起跑线：先确保端口是空的 ==")
if d.status()["running"]:
    print("  发现已有实例，先关掉")
    d.close_ui()
time.sleep(1)
check("起点端口空闲", not d._port_alive())

print("\n== 1) 首次打开 ==")
t0 = time.time()
r1 = d.open_ui()
check("首次打开成功", r1.get("ok") is True, r1.get("error", ""))
check("拿到带 token 的地址", "token=" in (r1.get("url") or ""), r1.get("url", "")[:60])
check("是新建而非复用", r1.get("reused") is False)
print("  启动耗时 %.1f 秒" % (time.time() - t0))
check("地址真能访问", (subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "try { (Invoke-WebRequest '%s' -UseBasicParsing -TimeoutSec 8).StatusCode } catch { 0 }" % r1.get("url", "").replace("&", "^&")],
    capture_output=True, text=True, timeout=25).stdout or "").strip() in ("200", "401"))

print("\n== 2) 再点一次：必须复用 ==")
r2 = d.open_ui()
check("第二次打开返回复用", r2.get("reused") is True)
check("两次地址一致", r2.get("url") == r1.get("url"))
st = d.status()
check("status 报 running", st.get("running") is True)

print("\n== 3) 关闭：进程与端口都要释放 ==")
c = d.close_ui()
check("close 返回 ok", c.get("ok") is True)
check("端口已释放", not d._port_alive())
check("status 不再 running", d.status().get("running") is False)

print("\n== 4) 残留实例 + 日志里没 token：要能接管 ==")
p = boot_outside()
deadline = time.time() + 45
while time.time() < deadline and not d._port_alive():
    time.sleep(0.5)
check("残留实例已起来（端口被占）", d._port_alive())
try:                                     # 清掉日志里的 token，逼出"占着端口但拿不到地址"这条路径
    open(d.LOG, "w", encoding="utf-8").close()
except Exception:
    pass
r4 = d.open_ui()
check("能接管残留实例并重新拉起", r4.get("ok") is True, r4.get("error", ""))
check("接管后拿到新地址", "token=" in (r4.get("url") or ""))
check("不是复用（确实重起了）", r4.get("reused") is False)

print("\n== 5) 收尾 ==")
c2 = d.close_ui()
check("收尾关闭成功", c2.get("ok") is True)
time.sleep(1)
check("收尾后端口空闲", not d._port_alive())
try:
    p.kill()
except Exception:
    pass

print("\n===== 结果：%d 通过 / %d 失败 =====" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + "；".join(FAIL))
sys.exit(1 if FAIL else 0)
