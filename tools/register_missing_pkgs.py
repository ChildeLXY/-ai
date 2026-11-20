# -*- coding: utf-8 -*-
"""把补进来的两个市场包装登记进 profile 依赖，宿主名单里本来就有它们。"""
import io, json, os, shutil, time

PKG = r"D:\本地部署大肥鱼环境\dsh\profiles\desktop\package.json"
ADD = {"dsh-live-compact": "^1.1.11", "dsh-bili-scroll": "^2.5.2"}

bak = PKG + ".bak_before_add"
if not os.path.exists(bak):
    shutil.copy2(PKG, bak)
    print("已备份:", bak)

with io.open(PKG, encoding="utf-8") as f:
    data = json.load(f)
deps = data.setdefault("dependencies", {})
changed = []
for k, v in ADD.items():
    if k not in deps:
        deps[k] = v
        changed.append(k)

with io.open(PKG, "w", encoding="utf-8", newline="\n") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")

print("新登记:", changed or "无需变更")
print("现在依赖数:", len(deps))
for k in ADD:
    print("  %-20s %s" % (k, deps.get(k)))
