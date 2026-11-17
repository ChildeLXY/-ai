# -*- coding: utf-8 -*-
"""
大肥鱼 AI · L1 · 多代理/子代理（架构 P7）
==========================================
:class:`SubAgent` 独立角色 + 独立上下文，:class:`AgentTeam` 负责委派、并行 fan-out、
结果汇总；单个子代理失败被隔离，不影响主代理和其他成员。

子代理执行器（runner）可注入，测试用 mock，不依赖真实模型。
"""

import threading
from concurrent.futures import ThreadPoolExecutor

__all__ = ["SubAgent", "AgentTeam", "SubAgentError"]


class SubAgentError(Exception):
    pass


class SubAgent(object):
    def __init__(self, name, role, runner, tools=None):
        self.name = name
        self.role = role
        # runner(agent, task)->str
        self.runner = runner
        self.tools = list(tools or [])

    def run(self, task):
        return self.runner(self, task)


class AgentTeam(object):
    def __init__(self, on_event=None, max_workers=4):
        self._agents = {}
        self.on_event = on_event or (lambda kind, **kw: None)
        self.max_workers = max_workers
        self._lock = threading.RLock()

    def add(self, agent):
        with self._lock:
            self._agents[agent.name] = agent
        return self

    def has(self, name):
        return name in self._agents

    def names(self):
        return list(self._agents)

    def _emit(self, kind, **kw):
        self.on_event(kind, **kw)

    def delegate(self, name, task):
        """派给单个子代理；失败隔离为 error 结果。"""
        if name not in self._agents:
            raise SubAgentError("子代理 %r 不存在" % name)
        agent = self._agents[name]
        self._emit("delegate", agent=name, task=task)
        try:
            result = agent.run(task)
            self._emit("delegate_done", agent=name, ok=True)
            return {"agent": name, "ok": True, "result": result}
        except Exception as e:
            self._emit("delegate_done", agent=name, ok=False)
            return {"agent": name, "ok": False, "error": "%r" % e}

    def fanout(self, task_map):
        """并行委派：{agent_name: task}，返回各成员结果（含失败），主线程不被单个失败打断。"""
        missing = [n for n in task_map if n not in self._agents]
        if missing:
            raise SubAgentError("子代理不存在: %r" % missing)
        results = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            future_map = {
                pool.submit(self.delegate, name, task): name
                for name, task in task_map.items()}
            for fut in future_map:
                r = fut.result()
                results[r["agent"]] = r
        return results

    def broadcast(self, task):
        """同一任务派给所有子代理。"""
        return self.fanout({name: task for name in self._agents})

    @staticmethod
    def summarize(results):
        """汇总：拼接成功结果，列出失败成员。"""
        ok, failed = [], []
        for name, r in results.items():
            if r.get("ok"):
                ok.append("[%s] %s" % (name, r["result"]))
            else:
                failed.append(name)
        return {"text": "\n".join(ok), "failed": failed,
                "all_ok": not failed}
