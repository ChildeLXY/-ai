# -*- coding: utf-8 -*-
"""
多代理子代理单元测试
====================
直接运行：python tools/test_subagents.py
"""

import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from core import SubAgent, AgentTeam, SubAgentError

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  [FAIL]", label)


def make_runner(prefix, fail=False, delay=0):
    def runner(agent, task):
        if delay:
            time.sleep(delay)
        if fail:
            raise RuntimeError("boom-%s" % prefix)
        return "%s:%s" % (prefix, task)
    return runner


def build_team():
    team = AgentTeam()
    team.add(SubAgent("a", "角色A", make_runner("RA")))
    team.add(SubAgent("b", "角色B", make_runner("RB")))
    team.add(SubAgent("c", "角色C", make_runner("RC", fail=True)))
    return team


def test_basic_and_delegate():
    team = build_team()
    check(team.has("a") and team.names() == ["a", "b", "c"], "add/has/names")
    r = team.delegate("a", "任务1")
    check(r["ok"] and r["result"] == "RA:任务1", "delegate 成功")
    # 失败隔离
    r2 = team.delegate("c", "任务2")
    check(r2["ok"] is False and "boom" in r2["error"], "delegate 失败隔离不抛出")
    try:
        team.delegate("nope", "x")
        check(False, "不存在子代理应报错")
    except SubAgentError:
        check(True, "不存在子代理报错")


def test_fanout():
    team = build_team()
    results = team.fanout({"a": "ta", "b": "tb"})
    check(len(results) == 2, "fanout 结果齐全")
    check(results["a"]["result"] == "RA:ta" and
          results["b"]["result"] == "RB:tb", "fanout 各成员结果正确")
    # 含失败成员的隔离
    results2 = team.fanout({"a": "x", "c": "y"})
    check(results2["a"]["ok"] and results2["c"]["ok"] is False,
          "fanout 单成员失败被隔离")


def test_parallel_and_broadcast():
    # 三个各延迟 0.2s，并行应明显快于串行 0.6s
    team = AgentTeam()
    for n in ("a", "b", "c"):
        team.add(SubAgent(n, "r", make_runner(n, delay=0.2)))
    t0 = time.time()
    results = team.broadcast("任务")
    elapsed = time.time() - t0
    check(len(results) == 3, "broadcast 全员执行")
    check(elapsed < 0.5, "并行执行（用时 %.2fs < 串行 0.6s）" % elapsed)


def test_summarize():
    team = build_team()
    results = team.fanout({"a": "x", "b": "y"})
    s = AgentTeam.summarize(results)
    check(s["all_ok"] and len(s["text"]) > 0, "summarize 成功拼接")
    results2 = team.fanout({"a": "x", "c": "y"})
    s2 = AgentTeam.summarize(results2)
    check(s2["all_ok"] is False and s2["failed"] == ["c"],
          "summarize 标注失败成员")


if __name__ == "__main__":
    test_basic_and_delegate()
    test_fanout()
    test_parallel_and_broadcast()
    test_summarize()
    print("\n================================")
    print("多代理测试：通过 %d，失败 %d" % (_passed, _failed))
    print("================================")
    sys.exit(1 if _failed else 0)
