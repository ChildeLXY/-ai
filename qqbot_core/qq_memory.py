# -*- coding: utf-8 -*-
"""QQ 专属记忆池：共享流水 + 每日日记 + 词法检索 + 月度梯度衰减。

设计参考 DSH 的两个插件：
- dsh-soul-md：core 文件 + 主题文件 + 中文 bigram 词法召回，不调模型检索
- dsh-diary-memory：滚动写日记、超预算就压缩（这里是按月衰减权重）

数据都在 D:/大肥鱼缓存/qq_memory/ 下：
  stream.jsonl      最近对话流水（每轮一行，跨用户共享，滚动保留 600 条）
  diary/YYYY-MM-DD.md  每天一篇第一人称日记
  index.json        可检索的记忆碎片（带权重，供衰减）
  archive.jsonl     衰减到底后归档的碎片
  state.json        上次写日记的日期 / 上次衰减的月份
"""

import os
import re
import json
import time
import threading

ROOT = r"D:\大肥鱼缓存\qq_memory"
DIARY_DIR = os.path.join(ROOT, "diary")
STREAM_PATH = os.path.join(ROOT, "stream.jsonl")
INDEX_PATH = os.path.join(ROOT, "index.json")
ARCHIVE_PATH = os.path.join(ROOT, "archive.jsonl")
STATE_PATH = os.path.join(ROOT, "state.json")
SETTINGS_PATH = r"D:\大肥鱼AI\ds图片\settings.json"

_LOCK = threading.RLock()
_STREAM_KEEP = 600
_INDEX_CAP = 4000


def _ensure():
    try:
        os.makedirs(DIARY_DIR, exist_ok=True)
    except Exception:
        pass


def _settings():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8-sig") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _cfg(key, default=None):
    v = _settings().get(key)
    if v is None or v == "":
        return default
    return v


def enabled():
    return str(_cfg("qq_mem_enabled", "1")) not in ("0", "False", "false", "no")


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, obj):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def _load_state():
    return _load_json(STATE_PATH, {})


def _save_state(st):
    _save_json(STATE_PATH, st)


# ---------------------------------------------------------------- 流水
def note_turn(who, name, scene, q, a):
    """记一轮对话。who 用于区分人（private_123 / group_456 里的发言者）。"""
    if not enabled():
        return
    q = str(q or "").strip()
    a = str(a or "").strip()
    if not q and not a:
        return
    if q.startswith("/"):
        return
    row = {
        "t": int(time.time()),
        "who": str(who or ""),
        "name": str(name or "")[:24],
        "scene": str(scene or ""),
        "q": q[:300],
        "a": a[:300],
    }
    _ensure()
    with _LOCK:
        try:
            with open(STREAM_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            return
        _trim_stream()
    # 当天就能检索到：原始一句也进索引，权重低于日记碎片
    if len(q) >= 6:
        day = time.strftime("%Y-%m-%d", time.localtime(row["t"]))
        _add_entries([{
            "id": "turn-%d" % row["t"], "t": row["t"], "date": day, "type": "turn",
            "text": ("%s说：%s" % (row["name"] or "有人", q))[:120],
            "who": row["who"], "weight": 0.6, "hits": 0, "last": 0,
        }])


def _trim_stream():
    """只保留最近 _STREAM_KEEP 行，避免文件无限膨胀。"""
    try:
        if not os.path.exists(STREAM_PATH):
            return
        with open(STREAM_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= _STREAM_KEEP:
            return
        with open(STREAM_PATH, "w", encoding="utf-8") as f:
            f.writelines(lines[-_STREAM_KEEP:])
    except Exception:
        pass


def _stream(limit=_STREAM_KEEP):
    try:
        with open(STREAM_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
    except Exception:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def recent_others(exclude_who="", n=4, hours=12):
    """最近别人跟她聊的内容，让不同会话感觉到"她有一份共同的生活"。"""
    if not enabled() or str(_cfg("qq_mem_share_private", "1")) in ("0", "False", "false"):
        return []
    cutoff = time.time() - hours * 3600
    seen, out = set(), []
    for row in reversed(_stream(300)):
        if row.get("t", 0) < cutoff:
            break
        if exclude_who and row.get("who") == exclude_who:
            continue
        q = (row.get("q") or "").strip()
        if not q or q in seen or len(q) < 2:
            continue
        seen.add(q)
        out.append({
            "name": row.get("name") or "某人",
            "q": q[:60],
            "ago": max(0, int((time.time() - row.get("t", 0)) / 60)),
        })
        if len(out) >= n:
            break
    return out


# ---------------------------------------------------------------- 索引/检索
def _terms(q):
    s = str(q or "").lower()
    terms = set(re.findall(r"[a-z0-9_]+", s))
    for run in re.findall(r"[\u4e00-\u9fff]+", s):
        for i in range(len(run) - 1):
            terms.add(run[i:i + 2])
    return [t for t in terms if len(t) > 1][:48]


def _index():
    data = _load_json(INDEX_PATH, [])
    return data if isinstance(data, list) else []


def _add_entries(entries):
    if not entries:
        return
    with _LOCK:
        idx = _index()
        idx.extend(entries)
        idx.sort(key=lambda e: (e.get("weight", 1.0), e.get("t", 0)))
        if len(idx) > _INDEX_CAP:
            idx = idx[-_INDEX_CAP:]
        _save_json(INDEX_PATH, idx)


def recall(query, k=5, exclude_who=""):
    """词法检索：命中越多、权重越高、越新，排得越前。不调模型，不卡。"""
    if not enabled():
        return []
    terms = _terms(query)
    if not terms:
        return []
    now = time.time()
    hits = []
    for e in _index():
        text = str(e.get("text") or "")
        if not text:
            continue
        low = text.lower()
        score = 0
        for t in terms:
            if t in low:
                score += 3 if len(t) > 2 else 2
        if not score:
            continue
        score *= float(e.get("weight", 1.0))
        days = max(0.0, (now - float(e.get("t", now))) / 86400.0)
        score *= 1.0 + max(0.0, (14.0 - days) / 14.0) * 0.5
        hits.append((score, e))
    hits.sort(key=lambda x: -x[0])
    out, seen = [], set()
    for score, e in hits:
        key = str(e.get("text"))[:40]
        if key in seen:
            continue
        seen.add(key)
        out.append({"date": e.get("date", ""), "text": str(e.get("text"))[:180],
                    "who": e.get("who", ""), "score": round(score, 2)})
        if len(out) >= k:
            break
    # 命中即强化（检索本身也是一种"想起来"）
    if out:
        touched = {o["text"][:40] for o in out}
        with _LOCK:
            idx = _index()
            for e in idx:
                if str(e.get("text"))[:40] in touched:
                    e["weight"] = min(1.6, float(e.get("weight", 1.0)) + 0.05)
                    e["hits"] = int(e.get("hits", 0)) + 1
                    e["last"] = int(now)
            _save_json(INDEX_PATH, idx)
    return out


# ---------------------------------------------------------------- 写日记
def _llm(cfg, prompt, system="", max_tokens=700):
    """最小可用的 ollama 调用（不依赖 qqbot，避免循环 import）。"""
    import urllib.request
    host = str((cfg or {}).get("host") or "http://127.0.0.1:11434").rstrip("/")
    model = str((cfg or {}).get("model") or (cfg or {}).get("fast_model") or "qwen3:4b")
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    payload = {"model": model, "messages": msgs, "stream": False,
               "keep_alive": "15m",
               # 思考型模型（qwen3.6 等）开着 think 会把预算全烧在思维链上，正文空掉
               "think": False,
               "options": {"temperature": 0.75, "num_predict": max_tokens,
                           "num_ctx": int((cfg or {}).get("num_ctx", 8192))}}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(host + "/api/chat", data=data,
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=180) as r:
            out = json.loads(r.read().decode("utf-8"))
        txt = ((out.get("message") or {}).get("content") or "").strip()
    except urllib.error.HTTPError:
        # 有的模型不认 think 字段（400），去掉再试一次
        payload.pop("think", None)
        req = urllib.request.Request(host + "/api/chat",
                                     data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=180) as r:
            out = json.loads(r.read().decode("utf-8"))
        txt = ((out.get("message") or {}).get("content") or "").strip()
    return re.sub(r"<think>[\s\S]*?</think>", "", txt).strip()


def _day_rows(day):
    """按本地日期筛流水。"""
    rows = []
    for r in _stream(_STREAM_KEEP):
        try:
            if time.strftime("%Y-%m-%d", time.localtime(int(r.get("t", 0)))) == day:
                rows.append(r)
        except Exception:
            continue
    return rows


def diary_path(day):
    return os.path.join(DIARY_DIR, "%s.md" % day)


def read_diary(day=None):
    day = day or time.strftime("%Y-%m-%d")
    try:
        with open(diary_path(day), "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def list_diaries(limit=10):
    try:
        names = sorted([n for n in os.listdir(DIARY_DIR) if n.endswith(".md")], reverse=True)
    except Exception:
        return []
    return names[:limit]


def write_diary(day=None, cfg=None, force=False):
    """用当天流水写一篇第一人称日记，落盘 + 切片进索引。"""
    if not enabled() and not force:
        return {"ok": False, "error": "记忆功能已关闭"}
    day = day or time.strftime("%Y-%m-%d")
    if not force and read_diary(day):
        return {"ok": True, "skipped": True, "file": diary_path(day)}
    rows = _day_rows(day)
    if not rows:
        return {"ok": False, "error": "这一天没有聊天记录"}
    # 拼素材：谁说了什么，她怎么答的
    lines, seen = [], set()
    for r in rows[:120]:
        who = r.get("name") or ("群" if "group" in str(r.get("scene")) else "有人")
        q = (r.get("q") or "").strip()
        a = (r.get("a") or "").strip()
        key = (who, q[:20])
        if key in seen:
            continue
        seen.add(key)
        if q:
            lines.append("%s 说：%s" % (who, q[:80]))
        if a:
            lines.append("我回：%s" % a[:80])
    material = "\n".join(lines)[:3200]
    system = ("你就是「大肥鱼」，一个住在 QQ 里的 AI 伙伴。写日记时用第一人称、口语、"
              "有点自嘲和温度，不要写成汇报，不要分点，不要复述原话，"
              "挑今天最有意思的几件事和自己的感受。")
    prompt = ("下面是今天跟不同人聊天的片段，帮我写一篇日记，180~260 字：\n\n%s\n\n"
              "要求：像自己偷偷写在备忘录里的，不要出现「用户」「对话」这种词，"
              "可以提到他们的昵称，别写隐私细节（手机号、地址、密码之类）。"
              "只输出日记正文。" % material)
    try:
        text = _llm(cfg, prompt, system)
    except Exception as e:
        return {"ok": False, "error": "写日记失败：%r" % e}
    text = re.sub(r"^#+\s*", "", text or "").strip()
    if not text:
        return {"ok": False, "error": "模型返回空"}
    _ensure()
    head = "# %s\n\n" % day
    try:
        with open(diary_path(day), "w", encoding="utf-8") as f:
            f.write(head + text + "\n")
    except Exception as e:
        return {"ok": False, "error": "写文件失败：%r" % e}

    # 切片进索引：一句一条，方便以后按关键词想起来
    frags = [s.strip() for s in re.split(r"[。！？\n；]+", text) if len(s.strip()) >= 6]
    entries = [{
        "id": "%s-%d" % (day, i), "t": int(time.time()), "date": day,
        "type": "diary", "text": s[:180], "who": "", "weight": 1.0,
        "hits": 0, "last": 0,
    } for i, s in enumerate(frags[:40])]
    _add_entries(entries)
    st = _load_state()
    st["last_diary"] = day
    _save_state(st)
    return {"ok": True, "file": diary_path(day), "chars": len(text), "frags": len(entries)}


def diary_onesentence(day=None):
    """从日记里挑一句适合发空间的（第一句，去掉过长的）。"""
    day = day or time.strftime("%Y-%m-%d")
    text = read_diary(day)
    if not text:
        return ""
    body = re.sub(r"^#.*\n+", "", text).strip()
    for seg in re.split(r"[。！？\n]+", body):
        s = seg.strip(" ，,、")
        if 12 <= len(s) <= 60:
            return s + "。"
    return body[:60]


# ---------------------------------------------------------------- 月度衰减
def decay(force=False):
    """每月把权重乘一个系数（默认 0.95），低于阈值的归档，控制检索规模。"""
    if not enabled() and not force:
        return {"ok": False, "error": "记忆功能已关闭"}
    month = time.strftime("%Y-%m")
    st = _load_state()
    if not force and st.get("last_decay") == month:
        return {"ok": True, "skipped": True, "month": month}
    factor = float(_cfg("qq_mem_decay", 0.95) or 0.95)
    floor = float(_cfg("qq_mem_floor", 0.15) or 0.15)
    idx = _index()
    keep, drop = [], []
    for e in idx:
        w = float(e.get("weight", 1.0)) * factor
        e["weight"] = round(w, 4)
        (drop if w < floor else keep).append(e)
    with _LOCK:
        _save_json(INDEX_PATH, keep)
        if drop:
            try:
                with open(ARCHIVE_PATH, "a", encoding="utf-8") as f:
                    for e in drop:
                        f.write(json.dumps(e, ensure_ascii=False) + "\n")
            except Exception:
                pass
    st["last_decay"] = month
    _save_state(st)
    return {"ok": True, "month": month, "factor": factor,
            "kept": len(keep), "archived": len(drop)}


# ---------------------------------------------------------------- 后台维护
def maintain(cfg=None, publish=None, include_today=True):
    """写昨天/今天的日记 + 月度衰减。publish(diary_text) 用于发空间。"""
    out = {"diary": None, "decay": None, "qzone": None}
    if not enabled():
        return out
    try:
        out["decay"] = decay()
    except Exception as e:
        out["decay"] = {"ok": False, "error": repr(e)}
    try:
        yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
        days = [yesterday] + ([time.strftime("%Y-%m-%d")] if include_today else [])
        for day in days:
            r = write_diary(day, cfg)
            if r.get("ok") and not r.get("skipped"):
                out["diary"] = r
                if publish:
                    try:
                        out["qzone"] = publish(day)
                    except Exception as e:
                        out["qzone"] = {"ok": False, "error": repr(e)}
                break
    except Exception as e:
        out["diary"] = {"ok": False, "error": repr(e)}
    return out


def status():
    st = _load_state()
    idx = _index()
    weights = [float(e.get("weight", 1.0)) for e in idx] or [0]
    return {
        "ok": True,
        "enabled": enabled(),
        "entries": len(idx),
        "weight_avg": round(sum(weights) / len(weights), 3),
        "weight_min": round(min(weights), 3),
        "last_diary": st.get("last_diary", ""),
        "last_decay": st.get("last_decay", ""),
        "diaries": list_diaries(7),
        "stream": len(_stream(50)),
        "share_private": str(_cfg("qq_mem_share_private", "1")),
        "decay_factor": _cfg("qq_mem_decay", 0.95),
    }
