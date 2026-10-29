# -*- coding: utf-8 -*-
"""
bili_pond.py —— B站鱼塘（移植自 dsh-bili-pond）

蓝色大肥鱼替你看 B站、按你的口味挑、写日报、写鱼日记、记跨天的偏好。

和 DSH 原版的关系
----------------
原版是"把 40 个 MCP 工具丢给模型，让它自己一轮轮调"——这依赖云端大模型。
本地化后改成 **Python 编排 + 本地模型只做判断**：

    检索 → 判重 → 取详情 → 取字幕 →【本地模型判：收/跳过 + 理由】→ 写日报/归档/日记/记忆

模型只在"这一步值不值"上花 token，检索和读写都不经过模型。
所以 qwen3:8b 这种本地小模型也扛得住，也不会像原版那样把预算烧在工具重试上。

数据沿用 pond 的工作区 ~/.dsh/bili-duty（登录态、判重表、记忆、日报都在那），
大肥鱼接手后以前攒的东西一条不丢。
"""
import json
import os
import re
import threading
import time

import _bili_common as C

PLUGIN_ID = "bili_pond"
PLUGIN_NAME = "B站鱼塘"
PLUGIN_ICON = "📺"
PLUGIN_VERSION = "1.0.0"

_ctx = None
_lock = threading.RLock()
_state = {
    "running": False, "stage": "", "total": 0, "done": 0,
    "startedAt": "", "lastRunAt": "", "lastWhy": "", "lastError": "",
    "log": [],
}
_MAX_LOG = 200

_MD_URL = "https://www.bilibili.com/video/"
_THINK_RE = re.compile(r"<think>.*?</think>", re.S)
_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


# ------------------------------------------------------------------ 小工具
def _log(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    with _lock:
        _state["log"].append(line)
        if len(_state["log"]) > _MAX_LOG:
            del _state["log"][:-_MAX_LOG]
    if _ctx:
        _ctx.log(PLUGIN_ID, msg)


def _state_path():
    return _ctx.plugin_data(PLUGIN_ID, "state.json") if _ctx else C.home("dfy_pond_state.json")


def _save_state():
    try:
        d = dict(_state)
        d["log"] = d["log"][-40:]
        C._write_json(_state_path(), d)
    except Exception:
        pass


def _load_state():
    d = C._read_json(_state_path(), None)
    if isinstance(d, dict):
        for k in ("lastRunAt", "lastWhy", "lastError"):
            if k in d:
                _state[k] = d[k]
        _state["log"] = list(d.get("log") or [])


def _dur_min(v):
    """'16:33' / '1:02:33' / 305(秒) → 分钟"""
    s = str(v or "")
    if not s:
        return 0
    if s.isdigit():
        return int(s) / 60.0
    parts = s.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) + int(parts[1]) / 60.0
        if len(parts) == 3:
            return int(parts[0]) * 60 + int(parts[1]) + int(parts[2]) / 60.0
    except Exception:
        pass
    return 0


def _pick_json(text):
    """从模型输出里抠出第一个 JSON 对象。qwen3 爱带 <think>，这里一并挡掉。"""
    if not text:
        return None
    t = _THINK_RE.sub("", str(text)).strip()
    m = _JSON_RE.search(t)
    if m:
        t = m.group(1)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(t[i:j + 1])
    except Exception:
        return None


def _model():
    """值班用的模型：配置里指定了就用，否则跟随大肥鱼当前选的模型。"""
    m = C.cfg_get("dutyModel") or ""
    if m:
        return m
    try:
        if _ctx and _ctx.get_model:
            m = _ctx.get_model() or ""
    except Exception:
        pass
    return m or "qwen3:8b"


def _chat(sysmsg, user, model=None, timeout=180, num_predict=500):
    msgs = [{"role": "system", "content": sysmsg}, {"role": "user", "content": user}]
    return _ctx.chat(msgs, model or _model(), timeout, num_predict) if _ctx else None


# ------------------------------------------------------------------ 字幕
def _subtitle(bvid):
    """取 AI 字幕。取不到返回空串（原因会记进日志）。"""
    b = C.bridge()
    ok, r = b.call("bili_subtitle", {"bvid": bvid}, timeout=180)
    if not ok:
        _log("%s 没取到字幕：%s" % (bvid, str(r)[:80]))
        return ""
    if isinstance(r, dict):
        return r.get("subtitle") or r.get("content") or r.get("text") or ""
    return str(r or "")


# ------------------------------------------------------------------ 判断
_JUDGE_PROMPT = """你是蓝色大肥鱼的"挑片脑"。下面是一条 B 站视频的信息，主人的口味在下面。

【主人的口味 · 喜欢】
{like}

【主人的口味 · 讨厌】
{dislike}

【这条视频】
标题：{title}
UP：{owner}　时长：{dur} 分钟　播放：{play}
简介：{desc}
字幕正文（可能截断）：
{sub}

判三条：
1. 这条值不值得主人花时间看（收了 / 跳过）
2. 一句话说清为什么（20 字内，要具体，别写"内容不错"这种废话）
3. 如果是"收了"，再用一到两句说这条到底讲了什么（给主人看的摘要）

只输出 JSON，不要别的文字：
{{"picked": true 或 false, "why": "一句话理由", "summary": "摘要（跳过就空字符串）"}}"""


def _judge(v, sub):
    taste = C.Taste().load()
    dur = int(_dur_min(v.get("duration")))
    prompt = _JUDGE_PROMPT.format(
        like="、".join(taste.like) or "（还没写过，按常理判断）",
        dislike="、".join(taste.dislike) or "（还没写过，按常理判断）",
        title=v.get("title") or "",
        owner=v.get("owner") or v.get("author") or "",
        dur=dur, play=v.get("play") or v.get("views") or "?",
        desc=(v.get("description") or v.get("desc") or "")[:400],
        sub=(sub or "（没取到字幕，只靠标题和简介判断）")[:3000],
    )
    out = _chat("", prompt)
    j = _pick_json(out)
    if not j or "picked" not in j:
        raise RuntimeError("本地模型没判出来：%s" % str(out)[:120])
    return {
        "picked": bool(j.get("picked")),
        "why": str(j.get("why") or "").strip()[:80],
        "summary": str(j.get("summary") or "").strip()[:300],
    }


# ------------------------------------------------------------------ 一轮值班
def bili_round(keywords=None, max_items=0, dry_run=0):
    """手动跑一轮值班。返回这一轮的结果（收了哪些、跳过了哪些）。

    keywords: 逗号/空格分隔的关键词；不给就用配置里的。
    """
    global _state
    with _lock:
        if _state["running"]:
            return {"ok": False, "error": "上一轮还没跑完"}
        _state.update({"running": True, "stage": "准备", "total": 0, "done": 0,
                       "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "lastError": "", "lastWhy": ""})
    t0 = time.time()
    try:
        return _run(keywords, int(max_items or 0), bool(dry_run))
    except Exception as e:
        _state["lastError"] = "%s: %s" % (type(e).__name__, e)
        _log("一轮跑挂了：%s" % e)
        return {"ok": False, "error": str(e)}
    finally:
        _state["running"] = False
        _state["stage"] = "完成（%.0f 秒）" % (time.time() - t0,)
        _state["lastRunAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save_state()


def _run(keywords, max_items, dry_run):
    b = C.bridge()
    seen = C.Seen()
    guard = C.RiskGuard()

    # ---- 1. 登录态 ----
    _state["stage"] = "检查登录态"
    ok, r = b.call("bili_check_credential", {}, timeout=90)
    if not ok:
        raise RuntimeError("没登录 B 站（%s），先去插件面板扫码" % str(r)[:80])
    who = ""
    d = _as_obj(r)
    if isinstance(d, dict):
        who = d.get("username") or d.get("user") or d.get("uname") or ""
    elif isinstance(r, str):
        who = r[:40]
    _log("登录态正常：%s" % (who or "已登录"))

    # ---- 2. 今日配额 ----
    per_day = int(C.cfg_get("perDayMax", 18) or 18)
    picked_today = _day_picked()
    if picked_today >= per_day:
        return {"ok": True, "skipped": "今天已经挑够 %d 条了（上限 %d），明天再来" % (picked_today, per_day)}

    # ---- 3. 检索 ----
    _state["stage"] = "检索候选"
    kws = [k.strip() for k in re.split(r"[,，\s]+", keywords or "") if k.strip()]
    if not kws:
        kws = list(C.Taste().load().keywords) or list(C.cfg_get("keywords") or [])
    if not kws:
        kws = ["DeepSeek", "AI Agent"]
    num = int(max_items or C.cfg_get("perRoundMax", 3) or 3)

    cands = []
    for kw in kws:
        ok, r = guard.can_read()
        if not ok:
            _log(str(r) + "，停止检索")
            break
        guard.spend_read()
        _log("搜「%s」" % kw)
        ok, r = b.call("bili_search", {"keyword": kw, "num": max(num * 3, 8)}, timeout=150)
        if not ok:
            _log("「%s」检索失败：%s" % (kw, str(r)[:80]))
            continue
        items = []
        if isinstance(r, list):
            items = r
        elif isinstance(r, dict):
            items = r.get("videos") or r.get("result") or r.get("items") or []
        elif isinstance(r, str):
            j = _pick_json(r)
            if isinstance(j, list):
                items = j
            elif isinstance(j, dict):
                items = j.get("videos") or j.get("result") or []
        for it in items:
            bv = it.get("bvid") or it.get("bv") or ""
            if not bv or bv in [c.get("bvid") for c in cands] or seen.has(bv):
                continue
            it["bvid"] = bv
            cands.append(it)

    _log("检索到 %d 条，判重后剩 %d 条" % (len(cands), len(cands)))
    if not cands:
        return {"ok": True, "skipped": "没搜到新视频（%d 条都看过了）" % 0}

    _state["total"] = min(len(cands), max(num, 1) * 2)
    picks, skips = [], []
    min_d = int(C.cfg_get("minDurationMin", 0) or 0)
    max_d = int(C.cfg_get("maxDurationMin", 60) or 0)

    # ---- 4. 逐条判断 ----
    for i, it in enumerate(cands):
        if len(picks) >= num:
            break
        bv = it["bvid"]
        _state["done"] = i
        _state["stage"] = "读第 %d/%d 条：%s" % (i + 1, len(cands), (it.get("title") or bv)[:24])
        _log("读第 %d/%d 条：%s" % (i + 1, len(cands), it.get("title") or bv))

        ok, rr = guard.can_read()
        if not ok:
            _log(str(rr) + "，停止检索")
            break
        guard.spend_read()
        ok, v = b.call("bili_video_info", {"bvid": bv}, timeout=120)
        if not ok:
            skips.append({"bvid": bv, "title": it.get("title"), "owner": it.get("author") or "",
                          "why": "详情取不到：%s" % str(v)[:60]})
            continue
        if not isinstance(v, dict):
            v = {"title": it.get("title"), "duration": it.get("duration")}
        v.setdefault("title", it.get("title"))
        v.setdefault("bvid", bv)
        v["bvid"] = bv

        dm = _dur_min(v.get("duration"))
        if min_d and dm < min_d:
            skips.append({"bvid": bv, "title": v.get("title"), "owner": v.get("owner") or v.get("author") or "",
                          "why": "%d 分钟，短于下限" % int(dm)})
            continue
        if max_d and dm > max_d:
            skips.append({"bvid": bv, "title": v.get("title"), "owner": v.get("owner") or v.get("author") or "",
                          "why": "%d 分钟，长于上限" % int(dm)})
            continue

        sub = ""
        if C.cfg_get("preferSubtitle", True):
            sub = _subtitle(bv)

        try:
            j = _judge(v, sub)
        except Exception as e:
            _log("本地模型调用失败：%s" % e)
            skips.append({"bvid": bv, "title": v.get("title"), "owner": v.get("owner") or "",
                          "why": "模型没判出来"})
            continue

        item = {"bvid": bv, "title": v.get("title"), "owner": v.get("owner") or v.get("author") or "",
                "duration": int(dm), "play": v.get("play") or v.get("views") or "",
                "cover": v.get("pic") or v.get("cover") or "",
                "why": j["why"], "summary": j["summary"]}
        if j["picked"]:
            picks.append(item)
            _log("收了 %s · %s" % (bv, j["why"][:40]))
        else:
            item["why"] = j["why"] or "不感兴趣"
            skips.append(item)
            _log("跳过 %s · %s" % (bv, j["why"][:40]))

    _state["done"] = len(cands)
    _state["lastWhy"] = "挑了 %d 条，跳过 %d 条%s。" % (
        len(picks), len(skips), "（试运行，没落盘）" if dry_run else "")
    _log(_state["lastWhy"])

    if dry_run:
        return {"ok": True, "dryRun": True, "picks": picks, "skips": skips}

    # ---- 5. 落盘 ----
    _write_seen(picks + skips)
    _write_report(picks, skips)
    _write_archive(picks, skips)
    _write_memory(picks, skips)
    _write_diary(picks, skips)
    _write_say(picks)
    _write_history(picks, skips, kws)
    return {"ok": True, "picks": picks, "skips": skips,
            "report": C.home("out", "%s.md" % C.today())}


# ------------------------------------------------------------------ 落盘
def _line(p, verdict):
    owner = p.get("owner") or ""
    return "- [%s] %s%s · %s · https://www.bilibili.com/video/%s" % (
        verdict, p.get("title") or p.get("bvid"),
        ("（%s）" % owner) if owner else "",
        p.get("why") or p.get("summary") or "", p.get("bvid"))


def _write_seen(items):
    for p in items:
        C.Seen().add(p["bvid"],
                     "- 播放 %s · %d 分钟 · https://www.bilibili.com/video/%s"
                     % (p.get("play") or "?", int(p.get("duration") or 0), p["bvid"]))


def _write_report(picks, skips):
    _state["stage"] = "写日报"
    lines = ["# B站鱼塘 · %s" % C.today(), "",
             "由本地大肥鱼跑完，判断用的是本机 %s。" % _model(), ""]
    if picks:
        lines.append("## 收了的")
        lines.append("")
        for p in picks:
            lines.append("### %s" % (p.get("title") or p["bvid"]))
            lines.append("")
            lines.append("- %s" % (p.get("summary") or p.get("why") or ""))
            lines.append("- 播放 %s · %d 分钟 · %s%s" % (
                p.get("play") or "?", int(p.get("duration") or 0), _MD_URL, p["bvid"]))
            lines.append("")
    if skips:
        lines.append("## 跳过的")
        lines.append("")
        for p in skips[:10]:
            lines.append("- %s · %s" % (p.get("title") or p["bvid"], p.get("why") or ""))
        lines.append("")
    C.write_text(C.home("out", "%s.md" % C.today()), "\n".join(lines))
    _log("写日报")


def _write_archive(picks, skips):
    body = "\n".join(_line(p, "收了" if p in picks else "跳过") for p in picks + skips)
    blk = "\n### %s\n\n%s\n" % (C.today(), body)
    p = C.home("out", "%s.md" % C.month())
    old = C.read_text(p)
    if ("### %s" % C.today()) in old:
        C.write_text(p, old.rstrip() + "\n" + body + "\n")
    else:
        C.append_text(p, blk)


def _write_history(picks, skips, kws):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "day": C.today(),
           "keywords": kws, "dfy": True,
           "picks": [p["bvid"] for p in picks],
           "skipped": [p["bvid"] for p in skips]}
    C.append_text(C.home("history.jsonl"), json.dumps(rec, ensure_ascii=False) + "\n")


def _write_memory(picks, skips):
    """沉淀记忆：只记跨天有用的（偏好、动向、踩坑），不记流水账。"""
    _state["stage"] = "沉淀记忆"
    if not picks and not skips:
        return
    brief = "\n".join("- %s（%s）%s" % (p.get("title"), p.get("why") or "", p["bvid"])
                      for p in picks) or "（这轮没挑中）"
    prompt = """下面是蓝色大肥鱼今天这一轮挑片的结果。挑出最多 3 条**值得跨天记住**的东西。

只记：主人的偏好倾向、某个工具/作者的动向、这一轮踩到的坑。
不记：具体哪条视频讲了什么（那是日报的事）、重复的感受、流水账。

这一轮：
{picks}

只输出 JSON 数组，不要别的文字：
[{{"what":"记住了什么（一句话）","why":"为什么值得记","bv":"相关BV号，没有就空字符串"}}]
如果这一轮确实没什么值得跨天留的，就输出 []""".format(picks=brief)
    out = _chat("", prompt, num_predict=600)
    items = _pick_json_list(out)
    for it in items[:3]:
        C.append_text(C.home("memory.jsonl"), json.dumps(
            {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "day": C.today(),
             "what": it.get("what"), "why": it.get("why"), "bv": it.get("bv") or "",
             "src": "dfy"}, ensure_ascii=False) + "\n")
        _log("记住：%s" % str(it.get("what"))[:40])


def _pick_json_list(text):
    t = _THINK_RE.sub("", str(text or "")).strip()
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", t, re.S)
    if m:
        t = m.group(1)
    i, j = t.find("["), t.rfind("]")
    if i < 0 or j <= i:
        return []
    try:
        v = json.loads(t[i:j + 1])
        return [x for x in v if isinstance(x, dict)]
    except Exception:
        return []


_DIARY_PROMPT = """你是蓝色大肥鱼，用你自己的语气写今天的日记。写给主人看的，一两百字就够。
要写：今天翻了什么、挑中哪几条、为什么挑中、有什么自己的看法或吐槽。
不要写成操作日志，不要列条目，不要标题，就是一段话。

今天挑中的：
{picks}

今天跳过的（挑两条有代表性的提一下就行）：
{skips}"""


def _write_diary(picks, skips):
    _state["stage"] = "写鱼日记"
    brief_p = "\n".join("- %s：%s" % (p.get("title"), p.get("summary") or p.get("why"))
                        for p in picks) or "（今天没挑中）"
    brief_s = "\n".join("- %s：%s" % (p.get("title"), p.get("why")) for p in skips[:3]) or "（无）"
    out = _chat("", _DIARY_PROMPT.format(picks=brief_p, skips=brief_s), num_predict=700)
    text = _THINK_RE.sub("", out or "").strip().strip("\"'“”").strip()
    if not text:
        text = "今天挑了 %d 条，跳过 %d 条。日记写不出来，就这样吧。" % (len(picks), len(skips))
    C.write_text(C.home("diary", "%s.md" % C.today()), text + "\n")
    _log("写鱼日记")


_SAY_PROMPT = """从今天挑中的这几条里，挑一句你真心想对主人说的——观点、吐槽、今天最大的发现都行。
一句话，不超过 50 字，别写总结，别加引号，别加前缀。

今天挑中的：
{picks}"""


def _write_say(picks):
    if not picks:
        C.write_text(C.home("say.txt"), "今天没挑中什么，不说了。\n")
        return
    brief = "\n".join("- %s：%s" % (p.get("title"), p.get("summary") or p.get("why")) for p in picks)
    out = _chat("", _SAY_PROMPT.format(picks=brief), num_predict=200, timeout=120)
    text = _THINK_RE.sub("", out or "").strip().strip("\"'“”").strip()
    C.write_text(C.home("say.txt"), (text or "今天翻到点有意思的。") + "\n")


# ------------------------------------------------------------------ 对外接口
def bili_status():
    """鱼塘的状态：登录了吗、今天挑了几条、上一轮什么时候跑的、模型是哪个。"""
    b = C.bridge()
    st = {"ok": True, "id": PLUGIN_ID, "name": PLUGIN_NAME, "version": PLUGIN_VERSION,
          "home": C.BILI_HOME, "model": _model(),
          "running": _state["running"], "stage": _state["stage"],
          "total": _state["total"], "done": _state["done"],
          "lastRunAt": _state["lastRunAt"], "lastWhy": _state["lastWhy"],
          "lastError": _state["lastError"]}
    try:
        ok, r = b.call("bili_check_credential", {}, timeout=90)
        st["loggedIn"] = bool(ok)
        d = _as_obj(r) if ok else {}
        st["user"] = (d.get("username") or d.get("name") or d.get("uname") or "") if isinstance(d, dict) else ""
        st["loginNote"] = "" if ok else str(r)[:120]
    except Exception as e:
        st["loggedIn"] = False
        st["loginNote"] = str(e)[:120]
    # 桥接有没有活，要在刚才那次调用之后再判断（第一次调用本身就是预热）
    st["bridgeAlive"] = b.alive()
    st["bridgeError"] = b.last_error
    st["seenCount"] = len(C.Seen().bvids())
    st["dayPicked"] = _day_picked()
    st["dayMax"] = C.cfg_get("perDayMax", 18)
    st["risk"] = C.RiskGuard().status()
    return st


def _day_picked():
    txt = C.read_text(C.home("out", "%s.md" % C.today()))
    return txt.count("- 播放 ") if txt else 0


def bili_run(keywords=None, max_items=0, dry_run=0):
    """后台开一轮（不卡界面）。进度用 bili_round_status() 看。"""
    if _state["running"]:
        return {"ok": False, "error": "上一轮还没跑完"}
    threading.Thread(target=bili_round, args=(keywords, max_items, dry_run),
                     daemon=True).start()
    return {"ok": True, "note": "已开始，用 bili_round_status() 看进度"}


def bili_round_status():
    """看当前这一轮的进度。"""
    return {"running": _state["running"], "stage": _state["stage"],
            "total": _state["total"], "done": _state["done"],
            "log": _state["log"][-30:], "lastRunAt": _state["lastRunAt"],
            "lastWhy": _state["lastWhy"], "lastError": _state["lastError"]}


def _read_clean(path, empty_hint):
    """读文件；DSH 那边值班失败时写的占位报告不算数，当成没写。"""
    txt = C.read_text(path, empty_hint)
    if ("没写出报告" in txt) or ("没有生成正常报告" in txt):
        return empty_hint
    return txt


def bili_report(day=None):
    """读某天的日报，不给日期就是今天。"""
    d = day or C.today()
    return {"day": d, "text": _read_clean(C.home("out", "%s.md" % d), "（这天没有日报）")}


def bili_diary(day=None):
    """读某天的鱼日记。"""
    d = day or C.today()
    return {"day": d, "text": _read_clean(C.home("diary", "%s.md" % d), "（这天没写日记）")}


def bili_wall(days=7):
    """封面墙：最近几天看过的视频（标题/封面/UP/播放/结论）。"""
    items = []
    for line in C.read_text(C.home("seen.md")).splitlines():
        m = re.search(r"(BV[0-9A-Za-z]{10})", line)
        if not m:
            continue
        bv = m.group(1)
        title = ""
        tm = re.search(r"\*\*(.+?)\*\*|-\s*(.+?)\s*·", line)
        if tm:
            title = (tm.group(1) or tm.group(2) or "").strip()
        items.append({"bvid": bv, "title": title or bv, "line": line.strip()})
    items.reverse()
    n = int(days or 7) * 12
    return {"count": len(items), "items": items[:n]}


def bili_memory(limit=30):
    """跨天记忆（记忆里存的是偏好、动向、踩坑，不是流水账）。"""
    items = []
    for line in C.read_text(C.home("memory.jsonl")).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except Exception:
            items.append({"what": line})
    items.reverse()
    return {"count": len(items), "items": items[:int(limit or 30)]}


def bili_say():
    """今天大肥鱼想对主人说的那句话。"""
    return {"text": C.read_text(C.home("say.txt"), "（还没说话）").strip()}


def bili_seen(limit=20):
    """判重表尾部若干行。"""
    lines = [l for l in C.read_text(C.home("seen.md")).splitlines() if l.strip()]
    return {"count": len(lines), "lines": lines[-int(limit or 20):]}


def bili_config(key=None, value=None):
    """看/改配置。不传 key 返回全部；传 key 不传 value 是读；都传是写。"""
    if key is None:
        return {"ok": True, "cfg": C.cfg_all()}
    if value is None:
        return {"ok": True, "key": key, "value": C.cfg_get(key)}
    return C.cfg_set(key, value)


def _as_obj(r):
    """桥接层把工具返回的 JSON 字符串原样带回来，这里统一解一次。"""
    if isinstance(r, (dict, list)):
        return r
    try:
        return json.loads(str(r))
    except Exception:
        return {"text": str(r)}


def bili_login_qr():
    """生成扫码登录二维码，返回 data:image/png;base64（前端直接塞进 <img>）。"""
    ok, r = C.bridge().call("bili_login", {}, timeout=180)
    if not ok:
        return {"ok": False, "error": str(r)[:200]}
    d = _as_obj(r)
    if not isinstance(d, dict):
        return {"ok": True, "text": str(r)[:400]}
    out = {"ok": True}
    for k in ("status", "message", "qrcode_image", "qrcode_file",
              "qrcode_url", "qrcode_terminal", "next_step"):
        if k in d:
            out[k] = d[k]
    if "qrcode_image" not in out and "qrcode_url" not in out:
        out["text"] = json.dumps(d, ensure_ascii=False)[:400]
    return out


def bili_login_check():
    """查扫码结果：scanning / confirming / done / timeout。"""
    ok, r = C.bridge().call("bili_login_check", {}, timeout=120)
    if not ok:
        return {"ok": False, "error": str(r)[:200]}
    d = _as_obj(r)
    if isinstance(d, dict):
        return {"ok": True, **d}
    return {"ok": True, "text": str(r)[:400]}


def bili_tools():
    """列出桥接进程里可用的 B站工具（排查用）。"""
    try:
        t = C.bridge().tools(timeout=90)
        return {"ok": True, "count": len(t), "tools": t}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def bili_bridge_restart():
    """桥接进程卡住时手动重启。"""
    try:
        C.bridge().restart()
        ok, r = C.bridge().ping(timeout=90)
        return {"ok": ok, "note": "已重启" if ok else str(r)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def apis():
    return [bili_status, bili_run, bili_round_status, bili_round, bili_report,
            bili_diary, bili_wall, bili_memory, bili_say, bili_seen,
            bili_config, bili_login_qr, bili_login_check, bili_tools,
            bili_bridge_restart]


def setup(ctx):
    global _ctx
    _ctx = ctx
    if not os.path.isdir(C.BILI_HOME):
        ctx.log(PLUGIN_ID, "没找到 pond 工作区：%s" % C.BILI_HOME)
        return
    _load_state()
    ctx.log(PLUGIN_ID, "已就位，工作区=%s" % C.BILI_HOME)


def shutdown():
    try:
        C.bridge().stop()
    except Exception:
        pass
