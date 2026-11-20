# -*- coding: utf-8 -*-
"""dfyweb 只做「插件市场 / 插件管理」界面，不重复挂载业务插件。

- package.json：依赖照抄 desktop（这样界面里看到的就是同一批插件），
  但 bundles 只保留 DSH 内核 + web 前端两束，避免 onebot / bili 等被跑第二份
- cordis.patch.yml：空表，不挂业务插件配置
- node_modules 仍指向 desktop，界面里装的东西会真的落到同一个目录
"""
import json, os, subprocess

HOME = r"D:\本地部署大肥鱼环境\dsh"
PROF = os.path.join(HOME, "profiles", "dfyweb")
DESK = os.path.join(HOME, "profiles", "desktop")

CORE_BUNDLES = ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app"]

with open(os.path.join(DESK, "package.json"), encoding="utf-8") as f:
    desk = json.load(f)

prof = {
    "name": "dsh-profile-dfyweb",
    "private": True,
    "dependencies": dict(desk.get("dependencies", {})),
    "dsh": {"profile": {"bundles": CORE_BUNDLES}},
}

# package.json 目前是硬链接，先断开再写，否则会连带改掉 desktop 的
pkg = os.path.join(PROF, "package.json")
if os.path.isfile(pkg):
    os.remove(pkg)
with open(pkg, "w", encoding="utf-8", newline="\n") as f:
    json.dump(prof, f, ensure_ascii=False, indent=2)
    f.write("\n")
print("dfyweb/package.json 已重写：依赖 %d 个，bundle 仅 %d 个（内核+web 前端）"
      % (len(prof["dependencies"]), len(CORE_BUNDLES)))

patch = os.path.join(PROF, "cordis.patch.yml")
if os.path.isfile(patch):
    os.remove(patch)
with open(patch, "w", encoding="utf-8", newline="\n") as f:
    f.write("# dfyweb 只挂管理界面，业务插件配置留空\n[]\n")
print("dfyweb/cordis.patch.yml 已改为空表")

# desktop 的 package.json 确认没被连带改掉
with open(os.path.join(DESK, "package.json"), encoding="utf-8") as f:
    d2 = json.load(f)
print("desktop bundles 仍是 %d 个（应含业务插件）：%s"
      % (len(d2["dsh"]["profile"]["bundles"]),
         "OK" if "dsh-onebot-qq" in d2["dsh"]["profile"]["bundles"] else "被改坏了"))
print("desktop 依赖 %d 个" % len(d2.get("dependencies", {})))
