# -*- coding: utf-8 -*-
"""
大肥鱼哈基米表情包（本地化移植自 DSH 插件 dsh-nekowhale-stickers）
==================================================================

322 张表情包，每张都标注了使用场景、画面描述、梗出处和情绪标签。

本地化的做法：
- **索引全本地**：标注数据合并好后落到 data/ 下，检索纯本地算，一个 token 不花；
- **图片按需下载**：第一次用到某张图才去 kiriyuki.com 拉，下载完就永久存在本机，
  之后再用就是纯本地文件，断网也能显示；
- 也提供 sticker_sync 一键把整个包镜像到本地（约 200MB，看自己硬盘）。
"""

import os
import re
import json
import time
import threading
import urllib.request
import urllib.parse

PLUGIN_ID = "nekowhale_stickers"
PLUGIN_NAME = "大肥鱼表情包"
PLUGIN_ICON = "🐱"
PLUGIN_VERSION = "1.0.0"

SITE = "https://kiriyuki.com"
GIF_BASE = "/media/gif"
UA = "DaFeiYuAI-nekowhale-stickers (+local)"

# DSH 那边的原始素材（用来首次生成索引，之后不再依赖它）
_DSH_META = os.path.join(
    os.path.expanduser("~"), ".dsh", "profiles", "desktop", "node_modules",
    "dsh-nekowhale-stickers", "meta", "nekowhale.json")
_DSH_MANIFEST = os.path.join(
    os.path.expanduser("~"), ".dsh", "nekowhale-stickers", "manifest.json")

_ctx = None
_lock = threading.RLock()
_index = None          # 合并后的完整索引
_by_id = {}
_downloading = set()


# ---------------------------------------------------------------- 工具
def _now():
    return int(time.time())


def _id_of(it):
    return it.get("id", "")


def _safe_name(s):
    return re.sub(r"[^\w\u4e00-\u9fff\-.]", "_", str(s or ""))


# ---------------------------------------------------------------- 索引
def _dsh_dir():
    """DSH 插件素材目录（首次建索引时用）。"""
    return os.path.dirname(_DSH_META)


def _build_index():
    """把标注（meta）和资源清单（manifest）合并成一份本地索引。"""
    meta = None
    mani = None
    for p in (_DSH_META,):
        try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    meta = json.load(f)
        except Exception:
            meta = None
    for p in (_DSH_MANIFEST,):
        try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    mani = json.load(f)
        except Exception:
            mani = None

    if not isinstance(meta, dict) or not isinstance(meta.get("stickers"), list):
        return None

    mani_map = {}
    if isinstance(mani, dict) and isinstance(mani.get("items"), list):
        for m in mani["items"]:
            mani_map[m.get("id")] = m

    items = []
    for s in meta["stickers"]:
        sid = s.get("id", "")
        if not sid:
            continue
        m = mani_map.get(sid, {})
        pack = m.get("pack") or (sid.split("/")[0] if "/" in sid else "")
        file_ = m.get("file")
        if not file_:
            stem = sid.split("/")[-1]
            file_ = stem + ".gif"
        items.append({
            "id": sid,
            "pack": pack,
            "name": m.get("name") or (sid.split("/")[-1].split("_")[-1]),
            "code": m.get("code", ""),
            "emotion": s.get("emotion", "") or "",
            "tags": list(s.get("tags") or []),
            "use": s.get("use", "") or "",
            "desc": s.get("desc", "") or "",
            "meme": s.get("meme", "") or "",
            "dir": s.get("dir", "") or "",
            "text": s.get("text", "") or "",
            "en": s.get("en", "") or "",
            "file": file_,
            "hash": s.get("hash") or m.get("hash", ""),
            "bytes": m.get("bytes", 0) or 0,
            "w": m.get("w", 0) or 0,
            "h": m.get("h", 0) or 0,
        })
    if not items:
        return None
    return {
        "version": (mani or {}).get("version", "") or meta.get("version", ""),
        "built": _now(),
        "total": len(items),
        "emotions": list(meta.get("emotions") or []),
        "items": items,
    }


def _index_path():
    return _ctx.plugin_data(PLUGIN_ID, "index.json")


def _load_index():
    global _index, _by_id
    if _index is not None:
        return _index
    _index = _ctx.read_json(_index_path(), None)
    if not isinstance(_index, dict) or not isinstance(_index.get("items"), list):
        _index = _build_index()
        if _index:
            _ctx.write_json(_index_path(), _index)
            _ctx.log(PLUGIN_ID, "索引已生成：%d 张" % _index["total"])
    if isinstance(_index, dict):
        _by_id = dict((it["id"], it) for it in _index.get("items", []))
    return _index


def _gif_dir():
    d = os.path.join(_ctx.plugin_data(PLUGIN_ID, "x"), "..")
    d = os.path.normpath(d)
    d = os.path.join(d, "gif")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _local_path(it):
    """本地缓存路径：<data>/plugins/nekowhale_stickers/gif/<hash>.gif"""
    h = it.get("hash") or _safe_name(it.get("id", ""))
    return os.path.join(_gif_dir(), "%s.gif" % h)


def _remote_url(it):
    return "%s%s/%s/%s?h=%s" % (
        SITE, GIF_BASE,
        urllib.parse.quote(it.get("pack", "")),
        urllib.parse.quote(it.get("file", "")),
        it.get("hash", ""))


def _download(it, timeout=30):
    """把一张图拉到本地。返回本地路径，失败返回 ''。"""
    p = _local_path(it)
    if os.path.exists(p) and os.path.getsize(p) > 0:
        return p
    url = _remote_url(it)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        if not data:
            return ""
        tmp = p + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
        os.rename(tmp, p)
        return p
    except Exception as e:
        _ctx.log(PLUGIN_ID, "下载失败 %s: %r" % (it.get("id"), e))
        return ""


# ---------------------------------------------------------------- 检索打分
def _tokens(q):
    q = str(q or "").lower()
    parts = re.split(r"[\s,，。、；;！!？?\.~/|]+", q)
    out = [p for p in parts if p]
    # 中文长串再切 2 字片段，提高召回
    extra = []
    for p in out:
        if len(p) >= 3 and re.match(r"^[\u4e00-\u9fff]+$", p):
            extra += [p[i:i + 2] for i in range(len(p) - 1)]
    return out + extra


def _score(it, toks, emotion=""):
    if not toks and not emotion:
        return 0.0
    sc = 0.0
    name = str(it.get("name", "")).lower()
    emo = str(it.get("emotion", "")).lower()
    tags = " ".join(it.get("tags") or []).lower()
    use = str(it.get("use", "")).lower()
    desc = str(it.get("desc", "")).lower()
    meme = str(it.get("meme", "")).lower()
    text = str(it.get("text", "")).lower()

    if emotion and emo == str(emotion).lower():
        sc += 8.0

    for t in toks:
        if not t:
            continue
        if t == name:
            sc += 10.0
        elif t in name:
            sc += 5.0
        if t in tags:
            sc += 6.0
        if t == emo:
            sc += 7.0
        elif t and t in emo:
            sc += 3.0
        if t in use:
            sc += 3.0
        if t in text:
            sc += 2.5
        if t in meme:
            sc += 2.0
        if t in desc:
            sc += 1.0
    return sc


# ---------------------------------------------------------------- 对外接口
def setup(ctx):
    global _ctx
    _ctx = ctx
    idx = _load_index()
    if isinstance(idx, dict):
        ctx.log(PLUGIN_ID, "表情包就绪：%d 张" % idx.get("total", 0))
    else:
        ctx.log(PLUGIN_ID, "没找到 DSH 素材，索引未生成")


def sticker_search(query, emotion="", limit=6):
    """按语境检索表情包。纯本地算分，不调模型。

    query 可以是「期待好事」「想吃白饭」这种短句，也可以是「搓手」这种关键词。
    """
    if not _ctx:
        return []
    idx = _load_index()
    if not isinstance(idx, dict):
        return []
    toks = _tokens(query)
    try:
        n = int(limit or 6)
    except Exception:
        n = 6
    scored = []
    for it in idx.get("items", []):
        sc = _score(it, toks, emotion)
        if sc > 0:
            scored.append((sc, it))
    scored.sort(key=lambda x: -x[0])
    out = []
    for sc, it in scored[:max(1, min(n, 30))]:
        lp = _local_path(it)
        out.append({
            "id": it["id"], "code": it.get("code", ""), "name": it["name"], "pack": it["pack"],
            "emotion": it["emotion"], "tags": it["tags"],
            "use": it["use"], "desc": it["desc"], "meme": it["meme"],
            "file": it["file"], "hash": it["hash"],
            "cached": os.path.exists(lp), "score": round(sc, 2),
        })
    return out


def sticker_local(sticker_id):
    """拿到某张图的本地路径；本地没有就下载一次（之后永久本地）。

    返回 dict: {ok, path, url, cached}
    """
    if not _ctx:
        return {"ok": False, "error": "no ctx"}
    idx = _load_index()
    if not isinstance(idx, dict):
        return {"ok": False, "error": "索引未生成"}
    it = _by_id.get(sticker_id)
    if not it:
        # 也允许按 name / code 模糊找
        for x in idx.get("items", []):
            if x.get("name") == sticker_id or x.get("code") == sticker_id:
                it = x
                break
    if not it:
        return {"ok": False, "error": "找不到这张表情包"}
    lp = _local_path(it)
    if os.path.exists(lp) and os.path.getsize(lp) > 0:
        return {"ok": True, "path": lp, "cached": True, "url": ""}
    with _lock:
        if it.get("hash") in _downloading:
            return {"ok": False, "error": "正在下载中", "url": _remote_url(it)}
        _downloading.add(it.get("hash"))
    try:
        p = _download(it)
    finally:
        _downloading.discard(it.get("hash"))
    if p:
        return {"ok": True, "path": p, "cached": False, "url": ""}
    return {"ok": False, "error": "下载失败", "url": _remote_url(it)}


def sticker_hint(text, limit=4):
    """给一段话，返回候选表情包的提示文本——可以直接塞进 prompt 让大肥鱼自己挑。

    返回的每行形如：[10N01] 猫猫搓手 — 期待好事发生或迫不及待想试试时
    """
    if not _ctx:
        return ""
    idx = _load_index()
    if not isinstance(idx, dict):
        return ""
    res = sticker_search(text, "", limit)
    if not res:
        return ""
    lines = []
    for r in res:
        code = r.get("name") or r.get("code")   # 用中文名当代号：模型照抄最准，反查也稳
        use = (r.get("use") or r.get("desc") or "").strip()
        # 去掉描述开头的重复名字，避免 "[摸鱼] 摸鱼 — ..." 这种冗余
        name = r.get("name") or ""
        if name and use.startswith(name):
            use = use[len(name):].lstrip(" —-·、")
        lines.append("[%s] %s" % (code, use[:40] or name))
    return "\n".join(lines)


def sticker_sync(limit=0):
    """把表情包镜像到本地（后台任务，可选）。limit=0 表示全量。

    注意：322 张大约 200MB，硬盘紧张就别跑全量，按需下载其实够用。
    """
    if not _ctx:
        return {"ok": False}
    idx = _load_index()
    if not isinstance(idx, dict):
        return {"ok": False, "error": "索引未生成"}
    items = idx.get("items", [])
    try:
        n = int(limit or 0)
    except Exception:
        n = 0
    if n > 0:
        items = items[:n]

    result = {"ok": True, "total": len(items), "done": 0, "failed": 0}

    def _worker():
        done = failed = 0
        for it in items:
            try:
                p = _download(it)
                if p:
                    done += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
        _ctx.log(PLUGIN_ID, "镜像完成：成功 %d 失败 %d" % (done, failed))
        try:
            _ctx.emit("stickers.synced", {"done": done, "failed": failed})
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()
    return result


def sticker_stats():
    """统计：总数、已缓存多少、占多少硬盘。"""
    if not _ctx:
        return {"ok": False}
    idx = _load_index()
    if not isinstance(idx, dict):
        return {"ok": False, "error": "索引未生成", "total": 0}
    d = _gif_dir()
    cached = 0
    size = 0
    try:
        for fn in os.listdir(d):
            if fn.endswith(".gif"):
                cached += 1
                try:
                    size += os.path.getsize(os.path.join(d, fn))
                except Exception:
                    pass
    except Exception:
        pass
    return {
        "ok": True, "total": idx.get("total", 0), "cached": cached,
        "bytes": size, "mb": round(size / 1048576.0, 1),
        "gifDir": d, "emotions": idx.get("emotions", []),
    }


def sticker_emotions():
    idx = _load_index() if _ctx else None
    if not isinstance(idx, dict):
        return []
    return list(idx.get("emotions", []))


# ---------------------------------------------------------------- 两段式选图
_PICK_SYS = ("你是表情包挑选器。看这段对话，从候选里挑一张最贴的图，"
             "只输出它的序号数字。只输出数字，不要解释、不要标点、不要任何其他文字。")


def sticker_pick(user_text, ai_text="", limit=4, model=None):
    """让模型从候选里挑一张（独立的一次极短推理，不干扰正文生成）。

    为什么单独做一次：让模型在长回复里自己写代号，7B 模型经常漏写或写崩；
    拆成"只挑一个序号"后准确率高很多，代价只有零点几秒。

    返回 {ok, id, name, path, cached} 或 {ok: False, error}
    """
    if not _ctx:
        return {"ok": False, "error": "no ctx"}
    q = ("%s %s" % (user_text or "", ai_text or "")).strip()
    if not q:
        return {"ok": False, "error": "没内容"}
    try:
        n = max(2, min(int(limit or 4), 8))
    except Exception:
        n = 4
    cand = sticker_search(q, "", n)
    if not cand:
        return {"ok": False, "error": "没有候选"}

    lst = "\n".join(
        "%d. [%s] %s" % (i + 1, c.get("name"), (c.get("use") or c.get("desc") or "")[:26])
        for i, c in enumerate(cand))
    msg = ("对话：\n用户：%s\n我：%s\n\n候选：\n%s\n\n最贴的是第几个？只回数字。"
           % ((user_text or "")[:160], (ai_text or "")[:260], lst))
    out = None
    try:
        out = _ctx.chat(
            [{"role": "system", "content": _PICK_SYS},
             {"role": "user", "content": msg}],
            model=model, timeout=25, num_predict=8, think=False)
    except Exception as e:
        return {"ok": False, "error": "选图失败: %r" % (e,)}
    m = re.search(r"[1-%d]" % len(cand), str(out or ""))
    if not m:
        return {"ok": False, "error": "模型没给出序号", "raw": str(out or "")[:40]}
    it = cand[int(m.group()) - 1]
    loc = sticker_local(it["id"])
    if not loc.get("ok"):
        return {"ok": False, "error": loc.get("error") or "取图失败", "id": it["id"]}
    return {"ok": True, "id": it["id"], "name": it.get("name"),
            "path": loc.get("path"), "cached": loc.get("cached")}


def apis():
    return [sticker_search, sticker_local, sticker_hint, sticker_sync,
            sticker_stats, sticker_emotions, sticker_pick]
