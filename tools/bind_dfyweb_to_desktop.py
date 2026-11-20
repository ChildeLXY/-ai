# -*- coding: utf-8 -*-
"""把 dfyweb profile 真正接到 desktop profile 上：

- 桌面 profile 的 node_modules 里补进 DSH 内核包（junction，纯新增，不动原有插件）
- dfyweb 的 node_modules 直接指向 desktop 的 node_modules
- dfyweb 的 package.json / cordis.patch.yml 用硬链接指向 desktop 的同名文件

这样在 DSH 原生界面里装/卸/开关插件，改的就是 desktop profile 本身，
大肥鱼宿主重启后立刻能看到同一批插件——不会再出现两份清单。
"""
import os, subprocess

HOME = r"D:\本地部署大肥鱼环境\dsh"
PROF = os.path.join(HOME, "profiles", "dfyweb")
DESK = os.path.join(HOME, "profiles", "desktop")
DESK_NM = os.path.join(DESK, "node_modules")
WEB_NM = r"D:\大肥鱼AI\dsh_web\node_modules"

def jlink(dst, src):
    if os.path.lexists(dst):
        return False
    r = subprocess.run(["cmd", "/c", "mklink", "/J", dst, src], capture_output=True, text=True)
    return r.returncode == 0

def hlink(dst, src):
    if os.path.exists(dst):
        return False
    r = subprocess.run(["cmd", "/c", "mklink", "/H", dst, src], capture_output=True, text=True)
    return r.returncode == 0

# 1) 把 DSH 内核补进 desktop 的 node_modules（只补缺的）
core = os.path.join(WEB_NM, "@deepseek-ai")
target = os.path.join(DESK_NM, "@deepseek-ai")
os.makedirs(target, exist_ok=True)
added = 0
for name in sorted(os.listdir(core)):
    src = os.path.join(core, name)
    if os.path.isdir(src) and jlink(os.path.join(target, name), src):
        added += 1
print("desktop 里新增 DSH 内核包: %d" % added)

# 2) dfyweb 的 node_modules 指向 desktop 的（先删掉之前建的空壳）
prof_nm = os.path.join(PROF, "node_modules")
if os.path.isdir(prof_nm) and not os.path.islink(prof_nm):
    # 里面全是我们自己建的 junction，直接删目录不影响目标
    subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", prof_nm], capture_output=True, text=True)
linked = jlink(prof_nm, DESK_NM)
print("dfyweb/node_modules -> desktop/node_modules : %s" % linked)

# 3) package.json / cordis.patch.yml 改成硬链接，保证两边是同一份配置
pkg_dst = os.path.join(PROF, "package.json")
if os.path.isfile(pkg_dst) and not os.path.islink(pkg_dst):
    os.remove(pkg_dst)
print("package.json 硬链接: %s" % hlink(pkg_dst, os.path.join(DESK, "package.json")))

patch_src = os.path.join(DESK, "cordis.patch.yml")
patch_dst = os.path.join(PROF, "cordis.patch.yml")
if os.path.isfile(patch_dst):
    if os.path.isfile(patch_src) and os.path.getsize(patch_dst) == os.path.getsize(patch_src):
        pass
    else:
        os.remove(patch_dst)
print("cordis.patch.yml 硬链接: %s" % hlink(patch_dst, patch_src))

# 4) 自检
for p in ("node_modules/@deepseek-ai/dsh-web-app/package.json",
          "node_modules/@deepseek-ai/dsh-base/package.json",
          "package.json", "cordis.patch.yml", "node_modules/dsh-bili-pond/package.json"):
    print("  %-46s %s" % (p, "OK" if os.path.exists(os.path.join(PROF, p)) else "缺"))
