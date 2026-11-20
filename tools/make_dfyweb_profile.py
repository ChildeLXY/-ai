# -*- coding: utf-8 -*-
"""新建 dfyweb profile：复用 desktop 的插件 + 借用 dsh_web 里的 DSH 内核。

只做 junction（不复制文件）：desktop profile 与正在跑的宿主完全不受影响。
"""
import json, os, shutil, subprocess

HOME = r"D:\本地部署大肥鱼环境\dsh"
PROF = os.path.join(HOME, "profiles", "dfyweb")
DESK = os.path.join(HOME, "profiles", "desktop")
DESK_NM = os.path.join(DESK, "node_modules")
WEB_NM = r"D:\大肥鱼AI\dsh_web\node_modules"
PROF_NM = os.path.join(PROF, "node_modules")


def jlink(dst, src):
    if os.path.lexists(dst):
        return False
    r = subprocess.run(["cmd", "/c", "mklink", "/J", dst, src], capture_output=True, text=True)
    return r.returncode == 0


os.makedirs(PROF, exist_ok=True)
os.makedirs(PROF_NM, exist_ok=True)

# 1) profile 描述：bundles 与 dependencies 照抄 desktop，保证插件清单一致
with open(os.path.join(DESK, "package.json"), encoding="utf-8") as f:
    desk = json.load(f)
prof = {
    "name": "dsh-profile-dfyweb",
    "private": True,
    "dependencies": desk.get("dependencies", {}),
    "dsh": desk.get("dsh", {}),
}
with open(os.path.join(PROF, "package.json"), "w", encoding="utf-8", newline="\n") as f:
    json.dump(prof, f, ensure_ascii=False, indent=2)
    f.write("\n")
print("已写 profile/package.json，依赖 %d 个，bundle %d 个" % (
    len(prof["dependencies"]), len((prof.get("dsh") or {}).get("profile", {}).get("bundles", []))))

# 2) cordis.yml 空表 + 复制 desktop 的 patch（插件配置保持一致）
with open(os.path.join(PROF, "cordis.yml"), "w", encoding="utf-8", newline="\n") as f:
    f.write("[]\n")
patch_src = os.path.join(DESK, "cordis.patch.yml")
patch_dst = os.path.join(PROF, "cordis.patch.yml")
if os.path.isfile(patch_src):
    shutil.copy2(patch_src, patch_dst)
    print("已复制 cordis.patch.yml")

# 3) node_modules：先链 DSH 内核（dsh_web），再链 desktop 的插件（不覆盖已有）
def link_tree(src_nm, skip_scopes=()):
    made = 0
    for name in sorted(os.listdir(src_nm)):
        src = os.path.join(src_nm, name)
        if not os.path.isdir(src) or name.startswith("."):
            continue
        if name.startswith("@"):
            target_scope = os.path.join(PROF_NM, name)
            os.makedirs(target_scope, exist_ok=True)
            for sub in sorted(os.listdir(src)):
                s2 = os.path.join(src, sub)
                if not os.path.isdir(s2):
                    continue
                if jlink(os.path.join(target_scope, sub), s2):
                    made += 1
        else:
            if jlink(os.path.join(PROF_NM, name), src):
                made += 1
    return made

n_web = link_tree(WEB_NM)
n_desk = link_tree(DESK_NM)
print("链接完成：DSH 内核新增 %d，desktop 插件新增 %d" % (n_web, n_desk))

# 4) 关键包自检
for pkg in ("@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app", "@deepseek-ai/dsh-web-frontend",
            "@deepseek-ai/dsh-client-ui-settings-plugins", "@deepseek-ai/dsh-plugin-market",
            "dsh-bili-pond", "dsh-soul-md"):
    p = os.path.join(PROF_NM, *pkg.split("/"))
    print("  %-42s %s" % (pkg, "OK" if os.path.isdir(p) else "缺"))
