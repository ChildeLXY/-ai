# -*- coding: utf-8 -*-
"""
P1 升级插件加载器单元测试
=========================
直接运行：python tools/test_plugin_loader.py
临时目录动态生成新旧格式插件，验证向后兼容、拓扑序、缺依赖拦截、
坏插件隔离、配置校验、热插拔、三级隔离执行器，并对真实 plugins 目录做兼容探测。
"""

import os
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from core import PluginLoader, Isolation, PluginLoadError, ConfigError

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  [FAIL]", label)


class MockCtx(object):
    def __init__(self):
        self.logs = []
    def log(self, id, msg):
        self.logs.append((id, msg))


def write_plugin(d, name, content):
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        f.write(content)


# 各类插件源码
OLD_PLUGIN = """
PLUGIN_ID='oldplug'
PLUGIN_NAME='旧插件'
def setup(ctx):
    ctx.log('oldplug','ok')
def apis():
    return []
"""

BASE_PLUGIN = """
PLUGIN_ID='base'
PLUGIN_NAME='基础'
ISOLATION='safe'
CONFIG_SCHEMA={'n':{'type':'int','default':1,'min':0}}
def setup(ctx):
    pass
def apis():
    return []
"""

TOP_PLUGIN = """
PLUGIN_ID='top'
PLUGIN_NAME='上层'
DEPENDS=['base']
ISOLATION='thread'
def setup(ctx):
    pass
def apis():
    return []
"""

MISSING_PLUGIN = """
PLUGIN_ID='needmissing'
DEPENDS=['ghost']
def setup(ctx):
    pass
def apis():
    return []
"""

BAD_PLUGIN = """
PLUGIN_ID='badplug'
def setup(ctx):
    raise RuntimeError('boom')
def apis():
    return []
"""


def build_dir():
    d = tempfile.mkdtemp(prefix="p1_")
    write_plugin(d, "oldplug.py", OLD_PLUGIN)
    write_plugin(d, "base.py", BASE_PLUGIN)
    write_plugin(d, "top.py", TOP_PLUGIN)
    write_plugin(d, "badplug.py", BAD_PLUGIN)
    return d


def test_backward_compat_and_load():
    d = build_dir()
    loader = PluginLoader(d, MockCtx())
    status = loader.load_all()
    by = {s["id"]: s for s in status}
    # 旧插件零改动加载成功
    check(by["oldplug"]["ok"] is True, "旧格式插件零改动加载")
    check(by["base"]["ok"] is True and by["base"]["isolation"] == "safe",
          "新契约 safe 隔离识别")
    check(by["top"]["ok"] is True and by["top"]["depends"] == ["base"],
          "依赖声明识别")
    # 坏插件隔离，不影响其他
    check(by["badplug"]["ok"] is False and "boom" in by["badplug"]["error"],
          "坏插件隔离不拖垮")
    # 拓扑序：base 在 top 前
    ids = [s["id"] for s in status]
    check(ids.index("base") < ids.index("top"), "拓扑序 base 先于 top")


def test_missing_dependency():
    d = tempfile.mkdtemp(prefix="p1miss_")
    write_plugin(d, "needmissing.py", MISSING_PLUGIN)
    loader = PluginLoader(d, MockCtx())
    try:
        loader.load_all()
        check(False, "缺依赖应拦截")
    except PluginLoadError:
        check(True, "缺依赖启动阶段拦截")


def test_config_validation():
    d = tempfile.mkdtemp(prefix="p1cfg_")
    write_plugin(d, "base.py", BASE_PLUGIN)
    loader = PluginLoader(d, MockCtx())
    loader.load_all({"base": {"n": 5}})
    check(loader.get("base").config["n"] == 5, "插件配置校验并写入")
    # 非法配置：load_all 隔离该插件（标记失败），不整体抛出
    loader2 = PluginLoader(d, MockCtx())
    st = loader2.load_all({"base": {"n": -1}})
    bad = {s["id"]: s for s in st}["base"]
    check(bad["ok"] is False and "n" in bad["error"], "配置超范围被隔离并报错")


def test_hotplug():
    d = build_dir()
    loader = PluginLoader(d, MockCtx())
    loader.load_all()
    # 热安装
    extra = os.path.join(d, "extra.py")
    write_plugin(d, "extra.py",
                 "PLUGIN_ID='extra'\ndef setup(ctx): pass\ndef apis(): return []")
    snap = loader.install_one(extra)
    check(snap["ok"] is True and loader.get("extra") is not None, "热安装单个插件")
    # 热卸载
    check(loader.remove_one("extra") is True, "热卸载")
    check(loader.get("extra") is None, "卸载后移除")


def test_isolation():
    # safe
    check(Isolation.safe_call(lambda x: x + 1, args=(1,)) == 2, "safe 直接调用")
    # thread 成功
    check(Isolation.thread_call(lambda x: x * 2, args=(3,), timeout=5) == 6,
          "thread 线程调用")
    # thread 超时
    try:
        Isolation.thread_call(lambda: time.sleep(5), timeout=1)
        check(False, "thread 应超时")
    except TimeoutError:
        check(True, "thread 超时拦截")
    # process 隔离（真实命令）
    py = r"D:\本地部署大肥鱼环境\venv312\Scripts\python.exe"
    r = Isolation.process_call([py, "-c", "print('hi')"], timeout=10)
    check(r["ok"] and "hi" in r["stdout"], "process 子进程隔离执行")


def test_real_plugins_compat():
    """对真实 plugins 目录做兼容探测，旧插件应全部可读取。"""
    real = os.path.join(BASE, "plugins")
    loader = PluginLoader(real, MockCtx())
    found = loader.discover()
    real_plugins = [p for p in found.values() if p.module is not None]
    check(len(real_plugins) >= 10, "真实插件可被新加载器读取（%d 个）" % len(real_plugins))


if __name__ == "__main__":
    test_backward_compat_and_load()
    test_missing_dependency()
    test_config_validation()
    test_hotplug()
    test_isolation()
    test_real_plugins_compat()
    print("\n================================")
    print("P1 插件加载器测试：通过 %d，失败 %d" % (_passed, _failed))
    print("================================")
    sys.exit(1 if _failed else 0)
