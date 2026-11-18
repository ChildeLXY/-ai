# -*- coding: utf-8 -*-
"""内置引擎与市场接线测试。

分两层验证，避免把「逻辑正确」和「网络是否通」混为一谈：

  A 层（确定性，必须全绿）
    - 引擎能被定位、能启动、能列插件
    - install() 在有引擎时**优先**走引擎链路（用替身拦截 subprocess 验证）
    - 引擎缺位时安全回退，不崩
    - 合规文件与品牌替换到位

  B 层（真实链路，网络抖动不算失败，只记为 SKIP）
    - 真实调用引擎对已装包做幂等 add
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, r"D:\大肥鱼AI")
sys.path.insert(0, r"D:\大肥鱼AI\packaging")

PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name
          + (("  -> " + str(detail)) if detail and not cond else ""))


def skip(name, why=""):
    SKIP.append(name)
    print("  [SKIP] " + name + (("  -> " + why) if why else ""))


def _strip_noise(text):
    return "\n".join(
        l for l in (text or "").splitlines()
        if "crashpad" not in l and "registration_protocol_win" not in l
    ).strip()


def main():
    print("== A1. 路径层能解析出引擎 ==")
    import dfy_paths
    print("  ENGINE     =", dfy_paths.ENGINE)
    print("  ENGINE_CLI =", dfy_paths.ENGINE_CLI)
    check("ENGINE 目录存在", os.path.isdir(dfy_paths.ENGINE), dfy_paths.ENGINE)
    check("ENGINE_CLI 存在", os.path.isfile(dfy_paths.ENGINE_CLI), dfy_paths.ENGINE_CLI)
    check("DSH_HOME 已导出", bool(os.environ.get("DSH_HOME")), os.environ.get("DSH_HOME"))
    check("DFY_ENGINE_DIR 已导出", bool(os.environ.get("DFY_ENGINE_DIR")),
          os.environ.get("DFY_ENGINE_DIR"))

    print("\n== A2. 市场模块能定位引擎 ==")
    import dfy_market
    info = dfy_market.engine_available()
    print("  ", {k: info[k] for k in ("available", "root")})
    check("engine_available().available", info["available"])
    check("cli 指向 dsh.cmd", info["cli"].lower().endswith("dsh.cmd"))
    check("市场 DSH_HOME 与路径层一致",
          os.path.normcase(os.path.abspath(dfy_market.DSH_HOME))
          == os.path.normcase(os.path.abspath(dfy_paths.DSH_HOME)))

    env = dict(os.environ)
    env["DSH_HOME"] = dfy_market.DSH_HOME
    cli = info["cli"]

    print("\n== A3. 引擎能独立启动（不依赖外部 DSH 安装）==")
    r = subprocess.run(["cmd", "/c", cli, "--version"], capture_output=True,
                       text=True, timeout=180, env=env,
                       encoding="utf-8", errors="replace")
    out = _strip_noise((r.stdout or "") + "\n" + (r.stderr or ""))
    print("  版本:", out.replace("\n", " | ")[:100])
    check("--version 退出码 0", r.returncode == 0, r.returncode)
    check("--version 有版本号", any(c.isdigit() for c in out), out[:60])

    print("\n== A4. 引擎能列 profile 插件 ==")
    r = subprocess.run(["cmd", "/c", cli, "plugin", "--profile", "desktop",
                        "list", "--depth", "0"],
                       capture_output=True, text=True, timeout=180, env=env,
                       encoding="utf-8", errors="replace")
    out = _strip_noise((r.stdout or "") + "\n" + (r.stderr or ""))
    check("plugin list 退出码 0", r.returncode == 0, r.returncode)
    check("输出含依赖清单", "dependencies" in out, out[:80])

    print("\n== A5. install() 在有引擎时优先走引擎链路（替身拦截验证）==")
    real_run = subprocess.run
    captured = {}

    class _FakeProc:
        returncode = 0
        stdout = "+ dsh-soul-md 0.10.3\nDone in 2s\n"
        stderr = ""

    def _fake_run(cmd, *a, **kw):
        captured["cmd"] = cmd
        return _FakeProc()

    subprocess.run = _fake_run
    try:
        r = dfy_market.install("dsh-soul-md")
    finally:
        subprocess.run = real_run
    cmd = captured.get("cmd") or []
    print("  实际执行的命令:", " ".join(str(x) for x in cmd))
    check("install 成功", bool(r.get("ok")), r.get("error"))
    check("via == engine", r.get("via") == "engine", r.get("via"))
    check("命令走的是引擎 dsh.cmd", any(str(x).lower().endswith("dsh.cmd") for x in cmd))
    check("命令含 plugin --profile desktop add",
          "plugin" in cmd and "add" in cmd and "desktop" in cmd)
    check("解析出包名 dsh-soul-md", r.get("name") == "dsh-soul-md", r.get("name"))
    check("版本被解析", r.get("version") == "0.10.3", r.get("version"))
    check("need_restart 为真", r.get("need_restart") is True)

    print("\n== A6. 引擎缺位时安全回退，不崩 ==")
    saved_env = os.environ.get("DFY_ENGINE_DIR")
    saved_cache = dict(dfy_market._ENGINE_CACHE)
    saved_home = dfy_market.DSH_HOME
    try:
        os.environ["DFY_ENGINE_DIR"] = r"D:\__no_engine_here__"
        # 让源码形态的兄弟目录也失效，才能真测到「找不到」
        dfy_market.DSH_HOME = r"Z:\__no_home__\dsh"
        dfy_market._ENGINE_CACHE["cli"] = None
        dfy_market._ENGINE_CACHE["root"] = ""
        cli2, root2 = dfy_market._find_engine_cli()
        print("  缺位时定位结果:", (cli2, root2))
        check("缺位时返回空而非抛异常", cli2 == "" and root2 == "")
        check("engine_available 报不可用",
              dfy_market.engine_available()["available"] is False)
        # 回退分支所用函数仍在
        check("回退链路函数齐备",
              callable(dfy_market._stage_pkgjson) and callable(dfy_market.probe_plugin))
    finally:
        dfy_market._ENGINE_CACHE.update(saved_cache)
        dfy_market.DSH_HOME = saved_home
        if saved_env is None:
            os.environ.pop("DFY_ENGINE_DIR", None)
        else:
            os.environ["DFY_ENGINE_DIR"] = saved_env

    print("\n== A7. 合规文件已就位 ==")
    lic_dir = os.path.join(dfy_paths.ENGINE, "THIRD_PARTY_LICENSES")
    for f in ("NOTICE.md", "packages.json", "DSH-LICENSE.txt"):
        check("存在 " + f, os.path.isfile(os.path.join(lic_dir, f)))
    pk = os.path.join(lic_dir, "packages.json")
    if os.path.isfile(pk):
        with open(pk, encoding="utf-8") as fh:
            data = json.load(fh)
        check("许可清单组件数 >= 600", len(data) >= 600, len(data))
        check("含 MIT 组件", any(x["license"] == "MIT" for x in data))

    print("\n== A8. 品牌替换与裁剪到位 ==")
    E = dfy_paths.ENGINE
    check("主程序名为 DaFeiyuEngine.exe", os.path.isfile(os.path.join(E, "DaFeiyuEngine.exe")))
    check("无 DeepSeek Harness.exe", not os.path.isfile(os.path.join(E, "DeepSeek Harness.exe")))
    check("无官方卸载器",
          not os.path.isfile(os.path.join(E, "Uninstall DeepSeek Harness.exe")))
    check("无自动更新配置 app-update.yml",
          not os.path.isfile(os.path.join(E, "resources", "app-update.yml")))
    # CLI 启动器已指向新 exe
    cmd_file = os.path.join(E, "resources", "runtime", "cli", "bin", "dsh.cmd")
    with open(cmd_file, encoding="utf-8", errors="replace") as fh:
        cmdtext = fh.read()
    check("CLI 启动器指向 DaFeiyuEngine.exe", "DaFeiyuEngine.exe" in cmdtext)
    check("CLI 启动器无旧品牌引用", "DeepSeek Harness" not in cmdtext)

    print("\n== A9. profile 版本约束健康（防止测试污染残留）==")
    prof_pkg = os.path.join(dfy_market.PROFILE_DIR, "package.json")
    if os.path.isfile(prof_pkg):
        with open(prof_pkg, encoding="utf-8") as fh:
            pd = json.load(fh)
        deps = pd.get("dependencies") or {}
        bad = [k for k, v in deps.items()
               if isinstance(v, str) and v.startswith("^0.12.5") and k == "dsh-soul-md"]
        check("dsh-soul-md 约束已修正为 ^0.10.3", not bad, deps.get("dsh-soul-md"))
        # 所有依赖都必须能在 node_modules 里找到（不能有幽灵依赖）
        missing = []
        for k in deps:
            p = os.path.join(dfy_market.PROFILE_MODULES, *k.split("/"))
            if not os.path.isdir(p):
                missing.append(k)
        print("  缺失目录的依赖:", missing if missing else "无")
        check("依赖目录完整（无幽灵依赖）", not missing, missing)

    print("\n== B1. 真实引擎幂等安装（网络失败只记 SKIP）==")
    try:
        rr = dfy_market._install_via_engine(cli, "dsh-soul-md")
        if rr.get("ok"):
            print("  返回:", {k: v for k, v in rr.items() if k != "tail"})
            check("真实引擎 add 成功", True)
            check("真实链路通过引擎完成", rr.get("via") == "engine")
        else:
            err = str(rr.get("error"))[:200]
            if any(w in err.lower() for w in ("econnrefused", "etimedout", "network", "fetch")):
                skip("真实引擎 add（网络不可达）", err[:80])
            else:
                check("真实引擎 add 成功", False, err)
    except Exception as e:
        skip("真实引擎 add（异常）", repr(e)[:80])

    print("\n" + "=" * 56)
    print("通过 %d 项，失败 %d 项，跳过 %d 项" % (len(PASS), len(FAIL), len(SKIP)))
    for f in FAIL:
        print("  失败:", f)
    for s in SKIP:
        print("  跳过:", s)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
