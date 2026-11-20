# -*- coding: utf-8 -*-
"""把「大肥鱼宿主」和「内置 DSH」的插件清单收成同一套。

现状（改造前）：
  - 大肥鱼宿主 host.mjs  = host-plugins.json ∪ desktop bundles ∪ dfyweb bundles → 16 个
  - 内置 DSH（dfyweb）   = dfyweb bundles（只有 5 个插件）→ 界面上其余全显示「未启用」
  两边各读各的，用户看到的就是「插件没共享一套」。

改造后：
  唯一真源 = dfyweb profile（内置 DSH 亲自用的那份）
  - dfyweb.dsh.profile.bundles 补齐成 16 个（与宿主当前实际加载集一致）
  - dsh-fish-memory 只装在 dsh_host/plugins 里，这里做目录联接进共享 node_modules
  - host.mjs 只读 dfyweb bundles（不再读 desktop，避免 desktop 残留造成再次分叉）
  - doctor 的插件体检同时认 dependencies 与 bundles

幂等，可重复执行。备份：
  profiles/dfyweb/package.json.bak-unify
  dsh_host/host.mjs.bak-unify
"""
import json
import os
import shutil
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
DSH_HOME = r"D:\本地部署大肥鱼环境\dsh"
DFYWEB = os.path.join(DSH_HOME, "profiles", "dfyweb")
SHARED_NM = os.path.join(DSH_HOME, "profiles", "desktop", "node_modules")
HOST_PLUGINS_NM = os.path.join(BASE, "dsh_host", "plugins", "node_modules")
HOST_MJS = os.path.join(BASE, "dsh_host", "host.mjs")

# 与宿主实际加载集一致的业务插件。
# 注意：dsh-bili-scroll 与 dsh-bili-pond 都注册全局工具 bili_whoami，
# DSH 不允许同名，两者不能同时作为 bundle 启用 —— 这里按用户既有配置
# （desktop bundles 本来就没启用 scroll）只保留 pond。
TARGET = [
    "dsh-feiyu-mood", "dsh-soul-md", "dsh-nekowhale-stickers", "dsh-fish-memory",
    "@vectorize-io/hindsight-coding-agents", "dsh-live-compact",
    "dsh-bili-pond", "dsh-bili-whale", "dsh-local-ai", "dsh-onebot-qq", "dsh-pet",
    "dsh-qq-bridge", "dsh-speak-up", "dshmarket", "dsh-qrcode",
]
# 已安装但不启用（避免工具重名冲突）
EXCLUDED = ["dsh-bili-scroll"]
# 官方框架 bundle（必须在前）
FRAMEWORK = [
    "@deepseek-ai/dsh-base",
    "@deepseek-ai/dsh-web-app",
    "@deepseek-ai/dsh-experimental-agent-team-profile",
]


def read_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_json(p, data, backup_suffix=".bak-unify"):
    if not os.path.isfile(p + backup_suffix):
        shutil.copy2(p, p + backup_suffix)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def link_fish_memory():
    """把只装在 dsh_host/plugins 里的 dsh-fish-memory 联接到共享 node_modules。"""
    dst = os.path.join(SHARED_NM, "dsh-fish-memory")
    src = os.path.join(HOST_PLUGINS_NM, "dsh-fish-memory")
    if not os.path.isdir(src):
        return "源目录不存在，跳过"
    if os.path.isdir(dst):
        return "已存在"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", dst, src],
                       capture_output=True, text=True)
    return "已建立联接" if os.path.isdir(dst) else ("失败: " + (r.stdout + r.stderr)[:200])


def unify_profile():
    pkg_path = os.path.join(DFYWEB, "package.json")
    pkg = read_json(pkg_path)
    deps = pkg.get("dependencies") or {}

    missing = [n for n in TARGET if n not in deps]
    bundles = list(FRAMEWORK) + TARGET

    old_bundles = ((pkg.get("dsh") or {}).get("profile") or {}).get("bundles", [])
    pkg.setdefault("dsh", {}).setdefault("profile", {})["bundles"] = bundles
    write_json(pkg_path, pkg)

    return {"missingDeps": missing, "oldBundles": old_bundles, "newBundles": bundles,
            "excluded": EXCLUDED}


def mirror_config():
    """把 desktop 的用户层配置镜像到 dfyweb —— 否则内置 DSH 里模型/人设/onebot 全是空的。"""
    src = os.path.join(DSH_HOME, "profiles", "desktop", "cordis.patch.yml")
    dst = os.path.join(DFYWEB, "cordis.patch.yml")
    if not os.path.isfile(src):
        return "desktop 配置不存在，跳过"
    try:
        a = open(src, encoding="utf-8").read()
        b = open(dst, encoding="utf-8").read() if os.path.isfile(dst) else ""
    except Exception as e:
        return "读取失败: %r" % (e,)
    if a == b:
        return "已一致"
    if not os.path.isfile(dst + ".bak-mirror"):
        shutil.copy2(dst, dst + ".bak-mirror")
    shutil.copy2(src, dst)
    return "已镜像"


def patch_host():
    """host.mjs 只读 dfyweb bundles —— 单一真源。"""
    t = open(HOST_MJS, encoding="utf-8").read()
    if "UNIFY_SINGLE_SOURCE" in t:
        return "already"
    if not os.path.isfile(HOST_MJS + ".bak-unify"):
        shutil.copy2(HOST_MJS, HOST_MJS + ".bak-unify")

    old = "  for (const prof of ['desktop', 'dfyweb']) {"
    new = ("  // UNIFY_SINGLE_SOURCE: 只读 dfyweb —— 它同时是内置 DSH 的 profile，\n"
           "  // 这样「内置 DSH 里启用什么」与「大肥鱼宿主加载什么」永远是同一份。\n"
           "  for (const prof of ['dfyweb']) {")
    if old not in t:
        raise SystemExit("host.mjs 锚点没找到，需人工确认")
    t = t.replace(old, new)
    open(HOST_MJS, "w", encoding="utf-8", newline="").write(t)
    return "patched"


def patch_doctor():
    """体检时把 bundles 也算进「应有插件」，否则 fish-memory 会被判缺失。"""
    p = os.path.join(BASE, "dfy_dsh_doctor.py")
    t = open(p, encoding="utf-8").read()
    if "UNIFY_BUNDLES_AWARE" in t:
        return "already"
    if not os.path.isfile(p + ".bak-unify"):
        shutil.copy2(p, p + ".bak-unify")
    old = """        for key in ("dependencies", "devDependencies", "optionalDependencies"):
            for name in (pkg.get(key) or {}):
                if name == "dshmarket" or name.startswith("dsh-"):
                    names.add(name)"""
    new = """        for key in ("dependencies", "devDependencies", "optionalDependencies"):
            for name in (pkg.get(key) or {}):
                if name == "dshmarket" or name.startswith("dsh-"):
                    names.add(name)
        # UNIFY_BUNDLES_AWARE: bundles 是「启用」的真源，也要纳入体检
        for name in ((pkg.get("dsh") or {}).get("profile") or {}).get("bundles", []):
            if name == "dshmarket" or name.startswith("dsh-"):
                names.add(name)"""
    if old not in t:
        raise SystemExit("doctor 锚点没找到，需人工确认")
    open(p, "w", encoding="utf-8", newline="").write(t.replace(old, new))
    return "patched"


def main():
    r = {"fishMemoryLink": link_fish_memory()}
    r.update(unify_profile())
    r["configMirror"] = mirror_config()
    r["hostPatch"] = patch_host()
    r["doctorPatch"] = patch_doctor()
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
