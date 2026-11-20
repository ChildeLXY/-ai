# -*- coding: utf-8 -*-
"""doctor 体检盲区回归测试：插件覆盖不再按前缀过滤，且能识别「目录在但加载不了」。"""
import json
import os
import shutil
import sys
import tempfile

BASE = r"D:\大肥鱼AI"
sys.path.insert(0, BASE)
import dfy_dsh_doctor as doc  # noqa: E402

FAIL = []


def check(cond, label, extra=""):
    if cond:
        print("  [ OK ] " + label)
    else:
        FAIL.append(label)
        print("  [FAIL] " + label + (("  -> " + str(extra)) if extra else ""))


print("A. 期望插件集不再按 dsh- 前缀过滤")
exp = doc._expected_plugins()
check("cc-safety-net" in exp, "cc-safety-net 纳入体检")
check("@vectorize-io/hindsight-coding-agents" in exp, "scoped 非 dsh 插件纳入体检")
check(any(not n.startswith("dsh") and n != "dshmarket" for n in exp),
      "存在非 dsh- 前缀插件（旧版会漏）")
print("     期望集 %d 个" % len(exp))

print()
print("B. 加载态校验（模拟各种破损，全部在临时目录，不动真实 node_modules）")
# doctor 现在按 home 出结论（两套 home 都体检），所以临时环境要造出
# 一个「假的 home」：<tmp>/profiles/desktop/{package.json,node_modules}。
tmp_home = tempfile.mkdtemp(prefix="doctor_home_")
tmp_prof = os.path.join(tmp_home, "profiles", "desktop")
tmp_nm = os.path.join(tmp_prof, "node_modules")
os.makedirs(tmp_nm, exist_ok=True)

LOCAL_PLUGINS = ["ok-plugin", "@scope/scoped-ok", "no-pkg", "bad-json",
                 "no-dsh-flag", "no-entry", "wrong-name"]
with open(os.path.join(tmp_prof, "package.json"), "w", encoding="utf-8") as f:
    json.dump({"name": "tmp-profile", "dependencies": {n: "*" for n in LOCAL_PLUGINS}}, f)

real_homes, real_nm = doc._HOMES_OVERRIDE, doc._NM_OVERRIDE
try:
    doc._HOMES_OVERRIDE = [tmp_home]
    doc._NM_OVERRIDE = tmp_nm
    exp = doc._expected_plugins(tmp_home)

    def make(name, pkg=None, entry="lib/index.js", write_entry=True):
        d = os.path.join(tmp_nm, name.replace("/", os.sep))
        os.makedirs(d, exist_ok=True)
        if pkg is not None:
            with open(os.path.join(d, "package.json"), "w", encoding="utf-8") as f:
                if isinstance(pkg, str):
                    f.write(pkg)
                else:
                    json.dump(pkg, f)
        if write_entry and entry:
            p = os.path.join(d, entry.replace("/", os.sep))
            os.makedirs(os.path.dirname(p) or d, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write("// stub\n")
        return d

    make("ok-plugin", {"name": "ok-plugin", "dsh": {"kind": "plugin"}, "main": "lib/index.js"})
    make("no-pkg", pkg=None)
    make("bad-json", "{ this is not json ")
    make("no-dsh-flag", {"name": "no-dsh-flag", "main": "lib/index.js"})
    make("no-entry", {"name": "no-entry", "dsh": {}, "main": "lib/index.js"}, write_entry=False)
    make("wrong-name", {"name": "someone-else", "dsh": {}, "main": "lib/index.js"})
    make("@scope/scoped-ok", {"name": "@scope/scoped-ok", "dsh": {}, "main": "lib/index.js"})

    check(set(LOCAL_PLUGINS) <= set(exp), "临时 home 的期望集包含它自己 package.json 的依赖",
          str(exp[:5]))
    check(len(exp) >= len(LOCAL_PLUGINS) + len(doc.BIZ_PLUGINS) - 1,
          "期望集同时保留核心名单（不因单 home 而缩水）")
    check(doc._plugin_problems("ok-plugin", tmp_home) == [], "健康插件无误报")
    check(doc._plugin_problems("@scope/scoped-ok", tmp_home) == [], "scoped 插件路径解析正确")
    check(doc._plugin_problems("no-pkg", tmp_home) == ["缺少 package.json"], "无 package.json 被识别")
    check(any("无法解析" in p for p in doc._plugin_problems("bad-json", tmp_home)), "坏 JSON 被识别")
    check(any("dsh 插件标记" in p for p in doc._plugin_problems("no-dsh-flag", tmp_home)), "缺插件标记被识别")
    check(any("入口文件缺失" in p for p in doc._plugin_problems("no-entry", tmp_home)), "入口缺失被识别")
    check(any("包名不符" in p for p in doc._plugin_problems("wrong-name", tmp_home)), "包名不符被识别")
    check(doc._plugin_problems("completely-absent", tmp_home) == ["目录缺失"], "目录缺失被识别")

    r = doc.check_modules()
    check(r["ok"] is False, "存在破损插件时 check_modules 不再全绿")
    missing = [m.rsplit("@", 1)[0] for m in r["extra"]["missing"]]
    # 上面 make() 已经把这 7 个本地插件建出来了，所以「缺失」应只剩核心名单里
    # 那些没建的（BIZ_PLUGINS）；这条断言同时证明「目录在场」不会被算成缺失。
    check(sorted(missing) == sorted(set(exp) - set(LOCAL_PLUGINS)),
          "缺失清单 = 期望集 - 已建目录的插件",
          "missing=%d exp=%d" % (len(missing), len(exp)))
    # 再造一个「期望内但破损」的插件：目录在、代码坏了
    victim = "ok-plugin"
    vd = os.path.join(tmp_nm, victim)
    with open(os.path.join(vd, "package.json"), "w", encoding="utf-8") as f:
        json.dump({"name": victim, "main": "lib/index.js"}, f)   # 没有 dsh 标记
    r2 = doc.check_modules()
    broken_names = [k.rsplit("@", 1)[0] for k in r2["extra"]["broken"]]
    check(victim in broken_names, "破损插件进入 broken 清单")
    still_missing = [m.rsplit("@", 1)[0] for m in r2["extra"]["missing"]]
    check(victim not in still_missing, "目录在场就不算缺失")

    # 两套 home 各自出结论：第二个 home 缺件不应污染第一个
    r3 = doc.check_install_path()
    check(r3["ok"] is True or "缺 .modules.yaml" in r3["detail"],
          "临时 home 的 installpath 不误判成故障", r3["detail"][:80])
finally:
    doc._HOMES_OVERRIDE, doc._NM_OVERRIDE = real_homes, real_nm
    shutil.rmtree(tmp_home, ignore_errors=True)

print()
print("C. 内置产物检查真实生效")
r = doc.check_builtin()
check(r["id"] == "builtin" and "check_builtin" in [c.__name__ for c in doc.CHECKS],
      "内置 DSH 产物已纳入体检")
check(r["ok"] is True, "当前内置入口完整（dist/host/node 都在）")

print()
print("D. 端口检查不再把「6811 空闲」当异常，但会明确标注 DSH 未运行")
r = doc.check_ports()
check(r["ok"] is True, "6811 空闲不算失败（按需启停）")
check("dsh_running" in r["extra"], "输出里标明 DSH 是否在跑")

print()
print("=" * 60)
if FAIL:
    print("FAILED %d 项" % len(FAIL))
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("doctor 回归测试全部通过")
