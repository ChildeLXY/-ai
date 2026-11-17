# -*- coding: utf-8 -*-
"""
大肥鱼 AI · L0 内核 · 依赖注入容器
==================================
对应架构方案 P0：服务注册表 + 依赖注入 + 生命周期。

设计原则（YAGNI，纯标准库，约 200 行）：
- 服务按"名"注册，工厂函数声明 ``depends``，容器按拓扑序解析依赖。
- 缺依赖、循环依赖、重复注册都在加载/启动阶段明确报错（对应 DSH 的
  inject 报错，但提前到安装/启动阶段，而不是运行到一半才炸）。
- 单例服务只构造一次；服务实例若实现了 ``start()`` / ``stop()``，
  由 ``start_all()`` / ``stop_all()`` 统一管理生命周期。

依赖注入约定：
    容器按 ``depends`` 声明的顺序，把依赖实例作为位置参数传给工厂::

        def make_agent(model, memory):   # depends=["model", "memory"]
            return MyAgent(model, memory)
        container.register("agent", make_agent, depends=["model", "memory"])
"""

import threading
from collections import OrderedDict

__all__ = ["ServiceError", "Container"]


class ServiceError(Exception):
    """内核服务错误：缺依赖、循环依赖、重复注册、生命周期失败等。"""


class _Definition(object):
    __slots__ = ("name", "factory", "depends", "singleton", "instance", "started", "manage")

    def __init__(self, name, factory, depends, singleton, manage):
        self.name = name
        self.factory = factory
        self.depends = list(depends or [])
        self.singleton = singleton
        self.instance = None
        self.started = False
        self.manage = manage          # 是否纳入 start/stop 生命周期管理


class Container(object):
    """轻量依赖注入容器。线程安全。"""

    def __init__(self):
        self._services = OrderedDict()      # name -> _Definition
        self._resolving = set()             # 解析栈，用于检测循环依赖
        self._lock = threading.RLock()
        self._all_started = False

    # ------------------------------------------------------------ 注册
    def register(self, name, factory, depends=None, singleton=True,
                 override=False, manage=True):
        """注册一个服务。

        :param name: 唯一服务名（也是其他插件 DEPENDS 里引用的名字）。
        :param factory: 无参/带依赖参数的工厂函数，返回服务实例。
        :param depends: 依赖的服务名列表，决定构造与启动顺序。
        :param singleton: True 时全局只构造一次；False 每次 resolve 都新建。
        :param override: True 允许覆盖同名注册（热替换用），默认禁止重复注册。
        :param manage: True 时由 start_all/stop_all 调用实例的 start/stop；
            内核内部基础服务设为 False，避免生命周期方法重入。
        """
        if not name or not isinstance(name, str):
            raise ServiceError("服务名必须是非空字符串，收到 %r" % (name,))
        if not callable(factory):
            raise ServiceError("服务 %s 的 factory 必须可调用" % name)
        deps = list(depends or [])
        if len(deps) != len(set(deps)):
            raise ServiceError("服务 %s 的 depends 存在重复项: %r" % (name, deps))
        with self._lock:
            if name in self._services and not override:
                raise ServiceError("服务 %s 已注册，需覆盖请显式 override=True" % name)
            self._services[name] = _Definition(
                name, factory, deps, singleton, manage)
        return self

    def register_instance(self, name, instance, depends=None,
                          override=False, manage=True):
        """直接注册一个已构造好的实例（单例）。"""
        self.register(name, lambda *a: instance,
                      depends=depends, singleton=True, override=override,
                      manage=manage)
        with self._lock:
            self._services[name].instance = instance
        return self

    def has(self, name):
        return name in self._services

    def names(self):
        return list(self._services.keys())

    # ------------------------------------------------------------ 解析
    def resolve(self, name):
        """解析服务，递归构造依赖。缺依赖/循环依赖抛 ServiceError。"""
        with self._lock:
            if name not in self._services:
                raise ServiceError("无法解析服务 %r：未注册（请检查 DEPENDS 拼写）" % name)
            return self._resolve(self._services[name])

    def _resolve(self, d):
        # 已构造的单例直接返回
        if d.singleton and d.instance is not None:
            return d.instance
        if d.name in self._resolving:
            chain = " -> ".join(list(self._resolving) + [d.name])
            raise ServiceError("检测到循环依赖: %s" % chain)

        self._resolving.add(d.name)
        try:
            kwargs = [self._resolve(self._services[dep])
                      for dep in d.depends]
            instance = d.factory(*kwargs)
        except ServiceError:
            raise
        except Exception as e:
            raise ServiceError("服务 %s 构造失败: %r" % (d.name, e))
        finally:
            self._resolving.discard(d.name)

        if d.singleton:
            d.instance = instance
        return instance

    def get(self, name):
        """已实例化则直接返回，否则解析。语义同 resolve，更短的别名。"""
        return self.resolve(name)

    # ------------------------------------------------------------ 拓扑序
    def _topo_order(self):
        """对全部服务做拓扑排序（DFS），同时校验缺依赖与循环依赖。"""
        order = []
        state = {}   # 0=访问中 1=完成

        def visit(d, stack):
            st = state.get(d.name)
            if st == 1:
                return
            if st == 0:
                raise ServiceError("检测到循环依赖: %s"
                                   % " -> ".join(stack + [d.name]))
            for dep in d.depends:
                if dep not in self._services:
                    raise ServiceError(
                        "服务 %s 依赖未注册的 %r（请先安装提供该能力的插件）"
                        % (d.name, dep))
                state[d.name] = 0
                visit(self._services[dep], stack + [d.name])
            state[d.name] = 1
            order.append(d)

        for d in self._services.values():
            visit(d, [])
        return order

    # ------------------------------------------------------------ 生命周期
    def start_all(self):
        """按拓扑序构造全部服务并调用各自的 start()（若有）。
        manage=False 的内部基础服务只构造、不调用生命周期方法。"""
        with self._lock:
            order = self._topo_order()
            for d in order:
                instance = self._resolve(d)
                if d.manage:
                    start = getattr(instance, "start", None)
                    if callable(start):
                        try:
                            start()
                        except Exception as e:
                            raise ServiceError(
                                "服务 %s start() 失败: %r" % (d.name, e))
                    d.started = True
            self._all_started = True
        return self

    def stop_all(self):
        """逆序调用 stop()（若有），并清空业务服务的单例缓存。
        manage=False 的内部基础服务完全保留，不 stop、不清空。"""
        with self._lock:
            order = self._topo_order()
            for d in reversed(order):
                if not d.manage:
                    continue
                if not d.started:
                    d.instance = None
                    continue
                instance = d.instance
                stop = getattr(instance, "_core_stop", None) or getattr(instance, "stop", None)
                if callable(stop) and instance is not None:
                    try:
                        stop()
                    except Exception:
                        pass
                d.started = False
                d.instance = None
            self._all_started = False

    def is_started(self):
        return self._all_started

    def status(self):
        """返回内核快照（供可观测面板）。"""
        with self._lock:
            return {
                "services": [
                    {"name": d.name, "depends": d.depends,
                     "singleton": d.singleton, "manage": d.manage,
                     "instantiated": d.instance is not None,
                     "started": d.started}
                    for d in self._services.values()
                ],
                "all_started": self._all_started,
            }
