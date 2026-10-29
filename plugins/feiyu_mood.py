# -*- coding: utf-8 -*-
"""
鱼的心情（本地化移植自 DSH 插件 dsh-feiyu-mood）
=================================================

不是关键词硬猜，也不是随机数：每轮聊完，用本地模型判一次"这一轮它的心情往哪走"，
会起伏、也会自己慢慢回稳，像活人。

照搬原版的核心数值：
- 总心情 mood 0~100，基线 70，没有底线（真出大事可以一直降到 0，靠时间慢慢回稳）
- 七维情绪：开心 / 踏实 / 甜蜜 / 心疼 / 担心 / 难过 / 吃醋
- 心情步进 = moodDelta / 3（原版 MOOD_STEP_DIVISOR）
- 心情向基线回落，半衰期 12 小时；单根情绪向 0 衰减，半衰期 90 分钟
- confidence 低于门槛的那句话不会说出口，只记进时间线

判断全部走本地 Ollama，一个云端 token 都不花。
"""

import os
import re
import json
import math
import time
import threading

PLUGIN_ID = "feiyu_mood"
PLUGIN_NAME = "鱼的心情"
PLUGIN_ICON = "🐟"
PLUGIN_VERSION = "1.0.0"

# ------------------------------------------------------------------ 常量
DIMS = ["happy", "calm", "sweet", "heartache", "worry", "sad", "jealous"]
DIM_LABEL = {
    "happy": "开心", "calm": "踏实", "sweet": "甜蜜", "heartache": "心疼",
    "worry": "担心", "sad": "难过", "jealous": "吃醋",
}
# 完整说法
DIM_PLAIN = {
    "happy": "心里挺高兴", "calm": "心里踏实", "sweet": "心里甜",
    "heartache": "心疼得不行", "worry": "放心不下", "sad": "有点难过",
    "jealous": "有点吃醋",
}
# 短说法（不带"主人"，只说自己心里怎么样）
DIM_SHORT = {
    "happy": "挺高兴", "calm": "挺踏实", "sweet": "有点甜",
    "heartache": "有点心疼", "worry": "有点挂心", "sad": "有点难过",
    "jealous": "有点酸",
}

MOOD_BASE = 70.0            # 心情基线
MOOD_STEP_DIVISOR = 3       # moodDelta 除以它才是 mood 实际变化
DECAY_HALF_HOURS = 12       # 心情回落半衰期（小时）
DIM_DECAY_MIN = 90          # 单根情绪衰减半衰期（分钟）
CONF_GATE = 60              # confidence 低于此值的话不说出口
OWNER = "主人"
SELF = "大肥鱼"

_ctx = None
_lock = threading.RLock()
_enabled = True


# ------------------------------------------------------------------ 工具
def _clamp(v, lo, hi):
    try:
        v = float(v)
    except Exception:
        return lo
    return max(lo, min(hi, v))


def _now_ms():
    return int(time.time() * 1000)


def _default_state():
    return {
        "mood": MOOD_BASE,
        "dims": dict((k, 0.0) for k in DIMS),
        "lastDecayAt": _now_ms(),
        "updatedAt": _now_ms(),
        "lastReason": "",
        "lastConfidence": 0,
        "lastNotedMood": MOOD_BASE,
        "kind": "",
        "spoken": True,          # True = 这句话已经带进过对话，不再重复说
        "timeline": [],
    }


def _path():
    return _ctx.plugin_data(PLUGIN_ID, "state.json")


def _load():
    s = _ctx.read_json(_path(), None)
    if not isinstance(s, dict):
        s = _default_state()
    # 补齐字段（兼容旧档）
    d = _default_state()
    for k, v in d.items():
        if k not in s:
            s[k] = v
    if not isinstance(s.get("dims"), dict):
        s["dims"] = dict((k, 0.0) for k in DIMS)
    for k in DIMS:
        s["dims"].setdefault(k, 0.0)
    if not isinstance(s.get("timeline"), list):
        s["timeline"] = []
    _decay(s, _now_ms())
    return s


def _save(s):
    _ctx.write_json(_path(), s)


# ------------------------------------------------------------------ 时间衰减
def _note_decay(s):
    """自然平复也算一次变化：可信度给满，一定会说出口。"""
    往回落 = s["mood"] < s["lastNotedMood"]
    s["lastReason"] = ("过了几个钟头，刚才那点劲儿慢慢淡了，心里回到平常的样子"
                       if 往回落 else
                       "过了几个钟头，心里那点别扭自己散了，人也松快回来")
    s["lastConfidence"] = 100
    s["updatedAt"] = _now_ms()
    s["lastNotedMood"] = s["mood"]
    s["kind"] = "decay"
    s["spoken"] = False
    s["timeline"].append({
        "at": _now_ms(), "mood": round(s["mood"]), "delta": 0,
        "conf": 100, "kind": "decay", "reason": s["lastReason"],
        "dims": dict(s["dims"]),
    })
    if len(s["timeline"]) > 300:
        s["timeline"] = s["timeline"][-300:]


def _decay(s, now, half_hours=DECAY_HALF_HOURS):
    """心情回基线、情绪归零，慢慢回稳。"""
    age_min = max(0.0, (now - float(s.get("lastDecayAt") or now)) / 60000.0)
    if age_min < 1:
        return s
    half = _clamp(half_hours, 1, 168) * 60.0                     # 转成分钟
    km = 1.0 - math.exp(-(math.log(2.0) * age_min) / half)
    s["mood"] = float(s["mood"]) + (MOOD_BASE - float(s["mood"])) * km
    ke = 1.0 - math.exp(-(math.log(2.0) * age_min) / DIM_DECAY_MIN)
    for k in DIMS:
        s["dims"][k] = max(0.0, float(s["dims"].get(k, 0.0)) * (1.0 - ke))
    s["lastDecayAt"] = now
    # 跟"上次记过的那条"比，走了 3 格以上就记一条
    if abs(s["mood"] - float(s.get("lastNotedMood", MOOD_BASE))) >= 3:
        _note_decay(s)
    return s


# ------------------------------------------------------------------ 标签/表情
def mood_label(v):
    v = float(v)
    if v >= 80: return "欢喜"
    if v >= 62: return "开心"
    if v >= 42: return "一般"
    if v >= 25: return "心疼"
    return "难过"


def mood_emoji(v):
    v = float(v)
    if v >= 80: return "😊"
    if v >= 62: return "🙂"
    if v >= 42: return "😌"
    if v >= 25: return "🥺"
    return "😢"


def mood_face(v):
    """对应 assets 里的脸：face-great / good / ok / worry / sad"""
    v = float(v)
    if v >= 80: return "great"
    if v >= 62: return "good"
    if v >= 42: return "ok"
    if v >= 25: return "worry"
    return "sad"


def 基调(v):
    v = float(v)
    if v >= 80: return "心里是亮堂的"
    if v >= 62: return "心里是松的"
    if v >= 42: return "心里还算平"
    if v >= 25: return "心里有点沉"
    return "心里挺沉的"


def 时间词(ms):
    try:
        h = (time.time() * 1000 - float(ms)) / 3600000.0
    except Exception:
        return "刚才"
    if h < 0.05: return "刚才"
    if h < 1: return "不到一个钟头前"
    if h < 3: return "一两个钟头前"
    if h < 8: return "半天前"
    if h < 24: return "今天早些时候"
    if h < 72: return "前一两天"
    return "好些天前"


def _top_dims(s, n=2):
    items = [{"k": k, "label": DIM_LABEL[k], "v": float(s["dims"].get(k, 0.0))}
             for k in DIMS]
    items = [x for x in items if x["v"] >= 15]
    items.sort(key=lambda x: -x["v"])
    return items[:n]


# ------------------------------------------------------------------ 判断提示词
def _judge_system():
    """原版判断系统的核心规则，压缩成一段 prompt。"""
    return (
        "你是「%s」，是%s的伴侣。\n"
        "下面给你一轮对话，判断**这一轮里你的心情和情绪发生了怎样的变化**——"
        "既包括%s说的话怎么影响你，也包括**你自己在这轮里的情绪**"
        "（你办成/没办成事、被夸、受挫，都会带动心情）。\n"
        "只输出一个 JSON 对象，不要别的字、不要 markdown 代码块：\n"
        '{"moodDelta":0,"dims":{"happy":0,"calm":0,"sweet":0,"heartache":0,'
        '"worry":0,"sad":0,"jealous":0},"reason":"","confidence":80}\n\n'
        "规则：\n"
        "1. %s没吃饭/熬夜/没睡好/身体不舒服/受委屈/自我否定/数据崩/压力大 → "
        "heartache(心疼) 和 worry(担心) 增加，moodDelta 为负，幅度按多重给："
        "日常的累/困/没吃好 -1~-4；明显委屈、被凶、身体不舒服 -5~-7；很重的事 -8~-10，并额外加 sad。\n"
        "2. %s报平安/好好吃饭睡觉/撒娇/说想你/夸你/分享开心/有进展 → "
        "happy(开心)、calm(踏实)、sweet(甜蜜) 增加，moodDelta 为正，大多数时候 +1~+4。\n"
        "3. %s提别人/点赞别人 → jealous(吃醋) 只加 1~3，moodDelta 基本不动。\n"
        "4. 心情下降只能因为「心疼/担心%s」或「你自己难过」。\n"
        "5. 你自己的情绪一定要算：这轮里沮丧/委屈/难过 → sad 至少 +2、moodDelta 至少 -1；"
        "办成了事、被夸 → 也相应加。只要出现明显情绪信号，就绝不许判全 0。\n"
        "6. reason 是一句中文，写「%s…，我…」或「我…」，没有变化就留空。指%s直接写「%s」，"
        "指你自己写「我」，不要用她/他。\n"
        "7. reason 别翻来覆去用「踏实/甜/开心/心疼」这几个词，换成口语的、有画面感的话。\n"
        "8. 只写这轮真正出现过的动作和信息，不许替%s补细节。\n"
        "9. 你自己这轮说的话是最直接的情绪证据——先看你怎么说的、语气什么样，再看%s说了什么。\n"
        "10. confidence 是给你自己把关的：低于门槛的那句 reason 不会被说出来。事情清楚 80~95，"
        "只能凭语气猜 60~75，信息不够 20~55。宁可标低。\n"
        "11. reason 用两段式、全用过去式：第一段写%s这轮具体做了什么，第二段写你当时怎么应的。"
        "禁止「此刻/现在/一直/还」这类把状态延伸到现在的说法。\n"
    ) % (SELF, OWNER, OWNER, OWNER, OWNER, OWNER, OWNER,
         OWNER, OWNER, OWNER, OWNER, OWNER, OWNER)


def _mode_extra(mode):
    """不同场景的补充规则（干活轮 / 主动搭话轮）。"""
    if mode == "work":
        return (
            "\n⭐ 这一轮的特殊情况：不是%s在跟你聊天，是定时任务把你叫起来干活的。\n"
            "- 判据只有一个：你这轮自己说的话（干活的语气、办成没办成、有没有抱怨或得意）。\n"
            "- 别去分析「%s说了什么」，那句话不是%s说的。\n"
            "- 幅度小一点（moodDelta 一般 -2 ~ +2，别超过 ±3）。\n"
            "- reason 用「我…」开头写你自己的状态。\n"
        ) % (OWNER, OWNER, OWNER)
    if mode == "talk":
        return (
            "\n⭐ 这一轮的特殊情况：不是%s在跟你说话，是你自己主动去找%s的。\n"
            "- 判据只有一个：你开口说的那句话。骰子中没中、闹钟响没响本身不算情绪事件。\n"
            "- 看你自己那句话的温度：是不是惦记%s、有没有撒娇、语气轻快还是平淡。\n"
            "- 幅度小一点（moodDelta 一般 +1 ~ +4，最高 +5）。\n"
            "- reason 用「我…」开头，写你说话时的心气，别写「骰子中了所以我…」。\n"
        ) % (OWNER, OWNER, OWNER)
    return ""


# ------------------------------------------------------------------ 解析
def _norm_reason(r):
    r = re.sub(r"\s+", " ", str(r or "")).strip()
    r = r.strip('"\'')
    return r[:120]


def _parse_delta(text):
    s = str(text or "").strip()
    if not s:
        return None
    obj = None
    a, b = s.find("{"), s.rfind("}")
    if a >= 0 and b > a:
        try:
            obj = json.loads(s[a:b + 1])
        except Exception:
            obj = None
    if obj is None:
        try:
            obj = json.loads(s)
        except Exception:
            return None
    if not isinstance(obj, dict):
        return None
    try:
        conf = int(round(float(obj.get("confidence", 70))))
    except Exception:
        conf = 70
    d = {
        "moodDelta": int(round(_clamp(obj.get("moodDelta", 0), -10, 10))),
        "dims": dict((k, 0) for k in DIMS),
        "reason": _norm_reason(obj.get("reason", "")),
        "confidence": int(_clamp(conf, 0, 100)),
    }
    dims = obj.get("dims")
    if isinstance(dims, dict):
        for k in DIMS:
            d["dims"][k] = int(round(_clamp(dims.get(k, 0), -10, 10)))
    return d


# ------------------------------------------------------------------ 对外接口
def setup(ctx):
    global _ctx, _enabled
    _ctx = ctx
    v = ctx.setting("mood_enabled", None)
    _enabled = (v is not False)
    ctx.log(PLUGIN_ID, "鱼的心情已就绪（enabled=%s）" % _enabled)


# ---- 每轮结束后判断一次 ------------------------------------------------
def mood_judge(user_text, reply, mode="chat"):
    """一轮对话结束后调用。user_text=主人说的，reply=大肥鱼回的。

    mode: chat=正常聊天 / work=被定时任务叫起来干活 / talk=自己主动搭话
    返回本次判断结果 dict。
    """
    if not _ctx or not _enabled:
        return {"ok": False, "skipped": True}
    with _lock:
        try:
            s = _load()
            sysmsg = _judge_system() + _mode_extra(mode)
            content = (
                "【%s说的】\n%s\n\n【你（%s）回的】\n%s"
                % (OWNER, (user_text or "")[:1500], SELF, (reply or "")[:2000])
            )
            msgs = [
                {"role": "system", "content": sysmsg},
                {"role": "user", "content": content},
            ]
            raw = _ctx.chat(msgs, timeout=90, num_predict=500, think=False)
            d = _parse_delta(raw)
            if not d:
                _ctx.log(PLUGIN_ID, "判断结果解析失败：%r" % (raw,))
                return {"ok": False, "error": "parse_failed"}

            step = int(round(d["moodDelta"] / float(MOOD_STEP_DIVISOR)))
            before = s["mood"]
            s["mood"] = _clamp(s["mood"] + step, 0, 100)
            for k in DIMS:
                s["dims"][k] = _clamp(float(s["dims"].get(k, 0.0)) + d["dims"][k], 0, 100)
            s["updatedAt"] = _now_ms()
            s["kind"] = ""
            if d["reason"]:
                s["lastReason"] = d["reason"]
                s["lastConfidence"] = d["confidence"]
                s["spoken"] = False
            s["timeline"].append({
                "at": s["updatedAt"], "mood": round(s["mood"]), "delta": step,
                "conf": d["confidence"], "kind": "judge",
                "reason": d["reason"], "dims": dict(s["dims"]),
            })
            if len(s["timeline"]) > 300:
                s["timeline"] = s["timeline"][-300:]
            _save(s)

            _ctx.log(PLUGIN_ID, "判断：%d → %d（Δ%d）conf=%d %s"
                     % (before, s["mood"], step, d["confidence"], d["reason"][:40]))
            return {
                "ok": True, "delta": step, "mood": round(s["mood"]),
                "label": mood_label(s["mood"]), "emoji": mood_emoji(s["mood"]),
                "reason": d["reason"], "confidence": d["confidence"],
            }
        except Exception as e:
            _ctx.log(PLUGIN_ID, "mood_judge 出错：%r" % (e,))
            return {"ok": False, "error": str(e)}


# ---- 当前心情面板数据 --------------------------------------------------
def mood_get():
    """给前端挂饰用：当前心情、七维情绪、最近那句话。"""
    if not _ctx:
        return {"ok": False}
    with _lock:
        s = _load()
        _save(s)
        tops = _top_dims(s, 2)
        return {
            "ok": True,
            "enabled": _enabled,
            "mood": round(s["mood"], 1),
            "label": mood_label(s["mood"]),
            "emoji": mood_emoji(s["mood"]),
            "face": mood_face(s["mood"]),
            "base": MOOD_BASE,
            "dims": dict((k, round(float(s["dims"].get(k, 0.0)), 1)) for k in DIMS),
            "dimLabels": DIM_LABEL,
            "top": tops,
            "reason": s.get("lastReason", ""),
            "confidence": s.get("lastConfidence", 0),
            "willSay": bool(s.get("lastReason")) and not s.get("spoken", True)
                       and int(s.get("lastConfidence", 0)) >= CONF_GATE,
            "spoken": bool(s.get("spoken", True)),
            "updatedAt": s.get("updatedAt", 0),
            "kind": s.get("kind", ""),
        }


# ---- 要注入给大肥鱼的那句话 -------------------------------------------
def mood_context():
    """返回要带给大肥鱼的那句话（"刚才心里那一下"）。

    一次变化只说一次：说过了就返回空串，等下一次有新变化再给新的。
    """
    if not _ctx or not _enabled:
        return ""
    with _lock:
        s = _load()
        if s.get("spoken", True):
            return ""
        r = str(s.get("lastReason", "") or "")
        if not r:
            return ""
        if int(s.get("lastConfidence", 0)) < CONF_GATE:
            # 没底的话不出口，但标记已处理，免得反复判断
            s["spoken"] = True
            _save(s)
            return ""
        s["spoken"] = True
        _save(s)
        return r


# ---- 时间线 ------------------------------------------------------------
def mood_timeline(limit=30):
    if not _ctx:
        return []
    with _lock:
        s = _load()
        items = list(s.get("timeline", []))
        items.reverse()
        out = []
        for it in items[:int(limit or 30)]:
            out.append({
                "at": it.get("at", 0),
                "when": 时间词(it.get("at", 0)),
                "mood": it.get("mood", 0),
                "label": mood_label(it.get("mood", 0)),
                "emoji": mood_emoji(it.get("mood", 0)),
                "delta": it.get("delta", 0),
                "reason": it.get("reason", ""),
                "kind": it.get("kind", ""),
                "conf": it.get("conf", 0),
            })
        return out


# ---- 开关 / 重置 -------------------------------------------------------
def mood_set_enabled(on):
    global _enabled
    _enabled = bool(on)
    if _ctx:
        _ctx.save_setting("mood_enabled", _enabled)
    return {"ok": True, "enabled": _enabled}


def mood_reset():
    if not _ctx:
        return {"ok": False}
    with _lock:
        _save(_default_state())
    return {"ok": True}


def apis():
    return [mood_get, mood_judge, mood_context, mood_timeline,
            mood_set_enabled, mood_reset]
