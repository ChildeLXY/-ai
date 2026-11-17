# -*- coding: utf-8 -*-
"""QQ 桥接 —— 内嵌在大肥鱼AI主进程里的版本。

和 D:/大肥鱼QQ/qqbot.py 的区别：
  · 不自己起进程：作为主程序的一个对象运行，生命周期跟着主程序，不会被工具会话回收。
  · 配置来自主程序 settings.json（qq_* 字段），不再依赖外部 config.json。
  · 人格 / 记忆 / 表情包直接复用主程序的数据文件，和桌面版是同一套。
  · 收到/发出消息时通过 push_event 回调实时推给前端 🐧 面板。

复用 qqbot.py 里已验证过的 Brain / StickerPicker / Sessions / OneBot 逻辑——
只改"配置来源"和"谁来启动"，避免重复造轮子。
"""
import os, sys, json, time, threading

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DFY_DATA_DIR") or r"D:\本地部署大肥鱼环境\data"
CACHE = r"D:\大肥鱼缓存"
LOG_PATH = os.path.join(r"D:\大肥鱼缓存", "qqbot.log")
QQBOT_DIR = os.path.join(BASE, "qqbot_core")


def _num(v, default):
    """设置里存的可能是 '25' / 25 / ''，统一转成数字。"""
    try:
        return int(float(str(v).strip()))
    except Exception:
        return default

_LOG = None          # 由主程序注入的日志函数
_PUSH = None         # 由主程序注入的事件推送函数
_LOCK = threading.Lock()


def _log(msg, level="INFO"):
    if _LOG:
        try:
            _LOG(msg, level)
            return
        except Exception:
            pass
    try:
        line = "[%s][%s] %s\n" % (time.strftime("%H:%M:%S"), level, msg)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# 让 qqbot.py 用它自己的 log 写到同一个文件即可；这里不改它的全局，
# 但有 push 需求时我们自己包装回调。
class Bridge:
    def __init__(self, setting_get, setting_set, push_event=None, log_fn=None):
        global _LOG, _PUSH
        self.get = setting_get
        self.set = setting_set
        _PUSH = push_event
        _LOG = log_fn
        self.brain = None
        self.onebot = None
        self.server = None
        self.thread = None
        self.running = False
        self.last_error = ""
        self.recent = []
        self.recent_lock = threading.Lock()
        # 上报统计：用来判断 NapCat 到底有没有把事件推过来
        self.post_count = 0
        self.last_post_at = 0.0
        self.last_post_type = ""
        # 保活探针：OneBot 通不通、是不是该重登 QQ 了
        self.link_ok = None
        self.need_relogin = False
        self._wd_started = False
        self._recover_at = 0.0
        # 用户是否希望它运行：区分「意外挂了该自愈」和「用户主动停的别乱开」
        self._want_running = False
        # 让 qqbot 模块可 import
        if QQBOT_DIR not in sys.path:
            sys.path.insert(0, QQBOT_DIR)
        try:
            import qqbot as _qb
            self._qb = _qb
        except Exception as e:
            self._qb = None
            self.last_error = "无法加载 qqbot.py：%r" % e

    def _mem_status(self):
        try:
            m = getattr(self.brain, "mem", None) if self.brain else None
            return m.status() if m else {}
        except Exception:
            return {}

    # -------- 配置：从主程序 settings.json 读，拼成 qqbot.py 期望的结构 --------
    def build_cfg(self):
        g = self.get
        def gi(key, default):
            try:
                v = g(key)
                return default if v in ("", None) else v
            except Exception:
                return default

        cfg_fast = str(gi("qq_fast_model", "") or gi("model", "") or "qwen3-vl:4b-instruct")
        cfg_deep = str(gi("qq_deep_model", "") or "qwen3:8b")
        # 看图单独用视觉模型：35B 这类纯文本模型看不了图，
        # 不单独指定的话表情包会全部"看不懂"
        cfg_vision = str(gi("qq_vision_model", "") or "qwen3-vl:4b-instruct")
        # 思考开关：qwen3.6 这类思考型模型开着 think 会把输出预算全烧在
        # 思维链上，正文空掉（实测 80 token 全进 thinking）→ 默认关
        _think_on = str(gi("qq_think", "0")) in ("1", "true", "True", 1, True)
        cfg = {
            "mode": "onebot",
            "ollama": {
                "host": "http://127.0.0.1:11434",
                # 默认跟主程序当前选的模型一致；本机确实装的是 qwen3-vl:4b-instruct，
                # 不是 qwen3:4b（那个 404）。用户可在高级设置里用 qq_fast_model/qq_deep_model 覆盖。
                "fast_model": cfg_fast,
                "deep_model": cfg_deep,
                "vision_model": cfg_vision,
                "auto_switch": True,
                "deep_keywords": ["分析", "推理", "为什么", "原理", "深度", "总结", "架构",
                                  "写代码", "调试", "数学", "证明", "对比", "评估", "规划"],
                "deep_min_len": 220,
                # vl-instruct 这类模型不支持 thinking（传 true 会 400），按名字区分；
                # 其余模型也默认关思考（聊天要的是快，不是思维链）
                "think_for_fast": _think_on and "vl" not in str(cfg_fast).lower(),
                "think_for_deep": _think_on and "vl" not in str(cfg_deep).lower(),
                "num_ctx": 3072,   # MoE 35B 必须 3072：所有调用方参数一致，否则 ollama 反复重载 runner
                "keep_alive": "30m",
                # 超时别设太长：一次卡死会把所有 worker 全占住，后面消息全排队。
                # 35B 冷加载 20s + 生成 4t/s，正常一条 8-15s，给 150 兜住冷启动。
                "timeout": 150,
                # 主模型卡住/超时时的兜底模型：又小又快，保证"能回话"
                "fallback_model": str(gi("qq_fallback_model", "") or "qwen3:8b"),
                # 思考型模型(qwen3-vl-instruct/R1)思维链和正文共用这个预算，
                # 给少了正文会被吃空（qqbot.py 注释里的实测坑），提到 3000。
                "max_tokens": 400,
                # MoE 35B 专用调参（qqbot._moe_tune 读取）：大 batch 提升 prefill、
                # 8 个 P 核、能塞进 4060 的层全上 GPU；生成期内存带宽是瓶颈，
                # 靠瘦身 prompt（DSH 注入≤600 字、5 轮历史、3 条记忆）把它压进可用窗口。
                "moe_tune": {"num_batch": 512, "num_thread": 8, "num_gpu": 99, "num_keep": 48},
                # 内存兜底阈值：35B 常驻时可用内存通常只剩 2-5G，阈值太高会让聊天
                # 永远退到小模型、35B 白常驻。只留够装下兜底 8b 的余量即可。
                "moe_min_avail_gb": _num(gi("qq_mem_min_avail_gb", 4), 4),
            },
            "brain": {
                "use_memory": True,
                "memory_file": os.path.join(DATA_DIR, "memory.json"),
                "settings_file": os.path.join(BASE, "ds图片", "settings.json"),
                "persona_extra": ("你现在在 QQ 上聊天，像一个真实网友：句子短、口语化，"
                                  "可以发表情和语气词，不要长篇大论，不要自称AI助手。"),
                "history_turns": 5,   # 35B 上下文金贵：5 轮够用，prompt 越 prefill 越快
                "max_reply_chars": 300,
                "use_stickers": str(gi("qq_stickers", "1")) in ("1", "true", "True", 1, True),
                "sticker_hint_count": 10,   # 35B 上下文金贵：10 个名字够选了
            },
            "brain_mode": gi("qq_brain_mode", "desktop"),
            # 语音条：文字 → edge-tts → mp3 → NapCat 转 SILK 发出去
            "tts": {
                "enabled": str(gi("qq_voice_enabled", "0")) in ("1", "true", "True", 1, True),
                "voice": str(gi("qq_voice_name", "zh-CN-XiaoyiNeural")),
                "rate": str(gi("qq_voice_rate", "+8%")),
                "probability": _num(gi("qq_voice_prob", 25), 25) / 100.0,
                "also_text": str(gi("qq_voice_text", "0")) in ("1", "true", "True", 1, True),
                "max_chars": 120,
                "dir": os.path.join(CACHE, "tts"),
                "keep": 80,
            },
            "onebot": {
                "enabled": True,
                "listen_host": "127.0.0.1",
                "listen_port": int(gi("qq_port", 6700) or 6700),
                "report_path": "/onebot",
                "api_base": "http://127.0.0.1:%d" % int(gi("qq_api_port", 3000) or 3000),
                "access_token": gi("qq_token", ""),
                "reply_private": True,
                "reply_group_at": True,
                "reply_group_prefix": ["鱼鱼", "大肥鱼", "肥鱼", "/ai"],
                "group_whitelist": [],
                "user_blacklist": self._read_blacklist(),
                "blacklist_save": self._save_blacklist,
                "admin_qq": "",
            },
            # 记忆池：到点写日记、检索条数、日记要不要发空间
            "mem_diary_hour": int(gi("qq_mem_diary_hour", 23) or 23),
            "mem_recall": int(gi("qq_mem_recall", 3) or 3),
            "qzone_diary_publish": str(gi("qq_qzone_diary", "0")),
            # DSH 插件转接口：注入多少字、要不要跳过插件里和自带人设重复的段落
            # 35B 上注入字数直接换算成 prefill 秒数，压到 600
            "dsh_skip_persona": str(gi("dsh_skip_persona", "1")),
            "dsh_max_chars": int(gi("dsh_max_chars", 600) or 600),
        }
        # num_ctx 跟随前面做的上下文拨杆
        try:
            import llm_params
            cfg["ollama"]["num_ctx"] = llm_params.current_ctx()
        except Exception:
            pass
        return cfg

    # -------- 黑名单持久化（settings 里的 qq_blacklist，字符串 QQ 号列表）--------
    def _read_blacklist(self):
        try:
            v = self.get("qq_blacklist")
            if isinstance(v, list):
                return [str(x).strip() for x in v if str(x).strip()]
        except Exception:
            pass
        return []

    def _save_blacklist(self, uids):
        try:
            lst = sorted({str(x).strip() for x in uids if str(x).strip()})
            ok = self.set("qq_blacklist", lst) == "ok"
            return ok, lst
        except Exception:
            return False, []

    # -------- 生命周期 --------
    def start(self):
        if self.running:
            return {"ok": True, "already": True}
        if not self._qb:
            return {"ok": False, "error": self.last_error or "qqbot.py 不可用"}
        try:
            cfg = self.build_cfg()
            # 每次启动重建 brain（重新读人格/记忆/表情包），保证和桌面版同步
            self.brain = self._qb.Brain(cfg)
            self.onebot = self._qb.OneBot(cfg, self.brain)
            # 包装 record：除了 qqbot 自己的 recent，也推给前端
            self._wrap_record()
            host = cfg["onebot"]["listen_host"]
            port = int(cfg["onebot"]["listen_port"])
            outer = self
            from http.server import BaseHTTPRequestHandler

            class H(BaseHTTPRequestHandler):
                def log_message(self, *a):
                    pass

                def _json(self, obj, code=200):
                    raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(raw)

                def do_GET(self):
                    p = self.path.split("?", 1)[0]
                    if p == "/status":
                        return self._json(outer.status())
                    if p == "/messages":
                        with outer.recent_lock:
                            return self._json({"ok": True, "messages": list(outer.recent)})
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write("大肥鱼AI 内嵌 QQ 桥接已就绪".encode("utf-8"))

                def _read_body(self):
                    """读请求体。

                    NapCat 的 HTTP 上报有时用 chunked（没有 Content-Length），
                    以前按 Content-Length 读会拿到 0 字节 → 事件全丢。
                    """
                    te = (self.headers.get("Transfer-Encoding") or "").lower()
                    if "chunked" in te:
                        parts = []
                        while True:
                            line = self.rfile.readline()
                            if not line:
                                break
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                size = int(line.split(b";")[0], 16)
                            except Exception:
                                break
                            if size == 0:
                                self.rfile.readline()
                                break
                            parts.append(self.rfile.read(size))
                            self.rfile.read(2)
                        raw = b"".join(parts)
                    else:
                        n = int(self.headers.get("Content-Length") or 0)
                        raw = self.rfile.read(n) if n else b""
                    enc = (self.headers.get("Content-Encoding") or "").lower()
                    if "gzip" in enc and raw:
                        try:
                            import gzip
                            raw = gzip.decompress(raw)
                        except Exception:
                            pass
                    return raw

                def do_POST(self):
                    body = self._read_body()
                    p = self.path.split("?", 1)[0]
                    if p == "/send":
                        try:
                            d = json.loads(body.decode("utf-8") or "{}")
                            r = outer.send(d.get("target"), d.get("text"),
                                           d.get("kind", "private"), d.get("sticker", ""))
                            return self._json(r)
                        except Exception as e:
                            return self._json({"ok": False, "error": str(e)})
                    # 默认：OneBot 事件上报
                    try:
                        ev = json.loads(body.decode("utf-8"))
                    except Exception:
                        ev = {}
                    try:
                        outer.post_count += 1
                        outer.last_post_at = time.time()
                        outer.last_post_type = str(ev.get("post_type") or "?")
                        if not ev.get("post_type"):
                            outer.last_post_raw = body[:300].decode("utf-8", "ignore")
                            # 只在出问题时落盘，方便排查格式
                            try:
                                with open(r"D:/大肥鱼缓存/_qq_post_debug.log", "a",
                                          encoding="utf-8") as f:
                                    f.write("%s ct=%s te=%s cl=%s len=%d raw=%r\n" % (
                                        time.strftime("%H:%M:%S"),
                                        self.headers.get("Content-Type"),
                                        self.headers.get("Transfer-Encoding"),
                                        self.headers.get("Content-Length"),
                                        len(body), body[:300]))
                            except Exception:
                                pass
                    except Exception:
                        pass
                    self._json({"retcode": 0})
                    threading.Thread(target=outer.onebot.on_event, args=(ev,), daemon=True).start()

            from http.server import ThreadingHTTPServer
            self.server = ThreadingHTTPServer((host, port), H)
            self.running = True
            self.thread = threading.Thread(target=self._serve, daemon=True, name="qq-bridge")
            self.thread.start()
            self._want_running = True
            _log("内嵌 QQ 桥接已启动：http://%s:%d/onebot" % (host, port))
            self._push_status()
            threading.Thread(target=self._warmup, args=(cfg,), daemon=True,
                             name="qq-warmup").start()
            self._start_watchdog()
            return {"ok": True, "port": port}
        except Exception as e:
            self.running = False
            self.last_error = str(e)
            _log("内嵌 QQ 桥接启动失败：%r" % e, "ERROR")
            return {"ok": False, "error": str(e)}

    def _serve(self):
        try:
            self.server.serve_forever()
        except Exception as e:
            _log("桥接服务异常：%r" % e, "ERROR")
        finally:
            self.running = False

    def _warmup(self, cfg):
        """开机把聊天模型先拉进内存。

        35B 这类大模型冷加载要 20~30 秒，用户第一条消息会以为"没回"。
        这里空跑一次 1 token 的请求，把模型和 keep_alive 先立起来。
        """
        try:
            import urllib.request
            o = cfg.get("ollama", {})
            # 必须用和聊天完全相同的 num_ctx，否则这次预热加载的 runner
            # 跟正式聊天不匹配，第一条消息又会冷加载 30 秒
            ctx = int(o.get("num_ctx", 3072))
            try:
                import llm_params
                ctx = int(llm_params.current_ctx())
            except Exception:
                pass
            model = str(o.get("fast_model") or "")
            if not model:
                return
            # MoE 35B：预热参数也必须与正式聊天完全一致（含 moe_tune），
            # 差一个字段 ollama 就当成不同配置重新加载 runner
            opts = {"num_predict": 1, "num_ctx": ctx}
            opts.update(o.get("moe_tune") or {})
            payload = {"model": model, "prompt": "你好", "stream": False,
                       "think": False, "keep_alive": o.get("keep_alive", "30m"),
                       # ctx 必须跟实际聊天一致，否则 Ollama 会当成不同配置重新加载
                       "options": opts}
            req = urllib.request.Request(
                str(o.get("host", "http://127.0.0.1:11434")).rstrip("/") + "/api/generate",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=300) as r:
                r.read()
            _log("模型预热完成：%s（%.1fs）" % (model, time.time() - t0))
        except Exception as e:
            _log("模型预热失败：%r" % e, "WARN")

    def _wrap_record(self):
        """把 OneBot 的 _record 包一层，收到/发出都推给前端。"""
        ob = self.onebot
        orig = ob._record
        outer = self

        def wrapped(direction, text, who="", sticker=""):
            try:
                orig(direction, text, who, sticker)
            except Exception:
                pass
            with outer.recent_lock:
                outer.recent.append({"t": time.strftime("%H:%M:%S"), "dir": direction,
                                     "who": str(who or ""), "text": str(text or "")[:300],
                                     "sticker": str(sticker or "")})
                if len(outer.recent) > 60:
                    del outer.recent[:len(outer.recent) - 60]
            if _PUSH:
                _PUSH("qq_msg", {"dir": direction, "text": str(text or "")[:300],
                                 "who": str(who or ""), "sticker": str(sticker or "")})
        ob._record = wrapped

    # -------- 保活：掉线自愈 --------
    def _probe_onebot(self):
        """问 NapCat 要一次登录信息：能答上来才算真的在线。"""
        try:
            import urllib.request
            api = str(self.build_cfg()["onebot"]["api_base"]).rstrip("/")
            req = urllib.request.Request(
                api + "/get_login_info",
                data=b"{}", headers={"Content-Type": "application/json"})
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with op.open(req, timeout=8) as r:
                d = json.loads(r.read().decode("utf-8", "ignore"))
            return int(d.get("retcode", -1)) == 0
        except Exception:
            return False

    def _start_watchdog(self):
        if self._wd_started:
            return
        self._wd_started = True
        threading.Thread(target=self._watchdog, daemon=True, name="qq-watchdog").start()

    def _watchdog(self):
        """每 40 秒探一次。

        - 连续 3 次不通 → 重启桥接（多数是本机 socket 卡住，重启就好）
        - 连续 6 次不通 → QQ 都不在了就自动拉起；QQ 还在就提示重登，
          绝不擅自杀掉用户的 QQ（可能正用着自己那个号）
        """
        fails = 0
        while True:
            time.sleep(40)
            if not self.running:
                # 意外挂了（用户没点停止）就自己拉回来
                if self._want_running:
                    _log("桥接意外停止，自动重启…", "WARN")
                    try:
                        self.start()
                    except Exception as e:
                        _log("自动重启失败：%r" % e, "ERROR")
                continue
            if self._probe_onebot():
                if fails >= 3:
                    _log("NapCat 连接已恢复（此前连续失败 %d 次）" % fails)
                fails = 0
                self.link_ok = True
                self.need_relogin = False
                self._recover_at = 0.0
                continue

            fails += 1
            self.link_ok = False
            _log("NapCat 探测失败（连续 %d 次）" % fails, "WARN")

            if fails == 3:
                _log("尝试自动重启桥接…", "WARN")
                try:
                    self.stop()
                    time.sleep(2)
                    self.start()
                    _log("桥接已自动重启", "INFO")
                except Exception as e:
                    _log("自动重启桥接失败：%r" % e, "ERROR")

            elif fails >= 6 and time.time() - self._recover_at > 300:
                self._recover_at = time.time()
                try:
                    import qq_launcher
                    pids = qq_launcher.all_qq_pids()
                except Exception:
                    pids = []
                if not pids:
                    _log("QQ 已经不在了，自动重新拉起机器人", "WARN")
                    try:
                        import qq_launcher
                        cfg = qq_launcher.cfg_from(self.get)
                        qq_launcher.launch(cfg, kill_all=False)
                        fails = 0
                    except Exception as e:
                        _log("自动拉起 QQ 失败：%r" % e, "ERROR")
                else:
                    self.need_relogin = True
                    _log("QQ 进程还在但 OneBot 不通：需要手动重登（界面已提示）", "ERROR")

    def stop(self):
        if not self.running or not self.server:
            return {"ok": True, "note": "未在运行"}
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass
        self.running = False
        self._want_running = False
        self.server = None
        _log("内嵌 QQ 桥接已停止")
        self._push_status()
        return {"ok": True}

    # -------- 接口 --------
    def status(self):
        api_ok = False
        login = {}
        if self.onebot:
            try:
                with self._qb._NO_PROXY.open(self.onebot.api + "/get_login_info", timeout=4) as r:
                    info = json.loads(r.read().decode("utf-8"))
                api_ok = info.get("retcode") in (0, None)
                login = info.get("data") or {}
            except Exception:
                pass
        # QQ 客户端本身在不在跑（跟 OneBot 是否连通是两件事，界面要分清楚）
        qq_up = None
        qq_n = None
        bot_alive = None
        try:
            import qq_launcher
            pids = qq_launcher.all_qq_pids()
            qq_up = bool(pids)
            qq_n = len(pids)
            bot_alive = bool(qq_launcher.bot_pids() & pids)
        except Exception:
            pass
        with self.recent_lock:
            recent = list(self.recent[-20:])
        # 语音可用性懒探测（只 import 一次，之后走缓存）
        _v = getattr(self.onebot, "voice", None)
        if _v and _v.enabled and getattr(_v, "_ok", None) is None:
            try:
                _v.available()
            except Exception:
                pass
        return {
            "ok": True, "online": bool(self.running),
            "running": bool(self.running),
            "onebot_api": api_ok, "login": login,
            "logged_in": bool(login.get("user_id")),
            "qq_running": qq_up,
            "qq_count": qq_n,
            "bot_alive": bot_alive,
            "brain_mode": (self.brain.desktop_mode and "desktop" or "standalone") if self.brain else "",
            "model": (self.brain.last_model if self.brain else "") or "",
            "sticker_count": len(self.brain.stickers.items) if self.brain and self.brain.stickers else 0,
            "memory": self._mem_status(),
            "pending": self.onebot.queue.qsize() if self.onebot else 0,
            "recent": recent,
            "post_count": self.post_count,
            "last_post_age": (int(time.time() - self.last_post_at) if self.last_post_at else -1),
            "last_post_type": self.last_post_type,
            "last_post_raw": getattr(self, "last_post_raw", "")[:200],
            "voice_enabled": bool(getattr(getattr(self.onebot, "voice", None), "enabled", False)),
            "voice_name": str(getattr(getattr(self.onebot, "voice", None), "voice", "") or ""),
            "voice_said": int(getattr(getattr(self.onebot, "voice", None), "said", 0) or 0),
            "voice_ok": getattr(getattr(self.onebot, "voice", None), "_ok", None),
            "embedded": True,
            "link_ok": self.link_ok,
            "need_relogin": bool(self.need_relogin),
            "slow_model": bool(self.brain and time.time() < getattr(self.brain, "_slow_until", 0)),
            "error": self.last_error,
        }

    def send(self, target, text, kind="private", sticker=""):
        if not self.running or not self.onebot:
            return {"ok": False, "error": "桥接未启动"}
        try:
            segs = []
            hint = str(sticker or "").strip()
            if hint and self.brain and self.brain.stickers:
                it, path = self.brain.stickers.pick(hint)
                if path:
                    seg = self.brain.stickers.image_segment(path)
                    if seg:
                        segs.append(seg)
            self.onebot.send("group" if kind == "group" else "private",
                             str(target or ""), str(text or ""), segments=segs)
            self.onebot._record("out", str(text or ""),
                                "%s:%s" % (kind, target), hint if segs else "")
            return {"ok": True, "sticker": bool(segs)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def voice_say(self, text, target="", kind="private"):
        """把一段文字念成语音条发出去（界面上的「试听」用）。"""
        if not self.running or not self.onebot:
            return {"ok": False, "error": "桥接未启动"}
        v = getattr(self.onebot, "voice", None)
        if not v:
            return {"ok": False, "error": "语音模块没加载（重启大肥鱼AI）"}
        if not v.available():
            return {"ok": False, "error": "没装 edge-tts"}
        t = str(text or "").strip() or "在的呀，我在听你说呢。"
        p = v.synth(t)
        if not p:
            return {"ok": False, "error": "合成失败，看 qqbot.log"}
        to = str(target or "").strip()
        if not to:
            # 没指定就发给「最近给我发消息的人」，这样「试听」能落到主人手机上
            try:
                with self.recent_lock:
                    for m in reversed(self.recent):
                        if m.get("dir") == "in":
                            who = str(m.get("who") or "")
                            if ":" in who:
                                to = who.split(":", 1)[1]
                                if to:
                                    break
            except Exception:
                to = ""
        if not to:
            try:
                to = str((self.status().get("login") or {}).get("user_id") or "")
            except Exception:
                to = ""
        if not to:
            return {"ok": False, "error": "没有可发送的 QQ 号"}
        try:
            self.onebot.send("group" if kind == "group" else "private", to, "",
                             segments=[self._qb.Voice.segment(p)])
            self.onebot._record("out", "［语音］" + t, "%s:%s" % (kind, to), "")
            return {"ok": True, "file": p, "target": to,
                    "kb": round(os.path.getsize(p) / 1024.0, 1)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def refresh(self):
        """人格/记忆/表情包改了以后重建 brain（下次回复生效）。"""
        if not self.running:
            return {"ok": False, "error": "未启动"}
        try:
            cfg = self.build_cfg()
            old = self.onebot
            self.brain = self._qb.Brain(cfg)
            self.onebot = self._qb.OneBot(cfg, self.brain)
            self._wrap_record()
            # 把已排队的消息转移过去
            try:
                while True:
                    item = old.queue.get_nowait()
                    self.onebot.queue.put_nowait(item)
            except Exception:
                pass
            _log("QQ 桥接已重新加载人格/记忆/表情包")
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _push_status(self):
        if _PUSH:
            _PUSH("qq_status", self.status())
