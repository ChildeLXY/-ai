# -*- coding: utf-8 -*-
"""
大肥鱼 AI · L0/L1 · 升级插件加载器（架构 P1）
=============================================
在现有 ``plugins/__init__.py`` 契约上增量升级，向后兼容，旧插件零改动可加载。

新增契约（都是可选，缺省走默认）：
    DEPENDS        = ["model"]            # 依赖的服务/插件，决定加载顺序
    CONFIG_SCHEMA  = { ... }              # 声明式配置，用 core.ConfigSchema 校验
    ISOLATION      = "thread"             # safe / thread / process，默认 thread

能力：
- 拓扑序加载，缺依赖在启动阶段明确拦截（不等到运行时）
- 坏插件隔离，不拖垮主程序；单插件热插拔 install/remove
- 三级隔离执行器：safe 直接调用 / thread 守护线程+超时 / process 子进程
"""

import os
import sys
import json
import threading
import importlib.util

from .config import ConfigSchema, ConfigError

__all__ = ["PluginLoader", "Isolation", "PluginLoadError", "ISOLATION_LEVELS"]

ISOLATION_LEVELS = ("safe", "thread", "process")
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class PluginLoadError(Exception):
    """插件加载/依赖/配置错误。"""


class Isolation(object):
    """三级隔离执行器。"""

    @staticmethod
    def safe_call(fn, args=(), kwargs=None):
        return fn(*args, **(kwargs or {}))

    @staticmethod
    def thread_call(fn, args=(), kwargs=None, timeout=60):
        box = {}

        def worker():
            try:
                box["value"] = fn(*args, **(kwargs or {}))
            except Exception as e:
                box["error"] = e

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            raise TimeoutError("线程隔离调用超时（%ss）" % timeout)
        if "error" in box:
            raise box["error"]
        return box.get("value")

    @staticmethod
    def process_call(command, cwd=None, timeout=120):
        """进程隔离：用于 Shell/外部命令/不可信脚本，子进程崩溃不影响主程序。"""
        import subprocess
        try:
            r = subprocess.run(command, cwd=cwd, capture_output=True,
                               text=True, timeout=timeout,
                               creationflags=_NO_WINDOW)
            return {"ok": r.returncode == 0, "returncode": r.returncode,
                    "stdout": r.stdout, "stderr": r.stderr}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "进程超时（%ss）" % timeout}


class _Plugin(object):
    """单个插件的运行时记录。"""

    def __init__(self, id):
        self.id = id
        self.name = id
        self.icon = "🧩"
        self.version = "0.0.0"
        self.depends = []
        self.isolation = "thread"
        self.config_schema = None
        self.module = None
        self.config = {}
        self.enabled = True
        self.ok = False
        self.error = ""
        self.apis = []

    def snapshot(self):
        return {"id": self.id, "name": self.name, "icon": self.icon,
                "version": self.version, "depends": self.depends,
                "isolation": self.isolation, "enabled": self.enabled,
                "ok": self.ok, "error": self.error,
                "config_schema": self.config_schema.form_spec()
                if self.config_schema else None, "apis": self.apis}


class PluginLoader(object):
    """升级插件加载器。"""

    def __init__(self, plugins_dir, ctx, switches=None):
        self.plugins_dir = plugins_dir
        self.ctx = ctx
        self._plugins = {}            # id -> _Plugin
        self._switches = switches or {}
        # 让插件之间能互相 import 下划线开头的共享模块（如 _bili_common）
        if plugins_dir and plugins_dir not in sys.path:
            sys.path.insert(0, plugins_dir)

    # ------------------------------------------------------------ 读取
    def _plugin_files(self):
        if not os.path.isdir(self.plugins_dir):
            return []
        return sorted(os.path.join(self.plugins_dir, n)
                      for n in os.listdir(self.plugins_dir)
                      if n.endswith(".py") and not n.startswith("_"))

    def _import_module(self, path):
        mod_name = "dfy_p1_" + os.path.basename(path)[:-3]
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            raise PluginLoadError("无法解析模块 %s" % path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod

    def _read_manifest(self, mod):
        pid = getattr(mod, "PLUGIN_ID", None)
        if not pid:
            raise PluginLoadError("插件缺少 PLUGIN_ID")
        p = _Plugin(pid)
        p.name = getattr(mod, "PLUGIN_NAME", pid)
        p.icon = getattr(mod, "PLUGIN_ICON", "🧩")
        p.version = getattr(mod, "PLUGIN_VERSION", "0.0.0")
        p.depends = list(getattr(mod, "DEPENDS", []) or [])
        p.isolation = getattr(mod, "ISOLATION", "thread")
        if p.isolation not in ISOLATION_LEVELS:
            raise PluginLoadError("插件 %s 的 ISOLATION 非法: %r" % (pid, p.isolation))
        raw_schema = getattr(mod, "CONFIG_SCHEMA", None)
        if raw_schema:
            p.config_schema = ConfigSchema(raw_schema)
        p.module = mod
        return p

    # ------------------------------------------------------------ 拓扑
    def _topo_sort(self, plugins):
        order = []
        state = {}

        def visit(p, stack):
            st = state.get(p.id)
            if st == 1:
                return
            if st == 0:
                raise PluginLoadError("循环依赖: %s" % " -> ".join(stack + [p.id]))
            for dep in p.depends:
                if dep not in plugins:
                    raise PluginLoadError(
                        "插件 %s 依赖未安装的 %r" % (p.id, dep))
                state[p.id] = 0
                visit(plugins[dep], stack + [p.id])
            state[p.id] = 1
            order.append(p)

        for p in plugins.values():
            visit(p, [])
        return order

    # ------------------------------------------------------------ 加载
    def discover(self):
        """扫描并读取全部 manifest（不执行 setup），返回 id 列表。"""
        found = {}
        for path in self._plugin_files():
            try:
                mod = self._import_module(path)
                p = self._read_manifest(mod)
                found[p.id] = p
            except Exception as e:
                # 坏插件隔离：用文件名占位，标记失败
                bad = _Plugin(os.path.basename(path)[:-3])
                bad.ok = False
                bad.error = "读取失败: %r" % e
                found[bad.id] = bad
        return found

    def load_all(self, configs=None):
        """拓扑序加载全部插件并执行 setup，返回快照列表。"""
        configs = configs or {}
        found = self.discover()
        self._plugins = found
        order = self._topo_sort({k: v for k, v in found.items() if v.module})

        for p in order:
            if self._switches.get(p.id, True) is False:
                p.enabled = False
                p.ok = True
                continue
            try:
                # 配置校验
                if p.config_schema:
                    p.config = p.config_schema.validate(configs.get(p.id))
                # setup（按隔离级别执行，默认 thread）
                if hasattr(p.module, "setup"):
                    if p.isolation == "safe":
                        p.module.setup(self.ctx)
                    else:
                        Isolation.thread_call(
                            p.module.setup, args=(self.ctx,), timeout=30)
                fns = list(p.module.apis()) if hasattr(p.module, "apis") else []
                p.apis = [getattr(f, "__name__", "?") for f in fns if callable(f)]
                p.ok = True
            except Exception as e:
                p.ok = False
                p.error = "setup 失败: %r" % e
        return self.status()

    # ------------------------------------------------------------ 热插拔
    def install_one(self, path, cfg=None):
        """热加载单个插件（不重启主进程）。"""
        try:
            mod = self._import_module(path)
            p = self._read_manifest(mod)
            for dep in p.depends:
                if dep not in self._plugins:
                    raise PluginLoadError("缺依赖 %r，先安装其依赖插件" % dep)
            if p.config_schema:
                p.config = p.config_schema.validate(cfg)
            if hasattr(mod, "setup"):
                mod.setup(self.ctx)
            p.apis = [getattr(f, "__name__", "?")
                      for f in (mod.apis() if hasattr(mod, "apis") else []) if callable(f)]
            p.ok = True
            self._plugins[p.id] = p
            return p.snapshot()
        except Exception as e:
            raise PluginLoadError("安装失败: %r" % e)

    def remove_one(self, id):
        """热卸载：先 shutdown 再移除。"""
        p = self._plugins.pop(id, None)
        if not p:
            return False
        fn = getattr(p.module, "shutdown", None) if p.module else None
        if callable(fn):
            try:
                fn()
            except Exception:
                pass
        return True

    def shutdown_all(self):
        for p in self._plugins.values():
            fn = getattr(p.module, "shutdown", None) if p.module else None
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass

    # ------------------------------------------------------------ 状态
    def get(self, id):
        return self._plugins.get(id)

    def status(self):
        return [p.snapshot() for p in self._plugins.values()]
