# -*- coding: utf-8 -*-
"""
大肥鱼 AI · L4 · 前端接入桥（架构 P9）
======================================
把内核能力组织成前端可调用的统一命令接口：前端只认 :meth:`FrontendBridge.call`，
不直接碰容器/事件/插件，便于替换前端（普通模式 / DSH 模式共用同一数据契约）。

命令处理为纯函数式返回，不绑定 WebSocket，可直接单测。
"""

__all__ = ["FrontendBridge", "BridgeError"]


class BridgeError(Exception):
    pass


class FrontendBridge(object):
    def __init__(self, kernel, tool_registry=None):
        self.kernel = kernel
        self.tool_registry = tool_registry
        self.mode = "normal"          # normal | dsh

    def call(self, cmd, params=None):
        params = params or {}
        try:
            return {"ok": True, "data": self._route(cmd, params)}
        except BridgeError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": "%r" % e}

    def _route(self, cmd, params):
        if cmd == "ping":
            return "pong"
        if cmd == "kernel.status":
            return self.kernel.status()
        if cmd == "kernel.has":
            return self.kernel.has(params.get("name"))
        if cmd == "tools.list":
            if self.tool_registry is None:
                return []
            if hasattr(self.tool_registry, "describe"):
                return self.tool_registry.describe()
            return self.tool_registry.names()
        if cmd == "mode.get":
            return self.mode
        if cmd == "mode.switch":
            target = params.get("mode")
            if target not in ("normal", "dsh"):
                raise BridgeError("未知模式 %r" % target)
            self.mode = target
            return self.mode
        if cmd == "event.emit":
            event = params.get("event")
            if not event:
                raise BridgeError("缺少 event 字段")
            self.kernel.events.emit(event, params.get("payload"))
            return "emitted"
        raise BridgeError("未知命令 %r" % cmd)
