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
import math
import random
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
# 工作区已从 C:\Users\<用户>\.dsh 搬到 D 盘，统一走 DSH_HOME，没设才回落默认位置
_DSH_ROOT = os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh")
_DSH_META = os.path.join(
    _DSH_ROOT, "profiles", "desktop", "node_modules",
    "dsh-nekowhale-stickers", "meta", "nekowhale.json")
_DSH_MANIFEST = os.path.join(_DSH_ROOT, "nekowhale-stickers", "manifest.json")

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
#
# 老版本的问题：把整句话按标点切开，再切成 2 字片段去和标注做字面匹配。
# "今天好累啊" → ["今天","天好","好累","累啊"]，一个都对不上标注里的"疲惫/熬夜/撑不住"，
# 于是返回空；能对上的又永远是"笑""猫""手机"这几个大众词，所以看起来只会发那四张。
#
# 现在分三层：
#   1) 情绪识别：把大白话映射到索引里的 25 种情绪（累→疲惫、想哭→难过…）；
#   2) 词表打分：给索引里真实出现过的 2/3 字片段算 IDF，罕见片段权重高，大众词自动降权；
#   3) 兜底 + 轮换：命中不够就按情绪补齐候选，并按最近用过的图扣分，让 322 张轮着上。
# ---------------------------------------------------------------- 情绪识别
# (正则, 命中的情绪, 权重)
_EMO_RULES = [
    (r"晚安|睡了|睡觉|好困|困死|打哈欠|该睡了|躺平|眯一会", ("疲惫",), 3.0),
    (r"累|肝|熬夜|通宵|加班|撑不住|干不动|上班|打工|上课|写代码|debug|bug|报告|作业|论文|复习|考试|赶工", ("疲惫",), 2.2),
    (r"早啊|早安|早上好|嗨|哈喽|hello|hi|在吗|在么|来了|我回来了|你好", ("打招呼",), 3.0),
    (r"哈哈|笑死|乐死|好玩|太好笑|绷不住|笑不活|xswl|233|草", ("大笑", "开心"), 3.0),
    (r"开心|高兴|舒服|太好了|赚了|赢了|成功|过了|拿到|录取|offer|满分|第一|上岸", ("开心", "兴奋"), 2.4),
    (r"生气|气死|火大|烦|讨厌|可恶|破防|被骂|挨骂|骂了|恶心|离谱|无语|服了|抓狂", ("生气", "无语"), 2.6),
    (r"难过|伤心|emo|失恋|孤独|寂寞|委屈|想哭|难受|崩溃|心累|没人理|被甩|分手", ("难过", "安慰"), 2.6),
    (r"哭|眼泪|呜呜|呜哇|泪目|qwq", ("哭", "难过"), 2.6),
    (r"害怕|恐怖|吓|好怕|鬼|不敢|紧张|发抖", ("害怕",), 2.6),
    (r"喜欢|想你|爱你|心动|可爱|好萌|老婆|贴贴|抱抱|亲亲|馋你", ("喜欢", "害羞"), 2.4),
    (r"谢谢|感谢|多谢|3q|thx|thank", ("感谢",), 3.0),
    (r"对不起|抱歉|不好意思|我的错|sorry", ("道歉",), 3.0),
    (r"饿了|吃饭|吃啥|夜宵|火锅|外卖|好吃|馋|奶茶|零食|炸鸡|干饭|甜品", ("卖萌", "兴奋"), 2.0),
    (r"不懂|为什么|为啥|咋回事|什么鬼|啥意思|怎么弄|不会做|教教我", ("疑惑", "思考"), 1.8),
    (r"卧槽|真的假的|不会吧|竟然|居然|惊了|啊这|什么情况", ("惊讶",), 2.6),
    (r"不要|不行|拒绝|算了|不想听|别提|滚啊|nonono|别烦", ("拒绝",), 2.0),
    (r"好耶|可以啊|没问题|收到|okay|ok了|行吧|赞成|同意|就这么办", ("赞同",), 2.4),
    (r"厉害|牛逼|牛啊|太强|我赢|佩服|好棒|绝了|太秀|天才", ("得意", "兴奋"), 2.2),
    (r"整活|搞怪|乐子|沙雕|抽象|鬼畜|离谱妈|整点活", ("搞怪",), 2.2),
    (r"就这|菜鸡|笑话|呵呵|嘲讽|阴阳|急了|破防了吧", ("嘲讽",), 2.4),
    (r"没事的|别难过|安慰|我在呢|会好的|不难过|抱一下|辛苦啦", ("安慰",), 2.4),
    # 下面这些是日常里最常出现、但上面没覆盖到的说法（补召回用）
    (r"还没睡|睡不着|失眠|熬夜中|起不来|要早起|闹钟|明天要", ("疲惫",), 2.0),
    (r"感冒|发烧|生病|不舒服|头疼|头痛|胃疼|拉肚子|嗓子", ("疲惫", "难过"), 2.2),
    (r"好难|太难|不会做|学不会|搞不懂|看不懂|听不懂", ("疑惑", "思考"), 2.0),
    (r"打完游戏|开黑|上分|掉分|排位|抽卡|出货|十连", ("兴奋", "搞怪"), 1.8),
    (r"老板|领导|班主任|辅导员|甲方|客户", ("无语", "疲惫"), 1.6),
    (r"周末|放假|休息|出去玩|旅游|约饭|逛街", ("开心", "兴奋"), 1.8),
    (r"忘记|别走|离开|舍不得|想留|不要走", ("难过", "安慰"), 2.2),
    (r"无聊|没事干|发呆|摸鱼中|划水", ("无语", "搞怪"), 1.8),
    (r"干嘛|在不在|人呢|在么", ("打招呼",), 2.4),
    (r"买了|快递|到了|新手机|新电脑|到手", ("兴奋", "开心"), 1.8),
    (r"下雨|天气|好热|好冷|太阳|下雪|刮风|台风", ("无语",), 1.6),
    (r"相亲|催婚|爸妈|我妈|我爸|家里催", ("无语", "疲惫"), 1.8),
    (r"躺一天|宅|赖床|床上|被窝", ("疲惫", "卖萌"), 1.8),
    (r"吃完饭|干饭|吃饱|吃撑|做饭|下厨", ("卖萌", "兴奋"), 1.8),
]


def _detect_emotion(text):
    """从一句人话里猜情绪，返回 [(情绪, 权重), ...]，权重从高到低。"""
    t = str(text or "").lower()
    if not t:
        return []
    got = {}
    for pat, emos, w in _EMO_RULES:
        try:
            if re.search(pat, t):
                for e in emos:
                    if w > got.get(e, 0.0):
                        got[e] = w
        except Exception:
            continue
    return sorted(got.items(), key=lambda x: -x[1])


# ---------------------------------------------------------------- 词表打分
_GRAM_CACHE = {}


def _grams(s):
    """中文切 2/3 字片段，英文数字按空格切。只有出现在索引里的片段才会被用上。"""
    s = str(s or "").lower()
    key = s[:200]
    hit = _GRAM_CACHE.get(key)
    if hit is not None:
        return hit
    s = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", " ", key)
    out = []
    for part in s.split():
        if re.match(r"^[a-z0-9]+$", part):
            if len(part) >= 2:
                out.append(part)
            continue
        L = len(part)
        for n in (2, 3):
            if L < n:
                continue
            for i in range(L - n + 1):
                out.append(part[i:i + n])
    if len(_GRAM_CACHE) > 4000:
        _GRAM_CACHE.clear()
    _GRAM_CACHE[key] = out
    return out


_FIELD_W = (("name", 3.0), ("tags", 2.2), ("use", 1.4), ("text", 1.2),
            ("emotion", 1.0), ("meme", 0.9), ("desc", 0.5))

_lex_idf = None      # 片段 -> IDF
_lex_item = None     # id -> {片段: 权重}


def _build_lexicon(idx):
    """从 322 条标注里现算一份片段权重表（纯本地，毫秒级）。"""
    global _lex_idf, _lex_item
    if _lex_idf is not None:
        return
    items = idx.get("items", [])
    n = max(1, len(items))
    df = {}
    per = {}
    for it in items:
        bag = {}
        for field, fw in _FIELD_W:
            v = it.get(field)
            if isinstance(v, (list, tuple)):
                v = " ".join(str(x) for x in v)
            for g in _grams(v):
                if fw > bag.get(g, 0.0):
                    bag[g] = fw
        per[it["id"]] = bag
        for g in bag:
            df[g] = df.get(g, 0) + 1
    _lex_idf = dict((g, math.log((n + 1.0) / (c + 0.5))) for g, c in df.items())
    _lex_item = per


# ---------------------------------------------------------------- 用过哪些（轮换用）
_usage = None
_used_lock = threading.RLock()


def _usage_path():
    return _ctx.plugin_data(PLUGIN_ID, "used.json")


def _load_usage():
    global _usage
    if _usage is not None:
        return _usage
    d = None
    try:
        d = _ctx.read_json(_usage_path(), None)
    except Exception:
        d = None
    if not isinstance(d, dict):
        d = {}
    _usage = d
    return _usage


def _mark_used(sid):
    """记一笔"这张刚发过"，落盘但只保留最近 300 条。"""
    if not _ctx or not sid:
        return
    try:
        with _used_lock:
            u = _load_usage()
            u[sid] = _now()
            if len(u) > 300:
                keep = sorted(u.items(), key=lambda kv: -kv[1])[:300]
                u.clear()
                u.update(keep)
            _ctx.write_json(_usage_path(), u)
    except Exception:
        pass


def _recent_ids(k=6):
    """最近发过的图（新的在前），用来避免连着发同一张。"""
    try:
        u = _load_usage()
        rows = sorted(u.items(), key=lambda kv: -kv[1])[:max(0, int(k))]
        return [sid for sid, _ts in rows]
    except Exception:
        return []


def _novelty_penalty(it):
    """刚发过就扣分，让图能轮起来；放久了慢慢恢复。"""
    try:
        ts = _load_usage().get(it.get("id"))
        if not ts:
            return 0.0
        age = max(0.0, _now() - float(ts))
        if age < 600:
            return 9.0
        if age < 7200:
            return 4.5
        if age < 86400:
            return 1.8
        return 0.0
    except Exception:
        return 0.0


def _rank(it, qtext, qgrams, emo, exact=False):
    """单张图对当前语境的分数。"""
    sc = 0.0
    name = str(it.get("name") or "")
    if name and name in qtext:
        sc += 8.0
    if exact and qtext.strip() in (name, str(it.get("code") or "")):
        sc += 40.0
    hit_tags = 0
    for tg in (it.get("tags") or []):
        tg = str(tg)
        if len(tg) >= 2 and tg in qtext:
            hit_tags += 1
    sc += min(hit_tags, 4) * 4.0
    bag = (_lex_item or {}).get(it["id"]) or {}
    if qgrams and bag:
        s = 0.0
        for g in qgrams:
            w = bag.get(g)
            if w:
                s += w * (_lex_idf or {}).get(g, 0.0)
        if s:
            sc += s / math.sqrt(len(qgrams)) * 1.35
    emo_name = str(it.get("emotion") or "")
    for e, w in emo:
        if e == emo_name:
            sc += 2.2 * w
            break
    return sc


def _candidates(query, n=6, emotion="", avoid=None):
    """检索核心：返回分数最高的 n 张，且保证凑得满（不够就按情绪/随机补）。

    avoid 里的 id 会被额外压分（一般是刚发过的那几张）。
    """
    idx = _load_index()
    if not isinstance(idx, dict):
        return []
    _build_lexicon(idx)
    items = idx.get("items", [])
    try:
        n = max(1, min(int(n or 6), 40))
    except Exception:
        n = 6
    qtext = str(query or "").lower()[:400]
    qgrams = _grams(qtext) if qtext else []
    emo = _detect_emotion(qtext)
    if emotion:
        emo = [(str(emotion), 3.0)] + [e for e in emo if e[0] != emotion]
    avoid = set(avoid or []) | set(_recent_ids(4))

    rows = []
    for it in items:
        sc = _rank(it, qtext, qgrams, emo)
        if it["id"] in avoid:
            sc -= 6.0
        sc -= _novelty_penalty(it)
        if sc > 0:
            sc += random.random() * 0.6      # 同分随机，避免每次都那几张
        rows.append((sc, it))
    rows.sort(key=lambda x: -x[0])

    out = [it for sc, it in rows if sc >= 1.6][:n]
    if len(out) >= n:
        return out

    # 补齐：优先同情绪的图（这样"好累"至少能拿到疲惫系，而不是空手）
    seen = set(x["id"] for x in out)
    pool = []
    emo_names = set(e for e, _w in emo)
    for it in items:
        if it["id"] in seen:
            continue
        if it.get("emotion") in emo_names:
            pool.append(it)
    if not pool:
        # 连情绪都没猜出来：从最常用的几类里随机，别整个沉默
        for it in items:
            if it["id"] not in seen and it.get("emotion") in ("开心", "搞怪", "卖萌", "打招呼"):
                pool.append(it)
    # 同一情绪里也有几十张，按"残留相关度"排，别纯随机
    soft = []
    for it in pool:
        s = _rank(it, qtext, qgrams, emo)
        s -= 4.0 if it["id"] in avoid else 0.0
        s -= _novelty_penalty(it)
        soft.append((s, random.random(), it))
    soft.sort(key=lambda x: (-x[0], x[1]))
    for _s, _r, it in soft:
        if len(out) >= n:
            break
        out.append(it)
    return out


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

    query 可以是「今天好累啊」这种整句话，也可以是「搓手」「摸鱼」这种关键词；
    搜不到会按猜出来的情绪补齐，不会返回空列表。
    """
    if not _ctx:
        return []
    idx = _load_index()
    if not isinstance(idx, dict):
        return []
    try:
        n = int(limit or 6)
    except Exception:
        n = 6
    cands = _candidates(query, max(1, min(n, 30)), emotion)
    out = []
    for it in cands:
        lp = _local_path(it)
        out.append({
            "id": it["id"], "code": it.get("code", ""), "name": it["name"], "pack": it["pack"],
            "emotion": it["emotion"], "tags": it["tags"],
            "use": it["use"], "desc": it["desc"], "meme": it["meme"],
            "file": it["file"], "hash": it["hash"],
            "cached": os.path.exists(lp),
        })
    return out


def sticker_browse(emotion="", q="", offset=0, limit=60):
    """图库浏览：给用户自己挑图用。

    和 sticker_search 的区别：**不打语境分、不随机、不做「刚发过就扣分」**，
    顺序固定（先按情绪再按名字），可以稳定翻页——用户翻到第 2 页不会发现
    第 1 页的图变了。也没联网、没模型调用，纯读本地索引。
    """
    if not _ctx:
        return {"ok": False, "error": "no ctx"}
    idx = _load_index()
    if not isinstance(idx, dict):
        return {"ok": False, "error": "索引未生成", "items": []}
    items = idx.get("items", [])
    emo = str(emotion or "").strip()
    kw = str(q or "").strip().lower()
    pool = []
    for it in items:
        if emo and it.get("emotion") != emo:
            continue
        if kw:
            hay = " ".join([
                str(it.get("name") or ""),
                " ".join(it.get("tags") or []),
                str(it.get("use") or ""),
                str(it.get("desc") or ""),
                str(it.get("text") or ""),
            ]).lower()
            if kw not in hay:
                continue
        pool.append(it)
    # 按索引里声明的情绪顺序分组（开心→大笑→喜欢→…），组内按名字；顺序固定才能翻页
    order = {str(e): i for i, e in enumerate(idx.get("emotions") or [])}
    pool.sort(key=lambda it: (order.get(str(it.get("emotion") or ""), 999),
                              str(it.get("name") or ""), str(it.get("id") or "")))
    try:
        off = max(0, int(offset or 0))
        n = max(1, min(int(limit or 60), 120))
    except Exception:
        off, n = 0, 60
    page = pool[off:off + n]
    out = []
    for it in page:
        lp = _local_path(it)
        out.append({
            "id": it["id"], "name": it.get("name") or "", "emotion": it.get("emotion") or "",
            "tags": it.get("tags") or [], "use": it.get("use") or "",
            "cached": os.path.exists(lp),
        })
    return {"ok": True, "total": len(pool), "offset": off,
            "items": out, "hasMore": off + n < len(pool)}


def sticker_resolve(name):
    """按名字/代号精确找一张（给前端把模型写的 [名字] 换成图用）。

    先认名字和代号完全相等的，再退回模糊检索的第一张。
    """
    if not _ctx:
        return {"ok": False, "error": "no ctx"}
    idx = _load_index()
    if not isinstance(idx, dict):
        return {"ok": False, "error": "索引未生成"}
    q = str(name or "").strip().strip("[]【】")
    if not q:
        return {"ok": False, "error": "空名字"}
    it = None
    for x in idx.get("items", []):
        if x.get("name") == q or x.get("code") == q:
            it = x
            break
    if it is None:
        cands = _candidates(q, 1)
        if not cands:
            return {"ok": False, "error": "找不到这张"}
        it = cands[0]
    loc = sticker_local(it["id"])
    if not loc.get("ok"):
        return {"ok": False, "error": loc.get("error") or "取图失败", "id": it["id"]}
    _mark_used(it["id"])
    return {"ok": True, "id": it["id"], "name": it.get("name"),
            "path": loc.get("path"), "cached": loc.get("cached")}


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


def sticker_hint(text, limit=6):
    """给一段话，返回候选表情包的提示文本——可以直接塞进 prompt 让大肥鱼自己挑。

    返回的每行形如：[猫猫搓手] 兴奋·期待好事发生或迫不及待想试试时
    """
    if not _ctx:
        return ""
    idx = _load_index()
    if not isinstance(idx, dict):
        return ""
    try:
        n = int(limit or 6)
    except Exception:
        n = 6
    res = sticker_search(text, "", max(2, n))
    if not res:
        return ""
    lines = []
    seen = set()
    for r in res:
        name = (r.get("name") or "").strip()
        if not name or name in seen:          # 名字是模型认图的关键，不能重复
            continue
        seen.add(name)
        use = (r.get("use") or r.get("desc") or "").strip()
        if use.startswith(name):
            use = use[len(name):].lstrip(" —-·、")
        emo = (r.get("emotion") or "").strip()
        tail = use[:40] or name
        lines.append("[%s] %s" % (name, ("%s·%s" % (emo, tail)) if emo else tail))
        if len(lines) >= max(2, n):
            break
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


def sticker_pick(user_text, ai_text="", limit=10, model=None):
    """让模型从候选里挑一张（独立的一次极短推理，不干扰正文生成）。

    为什么单独做一次：让模型在长回复里自己写代号，7B 模型经常漏写或写崩；
    拆成"只挑一个序号"后准确率高很多，代价只有零点几秒。
    候选从 4 个放宽到 10 个——候选越多越不容易老是那几张，7B 挑序号也扛得住。

    返回 {ok, id, name, path, cached} 或 {ok: False, error}
    """
    if not _ctx:
        return {"ok": False, "error": "no ctx"}
    q = ("%s %s" % (user_text or "", ai_text or "")).strip()
    if not q:
        return {"ok": False, "error": "没内容"}
    try:
        n = max(3, min(int(limit or 10), 12))
    except Exception:
        n = 10
    cand = sticker_search(q, "", n)
    if not cand:
        return {"ok": False, "error": "没有候选"}

    lst = "\n".join(
        "%d. [%s] %s%s" % (i + 1, c.get("name"),
                           (c.get("emotion") or "") + "·" if c.get("emotion") else "",
                           (c.get("use") or c.get("desc") or "")[:26])
        for i, c in enumerate(cand))
    msg = ("对话：\n用户：%s\n我：%s\n\n候选：\n%s\n\n最贴的是第几个？只回数字。"
           % ((user_text or "")[:160], (ai_text or "")[:260], lst))
    out = None
    try:
        out = _ctx.chat(
            [{"role": "system", "content": _PICK_SYS},
             {"role": "user", "content": msg}],
            # 超时给足：如果刚好在切模型（大模型还占着内存），Ollama 要先卸载再加载，
            # 25 秒会直接超时，表现为"突然不发表情包了"（日志里是 [ctx] chat 失败: timed out）
            model=model, timeout=45, num_predict=8, think=False)
    except Exception as e:
        return {"ok": False, "error": "选图失败: %r" % (e,)}
    raw = str(out or "")
    pick_i = None
    m = re.search(r"\d{1,2}", raw)
    if m and 1 <= int(m.group()) <= len(cand):
        pick_i = int(m.group()) - 1
    if pick_i is None:
        # 有的模型爱直接写名字而不写序号，兜一下
        for i, c in enumerate(cand):
            if c.get("name") and c["name"] in raw:
                pick_i = i
                break
    if pick_i is None:
        return {"ok": False, "error": "模型没给出序号", "raw": raw[:40]}
    it = cand[pick_i]
    loc = sticker_local(it["id"])
    if not loc.get("ok"):
        return {"ok": False, "error": loc.get("error") or "取图失败", "id": it["id"]}
    _mark_used(it["id"])
    return {"ok": True, "id": it["id"], "name": it.get("name"),
            "path": loc.get("path"), "cached": loc.get("cached")}


def apis():
    return [sticker_search, sticker_local, sticker_hint, sticker_sync,
            sticker_stats, sticker_emotions, sticker_pick, sticker_resolve,
            sticker_browse]
