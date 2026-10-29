# -*- coding: utf-8 -*-
"""
主动搭话（本地化移植自 DSH 插件 dsh-speak-up）
==============================================

没人在跟大肥鱼说话的时候，它也有可能先开口。

照搬原版的核心机制：
- **掷骰子是纯本地随机，一个 token 都不花**；只有掷中之后真开口才走模型
- 那两个数不是「概率」而是**命中线**：每轮掷 0~100，掷到命中线**以下**才开口
- 最终命中线 = 档位命中线 × 时机因子 + min(加成上限, 连没中 × 没中加成)，最后夹 0~100
- 峰时（工作日 9-12、14-18）命中线 20，少开口；谷时命中线 60，多开口
- 刚说过话 → 命中线 ×0.15（更难开口）；太久没说话 → ×1.5（更容易开口）
- 每没掷中一次，命中线攒 +10，最多攒到 +40；掷中了就归零
- 安静时段（默认 1~8 点）不掷骰子
- 默认开着「试运行」：掷中了也只记账不真发，先看几轮再关掉
"""

import os
import json
import time
import random
import threading
import datetime

PLUGIN_ID = "speak_up"
PLUGIN_NAME = "主动搭话"
PLUGIN_ICON = "💬"
PLUGIN_VERSION = "1.0.0"

DEFAULTS = {
    "总开关": True,
    "试运行": True,          # 掷中了也只记账、不真发
    "间隔分钟": 30,
    "峰时命中线": 20,
    "谷时命中线": 60,
    "没中加成": 10,
    "加成上限": 40,
    "安静起": 1,             # 起 == 止 表示不启用安静时段
    "安静止": 8,
    "近阈值分钟": 20,
    "近因子": 0.15,
    "远阈值分钟": 360,
    "远因子": 1.5,
    "搭话冷却分钟": 20,
    "触发文案": "好久没人跟我说话了，我该主动去打个招呼",
}

_ctx = None
_lock = threading.RLock()
_state = None
_thread = None
_stop = threading.Event()
_cfg = dict(DEFAULTS)


# ---------------------------------------------------------------- 工具
def _now():
    return int(time.time())


def _default_state():
    return {
        "连没中": 0,
        "上次开口At": 0,      # 我们自己主动开口的时间
        "上次说话At": _now(),  # 大肥鱼最近一次说话（含正常聊天）
        "上次掷骰At": 0,
        "下次掷骰At": 0,
        "命中次数": 0,
        "掷骰次数": 0,
        "记录": [],           # 最近 50 条掷骰记录
    }


def _path():
    return _ctx.plugin_data(PLUGIN_ID, "state.json")


def _load():
    global _state
    if _state is None:
        s = _ctx.read_json(_path(), None)
        if not isinstance(s, dict):
            s = _default_state()
        d = _default_state()
        for k, v in d.items():
            s.setdefault(k, v)
        _state = s
    return _state


def _save():
    if _state is not None:
        _ctx.write_json(_path(), _state)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ---------------------------------------------------------------- 峰谷/安静
def _is_peak(dt):
    """峰时 = 周一到周五 9:00~12:00、14:00~18:00（其余全是谷时）"""
    if dt.weekday() >= 5:          # 5=周六 6=周日
        return False
    h = dt.hour + dt.minute / 60.0
    return (9.0 <= h < 12.0) or (14.0 <= h < 18.0)


def _in_quiet(dt):
    """安静时段，含起不含止，支持跨午夜（起 === 止 表示不启用）"""
    try:
        a = int(_cfg.get("安静起", 1))
        b = int(_cfg.get("安静止", 8))
    except Exception:
        return False
    if a == b:
        return False
    h = dt.hour
    if a < b:
        return a <= h < b
    return h >= a or h < b         # 跨午夜，比如 23~7


def _base_line(dt):
    return int(_cfg["峰时命中线"]) if _is_peak(dt) else int(_cfg["谷时命中线"])


def _time_factor(s, now):
    """刚说过话 → 更难开口；太久没说话 → 更容易开口"""
    last = float(s.get("上次说话At", 0) or 0)
    gap_min = (now - last) / 60.0 if last else 1e9
    if gap_min < float(_cfg["近阈值分钟"]):
        return float(_cfg["近因子"])
    if gap_min > float(_cfg["远阈值分钟"]):
        return float(_cfg["远因子"])
    return 1.0


def _final_line(s, now):
    dt = datetime.datetime.fromtimestamp(now)
    base = _base_line(dt)
    factor = _time_factor(s, now)
    bonus = min(float(_cfg["加成上限"]),
                int(s.get("连没中", 0)) * float(_cfg["没中加成"]))
    # 先乘因子、再加攒下来的加成，最后夹 0~100
    return int(round(_clamp(base * factor + bonus, 0, 100))), base, factor, int(bonus)


# ---------------------------------------------------------------- 掷骰
def _roll_once(reason="auto"):
    """掷一次骰子。返回记录 dict。纯本地随机，不花模型。"""
    s = _load()
    now = _now()
    dt = datetime.datetime.fromtimestamp(now)
    rec = {
        "at": now,
        "when": dt.strftime("%m-%d %H:%M"),
        "reason": reason,
        "quiet": False,
        "cooling": False,
        "line": 0, "base": 0, "factor": 1.0, "bonus": 0,
        "roll": None, "hit": False, "fired": False, "note": "",
    }

    # 安静时段
    if _in_quiet(dt):
        rec["quiet"] = True
        rec["note"] = "安静时段，不掷"
        _push_rec(s, rec)
        return rec

    # 自己刚开过口的冷却
    cool = float(_cfg.get("搭话冷却分钟", 20) or 0)
    last_fire = float(s.get("上次开口At", 0) or 0)
    if cool > 0 and last_fire and (now - last_fire) / 60.0 < cool:
        rec["cooling"] = True
        rec["note"] = "冷却中，不掷"
        _push_rec(s, rec)
        return rec

    line, base, factor, bonus = _final_line(s, now)
    roll = random.randint(0, 100)
    hit = roll < line                      # 掷到命中线以下才开口
    rec.update({"line": line, "base": base, "factor": round(factor, 2),
                "bonus": bonus, "roll": roll, "hit": hit})

    s["掷骰次数"] = int(s.get("掷骰次数", 0)) + 1
    s["上次掷骰At"] = now

    if hit:
        s["连没中"] = 0
        s["命中次数"] = int(s.get("命中次数", 0)) + 1
        dry = bool(_cfg.get("试运行", True))
        if dry:
            rec["note"] = "掷中了（试运行：只记账不真发）"
        else:
            s["上次开口At"] = now
            rec["fired"] = True
            rec["note"] = "掷中了，已触发开口"
            try:
                _ctx.emit("speak_up", {
                    "prompt": _cfg.get("触发文案", ""),
                    "at": now,
                    "line": line,
                    "roll": roll,
                })
            except Exception as e:
                rec["note"] = "触发失败：%r" % (e,)
    else:
        s["连没中"] = int(s.get("连没中", 0)) + 1
        rec["note"] = "没掷中（%d/%d）" % (roll, line)

    _push_rec(s, rec)
    _save()
    return rec


def _push_rec(s, rec):
    recs = s.get("记录")
    if not isinstance(recs, list):
        recs = []
    recs.append(rec)
    s["记录"] = recs[-50:]


# ---------------------------------------------------------------- 后台循环
def _loop():
    while not _stop.is_set():
        try:
            interval = max(1, int(_cfg.get("间隔分钟", 30) or 30))
        except Exception:
            interval = 30
        # 每 5 秒醒一次看有没有到点，方便配置即时生效
        s = _load()
        now = _now()
        next_at = int(s.get("下次掷骰At", 0) or 0)
        if next_at <= now:
            try:
                if bool(_cfg.get("总开关", True)):
                    _roll_once("auto")
                s = _load()
                s["下次掷骰At"] = _now() + interval * 60
                _save()
            except Exception as e:
                if _ctx:
                    _ctx.log(PLUGIN_ID, "掷骰出错：%r" % (e,))
        for _ in range(10):
            if _stop.is_set():
                return
            time.sleep(0.5)


def _coerce(key, value):
    """把设置值转成配置项该有的类型；转不出来返回 None（保持原默认）。"""
    dv = DEFAULTS.get(key)
    if dv is None:
        return None
    if isinstance(dv, bool):
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("1", "true", "yes", "on")
    if isinstance(dv, int):
        try:
            return int(value)
        except Exception:
            return None
    if isinstance(dv, float):
        try:
            return float(value)
        except Exception:
            return None
    return value


def setup(ctx):
    global _ctx, _thread
    _ctx = ctx
    # 读用户设置（没设过就用默认）
    for k, v in DEFAULTS.items():
        got = ctx.setting("speakup_" + k, None)
        if got is None:
            continue
        cv = _coerce(k, got)
        if cv is not None:
            _cfg[k] = cv
    _load()
    s = _state
    if not s.get("下次掷骰At"):
        s["下次掷骰At"] = _now() + int(_cfg["间隔分钟"]) * 60
        _save()
    if bool(_cfg.get("总开关", True)):
        _thread = threading.Thread(target=_loop, daemon=True)
        _thread.start()
    ctx.log(PLUGIN_ID, "主动搭话已就绪（间隔 %s 分钟，试运行=%s）"
            % (_cfg["间隔分钟"], _cfg["试运行"]))


def shutdown():
    _stop.set()


# ---------------------------------------------------------------- 对外接口
def speak_up_get():
    """当前状态：配置、下次掷骰时间、最近记录。"""
    if not _ctx:
        return {"ok": False}
    with _lock:
        s = _load()
        now = _now()
        line, base, factor, bonus = _final_line(s, now)
        dt = datetime.datetime.fromtimestamp(now)
        return {
            "ok": True,
            "config": dict(_cfg),
            "peak": _is_peak(dt),
            "quiet": _in_quiet(dt),
            "连没中": s.get("连没中", 0),
            "命中次数": s.get("命中次数", 0),
            "掷骰次数": s.get("掷骰次数", 0),
            "下次掷骰At": s.get("下次掷骰At", 0),
            "下次掷骰还有秒": max(0, int(s.get("下次掷骰At", 0)) - now),
            "当前命中线": line, "基础命中线": base,
            "时机因子": round(factor, 2), "没中加成": bonus,
            "上次说话At": s.get("上次说话At", 0),
            "上次开口At": s.get("上次开口At", 0),
            "记录": list(s.get("记录", []))[-20:][::-1],
        }


def speak_up_roll(dry=1):
    """手动掷一次（演练用）。dry=1 只掷不真发，dry=0 掷中真发。"""
    if not _ctx:
        return {"ok": False}
    with _lock:
        old = _cfg.get("试运行", True)
        _cfg["试运行"] = bool(dry)
        try:
            rec = _roll_once("manual")
        finally:
            _cfg["试运行"] = old
        _save()
        return {"ok": True, "rec": rec}


def speak_up_config(key, value):
    """改一项配置，立刻生效（不用重启）。key 用中文，和 speak_up_get 里的一致。"""
    global _cfg
    if not _ctx:
        return {"ok": False}
    k = str(key or "")
    if k not in DEFAULTS:
        return {"ok": False, "error": "没有这一项：%s" % k}
    # 按默认值的类型转一下
    v = _coerce(k, value)
    if v is None:
        return {"ok": False, "error": "值不对：%r" % (value,)}
    with _lock:
        _cfg[k] = v
        _ctx.save_setting("speakup_" + k, v)
        # 间隔改了就把下次掷骰时间重算
        if k == "间隔分钟":
            s = _load()
            s["下次掷骰At"] = _now() + max(1, int(v)) * 60
            _save()
    return {"ok": True, "key": k, "value": v}


def speak_up_note_spoke():
    """大肥鱼刚说过话（正常聊天也算）——记一下时间，影响时机因子。"""
    if not _ctx:
        return {"ok": False}
    with _lock:
        s = _load()
        s["上次说话At"] = _now()
        _save()
    return {"ok": True}


def speak_up_reset():
    if not _ctx:
        return {"ok": False}
    with _lock:
        global _state
        _state = _default_state()
        _save()
    return {"ok": True}


def apis():
    return [speak_up_get, speak_up_roll, speak_up_config,
            speak_up_note_spoke, speak_up_reset]
