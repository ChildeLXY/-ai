# -*- coding: utf-8 -*-
"""
大肥鱼AI · QQ 接入端口

支持两条路：
  1) OneBot v11（NapCat / Lagrange / go-cqhttp）：HTTP 上报接收 + HTTP API 主动发消息
  2) QQ 官方机器人 API（bots.qq.com）：拿 token + WebSocket 收事件 + REST 发消息

只依赖 Python 标准库（官方 API 的 WebSocket 接收需要可选包 websockets，没装则只发不收）。

用法：
    python qqbot.py                 # 按 config.json 的 mode 启动
    python qqbot.py --mode onebot
    python qqbot.py --self-test     # 不启动服务，自测配置与模型连通性
"""
import json
import os
import re
import sys
import time
import queue
import random
import base64
import hashlib
import threading
import traceback
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
LOG_DIR = r"D:\大肥鱼缓存"
LOG_PATH = os.path.join(LOG_DIR, "qqbot.log")
SESSION_PATH = os.path.join(LOG_DIR, "qq_sessions.json")
os.makedirs(LOG_DIR, exist_ok=True)

_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# QQ 专属记忆池（共享流水 + 每日日记 + 检索 + 月度衰减）
try:
    import qq_memory
except Exception as _e:      # 记忆模块坏了也不能让机器人起不来
    qq_memory = None
    print("[Memory] 记忆模块加载失败：%r" % _e)

# QQ 系统小黄脸 face id → 中文名（够用即可，认不出的统一叫「QQ表情」）
FACE_NAMES = {
    0: "惊讶", 1: "撇嘴", 2: "色", 3: "发呆", 4: "得意", 5: "流泪", 6: "害羞",
    7: "闭嘴", 8: "睡", 9: "大哭", 10: "尴尬", 11: "发怒", 12: "调皮", 13: "呲牙",
    14: "微笑", 15: "难过", 16: "酷", 18: "抓狂", 19: "吐", 20: "偷笑", 21: "可爱",
    22: "白眼", 23: "傲慢", 24: "饥饿", 25: "困", 26: "惊恐", 27: "流汗", 28: "憨笑",
    29: "大兵", 30: "奋斗", 31: "咒骂", 32: "疑问", 33: "嘘", 34: "晕", 35: "折磨",
    36: "衰", 37: "骷髅", 38: "敲打", 39: "再见", 96: "冷汗", 97: "擦汗", 98: "抠鼻",
    99: "鼓掌", 100: "糗大了", 101: "坏笑", 102: "左哼哼", 103: "右哼哼", 104: "哈欠",
    105: "鄙视", 106: "委屈", 107: "快哭了", 108: "阴险", 109: "亲亲", 110: "吓",
    111: "可怜", 112: "菜刀", 113: "西瓜", 114: "啤酒", 115: "篮球", 116: "乒乓",
    117: "咖啡", 118: "饭", 120: "玫瑰", 121: "凋谢", 122: "示爱", 123: "爱心",
    124: "心碎", 125: "蛋糕", 126: "闪电", 127: "炸弹", 128: "刀", 129: "足球",
    130: "瓢虫", 131: "便便", 132: "月亮", 133: "太阳", 134: "礼物", 135: "拥抱",
    136: "强", 137: "弱", 138: "握手", 139: "胜利", 140: "抱拳", 141: "勾引",
    142: "拳头", 143: "差劲", 144: "爱你", 145: "NO", 146: "OK", 147: "爱情",
    148: "飞吻", 149: "跳跳", 150: "发抖", 151: "怄火", 152: "转圈", 153: "磕头",
    154: "回头", 155: "跳绳", 156: "挥手", 157: "激动", 158: "街舞", 159: "献吻",
    160: "左太极", 161: "右太极", 173: "泪奔", 174: "无奈", 175: "卖萌", 176: "小纠结",
    177: "喷血", 178: "斜眼笑", 179: "doge", 180: "惊喜", 181: "骚扰", 182: "笑哭",
    183: "我最美", 184: "河蟹", 185: "羊驼", 186: "栗子", 187: "幽灵", 188: "蛋",
    189: "汉堡", 190: "花痴", 191: "怒", 192: "发财", 193: "波波", 194: "鄙视你",
    195: "捂脸", 196: "奸笑", 197: "机智", 198: "皱眉", 199: "耶", 200: "吃瓜",
    201: "已阅", 202: "药丸", 203: "我想静静", 204: "托腮", 205: "敲开心",
}

# ---------------------------------------------------------------- 日志
_log_lock = threading.Lock()


def _int(v, default=-1):
    try:
        return int(str(v).strip())
    except Exception:
        return default


def log(msg, level="INFO"):
    line = "[%s][%s] %s" % (time.strftime("%H:%M:%S"), level, msg)
    # 先写文件：pythonw 下 sys.__stdout__ 是 None，先写 stdout 会让整条日志丢失
    try:
        with _log_lock:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass
    try:
        if sys.__stdout__ is not None:
            sys.__stdout__.write(line + "\n")
            sys.__stdout__.flush()
    except Exception:
        pass


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 表情包
class StickerPicker:
    """从大肥鱼AI 的 322 张本地图库里挑表情包，返回 gif 的 base64（OneBot 直接发）。

    复用桌面版同一份 index.json 和 gif 缓存——所以 QQ 里发的图和桌面上是同一批。
    纯标准库，ReadOnly：只读索引和 gif，不改任何文件。
    """
    INDEX = r"D:\本地部署大肥鱼环境\data\plugins\nekowhale_stickers\index.json"
    GIF_DIR = r"D:\本地部署大肥鱼环境\data\plugins\nekowhale_stickers\gif"

    def __init__(self):
        self.items = []
        self.by_name = {}
        self._lock = threading.Lock()
        try:
            d = json.load(open(self.INDEX, "r", encoding="utf-8-sig"))
            self.items = d.get("items") or []
            for it in self.items:
                self.by_name[str(it.get("name") or "").strip()] = it
                # code 也认（模型有时写 10N01_猫猫搓手 这种）
                if it.get("code"):
                    self.by_name.setdefault(str(it["code"]), it)
            log("表情包图库已加载：%d 张" % len(self.items))
        except Exception as e:
            log("表情包索引读取失败：%r" % e, "WARN")

    def pick(self, hint="", emotion=""):
        """按名字/情绪/tags 找一张，返回 (它, gif路径)；找不到返回 (None, '')。"""
        if not self.items:
            return None, ""
        hint = str(hint or "").strip()
        # 去掉方括号、下划线、编号前缀等噪声
        clean = re.sub(r"[\[\]【】]", "", hint).strip()
        clean = re.sub(r"^[0-9A-Za-z]+[_-]", "", clean).strip()
        cand = None
        if clean:
            # 1) 精确名字
            cand = self.by_name.get(clean)
            # 2) 名字/标签包含
            if not cand:
                for it in self.items:
                    hay = " ".join([str(it.get("name") or ""), str(it.get("emotion") or ""),
                                    " ".join(str(x) for x in (it.get("tags") or []))])
                    if clean in hay:
                        cand = it; break
            # 3) 逐字模糊（取命中字数最多的）
            if not cand and len(clean) >= 2:
                best, score = None, 0
                for it in self.items:
                    hay = " ".join([str(it.get("name") or ""), " ".join(str(x) for x in (it.get("tags") or []))])
                    s = sum(1 for ch in clean if ch in hay)
                    if s > score:
                        best, score = it, s
                if score >= 2:
                    cand = best
        # 4) 按情绪兜底
        if not cand and emotion:
            for it in self.items:
                if str(it.get("emotion") or "") == str(emotion):
                    cand = it; break
        if not cand:
            return None, ""
        h = str(cand.get("hash") or "")
        p = os.path.join(self.GIF_DIR, h + ".gif")
        if not os.path.exists(p):
            return None, ""
        return cand, p

    def image_segment(self, gif_path, max_mb=5):
        """把本地 gif 转成 OneBot image 段（base64://），太大或不存在返回 None。"""
        try:
            if not gif_path or not os.path.exists(gif_path):
                return None
            if os.path.getsize(gif_path) > int(max_mb) * 1024 * 1024:
                return None
            with self._lock:
                b64 = base64.b64encode(open(gif_path, "rb").read()).decode("ascii")
            return {"type": "image", "data": {"file": "base64://" + b64}}
        except Exception:
            return None

    def list_hints(self, limit=12, query=""):
        """给模型看的候选表情包清单（名字·情绪）。

        query 是对方原话：只给前 N 张的话，模型会在一堆不相关的图里瞎挑
        （5:47 那次「舔」排在第 47 位根本没进清单，它就抓了第 2 张「搓搓金猪」
        硬说成「超级舔屏」）。所以按原话把命中名字/标签的也补进清单。
        """
        picked = list(self.items[:max(0, int(limit))])
        for it in self.match_items(query, 6):
            if it not in picked:
                picked.append(it)
        out = []
        for it in picked:
            out.append("%s（%s）" % (it.get("name"), it.get("emotion")))
        return "、".join(out)

    def match_items(self, query, limit=6):
        """拿对方原话去撞名字/标签/情绪，返回最贴的几张（命中得越长越靠前）。"""
        q = str(query or "")
        if not q:
            return []
        scored = []
        for it in self.items:
            best = 0
            for c in [it.get("name"), it.get("emotion")] + list(it.get("tags") or []):
                s = str(c or "").strip()
                if len(s) >= 2 and s in q and len(s) > best:
                    best = len(s)
            if best:
                scored.append((best, it))
        scored.sort(key=lambda x: -x[0])
        return [it for _, it in scored[:max(1, int(limit))]]


# ---------------------------------------------------------------- 语音合成
class Voice:
    """把回复文字念成语音条。

    链路：文字 → edge-tts 合成 mp3 → OneBot 的 record 消息段 → NapCat 自己
    转 SILK v3 再上传（NapCat 内置 ffmpeg，本机不用装 ffmpeg）。
    可用性完全取决于是否装了 edge-tts（pip install edge-tts）。
    """

    # 常用中文音色（设置页给下拉用）
    VOICES = [
        ("zh-CN-XiaoyiNeural", "晓伊（少女，默认）"),
        ("zh-CN-XiaoxiaoNeural", "晓晓（温柔女声）"),
        ("zh-CN-XiaobeiNeural", "晓北（活泼东北）"),
        ("zh-CN-XiaohanNeural", "晓涵（沉稳女声）"),
        ("zh-CN-XiaomoNeural", "晓墨（知性女声）"),
        ("zh-CN-XiaoruiNeural", "晓睿（成熟女声）"),
        ("zh-CN-XiaoshuangNeural", "晓双（童声）"),
        ("zh-CN-XiaoxuanNeural", "晓萱（活泼女声）"),
        ("zh-CN-XiaoyanNeural", "晓颜（温柔女声）"),
        ("zh-CN-YunxiNeural", "云希（少年男声）"),
        ("zh-CN-YunjianNeural", "云健（沉稳男声）"),
        ("zh-CN-YunyangNeural", "云扬（播音男声）"),
        ("zh-CN-YunxiaNeural", "云夏（少年音）"),
    ]

    def __init__(self, cfg):
        c = {}
        try:
            c = (cfg or {}).get("tts") or {}
        except Exception:
            c = {}
        self.enabled = bool(c.get("enabled", False))
        self.voice = str(c.get("voice") or "zh-CN-XiaoyiNeural")
        self.rate = str(c.get("rate") or "+8%")
        self.pitch = str(c.get("pitch") or "+0Hz")
        self.volume = str(c.get("volume") or "+0%")
        self.max_chars = int(c.get("max_chars") or 120)
        try:
            self.prob = max(0.0, min(1.0, float(c.get("probability", 0.25))))
        except Exception:
            self.prob = 0.25
        self.also_text = bool(c.get("also_text", False))
        self.dir = str(c.get("dir") or r"D:/大肥鱼缓存/tts")
        self.keep = int(c.get("keep", 80))
        self._ok = None
        self.said = 0

    def available(self):
        if self._ok is None:
            try:
                import edge_tts  # noqa: F401
                self._ok = True
            except Exception as e:
                self._ok = False
                log("语音合成不可用（没装 edge-tts，pip install edge-tts）：%r" % e, "WARN")
        return self._ok

    @staticmethod
    def clean(text):
        """念之前洗一遍：去掉代码块、markdown、链接、CQ 码、句尾情绪括号。"""
        t = str(text or "")
        t = re.sub(r"```.*?```", "，", t, flags=re.S)
        t = re.sub(r"\[CQ:[^\]]*\]", "", t)
        t = re.sub(r"https?://\S+", "，", t)
        t = re.sub(r"\[([^\[\]\n]{1,20})\]", "", t)      # [表情名]/[语音]
        t = re.sub(r"[*_`#>|~]+", "", t)
        t = re.sub(r"[（(][^（）()]{0,8}[)）]\s*$", "", t)  # 句尾（情绪）
        t = re.sub(r"\s+", " ", t).strip(" ，,。.、")
        return t

    def synth(self, text):
        """合成一段 mp3，成功返回绝对路径，失败返回 None（调用方退回文字）。"""
        if not self.enabled or not self.available():
            return None
        t = self.clean(text)
        if not t:
            return None
        if len(t) > self.max_chars:
            t = t[:self.max_chars].rstrip() + "…"
        try:
            import asyncio
            import edge_tts
            os.makedirs(self.dir, exist_ok=True)
            path = os.path.join(self.dir, "v%d_%d.mp3" % (
                int(time.time() * 1000), random.randint(100, 999)))

            async def _go():
                c = edge_tts.Communicate(t, self.voice, rate=self.rate,
                                         pitch=self.pitch, volume=self.volume)
                await c.save(path)

            asyncio.run(_go())
            if os.path.isfile(path) and os.path.getsize(path) > 800:
                self.said += 1
                self._gc()
                return path
            log("语音合成出来的文件异常小，丢弃：%s" % path, "WARN")
        except Exception as e:
            log("语音合成失败：%r" % e, "WARN")
        return None

    def _gc(self):
        try:
            fs = [os.path.join(self.dir, f) for f in os.listdir(self.dir) if f.endswith(".mp3")]
            fs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            for p in fs[self.keep:]:
                try:
                    os.remove(p)
                except Exception:
                    pass
        except Exception:
            pass

    @staticmethod
    def segment(path):
        """OneBot record 段。NapCat 接受 file:/// 本地路径并自行转 SILK。"""
        u = "file:///" + os.path.abspath(path).replace("\\", "/")
        return {"type": "record", "data": {"file": u}}


# ---------------------------------------------------------------- 会话存储
class Sessions:
    """每个 QQ 会话的短历史，落盘保存，重启不丢。"""

    def __init__(self, path, turns=8):
        self.path = path
        self.turns = turns
        self.data = {}
        self.lock = threading.Lock()
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.path):
                self.data = json.load(open(self.path, "r", encoding="utf-8"))
        except Exception:
            self.data = {}

    def _save(self):
        try:
            tmp = self.path + ".tmp"
            json.dump(self.data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def get(self, key):
        with self.lock:
            return list(self.data.get(key, []))

    def add(self, key, role, content):
        with self.lock:
            h = self.data.setdefault(key, [])
            h.append({"role": role, "content": content})
            if len(h) > self.turns * 2:
                del h[:len(h) - self.turns * 2]
            self._save()

    def clear(self, key):
        with self.lock:
            self.data.pop(key, None)
            self._save()


# ---------------------------------------------------------------- 大脑
class Brain:
    def __init__(self, cfg):
        self.cfg = cfg
        self.ollama = cfg.get("ollama", {})
        self.b = cfg.get("brain", {})
        self.sessions = Sessions(SESSION_PATH, int(self.b.get("history_turns", 8)))
        self.model_lock = threading.Lock()   # 同一时刻只跑一个模型请求，防止显存抖动
        # 主模型卡死熔断：一旦某次请求超时/没响应，10 分钟内一律先用兜底小模型，
        # 否则一条卡住的消息会把 3 个 worker 全占满，之后所有消息排队等死。
        self._slow_until = 0.0
        self._fail_streak = 0
        self.last_model = None
        self.stickers = StickerPicker() if self.b.get("use_stickers", True) else None
        # 会话 -> 最近发过的表情包（喂进提示词，避免连着发同一张）
        self._recent_stickers = {}
        # desktop 模式：人格/记忆/表情包数据与桌面大肥鱼AI 共用（同一批文件），
        # 并在 system prompt 里补上桌面版的身份/时间/称呼等元素。
        self.desktop_mode = str(cfg.get("brain_mode", "standalone")).lower() == "desktop"
        self._img_cache = {}          # md5(图片字节) → 一句描述，避免同一个表情包反复"看"
        self._img_lock = threading.Lock()
        self.mem = qq_memory
        self._mem_boot()
        # DSH 插件转接口：插件往提示词里注入的内容（人设/记忆/心情/贴纸提示）
        # 和它们注册的工具，都由 D:/大肥鱼AI/dsh_host 那个兼容宿主提供
        self.dsh = None
        try:
            import sys as _sys
            if r"D:\大肥鱼AI" not in _sys.path:
                _sys.path.append(r"D:\大肥鱼AI")
            import dfy_dsh as _dsh
            self.dsh = _dsh
            log("DSH 转接口：%s" % ("在线" if _dsh.online() else "离线（未启动）"))
        except Exception as e:
            log("DSH 转接口不可用：%r" % e, "WARN")

    # -------- DSH 插件注入的内容 --------
    def _dsh_text(self):
        if not self.dsh:
            return ""
        try:
            if not self.dsh.enabled():
                return ""
            parts = self.dsh.context_parts()
            skip_persona = str(self.cfg.get("dsh_skip_persona", "1")).lower() in ("1", "true", "on")
            max_chars = int(self.cfg.get("dsh_max_chars", 1500) or 1500)
            # 插件注入的段落：跳过和自带人设重复的部分，其余按长度预算收进来
            rows, used = [], 0
            for p in parts:
                name = str(p.get("name") or "")
                body = str(p.get("text") or "").strip()
                if not body:
                    continue
                if skip_persona and "persona" in name.lower():
                    continue
                if used + len(body) > max_chars:
                    body = body[:max(0, max_chars - used)]
                if not body:
                    break
                rows.append(body)
                used += len(body)
                if used >= max_chars:
                    break
            out = []
            if rows:
                out.append("【来自你的插件（人设/长期记忆，是你本来就有的东西）】\n" + "\n".join(rows))
            line = self.dsh.mood_line()
            if line:
                out.append("【你此刻的状态】" + line)
            return "\n".join(out)
        except Exception as e:
            log("读取 DSH 注入失败：%r" % e, "WARN")
            return ""

    # -------- 记忆后台：到点写日记、每月衰减 --------
    def _mem_boot(self):
        if not self.mem:
            return

        def loop():
            # 起来先补昨天落下的日记，之后每 10 分钟巡一次；
            # 今天的日记只在设定时间之后才写（不然白天聊两句就生成一篇半截日记）
            while True:
                try:
                    hour = int(self.cfg.get("mem_diary_hour", 23) or 23)
                    now = time.localtime()
                    ocfg = {"host": self.ollama.get("host", "http://127.0.0.1:11434"),
                            "model": self.ollama.get("fast_model") or self.ollama.get("model"),
                            "fast_model": self.ollama.get("fast_model"),
                            "num_ctx": self.ollama.get("num_ctx", 8192)}
                    r = self.mem.maintain(ocfg, publish=self._publish_diary,
                                          include_today=now.tm_hour >= hour)
                    if (r.get("diary") or {}).get("ok") and not (r.get("diary") or {}).get("skipped"):
                        log("日记已写：%s（%s 字）" % (r["diary"].get("file"), r["diary"].get("chars")))
                    if (r.get("decay") or {}).get("ok") and not (r.get("decay") or {}).get("skipped"):
                        log("记忆衰减完成：保留 %s 条，归档 %s 条"
                            % (r["decay"].get("kept"), r["decay"].get("archived")))
                except Exception as e:
                    log("记忆维护失败：%r" % e, "WARN")
                time.sleep(600)

        threading.Thread(target=loop, daemon=True, name="qq-memory").start()

    def _publish_diary(self, day):
        """把日记里的一句话发到 QQ 空间（默认关，设置里打开才发）。"""
        try:
            if str(self.cfg.get("qzone_diary_publish", "0")) in ("0", "False", "false", "no"):
                return {"ok": False, "skipped": True}
            line = self.mem.diary_onesentence(day) if self.mem else ""
            if not line:
                return {"ok": False, "error": "日记里挑不出句子"}
            import sys as _sys
            if r"D:\大肥鱼AI" not in _sys.path:
                _sys.path.insert(0, r"D:\大肥鱼AI")
            import dfy_qzone
            right = int(dfy_qzone._cfg().get("查看权限", 1) or 1)
            r = dfy_qzone.publish(line, ugc_right=right or 1)
            try:
                dfy_qzone.bump_today()
            except Exception:
                pass
            log("日记发空间：%s -> %s" % (line[:24], r.get("ok")))
            return r
        except Exception as e:
            return {"ok": False, "error": repr(e)}

    # -------- 消息解析：把 文字/小黄脸/表情包/图片 都变成模型能读的东西 --------
    def parse_message(self, ev):
        """返回 (文字, 媒体列表)。

        以前只拼 text 段，所以纯表情包消息 text 为空 → 整条被丢掉，
        模型自然"读不懂"对方发的表情。这里把非文字段也翻成人话。
        """
        segs = ev.get("message")
        if not isinstance(segs, list):
            # 老格式：CQ 码字符串
            t = str(ev.get("raw_message") or "")
            t = re.sub(r"\[CQ:face,id=(\d+)[^\]]*\]",
                       lambda m: "［%s］" % FACE_NAMES.get(_int(m.group(1)), "QQ表情"), t)
            t = re.sub(r"\[CQ:(image|mface)[^\]]*\]", "［发来一个表情包/图片］", t)
            t = re.sub(r"\[CQ:record[^\]]*\]", "［发来一条语音］", t)
            t = re.sub(r"\[CQ:[^\]]+\]", "", t)
            return t.strip(), []

        parts, media = [], []
        for seg in segs:
            if not isinstance(seg, dict):
                continue
            ty = str(seg.get("type") or "")
            d = seg.get("data") or {}
            if ty == "text":
                parts.append(str(d.get("text") or ""))
            elif ty == "face":
                parts.append("［%s］" % FACE_NAMES.get(_int(d.get("id")), "QQ表情"))
            elif ty == "mface":
                sm = str(d.get("summary") or d.get("name") or "").strip()
                parts.append("［发来一个表情包%s］" % (("（%s）" % sm) if sm else ""))
                media.append({"kind": "mface", "url": d.get("url") or "",
                              "file": d.get("file") or "", "summary": sm})
            elif ty == "image":
                sm = str(d.get("summary") or "").strip()
                is_sticker = str(d.get("sub_type") or "") not in ("", "0") or "表情" in sm
                what = "表情包" if is_sticker else "图片"
                parts.append("［发来一张%s%s］" % (what, ("（%s）" % sm) if sm else ""))
                media.append({"kind": "image", "url": d.get("url") or "",
                              "file": d.get("file") or "", "summary": sm})
            elif ty == "record":
                parts.append("［发来一条语音］")
                media.append({"kind": "record", "url": d.get("url") or "",
                              "file": d.get("file") or ""})
            elif ty == "video":
                parts.append("［发来一段视频］")
            elif ty in ("json", "xml"):
                parts.append("［发来一张卡片/链接］")
            elif ty == "at":
                parts.append("")
        return "".join(parts).strip(), media

    def _napcat(self, action, params):
        """问 NapCat 要东西（Brain 里没有 OneBot 实例，按 cfg 自己发请求）。"""
        base = str(self.cfg.get("api_base") or "http://127.0.0.1:3000").rstrip("/")
        data = json.dumps(params, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        tok = str(self.cfg.get("access_token") or self.cfg.get("token") or "")
        if tok:
            headers["Authorization"] = "Bearer " + tok
        req = urllib.request.Request(base + "/" + action, data=data, headers=headers)
        with _NO_PROXY.open(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))

    def _read_local(self, p):
        try:
            p = str(p or "")
            if p.startswith("file:/"):
                p = re.sub(r"^file:/{0,3}", "", p)
            if p and os.path.isfile(p):
                with open(p, "rb") as fh:
                    return fh.read()
        except Exception:
            pass
        return b""

    def _fetch_media(self, m):
        """把图片/表情包拿成字节：url → NapCat get_image → 本地缓存文件。

        真实 QQ 里 url 经常缺失（本地缓存模式）或 rkey 过期，
        所以必须有 OneBot 侧的兜底，否则只能回一句"没看清"。
        """
        kind = m.get("kind")
        url = str(m.get("url") or "")
        if url.startswith("http"):
            for ua in ("Mozilla/5.0", "NapCat/1.0"):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": ua})
                    with _NO_PROXY.open(req, timeout=15) as r:
                        data = r.read()
                    if data:
                        return data
                except Exception as e:
                    log("下载%s失败(%s)：%r" % (kind, ua.split("/")[0], e), "WARN")

        fid = str(m.get("file") or "")
        # NapCat 可以把 file 直接换成服务器上的真实路径
        if fid:
            for api in ("get_image", "get_file"):
                try:
                    r = self._napcat(api, {"file": fid})
                    d = (r.get("data") or {}) if isinstance(r, dict) else {}
                    for key in ("file", "path", "base64"):
                        v = str(d.get(key) or "")
                        if key == "base64" and v:
                            try:
                                return base64.b64decode(v)
                            except Exception:
                                pass
                        if v:
                            blob = self._read_local(v)
                            if blob:
                                return blob
                except Exception as e:
                    log("%s 取图失败(%s)：%r" % (api, fid[:16], e), "WARN")

        # 本地缓存兜底：给的路径、NapCat cache、以及 napcat 数据目录下的同名文件
        for p in (fid, str(m.get("path") or ""),
                  os.path.join(r"D:\NapCat\cache", fid),
                  os.path.join(r"D:\NapCat", fid)):
            blob = self._read_local(p)
            if blob:
                return blob
        if fid and len(fid) < 60:
            import glob as _glob
            for pat in (r"D:\NapCat\**\*%s" % _glob.escape(fid),
                        r"D:\本地部署大肥鱼环境\**\*%s" % _glob.escape(fid)):
                try:
                    hit = _glob.glob(pat, recursive=True)
                except Exception:
                    hit = []
                for p in hit[:3]:
                    blob = self._read_local(p)
                    if blob:
                        return blob
        log("取%s失败：url=%s file=%s" % (kind, url[:60] or "-", fid[:40] or "-"), "WARN")
        return b""

    def _vision(self, img_bytes, kind, summary):
        """让视觉模型看一眼，返回一句中文描述（失败返回空串）。"""
        host = self.ollama.get("host", "http://127.0.0.1:11434").rstrip("/")
        model = (self.ollama.get("vision_model") or self.ollama.get("fast_model")
                 or self.ollama.get("model") or "")
        if not model:
            return ""
        ask = ("这是聊天里对方发来的%s%s。不要分析过程，直接输出一句话（不超过25个字）"
               "说清楚：画面是什么、传达什么情绪或态度。只输出这最终一句话，禁止任何其他文字。"
               % ("表情包" if kind == "mface" else "图片",
                  ("（标注：%s）" % summary) if summary else ""))
        # 视觉专职用 qwen3-vl:2b（1.9G）：让它走 CPU、不占 35B 的显存预算，
        # 实测两个模型能同时驻留，35B 完全不被踢（回复 2.6s）；
        # 看图从 35B 冷加载视觉编码器的 47s 降到 ~4s，还省 2-3G 显存。
        small_vision = "2b" in str(model)
        vis_opts = {"temperature": 0.3, "num_predict": 80,
                    "num_ctx": int(self.ollama.get("num_ctx", 3072))}
        if small_vision:
            # 2b 是 thinking 模型且思维链很长：预算给 1000（800 以内正文会被吃空），
            # num_gpu=0 走 CPU 不抢显存；think 必须开，否则正文塞进 thinking
            vis_opts.update({"num_predict": 1000})   # GPU 看图 4s；CPU 要 27s，不值
        else:
            vis_opts.update(self._moe_tune(model))
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": ask,
                          "images": [base64.b64encode(img_bytes).decode("ascii")]}],
            "stream": False,
            "think": True if small_vision else False,
            "keep_alive": self.ollama.get("keep_alive", "15m"),
            "options": vis_opts,
        }
        req = urllib.request.Request(
            host + "/api/chat", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with _NO_PROXY.open(req, timeout=240) as r:
                out = json.loads(r.read().decode("utf-8"))
            msg = out.get("message") or {}
            txt = ((msg.get("content") or "").strip() or
                   re.sub(r"</?think>", "", (msg.get("thinking") or "")).strip())
            txt = re.sub(r"<think>[\s\S]*?</think>", "", txt).strip()
            txt = txt.replace("\n", " ").strip(" 　")
            return txt[:60]
        except Exception as e:
            log("看图失败（%s）：%r" % (model, e), "WARN")
            return ""

    def see_media(self, media, limit=2):
        """返回 [(描述或空, 类型)]，最多看 limit 张，同一张图有缓存。"""
        out = []
        if not media:
            return out
        if str(self.b.get("read_images", True)) in ("0", "False", "false", "no"):
            return out
        for m in media[:limit]:
            raw = self._fetch_media(m)
            if not raw:
                out.append(("", m.get("kind")))
                continue
            key = hashlib.md5(raw).hexdigest()
            with self._img_lock:
                cached = self._img_cache.get(key)
            if cached:
                out.append((cached, m.get("kind")))
                continue
            desc = self._vision(raw, m.get("kind"), m.get("summary"))
            if desc:
                with self._img_lock:
                    self._img_cache[key] = desc
                log("看图结果：%s" % desc)
            out.append((desc, m.get("kind")))
        return out

    # -------- 提示词 --------
    def _memory_text(self):
        if not self.b.get("use_memory", True):
            return ""
        try:
            p = self.b.get("memory_file", "")
            if p and os.path.exists(p):
                m = json.load(open(p, "r", encoding="utf-8"))
                parts = []
                if m.get("user"):
                    parts.append("关于用户：" + str(m["user"]))
                if m.get("relationship"):
                    parts.append("你们的关系：" + str(m["relationship"]))
                facts = m.get("facts") or []
                if facts:
                    parts.append("已知事实：" + "；".join(str(x) for x in facts[-12:]))
                return "\n".join(parts)
        except Exception:
            pass
        return ""

    def _persona(self):
        try:
            p = self.b.get("settings_file", "")
            if p and os.path.exists(p):
                s = json.load(open(p, "r", encoding="utf-8-sig"))
                per = s.get("persona_global") or ""
                if per:
                    return str(per)
        except Exception:
            pass
        return "你是大肥鱼AI，一个在本地电脑上运行的贴心搭档，说话自然、有性格，不喜欢官话套话。"

    def system_prompt(self, scene, query="", who="", recent_stickers=None):
        parts = [self._persona()]
        # ① 能力自知：不说清楚就会出现"我发不了语音"这种自我否认
        parts.append(
            "【你的能力（这些都是真的，被问到就大方承认，别说自己做不到）】\n"
            "· 你会发 QQ 语音条：你说的话可以变成声音发给对方。对方要语音、或你觉得"
            "这句话用声音说更带劲儿时，直接说就行，不用解释你是怎么做到的，也别说"
            "「我发个开关」「你写个标记」这种话。\n"
            "· 你能看懂对方发的图片和表情包（你会收到画面描述）。\n"
            "· 你每天写日记，日记就是你的长期记忆，所以跨天、换个人聊你都记得发生过什么。\n"
            "· 你能发 QQ 空间说说。\n"
            "· 你有「B站鱼塘」：能直接按关键词去 B 站检索视频、看懂字幕、挑出合口味的，"
            "再写成带标题和观后感的推荐日报。所以对方问你会不会刷 B 站、让你推荐 B 站视频时，"
            "大方承认会——你不是用浏览器刷，是后台替他淘视频。对方真要你刷/推荐时，"
            "在回复最后单独写一行 [B站:关键词或口味]（没指定关键词就写 [B站:默认口味]），"
            "系统会真去跑，跑完把日报发回来。只是闲聊、对方没让你刷时别写这个标记。\n"
            "· 你身边有个能动手的助手（DeepBuddy）：真能跑命令、读写文件、装东西。"
            "所以对方让你「看看某个文件夹」「跑个脚本」「装个插件」时，别说自己做不了。\n"
            "【内部约定（对方看不到，别向人解释）】想用声音说这条时，在回复最后单独写"
            " [语音]（方括号里只有这两个字），系统就会把它念成语音条。\n"
            "如果聊着聊着对方要你办件事（看某个文件夹、跑个脚本、改个文件、查个东西），"
            "你判断光靠嘴说解决不了，就在回复最后单独写一行 [执行:要做的事]，"
            "系统会让你的助手真的去办，办完再回来跟你说结果。别为了闲聊乱写这个标记。\n"
            "如果聊着聊着你发现缺某个插件的能力、而装一个就能解决，就在回复最后单独写一行 "
            "[装插件:插件名]，系统会真的去装（插件名可以是 npm 包名，也可以是 github:作者/仓库)。"
            "不确定叫什么就别乱写，先问对方。\n"
            "对方让你「发条空间」「发个说说」「更新一下动态」时，别只嘴上说「这就去发」——"
            "直接在回复最后单独写一行 [发空间:你想发的说说内容|配图:表情包名字]，"
            "系统会替你真发出去，发完你会知道结果。内容要像你平时说话的样子，别写公告腔。"
            "想配图就写「|配图:表情包清单里的名字」；清单里找不到合适的就写「|配图:自己画」，"
            "你会亲手画一幅简笔画配上。只有对方明确让你发空间时才写这个标记，"
            "别自己主动天天发，也别闲聊两句就发一条。\n"
            "对方让你「以后别理某人」「拉黑/屏蔽某人」「去跟某人说清楚再不回他」时，"
            "别只嘴上答应，也别用 [执行:...] 让助手去做（它发不了QQ消息）。"
            "在回复最后单独写一行 [忽略:对方的昵称]，系统会把通知发给那个人本人、"
            "再把她加进忽略名单持久化，以后她发消息你都不回。想自定义对她说的话就写 "
            "[忽略:昵称|你想对她说的话]。")
        # DSH 插件（soul-md 的长期记忆、心情、贴纸提示…）注进来的内容
        dsh = self._dsh_text()
        if dsh:
            parts.append(dsh)
        # 用户画像（对同一用户稳定）放稳定前缀，KV 缓存能命中；
        # 超长会吃 prefill，截到 260 字
        mem = (self._memory_text() or "")[:260]
        if mem:
            parts.append("【你记得的关于这个用户的事】\n" + mem)
        # ---- 动态内容先攒着，最后统一放尾部 ----
        # 35B 的 prefill 是大头（60 t/s），前缀 KV 缓存按 token 逐个比对：
        # 稳定内容放最前、每轮变化的内容（记忆召回/日期/别人在找你）放最后，
        # 第二轮起 ollama 只需算增量，prefill 从 30s 降到 1s 级。
        dyn = []
        # ② 共享记忆池：所有人共用同一份"她的经历"
        if self.mem:
            try:
                hits = self.mem.recall(query, k=int(self.cfg.get("mem_recall", 3) or 3), exclude_who=who) if query else []
                if hits:
                    rows = []
                    for h in hits:
                        rows.append("· %s%s" % (("[" + h["date"] + "] ") if h.get("date") else "",
                                               h["text"]))
                    dyn.append(
                        "【你想起的事（来自你的日记，是所有对话共用的记忆）】\n"
                        + "\n".join(rows[:3])
                        + "\n（自然地用起来就行，别背课文，也别说\"根据我的记忆\"。）")
                others = self.mem.recent_others(exclude_who=who, n=2)
                if others:
                    rows = []
                    for o in others:
                        ago = o.get("ago", 0)
                        when = "刚刚" if ago < 3 else ("%d 分钟前" % ago if ago < 60 else "%d 小时前" % (ago // 60))
                        rows.append("· %s %s 找你说：%s" % (o["name"], when, o["q"]))
                    dyn.append(
                        "【最近别的人也在找你】\n" + "\n".join(rows)
                        + "\n（说明你同时在跟很多人聊，可以自然带一句\"刚才还有人说…\"，"
                          "但别把别人私聊的细节到处说。）")
            except Exception:
                pass
        extra = self.b.get("persona_extra", "")
        if extra:
            parts.append(extra)
        if self.desktop_mode:
            # 桌面版身份/称呼规则（QQ 里也保持一致的人格）
            name_rule = ("你是 Childe 做的本地 AI「大肥鱼」。被问开发者就答 Childe，"
                         "别自称别家公司的模型。用户给你起新昵称就开心接受，当作名字的一部分。")
            parts.append(name_rule)
            try:
                dyn.append("【今天是】" + time.strftime("%Y-%m-%d %A") + "。")
            except Exception:
                pass
        sticker_rule = ""
        if self.stickers:
            hint = self.stickers.list_hints(int(self.b.get("sticker_hint_count", 16)),
                                            query=query or "")
            sticker_rule = (
                "\n\n【表情包】你手上有这些表情包可以配：" + hint +
                "（名字后面的括号是这张的情绪）。必须挑跟对方要求对得上的一张，"
                "对不上就别配，别硬凑；"
                "如果这句回复配一张表情包会更生动，就在「回复：」那行之后另起一行，"
                "只写 [表情包名字]（方括号括住、只写名字，必须是上面清单里的名字）；"
                "不想配就不写，别自己编名字，也别勉强。"
                "\n【硬性要求】对方明确开口要表情包时（比如「发个表情包」「来个舔屏的」「你发啊」「发图」），"
                "你必须真的在最后一行写出 [表情包名字]——光说「这就发」「马上发」而方括号里没名字，"
                "系统就发不出图，等于放空炮，绝对不允许。"
            )
            if recent_stickers:
                sticker_rule += ("\n最近这几张已经发过了：" + "、".join(recent_stickers) +
                                 "。除非对方点名就要这一张，否则别再连着发同一张，换张别的更自然。")
            sticker_rule += ("\n配图时正文要有实际内容，别只写「这就发」「马上发」这种空话——"
                             "说一句跟这张图相配的话再发。")
        tail = ("最多再写一行 [表情包名字]，不要有其他内容。" if sticker_rule
                else "不要再写任何内容。")
        base = ("场景：" + str(scene) + "。回复控制在 %d 字以内。\n"
                "输出格式要求（必须遵守）：思考不超过两句，随后另起一行，"
                "以「回复：」开头给出最终要发出的那句话，这一行之后" + tail) \
               % int(self.b.get("max_reply_chars", 500))
        parts.append(base + sticker_rule)
        parts.append(
            "【对方发图/表情包时】消息里会出现「（对方发来一个表情包，画面是：…）」这种说明。"
            "那是你看图得到的结果，直接顺着这个情绪回应就好；"
            "绝对不要复述「我看到一个表情包」「画面是…」这类话，也别解释说自己在看图。"
            "对方只发表情不说话时，就当成他用表情在跟你说话，接住那个情绪。")
        # 动态内容放最后（见上方注释：稳定前缀 → KV 缓存命中）
        if dyn:
            parts.append("\n\n".join(dyn))
        return "\n\n".join(parts)

    # -------- 模型选择 --------
    def _big_ok(self):
        """22G 级 MoE 能不能跑：可用内存不足时硬加载会把请求干等到超时。

        实测：权重 22.6G，凌晨空闲时可用 20.5G 能跑（热态 12s 一条）；
        白天浏览器/QQ 占住内存只剩 7G 时，加载卡满 150s 也没动静。
        所以发请求前先看一眼内存，不够就自动退小模型，内存缓过来再回去。
        """
        try:
            import psutil
            return psutil.virtual_memory().available >= \
                int(self.ollama.get("moe_min_avail_gb", 4)) * 2**30
        except Exception:
            return True

    def pick_model(self, text, forced=None):
        if forced:
            return forced
        model = self.ollama.get("fast_model", "qwen3:4b")
        kws = self.ollama.get("deep_keywords", []) or []
        low = (text or "").lower()
        hit = any(str(k).lower() in low for k in kws)
        if hit or len(text or "") >= int(self.ollama.get("deep_min_len", 220)):
            model = self.ollama.get("deep_model", model)
        # 大模型内存预检：可用内存不够直接退兜底小模型，保证能秒回
        fb = self.ollama.get("fallback_model")
        if fb and model != fb and self._is_moe_big(model) and not self._big_ok():
            log("可用内存不足（<%dG），%s 暂退 %s" % (
                int(self.ollama.get("moe_min_avail_gb", 4)), model, fb))
            return fb
        # 熔断中：主模型刚卡过，先用兜底顶上，优先保证能回话
        if time.time() < self._slow_until:
            if fb:
                return fb
        if not self.ollama.get("auto_switch", True):
            return model
        return model

    # -------- MoE 大模型调参 --------
    # qwen3.6:35b-a3b 这类 MoE：权重 22.6G，本机 8G 显存 + 32G 内存装不下，
    # 靠「部分层上 GPU + 小 batch + 8 个 P 核 + 小上下文」把它压到可用。
    # 关键：所有请求参数必须完全一致，否则 ollama 会卸载重跑 runner（20s+）。
    _moe_cache = {}

    def _is_moe_big(self, model):
        if model in self._moe_cache:
            return self._moe_cache[model]
        big = False
        low = str(model).lower()
        if "35b" in low or "30b" in low or "a3b" in low:
            big = True
        else:
            try:
                host = self.ollama.get("host", "http://127.0.0.1:11434").rstrip("/")
                with _NO_PROXY.open(host + "/api/tags", timeout=6) as r:
                    for m in json.loads(r.read().decode("utf-8")).get("models", []):
                        if m.get("name") == model and (m.get("size") or 0) >= 15 * 2**30:
                            big = True
                            break
            except Exception:
                pass
        self._moe_cache[model] = big
        return big

    def _moe_tune(self, model):
        if not self._is_moe_big(model):
            return {}
        t = self.ollama.get("moe_tune") or {}
        return {
            "num_batch": int(t.get("num_batch", 512)),   # 大 batch：prefill 吞吐翻倍
            "num_thread": int(t.get("num_thread", 8)),   # 只用 P 核，E 核参战反而拖慢
            "num_gpu": int(t.get("num_gpu", 99)),        # 能塞进 4060 的层全塞
            "num_keep": int(t.get("num_keep", 48)),      # 保留系统提示 KV，少算重复前缀
        }

    # -------- 调 Ollama --------
    def _ask_ollama(self, messages, model, think):
        host = self.ollama.get("host", "http://127.0.0.1:11434").rstrip("/")
        # think 由配置决定（qq_bridge 会按模型能力给合适的值）。
        # 有的模型（如 qwen3-vl:4b-instruct）明确不支持 thinking，传 true 会 400；
        # 在下面用 400 重试兜底自动去掉。
        # qwen3-vl:2b 是 thinking 模型：think=false 会把正文塞进 thinking 导致空回复，
        # 且思维链很长（实测简单任务也要 300+ token）——它当聊天兜底时必须开 think、
        # 给足预算、并走 CPU（不占 35B 显存，两个模型可同时驻留）。
        small_think = "2b" in str(model)
        if small_think:
            think = True
        opts = {
            "num_ctx": int(self.ollama.get("num_ctx", 3072)),
            "num_predict": int(self.ollama.get("max_tokens", 700)),
            "temperature": 0.8,
            **self._moe_tune(model),
        }
        if small_think:
            opts.update({"num_predict": 1200})
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": bool(think),
            "keep_alive": self.ollama.get("keep_alive", "15m"),
            "options": opts,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(host + "/api/chat", data=data,
                                     headers={"Content-Type": "application/json"})
        t0 = time.time()
        try:
            with _NO_PROXY.open(req, timeout=int(self.ollama.get("timeout", 180))) as r:
                out = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as he:
            detail = ""
            try:
                detail = he.read().decode("utf-8", "ignore")
            except Exception:
                pass
            # 模型不支持 thinking → 去掉 think 重发一次（qwen3-vl-instruct 等）
            if he.code == 400 and "think" in detail.lower() and payload.get("think"):
                log("模型 %s 不支持 thinking，自动去掉重试" % model, "WARN")
                payload.pop("think", None)
                req2 = urllib.request.Request(host + "/api/chat",
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"})
                with _NO_PROXY.open(req2, timeout=int(self.ollama.get("timeout", 180))) as r:
                    out = json.loads(r.read().decode("utf-8"))
            else:
                raise
        msg = out.get("message") or {}
        content = (msg.get("content") or "").strip()
        content = re.sub(r"<think>[\s\S]*?</think>", "", content).strip()
        if not content and small_think:
            # 2b 思维链长，预算偶尔还是被吃空 → 从 thinking 里捞最后一句兜底
            th = re.sub(r"</?think>", "", (msg.get("thinking") or "")).strip()
            lines = [x.strip() for x in th.splitlines() if x.strip()]
            content = lines[-1] if lines else ""
            log("2b 正文为空，从思维链兜底一句", "WARN")
        content = self._final_answer(content)
        if not content:
            content = "（想太久没想出来，再说一遍试试？）"
        log("模型 %s 用时 %.1fs，回复 %d 字（prompt %d 字/%d 轮）" % (
            model, time.time() - t0, len(content),
            sum(len(str(m.get("content") or "")) for m in messages), len(messages)))
        return content or "（模型没返回内容）"

    @staticmethod
    def _final_answer(text):
        """小模型经常把分析和草稿混在正文里，这里按「回复：」标记取最后一段干净答案。"""
        t = (text or "").strip()
        if not t:
            return ""
        marks = list(re.finditer(r"(?:\*\*)?\s*(?:最终)?回复\s*(?:\*\*)?\s*[：:]", t))
        if marks:
            t = t[marks[-1].end():].strip()
        t = re.sub(r"^\s*[\*\"'「」]+\s*", "", t)
        return t.strip()

    @staticmethod
    def _extract_sticker(text):
        """从回复末尾剥出 [表情包名字]，返回 (净文字, 表情名或'')。

        模型不一定另起一行，经常直接写在句尾（"...我超听话的~ [猫猫搓手]"），
        所以方括号前允许空格或换行；名字限 1-30 字且不含方括号/换行。
        只有末尾那一个方括号会被当成表情，正文中间的不动。
        """
        t = str(text or "")
        m = re.search(r"[\s]*\[([^\]\[\n]{1,30})\][\s]*$", t)
        name = ""
        if m:
            candidate = m.group(1).strip()
            # 「[语音]」是发语音的开关，不是表情名，必须原样留给后面的语音判断
            if candidate.lower() in ("语音", "voice"):
                return t, ""
            # 带前缀写法：[表情包:怎么个事] / [表情:xxx] / [贴纸:xxx] → 真实名字是冒号后的部分
            pre = re.match(r"^(?:表情包|表情|贴纸)\s*[:：]\s*(.+)$", candidate)
            if pre:
                candidate = pre.group(1).strip()
            # 名字里带明显句子标点的（模型在写正文而非表情名）不认
            if not re.search(r"[，。！？；:：]", candidate):
                name = candidate
                t = t[:m.start()].rstrip()
        # 兜底：带明确前缀的标记写在正文中间也能识别（前缀明确，误判风险低）
        if not name:
            mm = re.search(r"\[\s*(?:表情包|表情|贴纸)\s*[:：]\s*([^\]\[\n]{1,30})\]", t)
            if mm:
                cand = mm.group(1).strip()
                if not re.search(r"[，。！？；:：]", cand):
                    name = cand
                    t = (t[:mm.start()] + t[mm.end():]).strip()
        return t, name

    def reply(self, session_key, user_text, scene="QQ私聊", forced_model=None, who="", name=""):
        model = self.pick_model(user_text, forced_model)
        think = self.ollama.get("think_for_deep", True) if model == self.ollama.get("deep_model") \
            else self.ollama.get("think_for_fast", False)
        msgs = [{"role": "system", "content": self.system_prompt(
            scene, query=user_text, who=who,
            recent_stickers=(self._recent_stickers.get(session_key) or [])[-3:] or None)}]
        msgs += self.sessions.get(session_key)
        # 群里把说话人带上，否则张三问完李四接着问会串台
        if name and "群" in str(scene):
            msgs.append({"role": "user", "content": "%s：%s" % (name, user_text)})
            spoke = "%s：%s" % (name, user_text)
        else:
            msgs.append({"role": "user", "content": user_text})
            spoke = user_text
        with self.model_lock:
            if self.last_model and self.last_model != model:
                log("切换模型 %s -> %s（首次加载约需数秒）" % (self.last_model, model))
            self.last_model = model
            try:
                answer = self._ask_ollama(msgs, model, think)
                self._fail_streak = 0
            except Exception as e:
                # 主模型超时/连不上：立刻换兜底小模型重试，别把整条链路堵死
                fb = self.ollama.get("fallback_model")
                self._fail_streak += 1
                log("模型 %s 没响应（%r）" % (model, e), "WARN")
                answer = ""
                if fb and model != fb:
                    log("10 分钟内改用兜底 %s" % fb, "WARN")
                    self._slow_until = time.time() + 600
                    self.last_model = fb
                    try:
                        answer = self._ask_ollama(msgs, fb, False)
                    except Exception as e2:
                        log("兜底模型也失败：%r" % e2, "ERROR")
                if not answer:
                    # 至少让对面知道是卡了，不是不理人
                    answer = "（我这会儿脑子有点卡，你再说一句我马上接上～）"
        # 剥出表情包标记
        answer, sticker_hint = self._extract_sticker(answer)
        # 兜底：模型有时不写方括号，直接在句尾写「猫猫搓手（兴奋）」
        # 只有这个名字确实在图库里才认，避免误删正文
        if not sticker_hint and self.stickers:
            m = re.search(r"[ \t]*([\u4e00-\u9fa5A-Za-z0-9_]{1,12})[（(]([^（）()]{0,8})[)）][ \t]*$", answer)
            if m:
                cand = m.group(1).strip()
                if cand in self.stickers.by_name:
                    sticker_hint = cand
                    answer = answer[:m.start()].rstrip()
        # 再兜底：模型把表情名单独写在最后一行，但忘了加方括号
        if not sticker_hint and self.stickers and answer:
            lines = answer.rstrip().splitlines()
            if len(lines) > 1:
                last = lines[-1].strip()
                cand = re.sub(r"^[\[【]|[\]】]$", "", last).strip()
                cand = re.sub(r"[（(][^（）()]{0,8}[)）]$", "", cand).strip()
                if cand and cand in self.stickers.by_name:
                    sticker_hint = cand
                    answer = "\n".join(lines[:-1]).rstrip()
        self.sessions.add(session_key, "user", spoke)
        self.sessions.add(session_key, "assistant", answer)
        # 写进共享流水：日记就是从这些流水里长出来的
        if self.mem:
            try:
                self.mem.note_turn(who or session_key, name, scene, user_text, answer)
            except Exception as e:
                log("记忆写入失败：%r" % e, "WARN")
        # 通知 DSH 插件这一轮聊了什么（心情插件据此判断涨跌）
        if self.dsh:
            try:
                self.dsh.note_turn(user_text, answer)
            except Exception:
                pass
        mx = int(self.b.get("max_reply_chars", 500))
        if len(answer) > mx:
            answer = answer[:mx].rstrip() + "…"
        return answer, model, sticker_hint


# ---------------------------------------------------------------- OneBot
class OneBot:
    def __init__(self, cfg, brain):
        self.cfg = cfg.get("onebot", {})
        self.brain = brain
        self.voice = Voice(cfg)
        self.api = (self.cfg.get("api_base") or "http://127.0.0.1:3000").rstrip("/")
        self.token = self.cfg.get("access_token") or ""
        self.queue = queue.Queue(maxsize=200)
        self.seen = []          # 简单的 message_id 去重
        self.seen_lock = threading.Lock()
        self.forced = {}        # 会话 -> 强制模型
        self.recent = []        # 最近收发消息（给主界面检测框显示）
        self.recent_lock = threading.Lock()
        self.api_ok = None      # OneBot API 最近是否可达（None=未探测）
        # 发空间防刷：最近一次发布时间与内容（冷却期内不再发）
        self._qzone_last = (0.0, "")
        # 会话 -> 上次发出去的表情包名（对方追问「你发啊」时重发那张）
        self._last_sticker = {}
        self._last_api_check = 0
        # brain 模式：standalone=自己直连Ollama；desktop=转给桌面大肥鱼AI主程序
        self.brain_mode = self.cfg.get("brain_mode", "standalone")
        # 黑名单：内存副本 + 持久化回调（由 qq_bridge 注入）
        self.blacklist = [str(x) for x in (self.cfg.get("user_blacklist") or [])]
        self.blacklist_save = self.cfg.get("blacklist_save")
        for _ in range(3):
            threading.Thread(target=self._worker, daemon=True).start()

    # ---- 主动调用 ----
    def _api(self, action, params):
        url = self.api + "/" + action
        data = json.dumps(params, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(url, data=data, headers=headers)
        with _NO_PROXY.open(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    def send(self, kind, target_id, text, at_user=None, segments=None):
        msg = []
        if at_user:
            msg.append({"type": "at", "data": {"qq": str(at_user)}})
            msg.append({"type": "text", "data": {"text": " "}})
        if text:
            msg.append({"type": "text", "data": {"text": text}})
        # 表情包/图片段（image base64），跟在文字后面一起发
        if segments:
            for seg in segments:
                if seg:
                    msg.append(seg)
        if not msg:
            return None
        params = {"message_type": kind, "message": msg}
        if kind == "group":
            params["group_id"] = int(target_id)
        else:
            params["user_id"] = int(target_id)
        try:
            return self._api("send_msg", params)
        except Exception as e:
            log("发送失败(%s %s): %r" % (kind, target_id, e), "ERROR")
            return None

    # ---- 听语音：record 段 → 音频文件 → whisper 转文字 ----
    _asr_model = None
    _asr_lock = threading.Lock()
    _asr_cache = {}         # file_id → 转写文本

    def _asr(self):
        """懒加载 whisper（int8 量化，CPU 也能跑）。"""
        if OneBot._asr_model is None:
            with OneBot._asr_lock:
                if OneBot._asr_model is None:
                    try:
                        from faster_whisper import WhisperModel
                        # 模型缺失时走国内镜像下载，并清掉代理避免 502
                        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
                        for _k in ("http_proxy", "https_proxy", "HTTP_PROXY",
                                   "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
                            os.environ.pop(_k, None)
                        log("加载 whisper small（首次约几秒）...")
                        OneBot._asr_model = WhisperModel(
                            "small", device="cpu", compute_type="int8")
                        log("whisper 就绪")
                    except Exception as e:
                        log("whisper 加载失败：%r（语音将无法转文字）" % e, "WARN")
                        OneBot._asr_model = False
        return OneBot._asr_model or None

    def hear_record(self, m):
        """把一条语音转成中文文本，失败返回空串。"""
        fid = str(m.get("file") or "")
        if not fid:
            return ""
        if fid in OneBot._asr_cache:
            return OneBot._asr_cache[fid]
        # 1) 先问 OneBot 要本地音频文件（NapCat 会转成 mp3）
        path = ""
        # 有的客户端直接把本地路径塞在 file 里，能读就省一次 API
        cand = re.sub(r"^file:/{0,3}", "", fid)
        if os.path.isfile(cand):
            path = cand
        if not path:
            try:
                r = self._api("get_record", {"file": fid, "out_format": "mp3"})
                if r.get("retcode") in (0, None):
                    path = str((r.get("data") or {}).get("file") or "")
            except Exception as e:
                log("get_record 失败(%s)：%r" % (fid[:12], e), "WARN")
        # 2) 拿不到就试着直接下载 url
        raw = b""
        url = str(m.get("url") or "")
        if (not path or not os.path.isfile(path)) and url.startswith("http"):
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0", "Referer": "https://conn.qq.com/"})
                with _NO_PROXY.open(req, timeout=15) as r2:
                    raw = r2.read()
            except Exception as e:
                log("下载语音失败：%r" % e, "WARN")
                return ""
        if not path or not os.path.isfile(path):
            if not raw:
                log("语音取不到音频文件（file=%s）" % fid[:40], "WARN")
                return ""
            path = os.path.join(r"D:\大肥鱼缓存", "voice_in_%s.mp3" % fid[:16])
            try:
                with open(path, "wb") as fh:
                    fh.write(raw)
            except Exception:
                return ""
        # 3) whisper 转写
        model = self._asr()
        if not model:
            return ""
        try:
            segs, _info = model.transcribe(path, language="zh", vad_filter=True)
            txt = "".join(s.text for s in segs).strip()
            txt = re.sub(r"\s+", "", txt)[:120]
            OneBot._asr_cache[fid] = txt
            if txt:
                log("语音转写：%s" % txt)
            return txt
        except Exception as e:
            log("whisper 转写失败：%r" % e, "WARN")
            return ""

    def _record(self, direction, text, who="", sticker=""):
        """记一条最近消息，给主界面检测框显示（内存里，最多 60 条）。"""
        with self.recent_lock:
            self.recent.append({
                "t": time.strftime("%H:%M:%S"),
                "dir": direction,               # in=收到 / out=发出
                "who": str(who or ""),
                "text": str(text or "")[:300],
                "sticker": str(sticker or ""),
            })
            if len(self.recent) > 60:
                del self.recent[:len(self.recent) - 60]

    # ---- 事件处理 ----
    def _dedup(self, mid):
        with self.seen_lock:
            if mid in self.seen:
                return False
            self.seen.append(mid)
            if len(self.seen) > 300:
                del self.seen[:150]
        return True

    def on_event(self, ev):
        try:
            if ev.get("post_type") != "message":
                return
            mid = ev.get("message_id")
            if mid is not None and not self._dedup(mid):
                return
            # 文字 + 小黄脸 + 表情包/图片 全都要，别再把纯表情消息整条丢掉
            text, media = self.brain.parse_message(ev)
            text = re.sub(r"\[CQ:[^\]]+\]", "", text).strip()
            if not text and not media:
                return
            uid = str(ev.get("user_id") or "")
            if uid in self.blacklist:
                return
            sn = ev.get("sender") or {}
            uname = str(sn.get("card") or sn.get("nickname") or "").strip()[:24]
            msg_type = ev.get("message_type")
            self_id = str(ev.get("self_id") or "")

            if msg_type == "private":
                if not self.cfg.get("reply_private", True):
                    return
                kind, target = "private", uid
                clean, forced = self._strip_prefix(text)
                scene = "QQ 私聊"
            elif msg_type == "group":
                gid = str(ev.get("group_id") or "")
                wl = self.cfg.get("group_whitelist") or []
                if wl and gid not in [str(x) for x in wl]:
                    return
                at_me = ("[CQ:at,qq=%s" % self_id) in str(ev.get("raw_message") or "") or \
                        ("[CQ:at,qq=%s]" % self_id) in str(ev.get("raw_message") or "")
                prefixes = self.cfg.get("reply_group_prefix") or []
                has_prefix = any(str(p) and text.startswith(str(p)) for p in prefixes)
                if self.cfg.get("reply_group_at", True) and at_me:
                    clean, forced = self._strip_prefix(text)
                elif has_prefix:
                    clean, forced = self._strip_prefix(text)
                else:
                    return
                kind, target = "group", gid
                scene = "QQ 群聊（群里还有其他人在看，语气可以更活泼一点）"
            else:
                return

            clean = (clean or "").strip()
            # 先听语音（record），剩下的图片/表情包交给视觉模型看
            records = [m for m in media if m.get("kind") == "record"]
            media = [m for m in media if m.get("kind") != "record"]
            heard = []
            for m in records[:2]:
                txt = self.hear_record(m)
                heard.append(txt if txt else None)
            if records:
                got = [t for t in heard if t]
                if got:
                    clean = (clean.replace("［发来一条语音］", "").strip() + "\n" +
                             "\n".join("（对方发来一条语音，说的是：%s）" % t for t in got)).strip()
                else:
                    clean += "\n（对方发来一条语音，但我没能听清内容）"
            # 有图/表情包就让视觉模型看一眼，把画面翻译成文字给大脑
            if media:
                seen = self.brain.see_media(media)
                lines = []
                for i, (desc, kind) in enumerate(seen):
                    sm = str((media[i] or {}).get("summary") or "").strip() if i < len(media) else ""
                    what = "一个表情包" if kind == "mface" else "一张图"
                    if desc:
                        lines.append("（对方发来%s，画面是：%s）" % (what, desc))
                    elif sm:
                        # 图没取到，但至少知道它叫什么，别让大脑以为啥都没有
                        lines.append("（对方发来%s，名字是「%s」，画面没读取成功）" % (what, sm))
                    else:
                        lines.append("（对方发来%s，但没看清内容）" % what)
                if lines:
                    clean = (clean + "\n" + "\n".join(lines)).strip()
            if not clean:
                return
            self._record("in", clean, "%s:%s" % (kind, uid))
            try:
                self.queue.put_nowait((kind, target, uid, clean, scene, forced, uname))
            except queue.Full:
                log("队列已满，丢弃一条消息", "WARN")
        except Exception:
            log("事件解析异常:\n" + traceback.format_exc(), "ERROR")

    def _strip_prefix(self, text):
        forced = None
        t = (text or "").strip()
        for p in ("鱼鱼", "大肥鱼", "肥鱼", "/ai"):
            if t.startswith(p):
                t = t[len(p):].lstrip(" ，,：:")
        m = re.match(r"^/(模型|model)\s*(\S+)?", t)
        if m:
            forced = m.group(2)
            t = t[m.end():].strip()
        return t, forced

    def _worker(self):
        while True:
            try:
                item = self.queue.get()
                kind, target, uid, text, scene, forced = item[:6]
                uname = str(item[6]) if len(item) > 6 else ""
                sess = "%s_%s" % (kind, target)
                low = text.strip()
                # ---- 内置指令 ----
                if low in ("/清空", "/clear", "清空记忆"):
                    self.brain.sessions.clear(sess)
                    self.send(kind, target, "好，咱们这段聊天我忘掉了，重新来~")
                    continue
                if low in ("/状态", "/status"):
                    self.send(kind, target, "在呢～当前模型：%s" % (self.brain.last_model or "未加载"))
                    continue
                if low in ("/记忆", "/memory"):
                    if not self.brain.mem:
                        self.send(kind, target, "记忆模块没加载起来")
                        continue
                    st = self.brain.mem.status()
                    log("查询记忆池：%s 条" % st["entries"])
                    self.send(kind, target,
                              "记忆池：%s 条碎片，平均权重 %.2f\n最近日记：%s\n上次衰减：%s"
                              % (st["entries"], st["weight_avg"], st["last_diary"] or "还没写",
                                 st["last_decay"] or "还没做"))
                    continue
                if low in ("/日记", "/diary"):
                    if not self.brain.mem:
                        self.send(kind, target, "记忆模块没加载起来")
                        continue
                    txt = self.brain.mem.read_diary() or self.brain.mem.read_diary(
                        time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400)))
                    log("查询日记：%s 字" % len(txt))
                    self.send(kind, target, txt[:600] if txt else "今天还没写日记，等我晚上慢慢记～")
                    continue
                if low in ("/写日记", "/diary now"):
                    if not self.brain.mem:
                        self.send(kind, target, "记忆模块没加载起来")
                        continue
                    ocfg = {"host": self.brain.ollama.get("host", "http://127.0.0.1:11434"),
                            "model": self.brain.ollama.get("fast_model") or self.brain.ollama.get("model"),
                            "num_ctx": self.brain.ollama.get("num_ctx", 8192)}
                    r = self.brain.mem.write_diary(force=True, cfg=ocfg)
                    if r.get("ok"):
                        self.send(kind, target, self.brain.mem.read_diary()[:600])
                    else:
                        self.send(kind, target, "日记没写成：%s" % r.get("error"))
                    continue
                if low in ("/帮助", "/help"):
                    self.send(kind, target,
                             "直接跟我说话就行。群聊 @我 或 打「鱼鱼」开头。\n"
                             "/清空 清掉这段上下文\n/状态 看当前模型\n"
                             "/表情 开心 发一张指定表情包\n/贴纸 摸鱼 从大贴纸库里发一张\n/语音 文字 把这句话念成语音条\n"
                             "/日记 看我今天写的日记\n/写日记 现在就写一篇\n/记忆 记忆池状态\n"
                             "/心情 看我现在的心情\n/插件 看插件转接口状态\n"
                             "/搜插件 关键词 在 DSH 官方目录里搜（和 DSH 市场同一份）\n"
                             "/装插件 名字 真装一个插件（装进 DSH，两边共用）\n"
                             "/市场 在本机弹出插件市场窗口\n/执行 任务 让我真的动手做一件事\n"
                             "发语音条给我我能听懂哦~ 想让我用语音答就说「发条语音」\n"
                             "/模型 8b 临时用大模型（管理员）")
                    continue
                # 手动发表情包：/表情 开心
                msticker = re.match(r"^/(?:表情|sticker)\s*(.*)$", low)
                if msticker and self.brain.stickers:
                    hint = msticker.group(1).strip()
                    it, path = self.brain.stickers.pick(hint)
                    seg = self.brain.stickers.image_segment(path) if path else None
                    if seg:
                        self.send(kind, target, "", segments=[seg])
                        log("手动发表情包：%s" % (it.get("name") if it else hint))
                    else:
                        self.send(kind, target, "没找到这张表情包，换个名字试试（比如 /表情 开心）")
                    continue
                # 用 DSH 贴纸插件（nekowhale-stickers）发图：/贴纸 摸鱼
                mdsh_sticker = re.match(r"^/(?:贴纸|dsh贴纸)\s*(.*)$", low)
                if mdsh_sticker and self.brain.dsh:
                    kw = mdsh_sticker.group(1).strip() or "开心"
                    try:
                        r1 = self.brain.dsh.call_tool("sticker_search", {"query": kw, "limit": 1})
                        txt = ((r1 or {}).get("result") or {}).get("text") or ""
                        first = txt.split("\n")[0].split("｜")[0].strip() if txt else ""
                        if not first:
                            self.send(kind, target, "贴纸库里没找到「%s」，换个词试试" % kw)
                            continue
                        r2 = self.brain.dsh.call_tool("sticker_send", {"name": first})
                        out = ((r2 or {}).get("result") or {}).get("text") or ""
                        murl = re.search(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", out) \
                            or re.search(r"(https?://[^\s)\]]+\.(?:gif|png|jpg|jpeg|webp))", out)
                        if murl:
                            self.send(kind, target, "", segments=[{"type": "image", "data": {"file": murl.group(1)}}])
                            log("DSH 贴纸已发送：%s" % first[:30])
                        else:
                            self.send(kind, target, "贴纸找到了但没取到图片：%s" % (out[:120] or "空返回"))
                    except Exception as e:
                        self.send(kind, target, "贴纸插件出错了：%s" % str(e)[:80])
                    continue
                # 看心情（读 feiyu-mood 插件的真实状态）
                if low in ("/心情", "/mood"):
                    line = self.brain.dsh.mood_line() if self.brain.dsh else ""
                    self.send(kind, target, line or "心情插件还没连上")
                    continue
                # 看插件转接口状态
                if low in ("/插件", "/dsh"):
                    if not self.brain.dsh:
                        self.send(kind, target, "转接口没初始化")
                        continue
                    st = self.brain.dsh.status()
                    names = [n for n, ok in (st.get("plugins") or {}).items() if ok]
                    self.send(kind, target,
                              "DSH 转接口：%s\n已加载插件：%s\n可用工具 %d 个"
                              % ("在线" if st.get("online") else "离线", "、".join(names) or "无", st.get("tools") or 0))
                    continue
                # 打开插件市场窗口：/市场
                if low in ("/市场", "/market", "/插件市场"):
                    try:
                        if r"D:\大肥鱼AI" not in sys.path:
                            sys.path.insert(0, r"D:\大肥鱼AI")
                        import dfy_market as _mk
                        r = _mk.open_window()
                        if r.get("ok"):
                            self.send(kind, target, "插件市场窗口打开啦～（%s）" %
                                      ("本来就开着" if r.get("already") else "和 DSH 同一份目录"))
                        else:
                            self.send(kind, target, "打不开市场窗口：%s" % (r.get("error") or "未知原因"))
                    except Exception as e:
                        self.send(kind, target, "打开市场失败：%r" % e)
                    continue
                # 搜插件：/搜插件 记忆（npm + GitHub + Gitee 一起搜）
                mfind = re.match(r"^/(?:搜插件|找插件|插件搜索|find_plugin)\s*(.*)$", low)
                if mfind and self.brain.dsh:
                    kw = mfind.group(1).strip() or "dsh plugin"
                    log("搜插件：%s" % kw)
                    self.send(kind, target, "搜着呢…")
                    try:
                        items = self.brain.dsh.market_search(kw, 8)
                    except Exception as e:
                        items = []
                        log("搜插件失败：%r" % e, "WARN")
                    if not items:
                        self.send(kind, target, "没搜到「%s」相关的插件" % kw)
                        continue
                    rows = []
                    for i, it in enumerate(items[:6], 1):
                        star = (" ★%s" % it.get("stars")) if it.get("stars") else ""
                        rows.append("%d. [%s] %s%s\n   %s" % (
                            i, it.get("source", "?"), it.get("name"), star,
                            (it.get("description") or "")[:60]))
                    self.send(kind, target,
                              "「%s」找到这些：\n%s\n\n要装就说 /装插件 名字" % (kw, "\n".join(rows)))
                    continue
                # 装插件：/装插件 名字 或 /装插件 github:owner/repo
                minst = re.match(r"^/(?:装插件|安装插件|install_plugin)\s+(.+)$", low)
                if minst and self.brain.dsh:
                    name = minst.group(1).strip()
                    log("装插件：%s" % name)
                    self.send(kind, target, "开始装 %s，稍等…" % name)
                    try:
                        r = self.brain.dsh.market_install(name)
                    except Exception as e:
                        r = {"ok": False, "error": repr(e)}
                    if r.get("ok"):
                        self.send(kind, target, "装好了：%s。正在重启插件宿主…" % (r.get("name") or name))
                        rr = self.brain.dsh.restart_host()
                        self.send(kind, target, "宿主已重启：%s"
                                  % ("在线" if rr.get("ok") else ("失败 " + str(rr.get("error"))[:80])))
                    else:
                        self.send(kind, target, "没装上：%s" % str(r.get("error"))[:200])
                    continue
                # 执行任务：/执行 把 xxx 目录下的图片按日期重命名
                mrun = re.match(r"^/(?:执行|干活|agent)\s+(.+)$", low, re.S)
                if mrun:
                    task = mrun.group(1).strip()
                    if not task:
                        continue
                    if kind == "group":
                        self.send(kind, target, "群里不跑任务，私聊我说吧")
                        continue
                    self.send(kind, target, "好，我去做：%s\n跑完回你。" % task[:50])
                    outer = self

                    def _run_agent(tid=None, _t=task):
                        try:
                            import sys as _s
                            if r"D:\大肥鱼AI" not in _s.path:
                                _s.path.append(r"D:\大肥鱼AI")
                            import dfy_buddy as _bd
                            r = _bd.run(_t, timeout=240)
                            txt = (r.get("text") or r.get("error") or "").strip()
                            outer.send(kind, target, "搞定：\n%s" % (txt[:900] or "（没有输出）"))
                        except Exception as e:
                            outer.send(kind, target, "执行出错了：%s" % str(e)[:150])

                    threading.Thread(target=_run_agent, daemon=True).start()
                    continue
                # 手动发语音：/语音 在的呀
                mvoice = re.match(r"^/(?:语音|tts|voice)\s+(.+)$", low, re.S)
                if mvoice:
                    say = mvoice.group(1).strip()
                    p = self.voice.synth(say)
                    if p:
                        self.send(kind, target, "", segments=[Voice.segment(p)])
                        log("手动发语音：%s" % say[:30])
                    else:
                        self.send(kind, target, "语音合成没成功（检查 edge-tts 是否可用/开关有没有开）")
                    continue

                answer, model, sticker_hint = self.brain.reply(
                    sess, text, scene, forced or self.forced.get(sess),
                    who=uid, name=uname)
                # 忽略/拉黑：模型写 [忽略:名字] 就通知对方本人并加入黑名单（优先处理，避免被通用执行误判）
                answer = self._do_ignore_marker(kind, target, answer)
                # 插件自装：模型在回复里写 [装插件:xxx] 就真去装（它自己判断缺什么能力）
                answer = self._do_plugin_marker(kind, target, answer)
                # 发空间：模型写 [发空间:内容] 就真发一条说说，回复换成结果
                answer = self._do_qzone_marker(answer)
                # 执行：模型写 [执行:任务] 就拉起 DeepBuddy 真去办，办完回话
                answer = self._do_exec_marker(kind, target, answer)
                # B站鱼塘：模型写 [B站:关键词] 就后台跑一轮，日报发回这个会话
                answer = self._do_bili_marker(kind, target, answer)
                # 模型自动配图：按它写出的 [表情包名字] 找图
                segs = []
                # 兜底：对方要表情包，模型却只嘴上答应（没写 [名字]）→ 自己挑一张补上。
                # 5:18 那次连着三句「咕噜这就发，搓搓金猪～」一张图都没发，就是缺这层。
                if not sticker_hint and self.brain.stickers and self._wants_sticker(text):
                    tn = self._tail_sticker_name(answer)
                    if tn:
                        sticker_hint = tn
                        log("句尾写了表情包名字但没加括号，认作：%s" % tn)
                    else:
                        it2, p2 = self._auto_sticker(text, sess)
                        if p2:
                            sticker_hint = str((it2 or {}).get("name") or "")
                            log("对方要表情包，模型没给标记，自动补一张：%s" % sticker_hint)
                if sticker_hint and self.brain.stickers:
                    it, path = self.brain.stickers.pick(sticker_hint)
                    seg = self.brain.stickers.image_segment(path) if path else None
                    if seg:
                        segs.append(seg)
                        log("自动配表情包：%s（模型写的是 %s）" % (it.get("name"), sticker_hint))
                # ---- 语音条 ----
                want_voice, answer = self._voice_decision(answer, low)
                vpath = self.voice.synth(answer) if (want_voice and answer) else None
                at = (uid if kind == "group" else None)
                if vpath:
                    vseg = Voice.segment(vpath)
                    if self.voice.also_text and answer:
                        self.send(kind, target, answer, at_user=at, segments=[vseg])
                    else:
                        # 只发语音：文字不落屏（真发语音那样）
                        self.send(kind, target, "", at_user=at, segments=[vseg])
                    # 语音和图片塞进同一条消息时，QQ 客户端只显示语音、把图吞掉
                    # （5:50 实测：NapCat 记录是 [语音][图片]，手机上只有语音），
                    # 所以图片单独再发一条。
                    if segs:
                        log("语音和表情包分开两条发")
                        time.sleep(1.0)
                        try:
                            self.send(kind, target, "", at_user=None, segments=segs)
                        except Exception as e:
                            log("单独发表情包失败：%r" % e, "WARN")
                    log("发了语音条（%.1fKB，音色 %s）" % (
                        os.path.getsize(vpath) / 1024.0, self.voice.voice))
                else:
                    if want_voice and answer:
                        log("语音合成没成功，退回文字", "WARN")
                    self.send(kind, target, answer, at_user=at, segments=segs)
                used_sticker = ""
                if segs and sticker_hint and self.brain.stickers:
                    it, _ = self.brain.stickers.pick(sticker_hint)
                    used_sticker = (it or {}).get("name") or sticker_hint
                if used_sticker:
                    self._last_sticker[sess] = used_sticker
                    if getattr(self.brain, "_recent_stickers", None) is not None:
                        rl = self.brain._recent_stickers.setdefault(sess, [])
                        self.brain._recent_stickers[sess] = (rl + [used_sticker])[-5:]
                self._record("out", ("［语音］" + answer) if vpath else answer,
                             "%s:%s" % (kind, target), used_sticker)
            except Exception:
                log("工作线程异常:\n" + traceback.format_exc(), "ERROR")

    # ---- 插件/执行：模型自发写的标记 ----
    # 用户明说要表情包、模型却只答应不写标记时，兜底替他挑一张。
    _PIC_ASK = re.compile(r"(表情包|发表情|贴纸|动图|gif|GIF|发个图|来张|来一张|舔屏"
                          r"|发啊|还没发|怎么不发|发了没|你发)")
    _PIC_NO = re.compile(r"(别发|不要发|别发表情|不要表情|别再发|停止发)")
    # 光秃秃的催图：「你发啊」「你发」「还没发」——必须带“发”字，不能是“啊”“好”这种
    _BARE_NUDGE = re.compile(r"^(?:你)?(?:到底|怎么|还|还没)?(?:发|发表情包?)"
                             r"(?:了|没)?(?:啊|呀|呢|吧)?[\s。！？!?～~]*$")

    def _wants_sticker(self, text):
        text = str(text or "")
        if self._PIC_NO.search(text):
            return False
        if len(text) <= 8 and self._BARE_NUDGE.match(text.strip()):
            return True
        return bool(self._PIC_ASK.search(text))

    def _tail_sticker_name(self, answer):
        """模型把表情包名字直接写在句尾、忘了加方括号时（「…搓搓金猪～」）认出来。"""
        sp = getattr(self.brain, "stickers", None)
        if not sp or not sp.items:
            return ""
        tail = str(answer or "").strip()[-10:]
        best, ln = "", 0
        for it in sp.items:
            n = str(it.get("name") or "").strip()
            if len(n) >= 2 and n in tail and len(n) > ln:
                if os.path.exists(os.path.join(sp.GIF_DIR, str(it.get("hash") or "") + ".gif")):
                    best, ln = n, len(n)
        return best

    def _auto_sticker(self, text, session_key=""):
        """按用户原话挑一张表情包。返回 (条目, gif 路径)；挑不到返回 (None, '')。"""
        sp = getattr(self.brain, "stickers", None)
        if not sp or not sp.items:
            return None, ""
        # 0) 单纯催图就重发上次那张
        if session_key and len(str(text or "").strip()) <= 8:
            last = self._last_sticker.get(session_key) or ""
            if last:
                it, p = sp.pick(last)
                if p and it:
                    return it, p
        # 1) 拿用户原话去撞图库的名字/标签/情绪，取「命中得最长」的那张
        #    （"舔屏类型" 会撞到标签「舔屏」→ 那张名字就叫「舔」）
        blob = str(text or "")
        best, best_len = None, 1
        for it in sp.items:
            cands = [it.get("name"), it.get("emotion")] + list(it.get("tags") or [])
            for c in cands:
                s = str(c or "").strip()
                if len(s) >= 2 and s in blob and len(s) > best_len:
                    if os.path.exists(os.path.join(sp.GIF_DIR, str(it.get("hash") or "") + ".gif")):
                        best, best_len = it, len(s)
        if best:
            return best, os.path.join(sp.GIF_DIR, str(best.get("hash") or "") + ".gif")
        # 2) 情绪兜底
        for emo in ("开心", "兴奋", "卖萌"):
            for it in sp.items:
                if str(it.get("emotion") or "") == emo:
                    p = os.path.join(sp.GIF_DIR, str(it.get("hash") or "") + ".gif")
                    if os.path.exists(p):
                        return it, p
        return None, ""

    def _do_qzone_marker(self, answer):
        """模型写 [发空间:内容(|配图:…)] → 真发一条说说，回复换成结果。

        配图：写「配图:表情包名」用图库里的图；写「配图:自己画」或找不到图时，
        让模型自己画一幅 SVG 简笔画转 PNG；都失败降级纯文字。
        防刷：冷却 600s、同内容 1h 去重、每日上限（qzone_每日上限）。
        """
        pat = (r"\s*[\[【]\s*(?:发空间|发说说|发QQ空间|发条空间)\s*[:：]\s*"
               r"([^\]】]+)[\]】]\s*")
        m = re.search(pat, answer)
        if not m:
            return answer
        raw = m.group(1).strip()
        # 内容|配图：xxx
        # 模型经常写全角「｜」和「配图：」，只认半角 | 的话会把
        # 「配图:猫猫搓手」整段留在正文里、配图变成 0 张（16:57 那次就是这么翻车的）
        pic_name = ""
        mm = re.search(r"[|｜]\s*配图\s*[:：]\s*(.+)$", raw)
        if mm:
            pic_name = mm.group(1).strip()
            raw = raw[:mm.start()].strip()
        elif "|" in raw or "｜" in raw:
            sep = "|" if "|" in raw else "｜"
            raw, _, tail = raw.partition(sep)
            m2 = re.search(r"配图\s*[:：]\s*(.+)", tail)
            pic_name = (m2.group(1).strip() if m2 else tail.strip())
            raw = raw.strip()
        text = raw.strip() or "（咕噜咕噜……今天有点沉默）"
        answer = re.sub(pat, "", answer).strip()

        # 防刷：模型有时每轮都主动想发（用户没要求）——冷却收紧到 600s，
        # 且触发前检查每日上限
        now = time.time()
        last_t, last_text = self._qzone_last
        if now - last_t < 600 or (text == last_text and now - last_t < 3600):
            log("发空间触发冷却，跳过：%s" % text[:30])
            return (answer + "\n（刚发过一条啦，空间也讲究细水长流～）").strip()

        # ---- 配图 ----
        images = []
        img_note = ""
        if pic_name:
            # 「自己画/画一幅/简笔画/AI画」直接走模型简笔画，不做表情包模糊匹配
            want_doodle = any(k in pic_name for k in ("自己画", "画一幅", "简笔画", "AI画", "画一个"))
            path = ""
            if not want_doodle and self.brain and self.brain.stickers:
                it, path = self.brain.stickers.pick(pic_name)
                pic_name = (it or {}).get("name") or pic_name
            if path and os.path.isfile(path):
                images = [path]
                img_note = "（配了表情包「%s」）" % pic_name
            else:
                # 没有合适的表情包 → AI 自己画简笔画
                try:
                    import sys as _sys
                    if r"D:\大肥鱼AI" not in _sys.path:
                        _sys.path.insert(0, r"D:\大肥鱼AI")
                    import dfy_qzone
                    dp = dfy_qzone.gen_doodle(text or pic_name)
                    if dp:
                        images = [dp]
                        img_note = "（自己画了幅简笔画）"
                except Exception as e:
                    log("简笔画生成失败：%r" % e, "WARN")

        try:
            import sys as _sys
            if r"D:\大肥鱼AI" not in _sys.path:
                _sys.path.insert(0, r"D:\大肥鱼AI")
            import dfy_qzone
            # 每日上限检查（设置 qzone_每日上限，默认 3）
            try:
                st = dfy_qzone._state()
                limit = int(dfy_qzone._cfg().get("每日上限", 3) or 3)
                if st.get("date") == dfy_qzone._today() and \
                        int(st.get("count", 0) or 0) >= limit:
                    log("发空间已达每日上限 %d，跳过" % limit)
                    self._qzone_last = (now, text)
                    return (answer + "\n（今天说够多啦，明天再更新空间吧）").strip()
            except Exception:
                pass
            right = int(dfy_qzone._cfg().get("查看权限", 1) or 1)
            r = dfy_qzone.publish(text, ugc_right=right or 1, images=images or None)
            try:
                dfy_qzone.bump_today()
            except Exception:
                pass
            if r.get("ok"):
                self._qzone_last = (time.time(), text)
                log("发空间成功：%s（tid=%s，图=%d）" % (text[:40], r.get("tid"), len(images)))
                answer = (answer + "\n发好啦！" + (img_note or "") +
                          "快去空间看～").strip()
            else:
                # 带图失败自动降级：先试一次纯文字
                if images:
                    r2 = dfy_qzone.publish(text, ugc_right=right or 1)
                    if r2.get("ok"):
                        self._qzone_last = (time.time(), text)
                        log("带图失败，纯文字发出：%s" % text[:40])
                        answer = (answer + "\n发好啦！不过图片没传上去，先凑合看文字～").strip()
                        return answer
                log("发空间失败：%s" % (r.get("error") or r), "WARN")
                answer = (answer + "\n（刚才被腾讯那边挡了一下没发出去，我待会儿再试一次）").strip()
        except Exception as e:
            log("发空间异常：%r" % e, "ERROR")
            answer = (answer + "\n（发空间的小零件好像松了，回头我修修）").strip()
        return answer

    def _do_exec_marker(self, kind, target, answer):
        """模型写 [执行:任务] → 后台拉起 DeepBuddy 真去办，办完把结果发回这个会话。

        之前这个标记只有提示词没有处理器（模型写了也白写），现在补上闭环。
        """
        pat = r"\s*[\[【]\s*执行\s*[:：]\s*([^\]】]+)[\]】]\s*"
        hits = re.findall(pat, answer)
        if not hits:
            return answer
        answer = re.sub(pat, "", answer).strip()
        task = hits[0].strip()
        answer = (answer + "\n（收到，我去干活了，办完回来跟你汇报～）").strip()

        def _go():
            try:
                import sys as _sys
                if r"D:\大肥鱼AI" not in _sys.path:
                    _sys.path.insert(0, r"D:\大肥鱼AI")
                import dfy_buddy
                dfy_buddy.launch()
                evs = []
                r = dfy_buddy.run(task, timeout=600,
                                  on_event=lambda ev: evs.append(ev) if len(evs) < 30 else None)
                out = ""
                if isinstance(r, dict):
                    out = str(r.get("text") or "")
                    if not r.get("ok"):
                        out = out or ("出错了：" + str(r.get("error") or ""))
                if out:
                    self.send(kind, target, ("活儿干完了：" + out)[:800])
                else:
                    self.send(kind, target, "干完了，不过没带回什么要说的。")
                log("执行完成：%s（事件 %d 个）" % (task[:40], len(evs)))
            except Exception as e:
                try:
                    self.send(kind, target, "干活的时候翻车了：%s" % e)
                except Exception:
                    pass
                log("执行失败：%r" % e, "ERROR")

        threading.Thread(target=_go, daemon=True, name="qq-exec").start()
        return answer

    def _do_bili_marker(self, kind, target, answer):
        """模型写 [B站:关键词] → 后台跑一轮 B 站鱼塘，完成后把推荐日报发回这个会话。"""
        pat = r"\s*[\[【]\s*B站\s*[:：]\s*([^\]】]+)[\]】]\s*"
        hits = re.findall(pat, answer)
        if not hits:
            return answer
        answer = re.sub(pat, "", answer).strip()
        raw = hits[0].strip()
        # “默认口味/随便/推荐/日报”这类 → 不传关键词，用配置里的口味
        kws = "" if any(w in raw for w in ("默认", "随便", "推荐", "日报", "口味")) else raw
        answer = (answer + "\n（收到，我这就去 B 站给你淘点好东西，挑好写成日报发你～）").strip()

        def _go():
            st = {}
            try:
                import sys as _sys
                for p in (r"D:\大肥鱼AI", r"D:\大肥鱼AI\plugins"):
                    if p not in _sys.path:
                        _sys.path.insert(0, p)
                import bili_pond as _bp
                r = _bp.bili_run(keywords=kws or None)
                if not r.get("ok"):
                    self.send(kind, target, "B 站没跑起来：%s" % (r.get("error") or "未知原因"))
                    return
                # 轮询等这一轮结束（上限约 8 分钟）
                waited = 0
                while waited < 480:
                    st = _bp.bili_round_status()
                    if not st.get("running"):
                        break
                    time.sleep(4)
                    waited += 4
                rep = _bp.bili_report()
                txt = (rep.get("text") or "").strip()
                if txt and "没有日报" not in txt:
                    self.send(kind, target, ("B 站淘好啦，这是今天的推荐日报：\n" + txt)[:1200])
                else:
                    self.send(kind, target, "跑完了但没产出日报。%s" % (st.get("lastError") or ""))
                log("B 站鱼塘轮次完成，等待 %ds" % waited)
            except Exception as e:
                try:
                    self.send(kind, target, "刷 B 站的时候翻车了：%s" % e)
                except Exception:
                    pass
                log("B 站 marker 失败：%r" % e, "ERROR")

        threading.Thread(target=_go, daemon=True, name="qq-bili").start()
        return answer

    def _do_plugin_marker(self, kind, target, answer):
        """模型自己判断"缺个插件"，在回复里写 [装插件:名字] → 真去装。

        跟 [语音] 一样是内部约定：标记从正文里去掉，不让对方看到。
        """
        s = str(answer or "")
        # ① 「要动手」的标记：模型判断光靠嘴办不了，就自己派活给自己
        runs = re.findall(r"[\[【]\s*执行\s*[:：]\s*([^\]】]+)[\]】]", s)
        if runs:
            s = re.sub(r"\s*[\[【]\s*执行\s*[:：][^\]】]*[\]】]\s*", "", s).strip()
            task = runs[0].strip()
            if kind == "private" and len(task) >= 4:
                outer = self
                log("模型自主派活：%s" % task[:50])

                def _go(_t=task, _k=kind, _target=target):
                    try:
                        import sys as _s
                        if r"D:\大肥鱼AI" not in _s.path:
                            _s.path.append(r"D:\大肥鱼AI")
                        import dfy_buddy as _bd
                        r = _bd.run(_t, timeout=240)
                        txt = (r.get("text") or r.get("error") or "").strip()
                        outer.send(_k, _target, "刚才那事我做完了：\n%s" % (txt[:900] or "（没输出）"))
                    except Exception as e:
                        outer.send(_k, _target, "那事没做成：%s" % str(e)[:150])

                threading.Thread(target=_go, daemon=True).start()

        # ② 「装插件」的标记
        pat = r"\s*[\[【]\s*装插件\s*[:：][^\]】]*[\]】]\s*"
        hits = re.findall(r"[\[【]\s*装插件\s*[:：]\s*([^\]】]+)[\]】]", s)
        if not hits:
            return s
        s = re.sub(pat, "", s).strip()
        if not self.brain.dsh:
            return s
        for name in hits[:2]:
            n = name.strip()
            if not n:
                continue
            r = self.brain.dsh.market_install(n)
            if r.get("ok"):
                self.send(kind, target, "（顺手装了个插件：%s）" % (r.get("name") or n))
                log("模型自装插件成功：%s" % n)
                try:
                    rr = self.brain.dsh.restart_host()
                    log("插件宿主重启：%s" % ("ok" if rr.get("ok") else rr.get("error")))
                except Exception as e:
                    log("插件宿主重启失败：%r" % e, "WARN")
            else:
                self.send(kind, target, "（想装 %s 但没成：%s）" % (n, str(r.get("error"))[:100]))
                log("模型自装插件失败：%s" % n, "WARN")
        return s

    # ---- 联系人解析 + 忽略名单 ----
    def _resolve_friend(self, name):
        """按昵称/备注找好友 QQ 号，返回字符串 uid 或 ''。"""
        name = str(name or "").strip()
        if not name:
            return ""
        try:
            r = self._api("get_friend_list", {})
            friends = (r or {}).get("data") or []
        except Exception:
            friends = []
        for f in friends:
            if name == str(f.get("nickname") or "").strip() or \
               name == str(f.get("remark") or "").strip():
                return str(f.get("user_id") or "")
        for f in friends:
            hay = str(f.get("nickname") or "") + " " + str(f.get("remark") or "")
            if name in hay:
                return str(f.get("user_id") or "")
        return ""

    def _do_ignore_marker(self, kind, target, answer):
        """模型写 [忽略:名字] / [拉黑:名字] → 通知对方本人 + 加入黑名单持久化。

        关键：对联系人说的话发给联系人本人，结果汇报给主人，不再发错对象。
        支持「名字|通知内容」写法。
        """
        pat = r"\s*[\[【]\s*(?:忽略|拉黑|不回|屏蔽)\s*[:：]\s*([^\]】]+)[\]】]\s*"
        hits = re.findall(pat, answer)
        if not hits:
            return answer
        answer = re.sub(pat, "", answer).strip()
        raw = hits[0].strip()
        notify = "主人有令，恕不奉陪啦，以后就不打扰你啦。"
        cname = raw
        if "|" in raw:
            cname, _, tail = raw.partition("|")
            if tail.strip():
                notify = tail.strip()
        cname = cname.strip()
        uid = self._resolve_friend(cname)
        if not uid:
            self.send(kind, target,
                      "我想把「%s」拉进忽略名单，但好友列表里没找到这个人，"
                      "你先让她给我发条消息，我就能记住她啦。" % cname)
            return answer
        # 1) 通知对方本人（不是发给主人）
        self.send("private", uid, notify)
        # 2) 加入黑名单并持久化
        if uid not in self.blacklist:
            self.blacklist.append(uid)
        saved = True
        if self.blacklist_save:
            try:
                saved, _ = self.blacklist_save(self.blacklist)
            except Exception:
                saved = False
        # 3) 给主人汇报（只显示 QQ 号尾号，不暴露完整号码）
        self.send(kind, target,
                  "已经跟「%s」说清楚，也把她（QQ号尾号 %s）加进忽略名单，"
                  "以后她发消息我都不回啦。%s" %
                  (cname, uid[-4:], "" if saved else "（名单没存上，重启可能失效）"))
        log("已忽略联系人 %s uid尾号%s saved=%s" % (cname, uid[-4:], saved))
        return answer

    # ---- 要不要发语音 ----
    def _voice_decision(self, answer, user_text):
        """返回 (是否发语音, 去掉标记后的文字)。

        三种触发：模型自己写 [语音] 标记 / 用户明确要求 / 按概率随机。
        """
        answer = str(answer or "")
        if not self.voice.enabled:
            return False, re.sub(r"\s*[\[【](语音|voice)[\]】]\s*", "", answer).strip()
        if re.search(r"[\[【]\s*(语音|voice)\s*[\]】]", answer):
            answer = re.sub(r"\s*[\[【]\s*(语音|voice)\s*[\]】]\s*", "", answer).strip()
            return True, answer
        if self._asks_voice(user_text):
            return True, answer
        if self.voice.prob > 0 and random.random() < self.voice.prob:
            return True, answer
        return False, answer

    @staticmethod
    def _asks_voice(text):
        t = str(text or "")
        return bool(re.search(r"(发|来|说|给|录)[个条段]?.*语音|语音(回复|条|说)|用语音|"
                              r"说话给我听|念给我听|听听你(的)?声音", t))

    # ---- HTTP 上报服务器 ----
    def serve(self):
        port = int(self.cfg.get("listen_port", 6700))
        host = self.cfg.get("listen_host", "127.0.0.1")
        path = self.cfg.get("report_path", "/onebot")
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _ok(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"retcode":0}')

            def _json_resp(self, obj, code=200):
                raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                p = self.path.split("?", 1)[0]
                # 主界面检测框用的控制接口
                if p == "/send":
                    try:
                        d = json.loads(body.decode("utf-8") or "{}")
                        kind = "group" if d.get("kind") == "group" else "private"
                        target = str(d.get("target") or "").strip()
                        text = str(d.get("text") or "")
                        if not target:
                            return self._json_resp({"ok": False, "error": "缺 target（QQ号/群号）"})
                        segs = []
                        hint = str(d.get("sticker") or "").strip()
                        if hint and outer.brain.stickers:
                            it, sp = outer.brain.stickers.pick(hint)
                            if sp:
                                seg = outer.brain.stickers.image_segment(sp)
                                if seg: segs.append(seg)
                        outer.send(kind, target, text, segments=segs)
                        outer._record("out", text, "%s:%s" % (kind, target), hint)
                        return self._json_resp({"ok": True, "sticker": bool(segs)})
                    except Exception as e:
                        return self._json_resp({"ok": False, "error": str(e)})
                if p == "/clear":
                    try:
                        d = json.loads(body.decode("utf-8") or "{}")
                        sess = str(d.get("session") or "")
                        outer.brain.sessions.clear(sess)
                        return self._json_resp({"ok": True})
                    except Exception as e:
                        return self._json_resp({"ok": False, "error": str(e)})
                # 默认：OneBot 事件上报
                try:
                    if self.headers.get("Authorization"):
                        pass
                    try:
                        ev = json.loads(body.decode("utf-8"))
                    except Exception:
                        ev = {}
                    self._ok()
                    threading.Thread(target=outer.on_event, args=(ev,), daemon=True).start()
                except Exception:
                    try:
                        self._ok()
                    except Exception:
                        pass

            def do_GET(self):
                p = self.path.split("?", 1)[0]
                if p == "/status":
                    # 探测 OneBot API 是否可达（缓存 10 秒，别每次都打）
                    now = time.time()
                    if outer.api_ok is None or now - outer._last_api_check > 10:
                        outer._last_api_check = now
                        try:
                            with _NO_PROXY.open(outer.api + "/get_login_info", timeout=4) as r:
                                info = json.loads(r.read().decode("utf-8"))
                            outer.api_ok = bool(info.get("retcode") in (0, None))
                            outer.login_info = info.get("data") or {}
                        except Exception:
                            outer.api_ok = False
                            outer.login_info = {}
                    with outer.recent_lock:
                        recent = list(outer.recent[-20:])
                    return self._json_resp({
                        "ok": True,
                        "running": True,
                        "onebot_api": outer.api_ok,
                        "login": getattr(outer, "login_info", {}),
                        "brain_mode": outer.brain_mode,
                        "model": outer.brain.last_model or "",
                        "sticker_count": len(outer.brain.stickers.items) if outer.brain.stickers else 0,
                        "pending": outer.queue.qsize(),
                        "recent": recent,
                    })
                if p == "/messages":
                    with outer.recent_lock:
                        return self._json_resp({"ok": True, "messages": list(outer.recent)})
                if p == "/stickers":
                    if not outer.brain.stickers:
                        return self._json_resp({"ok": True, "items": []})
                    items = [{"name": it.get("name"), "emotion": it.get("emotion")}
                             for it in outer.brain.stickers.items[:80]]
                    return self._json_resp({"ok": True, "items": items})
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(("大肥鱼AI QQ 端口已就绪 · /status 看状态 · 上报 POST %s" % path).encode("utf-8"))

        srv = ThreadingHTTPServer((host, port), H)
        log("OneBot 上报服务已启动：http://%s:%d%s" % (host, port, path))
        srv.serve_forever()


# ---------------------------------------------------------------- QQ 官方 API
class Official:
    """QQ 官方机器人（bots.qq.com）。收发走官方接口，需要 appid/clientSecret。"""

    def __init__(self, cfg, brain):
        self.cfg = cfg.get("official", {})
        self.brain = brain
        self.appid = self.cfg.get("appid", "")
        self.secret = self.cfg.get("client_secret", "")
        self.token = ""
        self.expire = 0

    def _base(self):
        return "https://api.sgroup.qq.com"

    def get_token(self):
        if self.token and time.time() < self.expire - 60:
            return self.token
        payload = json.dumps({"appId": self.appid, "clientSecret": self.secret}).encode()
        req = urllib.request.Request("https://bots.qq.com/app/getAppAccessToken", data=payload,
                                     headers={"Content-Type": "application/json"})
        with _NO_PROXY.open(req, timeout=20) as r:
            j = json.loads(r.read().decode("utf-8"))
        self.token = j.get("access_token", "")
        self.expire = time.time() + int(j.get("expires_in", 7200))
        log("官方 access_token 已获取，有效期 %ss" % j.get("expires_in"))
        return self.token

    def _post(self, path, body):
        tk = self.get_token()
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self._base() + path, data=data, headers={
            "Content-Type": "application/json",
            "Authorization": "QQBot " + tk,
            "X-Union-Appid": self.appid,
        })
        with _NO_PROXY.open(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    def send_group(self, group_openid, text, msg_id=None):
        body = {"content": text, "msg_type": 0}
        if msg_id:
            body["msg_id"] = msg_id
        return self._post("/v2/groups/%s/messages" % group_openid, body)

    def send_user(self, openid, text, msg_id=None):
        body = {"content": text, "msg_type": 0}
        if msg_id:
            body["msg_id"] = msg_id
        return self._post("/v2/users/%s/messages" % openid, body)

    def serve(self):
        """官方事件是 WebSocket 长连接，需要可选包 websockets。"""
        try:
            import asyncio
            import websockets  # noqa
        except Exception:
            log("官方模式需要 websockets 包：pip install websockets（当前只发不收）", "WARN")
            while True:
                time.sleep(3600)
        try:
            import asyncio
            import websockets

            async def run():
                tk = self.get_token()
                url = "wss://api.sgroup.qq.com/websocket"
                async with websockets.connect(url, extra_headers={"Authorization": "QQBot " + tk}) as ws:
                    log("官方 WS 已连接")
                    while True:
                        raw = await ws.recv()
                        try:
                            ev = json.loads(raw)
                        except Exception:
                            continue
                        op = ev.get("op")
                        d = ev.get("d") or {}
                        if op == 0 and d.get("type") == "READY":
                            continue
                        if op == 0 and "content" in d:
                            # 群/单聊消息
                            content = str(d.get("content") or "").strip()
                            if not content:
                                continue
                            gid = d.get("group_openid")
                            oid = d.get("author", {}).get("member_openid") or d.get("user_openid")
                            answer, _, _sticker = self.brain.reply(
                                ("official_group_%s" % gid) if gid else ("official_user_%s" % oid),
                                content, "QQ 官方机器人会话")
                            # 官方 API 发图走另一套（需先上传），这里先只发文字，标记丢弃
                            if _sticker:
                                log("官方模式暂不发表情包（%s）" % _sticker, "DEBUG")
                            try:
                                if gid and self.cfg.get("reply_group_at", True):
                                    self.send_group(gid, answer, d.get("id"))
                                elif oid and self.cfg.get("reply_private", True):
                                    self.send_user(oid, answer, d.get("id"))
                            except Exception:
                                log("官方回复失败:\n" + traceback.format_exc(), "ERROR")

            asyncio.run(run())
        except Exception:
            log("官方 WS 异常:\n" + traceback.format_exc(), "ERROR")
            time.sleep(10)


# ---------------------------------------------------------------- 入口
def self_test(cfg):
    brain = Brain(cfg)
    log("== 自检开始 ==")
    host = brain.ollama.get("host", "http://127.0.0.1:11434").rstrip("/")
    try:
        with _NO_PROXY.open(host + "/api/tags", timeout=6) as r:
            tags = json.loads(r.read().decode("utf-8"))
        names = [m.get("name") for m in tags.get("models", [])]
        log("Ollama 在线，本机模型：%s" % names)
    except Exception as e:
        log("Ollama 不可达：%r（先启动 大肥鱼AI.bat 或 ollama serve）" % e, "ERROR")
        return False
    for m in (brain.ollama.get("fast_model"), brain.ollama.get("deep_model")):
        if m and m not in names:
            log("缺少模型 %s，请执行 ollama pull %s" % (m, m), "WARN")
    log("system prompt 示例：\n" + brain.system_prompt("QQ私聊"))
    if cfg.get("onebot", {}).get("enabled"):
        api = cfg["onebot"].get("api_base", "")
        try:
            with _NO_PROXY.open(api + "/get_login_info", timeout=5) as r:
                log("OneBot 端可达：%s" % r.read().decode("utf-8")[:120])
        except Exception as e:
            log("OneBot 端 %s 不可达：%r（先启动 NapCat 并开启 HTTP）" % (api, e), "WARN")
    log("== 自检结束 ==")
    return True


def main():
    cfg = load_config()
    mode = cfg.get("mode", "onebot")
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]
    brain = Brain(cfg)
    log("启动 mode=%s fast=%s deep=%s" % (mode,
                                          brain.ollama.get("fast_model"),
                                          brain.ollama.get("deep_model")))

    threads = []
    if mode in ("onebot", "both") and cfg.get("onebot", {}).get("enabled"):
        ob = OneBot(cfg, brain)
        threads.append(threading.Thread(target=ob.serve, daemon=True))
    if mode in ("official", "both") and cfg.get("official", {}).get("enabled"):
        if not cfg["official"].get("appid") or not cfg["official"].get("client_secret"):
            log("官方模式缺少 appid/client_secret，已跳过", "WARN")
        else:
            of = Official(cfg, brain)
            threads.append(threading.Thread(target=of.serve, daemon=True))
    if not threads:
        log("没有任何适配器被启用，请检查 config.json", "ERROR")
        return
    for t in threads:
        t.start()
    log("大肥鱼AI QQ 端口运行中，Ctrl+C 退出。日志：%s" % LOG_PATH)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("已退出")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test(load_config())
    else:
        main()
