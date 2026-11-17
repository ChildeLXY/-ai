# -*- coding: utf-8 -*-
"""
L0 内核 P0 单元测试
===================
纯标准库，直接运行：python tools/test_core_kernel.py
覆盖：DI 容器（注册/解析/依赖/单例/循环/缺依赖/拓扑/生命周期）、
事件总线（on/once/off/异常隔离/异步）、配置 schema（类型/默认/必填/范围/枚举）、
Kernel 门面集成。
"""

import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from core import (Kernel, Container, EventBus, ConfigSchema,
                  ServiceError, ConfigError, field)

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  [FAIL]", label)


def expect_error(fn, errtype, label):
    try:
        fn()
        check(False, label + "（应抛 %s）" % errtype.__name__)
    except errtype:
        check(True, label)
    except Exception as e:
        check(False, label + "（错误类型不对: %r）" % e)


# ============================================================== 容器
def test_container_basic():
    c = Container()
    c.register("a", lambda: "A")
    check(c.resolve("a") == "A", "基本注册/解析")
    check(c.has("a") and not c.has("x"), "has 判断")

    # 依赖注入（按 depends 顺序位置传参）
    c.register("b", lambda a: "B+" + a, depends=["a"])
    check(c.resolve("b") == "B+A", "依赖注入位置传参")

    # 单例
    n = {"v": 0}
    def mk():
        n["v"] += 1
        return object()
    c.register("s", mk)
    check(c.resolve("s") is c.resolve("s"), "单例只构造一次")
    check(n["v"] == 1, "单例工厂只调用一次")

    # 非单例
    c.register("ns", mk, singleton=False)
    check(c.resolve("ns") is not c.resolve("ns"), "非单例每次新建")


def test_container_errors():
    c = Container()
    c.register("a", lambda: 1)
    # 缺依赖
    c.register("bad", lambda x: x, depends=["missing"])
    expect_error(lambda: c.resolve("bad"), ServiceError, "缺依赖明确报错")
    # 重复注册
    expect_error(lambda: c.register("a", lambda: 2), ServiceError, "重复注册报错")
    # override
    c.register("a", lambda: 2, override=True)
    check(c.resolve("a") == 2, "override 覆盖注册")
    # 非法名/工厂
    expect_error(lambda: c.register("", lambda: 1), ServiceError, "空服务名报错")
    expect_error(lambda: c.register("x", "notcallable"), ServiceError, "非可调用工厂报错")
    # 工厂构造失败
    c.register("boom", lambda: 1 / 0)
    expect_error(lambda: c.resolve("boom"), ServiceError, "构造失败包装为 ServiceError")


def test_circular():
    c = Container()
    c.register("x", lambda y: y, depends=["y"])
    c.register("y", lambda x: x, depends=["x"])
    expect_error(lambda: c.resolve("x"), ServiceError, "循环依赖报错")
    expect_error(lambda: c._topo_order(), ServiceError, "拓扑排序检测循环")


def test_lifecycle():
    log = []

    class L(object):
        def __init__(self, tag):
            self.tag = tag
        def start(self):
            log.append("start:" + self.tag)
        def stop(self):
            log.append("stop:" + self.tag)

    c = Container()
    c.register("base", lambda: L("base"))
    c.register("top", lambda b: L("top"), depends=["base"])
    c.start_all()
    check(log == ["start:base", "start:top"], "start 按拓扑序")
    check(c.is_started(), "start_all 标记已启动")
    c.stop_all()
    check(log == ["start:base", "start:top", "stop:top", "stop:base"], "stop 逆序")
    check(not c.is_started(), "stop_all 取消标记")


# ============================================================== 事件总线
def test_events():
    bus = EventBus()
    got = []
    bus.on("e", lambda d: got.append(d))
    n = bus.emit("e", 1)
    check(got == [1] and n == 1, "on/emit 同步派发")

    # once
    once_got = []
    bus.once("o", lambda d: once_got.append(d))
    bus.emit("o", 1)
    bus.emit("o", 2)
    check(once_got == [1], "once 只触发一次")

    # off
    bus.off("e")
    check(bus.emit("e", 9) == 0, "off 清空监听者")

    # 异常隔离
    def bad_cb(d):
        raise RuntimeError("boom")
    ok_got = []
    bus.on("f", bad_cb)
    bus.on("f", lambda d: ok_got.append(d))
    n = bus.emit("f", "x")
    check(n == 1 and ok_got == ["x"], "回调异常隔离不影响其他")

    # 异步
    ag = []
    bus.on("a", lambda d: ag.append(d))
    bus.emit_async("a", "async")
    time.sleep(0.5)
    check(ag == ["async"], "emit_async 后台派发")


# ============================================================== 配置
def test_config():
    sch = ConfigSchema({
        "name": field("str", default="dfy", label="名称"),
        "count": field("int", default=1, min=0, max=10),
        "ratio": field("float", required=True),
        "enable": field("bool", default=True),
        "mode": field("choice", default="safe", choices=["safe", "thread", "process"]),
    })
    out = sch.validate({"ratio": "0.5", "count": "5", "enable": "false"})
    check(out["name"] == "dfy", "默认值填充")
    check(out["count"] == 5 and isinstance(out["count"], int), "字符串转 int")
    check(out["ratio"] == 0.5 and isinstance(out["ratio"], float), "字符串转 float")
    check(out["enable"] is False, "字符串转 bool")
    check(out["mode"] == "safe", "choice 默认值")

    # 必填缺失
    expect_error(lambda: sch.validate({"count": 1}), ConfigError, "必填缺失报错")
    # 超范围
    expect_error(lambda: sch.validate({"ratio": 1, "count": 99}), ConfigError, "max 超范围报错")
    # 非法枚举
    expect_error(lambda: sch.validate({"ratio": 1, "mode": "x"}), ConfigError, "非法 choice 报错")
    # 非法类型
    expect_error(lambda: sch.validate({"ratio": 1, "count": "abc"}), ConfigError, "int 转换失败报错")

    spec = sch.form_spec()
    check(len(spec["fields"]) == 5, "form_spec 输出 5 字段")
    check(spec["fields"][0]["label"] == "名称", "form_spec 带 label")


# ============================================================== Kernel 集成
def test_kernel():
    k = Kernel()
    check(k.has("events") and k.has("kernel"), "内核自身能力已注册")
    k.register("model", lambda: "MODEL")
    k.register("agent", lambda m: "AGENT+" + m, depends=["model"])
    k.start()
    check(k.resolve("agent") == "AGENT+MODEL", "Kernel 依赖解析")
    snap = k.status()
    names = [s["name"] for s in snap["services"]]
    check("agent" in names and snap["version"], "status 快照含服务与版本")
    k.stop()
    check(not k.is_started(), "Kernel 停止")


if __name__ == "__main__":
    test_container_basic()
    test_container_errors()
    test_circular()
    test_lifecycle()
    test_events()
    test_config()
    test_kernel()
    print("\n================================")
    print("P0 内核测试：通过 %d，失败 %d" % (_passed, _failed))
    print("================================")
    sys.exit(1 if _failed else 0)
