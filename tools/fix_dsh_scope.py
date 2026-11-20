# -*- coding: utf-8 -*-
"""把 DSH 的扁平备份补成 node 能解析的布局（造一个 @deepseek-ai 作用域目录）。

只做名录（junction），不复制文件，不改原始包内容。
"""
import os, subprocess, sys

IG = r"E:\DSH-home\profiles\desktop\node_modules\.ignored\@deepseek-ai.bak-shadow-20261113-001912"
SCOPE = os.path.join(IG, "@deepseek-ai")

if not os.path.isdir(SCOPE):
    os.makedirs(SCOPE, exist_ok=True)
    print("已建作用域目录:", SCOPE)

made, skipped = 0, 0
for name in os.listdir(IG):
    src = os.path.join(IG, name)
    if not os.path.isdir(src) or name.startswith("@") or name.startswith("."):
        continue
    if not name.startswith("dsh"):
        continue
    dst = os.path.join(SCOPE, name)
    if os.path.exists(dst):
        skipped += 1
        continue
    # junction：Windows 下最快的链接，不需要管理员权限
    r = subprocess.run(["cmd", "/c", "mklink", "/J", dst, src],
                       capture_output=True, text=True)
    if r.returncode == 0:
        made += 1
    else:
        print("  失败:", name, r.stderr.strip()[:80])

print("链接完成: 新建 %d 个，已存在 %d 个" % (made, skipped))
print("作用域内包数:", len(os.listdir(SCOPE)))
