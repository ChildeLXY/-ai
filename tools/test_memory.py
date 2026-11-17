# -*- coding: utf-8 -*-
"""
记忆会话版本化单元测试
======================
直接运行：python tools/test_memory.py
"""

import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from core import VersionedState, MemoryStore, SessionMemory, MemoryError

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  [FAIL]", label)


# ============================================================ 版本化
def test_versioned():
    vs = VersionedState()
    v0 = vs.commit({"n": 1})
    v1 = vs.commit({"n": 2})
    check(v0 == 0 and v1 == 1 and vs.current_version == 1, "版本递增")
    check(vs.get(0)["n"] == 1, "get 目标版本")
    # 深拷贝不可变
    got = vs.get(0)
    got["n"] = 99
    check(vs.get(0)["n"] == 1, "快照不可变（深拷贝）")
    check(vs.rollback(0)["n"] == 1, "rollback")
    check(vs.history() == [0, 1], "history")
    try:
        vs.get(99)
        check(False, "不存在版本应报错")
    except MemoryError:
        check(True, "不存在版本报错")


# ============================================================ 记忆
def test_memory_store():
    ms = MemoryStore()
    id1 = ms.add("学习 Python 基础", tags=["学习", "python"])
    ms.add("做晚饭", tags=["生活"])
    check(id1 == 0 and ms.count() == 2, "add/count")
    r = ms.search("python")
    check(len(r) == 1 and r[0]["id"] == 0, "内容词法检索")
    r2 = ms.search("生活")
    check(len(r2) == 1, "标签检索")
    check(len(ms.search("")) == 2, "空关键词返回全部")
    check(ms.get(1)["content"] == "做晚饭", "get by id")


# ============================================================ 会话
def test_session_version():
    s = SessionMemory()
    s.add("user", "第一条")
    s.add("assistant", "第二条")
    snap = s.snapshot()
    s.add("user", "第三条")
    check(len(s.messages) == 3, "消息追加")
    s.rollback(snap)
    check(len(s.messages) == 2, "rollback 恢复消息")


def test_compaction():
    s = SessionMemory(compaction_threshold=5)
    for i in range(10):
        s.add("user", "消息%d" % i)
    check(s.needs_compaction(), "触发压缩阈值")
    summarizer = lambda old: "已压缩 %d 条" % len(old)
    ok = s.compact(summarizer, keep_recent=4)
    check(ok is True, "执行压缩")
    check(len(s.messages) == 5, "压缩为 1 摘要 + 4 最近")
    check(s.messages[0]["content"].startswith("之前对话摘要"),
          "首条为摘要")
    check(s.messages[-1]["content"] == "消息9", "保留最近消息")
    # 消息不足不压缩
    s2 = SessionMemory()
    s2.add("user", "only")
    check(s2.compact(summarizer, keep_recent=4) is False, "消息不足不压缩")


if __name__ == "__main__":
    test_versioned()
    test_memory_store()
    test_session_version()
    test_compaction()
    print("\n================================")
    print("记忆会话测试：通过 %d，失败 %d" % (_passed, _failed))
    print("================================")
    sys.exit(1 if _failed else 0)
