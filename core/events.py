# -*- coding: utf-8 -*-
"""
大肥鱼 AI · L0 内核 · 事件总线
==============================
对应架构方案 P0：统一同步/异步两类事件。

- 同步 ``emit``：在当前线程依次调用监听者，一个回调出错不影响其他回调。
- 异步 ``emit_async``：丢到守护线程派发，不阻塞调用方（如网络/桥接线程）。
- 纯标准库，线程安全。
"""

import threading

__all__ = ["EventBus"]


class _OnceWrapper(object):
    """包装一次性回调，触发后自动从总线摘除。"""

    def __init__(self, bus, event, cb):
        self.bus = bus
        self.event = event
        self.cb = cb

    def __call__(self, data):
        self.bus.off(self.event, self)
        self.cb(data)


class EventBus(object):
    def __init__(self):
        self._listeners = {}        # event -> [callable]
        self._lock = threading.RLock()
        self._async_pool = []       # 运行中的异步线程（守护，不 join）

    # ------------------------------------------------------------ 订阅
    def on(self, event, cb):
        if not callable(cb):
            raise TypeError("事件 %s 的监听者必须可调用" % event)
        with self._lock:
            self._listeners.setdefault(event, []).append(cb)
        return self

    def once(self, event, cb):
        return self.on(event, _OnceWrapper(self, event, cb))

    def off(self, event, cb=None):
        """摘除指定回调；cb 为 None 时清空该事件的全部监听者。"""
        with self._lock:
            if event not in self._listeners:
                return self
            if cb is None:
                self._listeners[event] = []
            else:
                self._listeners[event] = [c for c in self._listeners[event] if c != cb]
        return self

    def listeners(self, event):
        with self._lock:
            return list(self._listeners.get(event, []))

    def event_names(self):
        with self._lock:
            return list(self._listeners.keys())

    # ------------------------------------------------------------ 派发
    def emit(self, event, data=None):
        """同步派发：异常隔离，返回成功执行的回调数。"""
        cbs = self.listeners(event)
        ok = 0
        for cb in cbs:
            try:
                cb(data)
                ok += 1
            except Exception:
                continue
        return ok

    def emit_async(self, event, data=None):
        """异步派发：守护线程中执行，立即返回，不阻塞调用方。"""
        t = threading.Thread(target=self.emit, args=(event, data),
                             name="evt-%s" % event, daemon=True)
        t.start()
        with self._lock:
            self._async_pool = [x for x in self._async_pool if x.is_alive()]
            self._async_pool.append(t)
        return t
