# -*- coding: utf-8 -*-
"""按每个包 package.json 里的真实 name 重建 node_modules 目录布局（全用 junction）。

扁平备份里包目录名被改过（比如 cordis 实际叫 @deepseek-ai/cordis），
不重建的话 node 找不到依赖。
"""
import json, os, subprocess

IG = r"E:\DSH-home\profiles\desktop\node_modules\.ignored\@deepseek-ai.bak-shadow-20261113-001912"
NM = os.path.join(IG, "node_modules")

def link(dst, src):
    if os.path.exists(dst):
        return False
    r = subprocess.run(["cmd", "/c", "mklink", "/J", dst, src], capture_output=True, text=True)
    return r.returncode == 0

made = fail = 0
names = []
for name in sorted(os.listdir(IG)):
    src = os.path.join(IG, name)
    if not os.path.isdir(src) or name.startswith(".") or name == "node_modules":
        continue
    pj = os.path.join(src, "package.json")
    if not os.path.isfile(pj):
        continue
    try:
        with open(pj, encoding="utf-8") as f:
            real = (json.load(f) or {}).get("name") or ""
    except Exception:
        real = name
    if not real:
        continue
    names.append(real)
    parts = real.split("/") if real.startswith("@") else [real]
    d = os.path.join(NM, *parts)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    if link(d, src):
        made += 1
    elif not os.path.exists(d):
        fail += 1

print("包名总数:", len(names))
print("建立链接: %d，失败: %d" % (made, fail))
for probe in ("@deepseek-ai/cordis", "@deepseek-ai/dsh-app-boot", "@deepseek-ai/dsh-web-app", "@deepseek-ai/cosmokit"):
    p = os.path.join(NM, *probe.split("/"))
    print("  %-32s %s" % (probe, "OK" if os.path.isdir(p) else "缺"))
