# -*- coding: utf-8 -*-
"""
灵魂记忆 / 人格卡片（本地化移植自 DSH 插件 dsh-soul-md）
======================================================

让大肥鱼跨会话长大，而不是每次重置。三样东西：

1. **人格卡片（soul card）**：一张写着"它是谁"的 markdown，
   发现自己稳定的偏好/价值观就写回去，跨会话累积。
2. **分层长期记忆**：按话题分格存放（core + 项目/主题），有容量上限。
3. **按话题召回**：对话时按关键词捞回相关的几条塞进 prompt，不用每次全塞。

对齐原版的关键参数：
- 记忆总量上限 1MB（原版 maxBytes 1048576）
- 单次召回最多 5 个话题、1600 字（recallMaxTopics / recallMaxChars）
- 卡片上限 64KB（soulMaxBytes）
- 召回方式：关键词（recall: keyword）

顺带把旧的 data/memory.json 迁过来，并**洗掉里面混进的模型推理痕迹**
（"首先，""关键点：""现在，看对话"这类不是事实、是它自己碎碎念的条目）。
"""

import os
import re
import json
import time
import datetime

PLUGIN_ID = "soul_md"
PLUGIN_NAME = "灵魂记忆"
PLUGIN_ICON = "🧠"
PLUGIN_VERSION = "1.0.0"

MAX_BYTES = 1048576        # 记忆总量上限 1MB
SOUL_MAX_BYTES = 65536     # 卡片上限 64KB
RECALL_MAX_TOPICS = 5
RECALL_MAX_CHARS = 1600
INJECT_MAX_CHARS = 24000

# 旧 memory.json 的存放位置（主程序在用的那份）
LEGACY_MEMORY = r"D:\本地部署大肥鱼环境\data\memory.json"

# 一眼就能看出的"模型推理痕迹"——这些不是事实，不该进记忆
_NOISE_PAT = re.compile(
    r"(首先[，,、]|关键点[是：:]|我需要|现在[，,]?看对话|让我|"
    r"我需要提炼|我应该|根据要求|作为[^\s]{0,8}记忆整理器|"
    r"^\s*(用户|大肥鱼|大肥|AI|ai)\s*(说|问|答)?\s*[:：]|"   # 对话原文，不是事实
    r"输出规则|只输出|每条一行|已经记录过的不要重复)")

_ctx = None
_state = None


# ---------------------------------------------------------------- 工具
def _now():
    return int(time.time())


def _path():
    return _ctx.plugin_data(PLUGIN_ID, "state.json")


def _default_state():
    return {
        "cards": {},        # {名字: 卡片正文}
        "active": "大肥鱼",
        "topics": {},       # {话题: [ {at, text} ]}
        "migrated": False,
        "created": _now(),
    }


def _load():
    global _state
    if _state is None:
        s = _ctx.read_json(_path(), None)
        if not isinstance(s, dict):
            s = _default_state()
        for k, v in _default_state().items():
            s.setdefault(k, v)
        if not isinstance(s.get("topics"), dict):
            s["topics"] = {}
        if not isinstance(s.get("cards"), dict):
            s["cards"] = {}
        _state = s
    return _state


def _save():
    if _state is not None:
        _ctx.write_json(_path(), _state)


def _total_chars(s):
    n = 0
    for t, items in (s.get("topics") or {}).items():
        for it in items:
            n += len(str(it.get("text", "")))
    return n


def _clean_fact(t):
    """判断一条旧 fact 是不是真事实（不是模型的碎碎念）。"""
    t = str(t or "").strip()
    if not t:
        return None
    if len(t) > 60:                 # 太长的基本是把 prompt 抄进来了
        return None
    if _NOISE_PAT.search(t):
        return None
    if t.count("，") > 4:
        return None
    if not re.search(r"[\u4e00-\u9fff]", t):
        return None
    return t


def _tokens(q):
    q = str(q or "")
    parts = re.split(r"[\s,，。、；;！!？?\.~/|「」『』（）()【】\[\]]+", q)
    out = [p for p in parts if len(p) >= 2]
    extra = []
    for p in out:
        if len(p) >= 3 and re.match(r"^[\u4e00-\u9fff]+$", p):
            extra += [p[i:i + 2] for i in range(len(p) - 1)]
    return list(dict.fromkeys(out + extra))


# ---------------------------------------------------------------- 迁移
def _migrate_legacy():
    """把旧的 data/memory.json 迁进来，顺便洗掉推理痕迹。"""
    s = _load()
    if s.get("migrated"):
        return 0, 0
    if not os.path.exists(LEGACY_MEMORY):
        s["migrated"] = True
        _save()
        return 0, 0
    try:
        with open(LEGACY_MEMORY, "r", encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        s["migrated"] = True
        _save()
        return 0, 0

    kept, dropped = 0, 0
    if isinstance(old, dict):
        facts = old.get("facts")
        if isinstance(facts, list):
            for t in facts:
                c = _clean_fact(t)
                if c:
                    _append("core", c, save=False)
                    kept += 1
                else:
                    dropped += 1
        # 关系/用户名这类也留着
        for key in ("user", "relationship"):
            v = old.get(key)
            if isinstance(v, str) and v.strip() and len(v.strip()) <= 60:
                _append("core", "%s：%s" % (
                    "主人" if key == "user" else "关系", v.strip()), save=False)
                kept += 1
    s["migrated"] = True
    _save()
    return kept, dropped


# ---------------------------------------------------------------- 记忆读写
def _append(topic, text, save=True):
    s = _load()
    topic = str(topic or "core").strip() or "core"
    text = str(text or "").strip()
    if not text:
        return False
    items = s["topics"].setdefault(topic, [])
    # 去重：同一话题里已有一模一样的不重复写
    for it in items:
        if str(it.get("text", "")) == text:
            return False
    items.append({"at": _now(), "text": text})
    # 超容量就从最老的话题里丢最老的条目
    while _total_chars(s) > MAX_BYTES:
        oldest_topic, oldest_at = None, None
        for t, arr in s["topics"].items():
            if not arr:
                continue
            a = arr[0].get("at", 0)
            if oldest_at is None or a < oldest_at:
                oldest_at, oldest_topic = a, t
        if oldest_topic is None:
            break
        if oldest_topic == "core" and len(s["topics"]["core"]) <= 1:
            break
        s["topics"][oldest_topic].pop(0)
        if not s["topics"][oldest_topic]:
            del s["topics"][oldest_topic]
    if save:
        _save()
    return True


# ---------------------------------------------------------------- 对外接口
def setup(ctx):
    global _ctx
    _ctx = ctx
    _load()
    try:
        kept, dropped = _migrate_legacy()
        if kept or dropped:
            ctx.log(PLUGIN_ID, "旧记忆迁移：保留 %d 条，丢弃 %d 条推理痕迹" % (kept, dropped))
    except Exception as e:
        ctx.log(PLUGIN_ID, "迁移失败：%r" % (e,))
    ctx.log(PLUGIN_ID, "灵魂记忆就绪：%d 个话题" % len(_state.get("topics", {})))


def soul_get(name=""):
    """读人格卡片。没传名字就返回当前激活的那张。"""
    if not _ctx:
        return {"ok": False}
    s = _load()
    n = str(name or s.get("active") or "大肥鱼")
    return {"ok": True, "name": n, "card": s["cards"].get(n, ""),
            "names": list(s["cards"].keys()), "active": s.get("active", "")}


def soul_update(name, card):
    """更新（或新建）一张人格卡片。卡片上限 64KB。"""
    if not _ctx:
        return {"ok": False}
    s = _load()
    n = str(name or s.get("active") or "大肥鱼")
    c = str(card or "")[:SOUL_MAX_BYTES]
    s["cards"][n] = c
    s["active"] = n
    _save()
    return {"ok": True, "name": n, "chars": len(c)}


def memory_append(text, topic="core"):
    """写一条长期记忆。topic 用「core」或「项目/大肥鱼AI」这种。"""
    if not _ctx:
        return {"ok": False}
    ok = _append(topic, text)
    return {"ok": ok, "topic": topic, "total": len(_load().get("topics", {}))}


def memory_recall(query, max_topics=RECALL_MAX_TOPICS, max_chars=RECALL_MAX_CHARS):
    """按关键词捞回相关记忆，返回可以直接塞进 prompt 的文本。"""
    if not _ctx:
        return ""
    s = _load()
    toks = _tokens(query)
    try:
        mt = int(max_topics or RECALL_MAX_TOPICS)
        mc = int(max_chars or RECALL_MAX_CHARS)
    except Exception:
        mt, mc = RECALL_MAX_TOPICS, RECALL_MAX_CHARS

    scored = []
    for topic, items in (s.get("topics") or {}).items():
        sc = 0.0
        for t in toks:
            if t and t in topic:
                sc += 3.0
        hits = []
        for it in items:
            txt = str(it.get("text", ""))
            ts = 0.0
            for t in toks:
                if t and t in txt:
                    ts += 2.0
            if ts > 0:
                hits.append((ts, txt))
                sc += ts
        # core 兜底：没命中也给一点基础分，保证常识性记忆在场
        if topic == "core":
            sc += 0.5
        if sc > 0:
            hits.sort(key=lambda x: -x[0])
            scored.append((sc, topic, [h[1] for h in hits]))

    scored.sort(key=lambda x: -x[0])
    out, used = [], 0
    for sc, topic, texts in scored[:max(1, mt)]:
        for t in texts:
            if used + len(t) > mc:
                break
            out.append(t)
            used += len(t)
    return "\n".join(out)


def soul_context(query):
    """给 prompt 用的完整注入：人格卡片 + 召回的记忆。

    前端在发消息前调一次，把结果塞进 system/上下文里。
    """
    if not _ctx:
        return ""
    s = _load()
    name = s.get("active", "") or "大肥鱼"
    card = s["cards"].get(name, "")
    mem = memory_recall(query)
    parts = []
    if card:
        parts.append("【你是谁】\n" + card[:INJECT_MAX_CHARS])
    if mem:
        parts.append("【你记得的】\n" + mem)
    return "\n\n".join(parts)


def memory_list():
    """列出所有话题和条目数。"""
    if not _ctx:
        return {"ok": False}
    s = _load()
    info = []
    for t, items in (s.get("topics") or {}).items():
        info.append({"topic": t, "count": len(items)})
    info.sort(key=lambda x: -x["count"])
    return {"ok": True, "topics": info, "totalChars": _total_chars(s),
            "cards": list(s["cards"].keys())}


def memory_show(topic, limit=50):
    if not _ctx:
        return []
    s = _load()
    items = list((s.get("topics") or {}).get(str(topic or "core"), []))
    try:
        n = int(limit or 50)
    except Exception:
        n = 50
    out = []
    for it in items[-max(1, n):]:
        out.append({
            "at": it.get("at", 0),
            "when": datetime.datetime.fromtimestamp(
                it.get("at", 0) / 1000.0 if it.get("at", 0) > 1e12 else it.get("at", 0)
            ).strftime("%m-%d %H:%M") if it.get("at") else "",
            "text": it.get("text", ""),
        })
    return out


def memory_forget(topic, text=""):
    """删记忆：给了 text 就删那一条，否则删整个话题。"""
    if not _ctx:
        return {"ok": False}
    s = _load()
    t = str(topic or "core")
    if t not in s.get("topics", {}):
        return {"ok": False, "error": "没有这个话题"}
    if not text:
        del s["topics"][t]
        _save()
        return {"ok": True, "removed": "topic"}
    before = len(s["topics"][t])
    s["topics"][t] = [x for x in s["topics"][t] if str(x.get("text", "")) != str(text)]
    if not s["topics"][t]:
        del s["topics"][t]
    _save()
    return {"ok": True, "removed": before - len(s["topics"].get(t, []))}


def apis():
    return [soul_get, soul_update, memory_append, memory_recall, soul_context,
            memory_list, memory_show, memory_forget]
