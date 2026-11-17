# -*- coding: utf-8 -*-
"""
前端接入桥单元测试
==================
直接运行：python tools/test_bridge.py
"""

import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from core import Kernel, FrontendBridge

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  [FAIL]", label)


class MockReg(object):
    def describe(self):
        return [{"name": "t1", "enabled": True}]


def test_basic_commands():
    k = Kernel()
    b = FrontendBridge(k)
    check(b.call("ping")["data"] == "pong", "ping")
    # kernel.status
    r = b.call("kernel.status")
    check(r["ok"] and r["data"]["version"], "kernel.status")
    # has
    k.register_instance("svc", object())
    check(b.call("kernel.has", {"name": "svc"})["data"] is True, "kernel.has 命中")
    check(b.call("kernel.has", {"name": "nope"})["data"] is False, "kernel.has 未命中")
    # 未知命令
    check(b.call("xyz")["ok"] is False, "未知命令返回错误")


def test_tools_and_mode():
    k = Kernel()
    b = FrontendBridge(k, MockReg())
    check(b.call("tools.list")["data"][0]["name"] == "t1", "tools.list")
    # 无 registry
    b2 = FrontendBridge(Kernel())
    check(b2.call("tools.list")["data"] == [], "无 registry 返回空")
    # 模式
    check(b.call("mode.get")["data"] == "normal", "默认 normal")
    check(b.call("mode.switch", {"mode": "dsh"})["data"] == "dsh", "切换 dsh")
    check(b.call("mode.get")["data"] == "dsh", "模式保持 dsh")
    check(b.call("mode.switch", {"mode": "normal"})["data"] == "normal", "切回 normal")
    check(b.call("mode.switch", {"mode": "bad"})["ok"] is False, "非法模式报错")


def test_event_emit():
    k = Kernel()
    received = []
    k.events.on("test-event", lambda payload: received.append(payload))
    b = FrontendBridge(k)
    r = b.call("event.emit", {"event": "test-event", "payload": {"x": 1}})
    check(r["ok"] and received == [{"x": 1}], "event.emit 触发监听")
    check(b.call("event.emit", {})["ok"] is False, "缺 event 报错")


if __name__ == "__main__":
    test_basic_commands()
    test_tools_and_mode()
    test_event_emit()
    print("\n================================")
    print("前端桥测试：通过 %d，失败 %d" % (_passed, _failed))
    print("================================")
    sys.exit(1 if _failed else 0)
