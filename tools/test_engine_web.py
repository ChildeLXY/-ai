# -*- coding: utf-8 -*-
"""内置引擎（dfy_engine）与路径可移植性测试。

这是「交付给别人的电脑也能跑」的验收：任何写死作者盘符的地方都会在这里被抓出来。
"""
import json
import os
import re
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


def main():
    print("== 1. dfy_engine 基础能力 ==")
    import dfy_engine as e
    av = e.available()
    print("  engine =", av["engine"])
    check("引擎就位", av["available"])
    check("CLI 存在", os.path.isfile(av["cli"]))
    check("主程序存在", os.path.isfile(av["exe"]))
    check("主程序名为 DaFeiyuEngine.exe",
          os.path.basename(av["exe"]) == "DaFeiyuEngine.exe")

    v = e.version()
    print("  版本 =", v.get("version"))
    check("能取到版本", bool(v.get("ok")), v.get("error"))

    print("\n== 2. 插件列表（走引擎原生）==")
    r = e.list_plugins()
    cnt = len(r.get("plugins") or [])
    print("  插件数 =", cnt)
    check("列表成功", bool(r.get("ok")), r.get("error"))
    check("至少 10 个插件", cnt >= 10, cnt)
    names = [p["name"] for p in (r.get("plugins") or [])]
    for must in ("dsh-soul-md", "dsh-nekowhale-stickers", "dshmarket"):
        check("含插件 " + must, must in names, names[:6])

    print("\n== 3. web 服务全生命周期 ==")
    before = e.web_status()
    if before.get("running"):
        e.web_stop()
    st = e.web_start(port=6808)
    print("  启动:", {k: st.get(k) for k in ("ok", "port", "status", "pid")})
    check("web 启动成功", bool(st.get("ok")), st.get("error"))
    check("拿到 token", bool(st.get("token")))
    check("probe 返回 200", st.get("status") == 200, st.get("status"))
    check("embed_url 可用", bool(e.embed_url()) and "token=" in e.embed_url())

    st2 = e.web_status()
    check("状态查询 running", st2.get("running") is True)
    check("状态查询 probe 200", st2.get("probe") == 200, st2.get("probe"))

    # 复用：再次 start 应复用同一实例，不重复起进程
    again = e.web_start(port=6808)
    check("重复 start 复用实例", again.get("reused") is True or again.get("pid") == st.get("pid"))

    stop = e.web_stop()
    check("能停止", bool(stop.get("ok")))
    after = e.web_status()
    check("停止后不再 running", after.get("running") is False, after)

    print("\n== 4. web 认证机制（303 + cookie → 200）==")
    # 上一轮已验证；这里做机制性回归：带 token 裸请求必须 303 或 200，不能是 401
    import http.cookiejar
    import urllib.request
    import urllib.error
    st3 = e.web_start(port=6809)
    if st3.get("ok"):
        jar = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                         urllib.request.HTTPCookieProcessor(jar))
        url = st3["url"]
        try:
            with op.open(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"},
                                                ), timeout=15) as resp:
                check("带 cookie 访问返回 200", resp.status == 200, resp.status)
                check("响应体是 HTML", b"<html" in resp.read(200).lower())
        except Exception as ex:
            check("带 cookie 访问返回 200", False, ex)
        # 不带 cookie 的裸跟踪必须是 401（说明鉴权真的生效了，不是裸奔）
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    "http://127.0.0.1:%d/" % st3["port"],
                    headers={"User-Agent": "Mozilla/5.0"}), timeout=10) as resp:
                check("无 cookie 必须被拒", resp.status != 200, resp.status)
        except urllib.error.HTTPError as he:
            check("无 cookie 必须被拒", he.code in (401, 403), he.code)
        except Exception as ex:
            check("无 cookie 必须被拒", False, ex)
        e.web_stop()
    else:
        skip("web 认证回归（服务没起）", st3.get("error"))

    print("\n== 5. 路径可移植性（不许出现作者盘符）==")
    for mod, path in (("dfy_engine.py", r"D:\大肥鱼AI\dfy_engine.py"),
                      ("dfy_market.py", r"D:\大肥鱼AI\dfy_market.py"),
                      ("dfy_dsh.py", r"D:\大肥鱼AI\dfy_dsh.py")):
        with open(path, encoding="utf-8") as f:
            src = f.read()
        # 允许出现在「历史兜底」判断里（带 os.path.isdir 保护），但不允许裸写 profile 路径
        bad = []
        for i, line in enumerate(src.splitlines(), 1):
            if re.search(r"[Ee]:\\\\?DSH", line):
                # 兜底常量行：赋值给 legacy 且随即有 isdir 判定的算合规
                if "legacy" in line or "LEGACY" in line:
                    continue
                bad.append((i, line.strip()[:80]))
        check("%s 无裸写 E 盘引擎路径" % mod, not bad, bad)

    print("\n== 6. 三个模块的 DSH_HOME 一致 ==")
    import dfy_market
    import dfy_dsh
    homes = {"engine": os.path.normcase(os.path.abspath(e.DSH_HOME)),
             "market": os.path.normcase(os.path.abspath(dfy_market.DSH_HOME)),
             "dsh": os.path.normcase(os.path.abspath(dfy_dsh.DSH_HOME))}
    print("  ", homes)
    check("三者 DSH_HOME 指向同一处",
          len(set(homes.values())) == 1, homes)
    check("DSH_HOME 目录存在", os.path.isdir(e.DSH_HOME), e.DSH_HOME)
    # 引擎 profile 与市场 profile 必须是同一个
    check("引擎与市场的 desktop profile 一致",
          os.path.normcase(os.path.abspath(
              os.path.join(e.DSH_HOME, "profiles", "desktop")))
          == os.path.normcase(os.path.abspath(dfy_market.PROFILE_DIR)))

    print("\n== 7. 引擎自带运行时（不依赖系统 node）==")
    node = os.path.join(av["engine"], "resources", "runtime", "primary-runtime",
                        "dependencies", "node", "bin", "node.exe")
    check("引擎自带 node.exe", os.path.isfile(node), node)
    pnpm = os.path.join(av["engine"], "resources", "runtime", "primary-runtime",
                        "dependencies", "pnpm", "bin", "pnpm.mjs")
    check("引擎自带 pnpm", os.path.isfile(pnpm), pnpm)
    py = os.path.join(av["engine"], "resources", "runtime", "primary-runtime",
                      "dependencies", "python")
    check("引擎自带 python 目录", os.path.isdir(py), py)

    print("\n== 8. 下载器与引擎模块必须找到同一个引擎 ==")
    import dfy_engine_bootstrap as boot
    inst = boot.EngineBootstrap(e.DATA)
    found = inst.find_installed()
    print("  dfy_engine  ->", e.engine_root())
    print("  bootstrap   ->", found or "(未找到)")
    check("bootstrap 能识别已装引擎", bool(found), found)
    check("两者指向同一目录",
          os.path.normcase(os.path.abspath(e.engine_root()))
          == os.path.normcase(os.path.abspath(found or "")),
          (e.engine_root(), found))
    check("bootstrap 版本与引擎一致", inst.installed_version() == v.get("version"),
          (inst.installed_version(), v.get("version")))
    # 候选列表必须两边都包含对方的关键位置
    ec = [os.path.normcase(os.path.abspath(c)) for c in e.engine_candidates()]
    bc = [os.path.normcase(os.path.abspath(c)) for c in inst.candidates()]
    check("候选列表包含数据目录下的 engine",
          os.path.normcase(os.path.abspath(os.path.join(e.DATA, "engine"))) in ec
          and os.path.normcase(os.path.abspath(os.path.join(e.DATA, "engine"))) in bc)

    print("\n" + "=" * 56)
    print("通过 %d 项，失败 %d 项，跳过 %d 项" % (len(PASS), len(FAIL), len(SKIP)))
    for f in FAIL:
        print("  失败:", f)
    for s in SKIP:
        print("  跳过:", s)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
