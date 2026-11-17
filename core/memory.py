# -*- coding: utf-8 -*-
"""
大肥鱼 AI · L1 · 记忆会话版本化（架构 P5）
==========================================
独立于 QQ 专属记忆的通用能力：

- :class:`VersionedState` 任意状态的版本化（快照/回滚，不可变深拷贝）；
- :class:`MemoryStore` 记忆条目 + 词法检索；
- :class:`SessionMemory` 会话消息，版本化 + 上下文压缩（summarizer 可注入）。

纯标准库，默认内存存储，可直接单测。
"""

import copy
import time
import threading

__all__ = ["VersionedState", "MemoryStore", "SessionMemory", "MemoryError"]


class MemoryError(Exception):
    pass


# ============================================================ 版本化状态
class VersionedState(object):
    def __init__(self):
        self._versions = []          # [(version, state_copy)]
        self._lock = threading.RLock()

    def commit(self, state):
        with self._lock:
            v = len(self._versions)
            self._versions.append((v, copy.deepcopy(state)))
            return v

    def get(self, version):
        with self._lock:
            for v, s in self._versions:
                if v == version:
                    return copy.deepcopy(s)
            raise MemoryError("版本 %r 不存在" % version)

    def rollback(self, version):
        """回滚：返回目标版本的拷贝（不删除后续历史，便于再提交）。"""
        return self.get(version)

    @property
    def current_version(self):
        return self._versions[-1][0] if self._versions else -1

    def history(self):
        return [v for v, _ in self._versions]


# ============================================================ 记忆存储
class MemoryStore(object):
    def __init__(self):
        self._items = []
        self._lock = threading.RLock()

    def add(self, content, tags=None, meta=None):
        with self._lock:
            item = {"id": len(self._items), "content": content,
                    "tags": list(tags or []), "ts": time.time(),
                    "meta": meta or {}}
            self._items.append(item)
            return item["id"]

    def get(self, id):
        return self._items[id]

    def search(self, keyword):
        """词法子串匹配（内容 + 标签），大小写不敏感。"""
        kw = (keyword or "").lower()
        if not kw:
            return list(self._items)
        out = []
        for it in self._items:
            hay = (it["content"] + " " + " ".join(it["tags"])).lower()
            if kw in hay:
                out.append(it)
        return out

    def all(self):
        return list(self._items)

    def count(self):
        return len(self._items)


# ============================================================ 会话
class SessionMemory(object):
    def __init__(self, compaction_threshold=20):
        self.messages = []
        self.threshold = compaction_threshold
        self._vs = VersionedState()
        self._lock = threading.RLock()

    def add(self, role, content, **extra):
        with self._lock:
            msg = {"role": role, "content": content}
            msg.update(extra)
            self.messages.append(msg)
            return self._vs.commit(self.messages)

    def snapshot(self):
        with self._lock:
            return self._vs.commit(self.messages)

    def rollback(self, version):
        with self._lock:
            self.messages = self._vs.rollback(version)
            return version

    @property
    def current_version(self):
        return self._vs.current_version

    def needs_compaction(self):
        return len(self.messages) > self.threshold

    def compact(self, summarizer, keep_recent=4):
        """上下文压缩：旧消息经 summarizer 合并为一条 system 摘要，保留最近若干条。
        summarizer(old_messages)->str；测试可注入简单截断。"""
        with self._lock:
            if len(self.messages) <= keep_recent:
                return False
            old = self.messages[:-keep_recent]
            recent = self.messages[-keep_recent:]
            summary = summarizer(old)
            self.messages = (
                [{"role": "system", "content": "之前对话摘要：" + summary}]
                + recent)
            self._vs.commit(self.messages)
            return True
