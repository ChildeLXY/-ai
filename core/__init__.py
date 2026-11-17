# -*- coding: utf-8 -*-
"""
大肥鱼 AI · L0 内核
===================
全能高度开放 Agent 的最底层（架构方案 P0），纯标准库、无外部依赖。

组合三大件：
- :class:`Container` 服务注册表 + 依赖注入 + 生命周期
- :class:`EventBus` 同步/异步事件总线
- :class:`ConfigSchema` 声明式配置校验

典型用法::

    from core import Kernel
    k = Kernel()
    k.register("model", make_model)
    k.register("agent", make_agent, depends=["model"])
    k.start()
    k.resolve("agent")
    k.stop()
"""

from .container import Container, ServiceError
from .events import EventBus
from .config import ConfigSchema, ConfigError, field
from .plugin_loader import PluginLoader, Isolation, PluginLoadError
from .mcp import MCPServer, MCPClient, MCPError
from .memory import VersionedState, MemoryStore, SessionMemory, MemoryError

__all__ = ["Kernel", "Container", "EventBus",
           "ConfigSchema", "ServiceError", "ConfigError", "field",
           "PluginLoader", "Isolation", "PluginLoadError",
           "MCPServer", "MCPClient", "MCPError",
           "VersionedState", "MemoryStore", "SessionMemory", "MemoryError"]
__version__ = "1.0.0"


class Kernel(object):
    """内核门面：把 DI 容器、事件总线、配置组合成一个入口。"""

    def __init__(self):
        self.container = Container()
        self.events = EventBus()
        # 内核自身能力注册进容器，插件可直接 DEPENDS 拿到；
        # manage=False：内部基础服务不参与 start/stop，避免生命周期方法重入
        self.container.register_instance("kernel", self, manage=False)
        self.container.register_instance("events", self.events, manage=False)
        self.container.register_instance("container", self.container, manage=False)

    # --- 容器代理 ---
    def register(self, name, factory, depends=None, singleton=True, override=False):
        return self.container.register(name, factory, depends, singleton, override)

    def register_instance(self, name, instance, depends=None, override=False):
        return self.container.register_instance(name, instance, depends, override)

    def resolve(self, name):
        return self.container.resolve(name)

    def has(self, name):
        return self.container.has(name)

    # --- 生命周期 ---
    def start(self):
        self.container.start_all()
        return self

    def stop(self):
        self.container.stop_all()
        return self

    def is_started(self):
        return self.container.is_started()

    def status(self):
        snap = self.container.status()
        snap["events"] = self.events.event_names()
        snap["version"] = __version__
        return snap
