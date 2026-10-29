# -*- coding: utf-8 -*-
"""
bili_whale.py —— 养鱼（移植自 dsh-bili-whale）

给大肥鱼装一条"有日子的鱼"：按作息醒、看得懂才回、**最多回几次**，撞风控就退避。

本地化的三个关键取舍
--------------------
1. **默认全关**。原版说得很直白：长时间高频轮询是小号被风控的头号原因。
   所以监听开关默认 false，要主人自己在面板里打开。
2. **先本地过滤，再花 token**。水评、广告、太短的、回过的——这些用规则挡掉，
   只有真正值得回的才去问本地模型。模型只负责"这句话该怎么回"。
3. **风控闸门是真的**。读写预算、写冷却、撞 412/429 退避 5/10/20/30 分钟，
   全部照抄原版的保守默认值，写操作**真的会被挡住**，不是写在文档里好看。

遥控：主人在手机上给小号发私信「# 状态」，大肥鱼扫到就执行并回一条。
"""
import json
import os
import re
import threading
import time

import _bili_common as C

PLUGIN_ID = "bili_whale"
PLUGIN_NAME = "B站养鱼"
PLUGIN_ICON = "🐋"
PLUGIN_VERSION = "1.0.0"

_ctx = None
_lock = threading.RLock()
_cfg = {}
_thread = None
_stop_flag = False
_last = {"lastPollAt": "", "lastPollNote": "", "repliedToday": 0}

DEFAULTS = {
    "listenDm": False,
    "listenComment": False,
    "pollMinutes": 15,
    "onlyWindow": True,
    "replyEnabled": False,
    "replyPerDay": 3,
    "replyPerRound": 1,
    "minCommentLen": 4,
    "whitelist": "",
    "tone": "",
    "ownerUid": "",
}

# 水评 / 广告：这些用规则挡掉，不花 token
_SPAM_RE = re.compile(r"(加微|加v|威信|微信[:：\s]*[a-zA-Z0-9_-]{5,}|代做|代写|代练|刷单|"
                      r"兼职日结|低价|优惠券|私信我|联系我|扫码|公众号|QQ群|免费领|点击链接|www\.|http)", re.I)
# 纯表情/纯标点/纯数字，没有信息量
_NOISE_RE = re.compile(r"^[\s\W_\d]+$")
_THINK_RE = re.compile(r"<think>.*?</think>", re.S)

_REPLY_PROMPT = """你是蓝色大肥鱼，在 B 站评论区回一句话。

【对方说】
{text}

【这条视频】{title}

【口气】{tone}

要求：一句话，不超过 40 字，像真人说话，别客套、别@对方、别带链接、别写"感谢分享"。
不知道说什么就说实话，别硬编。只输出这句回复本身，不要引号、不要解释。"""


# ------------------------------------------------------------------ 配置
def _cfg_path():
    return _ctx.plugin_data(PLUGIN_ID, "config.json") if _ctx else C.home("dfy_whale.json")


def _replied_path():
    return _ctx.plugin_data(PLUGIN_ID, "replied.json") if _ctx else C.home("dfy_replied.json")


def _load_cfg():
    global _cfg
    _cfg = dict(DEFAULTS)
    d = C._read_json(_cfg_path(), None)
    if isinstance(d, dict):
        for k, v in d.items():
            if k in DEFAULTS:
                _cfg[k] = v
    # 主人的 UID：没填过就从 whale 配置里的 owner.mid / self.mid 推
    if not _cfg.get("ownerUid"):
        w = C.whale_cfg()
        _cfg["ownerUid"] = str((w.get("owner") or {}).get("mid")
                               or (w.get("self") or {}).get("mid") or "")


def _save_cfg():
    C._write_json(_cfg_path(), _cfg)


def _replied():
    d = C._read_json(_replied_path(), None) or {}
    if d.get("day") != C.today():
        d = {"day": C.today(), "rpid": []}
    return d


def _mark_replied(rpid):
    d = _replied()
    if rpid and rpid not in d["rpid"]:
        d["rpid"].append(rpid)
    C._write_json(_replied_path(), d)
    _last["repliedToday"] = len(d["rpid"])


def _log(msg):
    if _ctx:
        _ctx.log(PLUGIN_ID, msg)


def _model():
    m = _cfg.get("dutyModel") or C.cfg_get("dutyModel") or ""
    if m:
        return m
    try:
        if _ctx and _ctx.get_model:
            m = _ctx.get_model() or ""
    except Exception:
        pass
    return m or "qwen3:8b"


def _chat(prompt, timeout=180, num_predict=300):
    msgs = [{"role": "system", "content": "你是蓝色大肥鱼，一只会在 B 站说话的鱼。"},
            {"role": "user", "content": prompt}]
    if not _ctx:
        return None
    out = _ctx.chat(msgs, _model(), timeout, num_predict)
    if out:
        out = _THINK_RE.sub("", out).strip().strip("\"'“”").strip()
    return out


# ------------------------------------------------------------------ 作息窗口
def _windows():
    cfg = C.whale_cfg()
    wins = (cfg.get("night") or {}).get("windows") or []
    out = []
    for w in wins:
        s, e = str(w.get("start") or ""), str(w.get("end") or "")
        if s:
            out.append((s, e or "24:00"))
    return out


def _window_text():
    return " / ".join("%s-%s" % w for w in _windows()) or "（没设）"


def _in_window(now=None):
    """现在在不在作息窗口里。没设窗口就当全天。"""
    wins = _windows()
    if not wins:
        return True
    now = now or time.localtime()
    m = now.tm_hour * 60 + now.tm_min

    def _t(s):
        try:
            h, mi = s.split(":")[:2]
            return int(h) * 60 + int(mi)
        except Exception:
            return 0

    for s, e in wins:
        a, b = _t(s), _t(e if e != "24:00" else "23:59")
        if a <= m <= b:
            return True
    return False


# ------------------------------------------------------------------ 评论过滤
def _score(c):
    """给一条评论打分，决定值不值得回。规则挡掉的不花 token。"""
    text = str(c.get("content") or c.get("text") or "")
    uid = str(c.get("uid") or c.get("mid") or c.get("user_id") or "")
    rpid = c.get("rpid") or c.get("id")
    reasons, score = [], 0

    if rpid and rpid in _replied()["rpid"]:
        return 0, ["回过了"]
    if len(text.strip()) < int(_cfg.get("minCommentLen") or 4):
        return 0, ["太短"]
    if _SPAM_RE.search(text):
        return 0, ["像广告/水评"]
    if _NOISE_RE.match(text.strip()):
        return 0, ["没内容"]

    wl = [x.strip() for x in str(_cfg.get("whitelist") or "").replace(",", " ").split() if x.strip()]
    if uid and uid in wl:
        score += 100
        reasons.append("白名单")
    # @ 了本人：guard_scan 会给 at_list / mentionUid
    ats = c.get("at_list") or c.get("at") or []
    if isinstance(ats, (list, tuple)):
        ats = [str(x) for x in ats]
    else:
        ats = [str(ats)]
    mine = str((C.whale_cfg().get("self") or {}).get("mid") or "")
    if mine and (mine in ats or str(c.get("mentionUid") or "") == mine):
        score += 60
        reasons.append("@ 了我")
    if _cfg.get("ownerUid") and uid == str(_cfg["ownerUid"]):
        score += 60
        reasons.append("主人")
    if len(text.strip()) >= 12:
        score += 20
        reasons.append("有话说")
    if "？" in text or "?" in text:
        score += 15
        reasons.append("在问")
    return score, reasons


_WORTH_MIN = 40


# ------------------------------------------------------------------ 私信遥控
def _handle_cmd(text, from_uid):
    """主人在私信里发「# xxx」遥控。返回要回给主人的话。"""
    cmd = text.lstrip("#").strip()
    if not cmd or cmd in ("帮助", "help", "?"):
        return ("能听懂的指令：# 状态、# 心情、# 鱼塘、# 日记、# 闭嘴、# 说话、# 风控。"
                "直接说「# 状态」就行。")
    if cmd in ("状态", "status"):
        r = C.RiskGuard().status()
        return ("醒了没=%s｜窗口 %s｜今天回了 %d 条｜读 %d/%d，写 %d/%d%s"
                % ("醒着" if _thread else "没监听", _window_text(),
                   len(_replied()["rpid"]),
                   r["read"]["hour"], r["read"]["hourMax"],
                   r["write"]["daily"], r["write"]["dailyMax"],
                   "｜⚠️风控退避 %d 秒" % r["blockedLeftSec"] if r["blocked"] else ""))
    if cmd in ("心情", "mood"):
        try:
            import sys
            m = sys.modules.get("dfy_plugin_feiyu_mood")
            if m and hasattr(m, "mood_get"):
                d = m.mood_get()
                return "现在心情 %s（%s）｜%s" % (
                    d.get("face") or "", int(d.get("mood") or 0), d.get("why") or "")
        except Exception:
            pass
        return "心情插件没起来。"
    if cmd in ("鱼塘", "pond", "今天"):
        try:
            import sys
            m = sys.modules.get("dfy_plugin_bili_pond")
            if m:
                return "今天挑了 %d 条。%s" % (m._day_picked(), (m.bili_say() or {}).get("text") or "")
        except Exception:
            pass
        return "鱼塘插件没起来。"
    if cmd in ("日记", "diary"):
        try:
            import sys
            m = sys.modules.get("dfy_plugin_bili_pond")
            if m:
                return (m.bili_diary() or {}).get("text", "")[:180] or "今天还没写日记。"
        except Exception:
            pass
        return "鱼塘插件没起来。"
    if cmd in ("闭嘴", "shutup", "安静"):
        try:
            import sys
            m = sys.modules.get("dfy_plugin_speak_up")
            if m and hasattr(m, "speak_up_config"):
                m.speak_up_config("试运行", 1)
        except Exception:
            pass
        return "好，我闭嘴（只记账不真说）。"
    if cmd in ("说话", "开口", "speak"):
        try:
            import sys
            m = sys.modules.get("dfy_plugin_speak_up")
            if m and hasattr(m, "speak_up_config"):
                m.speak_up_config("试运行", 0)
        except Exception:
            pass
        return "那我说话了。"
    if cmd in ("风控", "risk"):
        r = C.RiskGuard().status()
        return json.dumps(r, ensure_ascii=False)
    return "没听懂「%s」。发「# 帮助」看看我会什么。" % cmd


# ------------------------------------------------------------------ 一轮轮询
def _poll_once(manual=False):
    b = C.bridge()
    guard = C.RiskGuard()
    note = []

    if not manual and _cfg.get("onlyWindow") and not _in_window():
        return {"ok": True, "skipped": "不在作息窗口（%s），跳过" % _window_text()}
    if not _cfg.get("listenDm") and not _cfg.get("listenComment"):
        return {"ok": True, "skipped": "两个监听都关着，先开一个再开始"}

    # ---- 私信 ----
    dm_done = []
    if _cfg.get("listenDm"):
        ok, r = guard.can_read()
        if not ok:
            note.append("私信读不了：%s" % r)
        else:
            guard.spend_read()
            ok, r = b.call("bili_dm_inbox", {"limit": 10}, timeout=150)
            if ok:
                items = r if isinstance(r, list) else ((r or {}).get("items") if isinstance(r, dict) else [])
                for it in items or []:
                    text = str(it.get("content") or it.get("text") or "")
                    uid = it.get("uid") or it.get("user_id") or it.get("mid")
                    if not text.startswith("#"):
                        continue
                    ans = _handle_cmd(text, uid)
                    if _cfg.get("ownerUid") and uid:
                        ok2, why = guard.can_dm()
                        if ok2:
                            ok3, rr = b.call("bili_send_message",
                                             {"receiver_uid": int(uid), "text": ans}, timeout=120)
                            if ok3:
                                guard.spend_dm()
                                dm_done.append({"cmd": text[:40], "reply": ans})
                            else:
                                note.append("发私信失败：%s" % str(rr)[:60])
                        else:
                            note.append(why)
                    else:
                        dm_done.append({"cmd": text[:40], "reply": ans})

    # ---- 评论 ----
    scanned, replied = [], []
    if _cfg.get("listenComment"):
        ok, r = guard.can_read()
        if not ok:
            note.append("评论读不了：%s" % r)
        else:
            guard.spend_read()
            ok, r = b.call("bili_guard_scan", {"max_pages": 3}, timeout=180)
            if ok:
                items = r if isinstance(r, list) else ((r or {}).get("comments")
                                                       or (r or {}).get("items") or [])
                scored = []
                for c in items or []:
                    s, why = _score(c)
                    if s >= _WORTH_MIN:
                        scored.append((s, c, why))
                scored.sort(key=lambda x: -x[0])
                for s, c, why in scored:
                    scanned.append({"rpid": c.get("rpid") or c.get("id"),
                                    "bvid": c.get("bvid"), "score": s,
                                    "why": "/".join(why),
                                    "text": str(c.get("content") or c.get("text") or "")[:120]})
                    if len(replied) >= int(_cfg.get("replyPerRound") or 1):
                        break
                    if len(_replied()["rpid"]) >= int(_cfg.get("replyPerDay") or 3):
                        note.append("今天回够了（上限 %s）" % _cfg.get("replyPerDay"))
                        break
                    if not _cfg.get("replyEnabled"):
                        note.append("回复开关没开（replyEnabled=关），先打开才真发")
                        break
                    ok2, why2 = guard.can_write()
                    if not ok2:
                        note.append("写预算/冷却挡住了")
                        break
                    ans = _chat(_REPLY_PROMPT.format(
                        text=str(c.get("content") or c.get("text") or "")[:400],
                        title=str(c.get("title") or c.get("bvid") or ""),
                        tone=_cfg.get("tone") or "你自己的语气，像个真在看视频的人"))
                    if not ans:
                        note.append("本地模型没回出来")
                        continue
                    ok3, rr = b.call("bili_reply", {
                        "bvid": c.get("bvid"), "text": ans,
                        "rpid": int(c.get("rpid") or c.get("id") or 0)}, timeout=150)
                    if ok3:
                        guard.spend_write()
                        _mark_replied(c.get("rpid") or c.get("id"))
                        replied.append({"rpid": c.get("rpid"), "text": ans})
                    else:
                        note.append("写回复失败：%s" % str(rr)[:60])

    _last["lastPollAt"] = time.strftime("%H:%M")
    _last["lastPollNote"] = "；".join(note) or ("处理了 %d 条指令：%s" % (
        len(dm_done), "、".join(d.get("cmd", "") for d in dm_done)) if dm_done else "没事")
    _last["repliedToday"] = len(_replied()["rpid"])
    _log("轮询：%s" % _last["lastPollNote"])
    return {"ok": True, "dm": dm_done, "scanned": scanned, "replied": replied,
            "note": _last["lastPollNote"], "at": _last["lastPollAt"]}


def _loop():
    while not _stop_flag:
        try:
            _poll_once()
        except Exception as e:
            _log("轮询出错：%s" % e)
        for _ in range(int(_cfg.get("pollMinutes") or 15) * 60):
            if _stop_flag:
                return
            time.sleep(1)


# ------------------------------------------------------------------ 对外接口
def whale_status():
    """养鱼的状态：监听开关、作息窗口、风控、今天回了几条。"""
    r = C.RiskGuard().status()
    return {"ok": True, "id": PLUGIN_ID, "name": PLUGIN_NAME, "version": PLUGIN_VERSION,
            "listening": bool(_thread and _thread.is_alive()),
            "window": _window_text(), "inWindow": _in_window(),
            "ownerUid": _cfg.get("ownerUid") or "",
            "cfg": dict(_cfg), "risk": r,
            "repliedToday": len(_replied()["rpid"]),
            "lastPollAt": _last["lastPollAt"], "lastPollNote": _last["lastPollNote"],
            "model": _model()}


def whale_config(key=None, value=None):
    """看/改配置。不传 key 返回全部；传 key 不传 value 是读；都传是写。

    只认白名单里的键，类型不对会直接报错，免得配出个四不像。
    """
    if key is None:
        return {"ok": True, "cfg": dict(_cfg)}
    if key not in DEFAULTS:
        return {"ok": False, "error": "没有这个配置项：%s" % key}
    if value is None:
        return {"ok": True, "key": key, "value": _cfg.get(key)}
    dv = DEFAULTS[key]
    try:
        if isinstance(dv, bool):
            v = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
        elif isinstance(dv, int):
            v = int(value)
        else:
            v = "" if value is None else str(value)
    except Exception as e:
        return {"ok": False, "error": "值不对：%r" % (e,)}
    with _lock:
        _cfg[key] = v
        _save_cfg()
    return {"ok": True, "key": key, "value": v}


def whale_poll(manual=1):
    """手动跑一轮：扫私信指令 + 扫评论 + 该回的回。"""
    try:
        return _poll_once(manual=bool(manual))
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def whale_start():
    """开始后台监听（默认只在作息窗口里动）。"""
    global _thread, _stop_flag
    with _lock:
        if _thread and _thread.is_alive():
            return {"ok": True, "note": "已经在听了"}
        if not _cfg.get("listenDm") and not _cfg.get("listenComment"):
            return {"ok": False, "error": "两个监听都关着，先开一个再开始"}
        _stop_flag = False
        _thread = threading.Thread(target=_loop, daemon=True)
        _thread.start()
    _log("养鱼已就位（监听默认关：私信=%s 评论=%s）"
         % (_cfg.get("listenDm"), _cfg.get("listenComment")))
    return {"ok": True, "note": "已开始，每 %s 分钟一轮" % _cfg.get("pollMinutes")}


def whale_stop():
    """停止后台监听。"""
    global _thread, _stop_flag
    _stop_flag = True
    with _lock:
        _thread = None
    return {"ok": True, "note": "停止监听"}


def whale_dm(limit=10):
    """收件箱里最近的私信。"""
    ok, r = C.bridge().call("bili_dm_inbox", {"limit": int(limit or 10)}, timeout=150)
    if not ok:
        return {"ok": False, "error": str(r)[:200]}
    items = r if isinstance(r, list) else ((r or {}).get("items") if isinstance(r, dict) else [])
    return {"ok": True, "items": items or []}


def whale_send_dm(uid, text):
    """给某人发私信（走风控闸门）。"""
    guard = C.RiskGuard()
    ok, why = guard.can_dm()
    if not ok:
        return {"ok": False, "error": why}
    ok, r = C.bridge().call("bili_send_message",
                            {"receiver_uid": int(uid), "text": str(text)}, timeout=120)
    if ok:
        guard.spend_dm()
        return {"ok": True}
    return {"ok": False, "error": str(r)[:200]}


def whale_scan():
    """只扫描评论区，不回复。看看现在有什么值得回的。"""
    guard = C.RiskGuard()
    ok, why = guard.can_read()
    if not ok:
        return {"ok": False, "error": why}
    guard.spend_read()
    ok, r = C.bridge().call("bili_guard_scan", {"max_pages": 3}, timeout=180)
    if not ok:
        return {"ok": False, "error": str(r)[:200]}
    items = r if isinstance(r, list) else ((r or {}).get("comments")
                                           or (r or {}).get("items") or [])
    out = []
    for c in items or []:
        s, why = _score(c)
        out.append({"rpid": c.get("rpid") or c.get("id"), "bvid": c.get("bvid"),
                    "score": s, "why": "/".join(why) or "不值得",
                    "worth": s >= _WORTH_MIN,
                    "text": str(c.get("content") or c.get("text") or "")[:160]})
    out.sort(key=lambda x: -x["score"])
    return {"ok": True, "count": len(out), "items": out}


def whale_reply(bvid, rpid, text=None):
    """手动回一条评论。不给 text 就让本地模型想一句。"""
    guard = C.RiskGuard()
    ok, why = guard.can_write()
    if not ok:
        return {"ok": False, "error": why}
    if not text:
        text = _chat(_REPLY_PROMPT.format(
            text="（手动回复）", title=bvid,
            tone=_cfg.get("tone") or "你自己的语气，像个真在看视频的人"))
    if not text:
        return {"ok": False, "error": "本地模型没回出来"}
    ok, r = C.bridge().call("bili_reply", {"bvid": bvid, "text": text,
                                           "rpid": int(rpid or 0)}, timeout=150)
    if not ok:
        return {"ok": False, "error": str(r)[:200]}
    guard.spend_write()
    _mark_replied(rpid)
    return {"ok": True, "text": text}


def whale_unblock():
    """风控退避时手动解除（确认没事了再用）。"""
    C.RiskGuard().clear_block()
    return {"ok": True, "note": "已解除退避"}


def apis():
    return [whale_status, whale_config, whale_poll, whale_start, whale_stop,
            whale_dm, whale_send_dm, whale_scan, whale_reply, whale_unblock]


def setup(ctx):
    global _ctx
    _ctx = ctx
    _load_cfg()
    _last["repliedToday"] = len(_replied()["rpid"])
    ctx.log(PLUGIN_ID, "已就位（监听默认关：私信=%s 评论=%s）"
            % (_cfg.get("listenDm"), _cfg.get("listenComment")))


def shutdown():
    global _stop_flag
    _stop_flag = True
