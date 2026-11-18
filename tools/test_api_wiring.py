# -*- coding: utf-8 -*-
"""前后端接线一致性检查。

前端通过 pywebview.api.<name>() 调后端，后端靠 window.expose(...) 白名单暴露。
任何一边写错名字，界面就是「点了没反应」而控制台未必报错——这类问题最难查。
这里把两边抓出来做差集，缺一边就报错。
"""
import os
import re
import sys

PY = r"D:\大肥鱼AI\大肥鱼AI.py"
HTML = r"D:\大肥鱼AI\大肥鱼AI.html"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name
          + (("  -> " + str(detail)) if detail and not cond else ""))


def main():
    py = open(PY, encoding="utf-8").read()
    html = open(HTML, encoding="utf-8").read()

    # 1) 后端暴露的函数名
    m = re.search(r"window\.expose\((.*?)\)\s*\n", py, re.S)
    if not m:
        print("找不到 window.expose 调用块")
        return 1
    block = m.group(1)
    block = re.sub(r"#.*", "", block)
    exposed = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", block))
    # PLUGIN_APIS / _safe_api 这类不是函数名，过滤掉
    exposed -= {"_safe_api", "_f", "for", "in", "PLUGIN_APIS", "os", "if", "else"}

    # 插件 API 是运行期由 plugins/*.py 动态加进 PLUGIN_APIS 的，静态扫不到，
    # 这里把插件目录里的 def 都并进来，否则会误报「前端调了后端没有」。
    plugin_dir = r"D:\大肥鱼AI\plugins"
    plugin_defs = set()
    if os.path.isdir(plugin_dir):
        for fn in os.listdir(plugin_dir):
            if not fn.endswith(".py"):
                continue
            try:
                src = open(os.path.join(plugin_dir, fn), encoding="utf-8").read()
            except Exception:
                continue
            plugin_defs |= set(re.findall(r"^def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", src, re.M))
    exposed |= plugin_defs
    print("后端暴露函数数:", len(exposed),
          "（其中插件提供 %d）" % len(plugin_defs))

    # 2) 前端调用名（pywebview.api.x / api.x）
    called = set(re.findall(r"pywebview\.api\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", html))
    called |= set(re.findall(r"[^.\w]api\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", html))
    print("前端调用名数:", len(called))

    # 3) 前端调了但后端没暴露
    missing = sorted(n for n in called if n not in exposed)
    check("前端调用的接口后端都有", not missing, missing)

    # 4) 关心的关键接口必须两边都在
    for name in ("engine_status", "engine_install", "market_install", "plugin_list"):
        check("后端暴露 %s" % name, name in exposed)
        # plugin_list 前端通过变量 api 调，可能是 api.plugin_list 或 pywebview
        if name.startswith("engine_"):
            check("前端调用 %s" % name, name in called)

    # 5) 前端新增的引擎条相关函数确实定义在 HTML 里
    for fn in ("function enginePoll", "function engineRetry", "function engineHideBar",
               "function _engApplyStatus", "function _engRefreshPlugins"):
        check("定义了 " + fn.replace("function ", ""), fn in html)

    # 6) HTML 里引擎条的 DOM 节点齐全
    for el in ('id="engBar"', 'id="engTxt"', 'id="engFill"', 'id="engPct"',
               'id="engRetry"', 'id="engHide"', 'id="engDot"'):
        check("存在节点 " + el, el in html)

    print("\n" + "=" * 50)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  失败:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
