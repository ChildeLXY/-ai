# -*- coding: utf-8 -*-
"""
大肥鱼 AI · 本地插件系统
========================

把 DSH（DeepSeek Harness）上那套"everything is a plugin"的思路，用一个轻量版本
搬到本地大肥鱼 AI 上。每个插件就是一个独立的 .py 文件，丢进 plugins/ 目录即可生效，
坏掉的插件只会自己跳过，不会拖垮主程序。

插件契约（一个 .py 文件里写这几个东西就够了）::

    PLUGIN_ID = "feiyu_mood"        # 必填，唯一 id，也是数据落盘的子目录名
    PLUGIN_NAME = "鱼的心情"         # 必填，显示在设置里的名字
    PLUGIN_VERSION = "1.0.0"        # 可选

    def setup(ctx):                 # 可选：初始化，ctx 是 PluginContext
        ...

    def apis():                     # 必填：返回要暴露给前端 JS 的函数列表
        return [mood_get, mood_judge]

    def shutdown():                 # 可选：退出前清理（停线程等）
        ...

约定：
- 插件只通过 ctx 访问宿主能力（调本地模型、读设置、落盘数据、往前端推事件），
  不直接改主程序文件——这样插件可以随便开关、随便删。
- 所有判断类调用都走本地 Ollama，不调云端 API。
"""

import os
import sys
import json
import time
import threading
import importlib.util

__all__ = ["PluginContext", "load_plugins", "call_event"]


# ---------------------------------------------------------------- 宿主上下文
class PluginContext(object):
    """插件能碰到的全部宿主能力。宿主在启动时把函数注入进来。"""

    def __init__(self, base, data_dir, cache_dir):
        self.base = base                                  # 程序目录
        self.data_dir = data_dir                          # 数据目录（D:\本地部署大肥鱼环境\data）
        self.cache_dir = cache_dir                        # 缓存目录（D:\大肥鱼缓存）
        self.plugins_dir = os.path.join(base, "plugins")

        # --- 由宿主注入 ---
        self.ollama_chat = None      # (messages, model, timeout, num_predict, think) -> str
        self.setting_get = None      # (key) -> value
        self.setting_set = None      # (key, value) -> None
        self.push_event = None       # (event, payload) -> None 往前端推事件
        self.get_model = None        # () -> str 当前默认模型名

        self._events = {}            # 事件总线：插件之间互相通知
        self._lock = threading.RLock()

    # ------------------------------------------------------------ 数据落盘
    def plugin_data(self, plugin_id, filename):
        """插件自己的数据文件，统一放在 data/plugins/<id>/ 下，互不干扰。"""
        d = os.path.join(self.data_dir, "plugins", plugin_id)
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return os.path.join(d, filename)

    def read_json(self, path, default):
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return default

    def write_json(self, path, obj):
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
            if os.path.exists(path):
                os.remove(path)
            os.rename(tmp, path)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------ 设置读写
    def setting(self, key, default=None):
        """读设置。注意宿主的 load_setting 对没存过的键返回 '' 而不是 None，
        所以这里把 None 和 '' 都当成"没设过"，免得把空串当成有效配置。"""
        try:
            if self.setting_get:
                v = self.setting_get(key)
                if v is not None and v != "":
                    return v
        except Exception:
            pass
        return default

    def save_setting(self, key, value):
        try:
            if self.setting_set:
                self.setting_set(key, value)
                return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------ 模型调用
    def chat(self, messages, model=None, timeout=60, num_predict=400, think=None):
        """调本地模型（非流式）。拿不到模型或宿主没注入时返回 None。"""
        try:
            if not self.ollama_chat:
                return None
            if not model and self.get_model:
                try:
                    model = self.get_model()
                except Exception:
                    model = None
            if not model:
                model = "qwen3:4b"
            return self.ollama_chat(messages, model, timeout, num_predict, think)
        except Exception as e:
            self.log("ctx", "chat 失败: %r" % (e,))
            return None

    # ------------------------------------------------------------ 事件总线
    def on(self, event, cb):
        with self._lock:
            self._events.setdefault(event, []).append(cb)

    def emit(self, event, data=None):
        """插件之间通知；同时尝试推给前端（宿主注入了 push_event 才行）。"""
        with self._lock:
            cbs = list(self._events.get(event, []))
        for cb in cbs:
            try:
                cb(data)
            except Exception as e:
                self.log("bus", "事件 %s 回调出错: %r" % (event, e))
        try:
            if self.push_event:
                self.push_event(event, data)
        except Exception:
            pass

    # ------------------------------------------------------------ 日志
    def log(self, plugin_id, msg):
        try:
            with open(os.path.join(self.cache_dir, "_plugins.log"),
                      "a", encoding="utf-8") as f:
                f.write("%s [%s] %s\n" % (time.strftime("%H:%M:%S"), plugin_id, msg))
        except Exception:
            pass


# ---------------------------------------------------------------- 开关配置
def _switch_file(ctx):
    return os.path.join(ctx.data_dir, "plugins.json")


def _load_switches(ctx):
    """返回 {plugin_id: True/False}。没写过的插件默认开启。"""
    try:
        p = _switch_file(ctx)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {}


# ---------------------------------------------------------------- 加载器
def load_plugins(ctx):
    """扫描 plugins/ 目录，导入所有插件，返回 (暴露给前端的函数列表, 插件清单)。

    这个函数被设计成"永不抛异常"——任何插件炸了都只是被跳过，
    主程序该干嘛干嘛。
    """
    api_funcs = []
    manifest = []
    d = ctx.plugins_dir
    if not os.path.isdir(d):
        return api_funcs, manifest

    # 让插件之间能互相 import（如 bili_pond / bili_whale 共用 _bili_common）
    if d not in sys.path:
        sys.path.insert(0, d)

    switches = _load_switches(ctx)
    names = sorted(n for n in os.listdir(d)
                   if n.endswith(".py") and not n.startswith("_"))

    for name in names:
        mod_name = "dfy_plugin_" + name[:-3]
        path = os.path.join(d, name)
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)

            pid = getattr(mod, "PLUGIN_ID", name[:-3])
            pname = getattr(mod, "PLUGIN_NAME", pid)

            # 开关：显式 false 才跳过
            if switches.get(pid, True) is False:
                manifest.append({"id": pid, "name": pname,
                                 "icon": getattr(mod, "PLUGIN_ICON", "🧩"),
                                 "enabled": False, "ok": True, "note": "已关闭"})
                continue

            # setup
            if hasattr(mod, "setup"):
                mod.setup(ctx)

            # 收集要暴露给前端的函数
            fns = []
            if hasattr(mod, "apis"):
                try:
                    fns = list(mod.apis()) or []
                except Exception as e:
                    ctx.log(pid, "apis() 出错: %r" % (e,))

            got = []
            for f in fns:
                if callable(f):
                    api_funcs.append(f)
                    got.append(getattr(f, "__name__", "?"))

            manifest.append({
                "id": pid,
                "name": pname,
                "icon": getattr(mod, "PLUGIN_ICON", "🧩"),
                "version": getattr(mod, "PLUGIN_VERSION", "0.0.0"),
                "enabled": True,
                "ok": True,
                "apis": got,
            })
            ctx.log(pid, "已加载，暴露接口 %d 个" % len(got))

        except Exception as e:
            # 插件加载失败：记下来，但绝不拖垮主程序
            import traceback
            try:
                ctx.log(name, "加载失败: %r\n%s" % (e, traceback.format_exc()))
            except Exception:
                pass
            manifest.append({"id": name[:-3], "name": name[:-3], "enabled": True,
                             "ok": False, "note": "加载失败：%s" % (e,)})
            continue

    return api_funcs, manifest


def shutdown_plugins():
    """退出前给所有插件一个收尾的机会（停后台线程等）。"""
    for name, mod in list(sys.modules.items()):
        if not name.startswith("dfy_plugin_"):
            continue
        fn = getattr(mod, "shutdown", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass


def call_event(ctx, event, data=None):
    ctx.emit(event, data)
