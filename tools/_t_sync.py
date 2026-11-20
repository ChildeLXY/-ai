import json, os, sys
sys.path.insert(0, r"D:\大肥鱼AI")
import dfy_dshui as d

prof = os.path.join(d.DSH_HOME, "profiles", "dfyweb", "package.json")
desk = os.path.join(d.DSH_HOME, "profiles", "desktop", "package.json")

# 造一条「在 DSH 界面里新装」的依赖，验证关闭时能同步回宿主
with open(prof, encoding="utf-8") as f:
    p = json.load(f)
p["dependencies"]["dsh-sync-probe"] = "^9.9.9"
with open(prof, "w", encoding="utf-8", newline="\n") as f:
    json.dump(p, f, ensure_ascii=False, indent=2)

added = d._sync_back()
print("同步回的依赖:", added)

with open(desk, encoding="utf-8") as f:
    dd = json.load(f)
print("desktop 现在有 dsh-sync-probe:", "dsh-sync-probe" in dd["dependencies"])

# 清理，恢复现场
dd["dependencies"].pop("dsh-sync-probe", None)
with open(desk, "w", encoding="utf-8", newline="\n") as f:
    json.dump(dd, f, ensure_ascii=False, indent=2)
p["dependencies"].pop("dsh-sync-probe", None)
with open(prof, "w", encoding="utf-8", newline="\n") as f:
    json.dump(p, f, ensure_ascii=False, indent=2)
print("已清理，desktop 依赖数:", len(dd["dependencies"]))
