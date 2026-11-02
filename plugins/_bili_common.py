# -*- coding: utf-8 -*-
"""
_bili_common.py —— 两个 B站插件共用的底座（不是插件，下划线开头，加载器会跳过）。

里面就四样东西：
  ① BiliBridge  —— 跟 _bili_bridge.py 子进程说话（长驻，import 一次要好几秒）
  ② RiskGuard   —— 风控闸门：读写预算、冷却、撞 412/429 就熔断
  ③ Seen        —— 判重表 seen.md（同一个视频第二轮不再看）
  ④ Taste / Cfg —— 你的口味偏好与 pond 的配置

数据放在哪
----------
默认复用 pond 的工作区 `~/.dsh/bili-duty/`——那里已经有登录态、判重表、记忆、
日报和日记，大肥鱼接手后这些东西**全都在**，不会从头开始。
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

# ------------------------------------------------------------------ 路径
# 工作区已从 C:\Users\<用户>\.dsh 搬到 D 盘，统一走 DSH_HOME，没设才回落默认位置
_DSH_ROOT = os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh")
BILI_HOME = (os.environ.get("BILI_DUTY_HOME")
             or os.path.join(_DSH_ROOT, "bili-duty"))

WHALE_CFG_PATH = os.path.join(_DSH_ROOT, "bili-whale.config.json")

_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")


def home(*parts):
    """bili-duty 工作区里的路径。"""
    return os.path.join(BILI_HOME, *parts)


def today():
    return time.strftime("%Y-%m-%d")


def month():
    return time.strftime("%Y-%m")


# ------------------------------------------------------------------ 文本读写
def read_text(path, default=""):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    except Exception:
        pass
    return default


def write_text(path, text):
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        if os.path.exists(path):
            os.remove(path)
        os.rename(tmp, path)
        return True
    except Exception:
        return False


def append_text(path, text):
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
        return True
    except Exception:
        return False


def _read_json(path, default=None):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def _write_json(path, obj):
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        if os.path.exists(path):
            os.remove(path)
        os.rename(tmp, path)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ 桥接进程
class BiliBridge(object):
    """跟 _bili_bridge.py 长驻子进程说话。一行一个 JSON。

    子进程用的是 pond 自己的 Python 3.13 + .venv（里面有 bilibili_api / mcp），
    import 一次要好几秒，所以必须长驻，不能每次现起。
    """

    def __init__(self):
        self._p = None
        self._lock = threading.RLock()
        self._seq = 0
        self.last_error = ""
        self._tools = None

    # ---- 环境 ----
    @staticmethod
    def python_exe():
        """pond 配置里写了 pythonPath 就用它，否则找工作区的 .venv。"""
        cfg = _read_json(home("config.json"), {}) or {}
        p = cfg.get("pythonPath") or ""
        if p and os.path.exists(p):
            return p
        for cand in (home(".venv", "Scripts", "python.exe"),
                     home(".venv", "bin", "python")):
            if os.path.exists(cand):
                return cand
        raise RuntimeError("找不到 pond 的 Python 环境：%s" % home(".venv"))

    @staticmethod
    def bridge_script():
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_bili_bridge.py")
        if not os.path.exists(p):
            raise RuntimeError("找不到桥接脚本：%s" % p)
        return p

    def _spawn(self):
        env = dict(os.environ)
        env["BILI_DUTY_HOME"] = BILI_HOME
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        env["HF_ENDPOINT"] = "https://hf-mirror.com"      # 转写模型走镜像，不然连不上
        # pond 的 .venv 在受限环境里可能拿不到 %TEMP%，钉死到工作区 tmp/
        tmp = home("tmp")
        try:
            os.makedirs(tmp, exist_ok=True)
            for k in ("TMPDIR", "TEMP", "TMP"):
                env[k] = tmp
        except Exception:
            pass

        kw = {}
        if sys.platform == "win32":
            kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._p = subprocess.Popen(
            [self.python_exe(), self.bridge_script()],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, cwd=BILI_HOME, text=True, encoding="utf-8", errors="replace", **kw)
        return self._p

    def alive(self):
        p = self._p
        return bool(p) and p.poll() is None

    def stop(self):
        with self._lock:
            p, self._p = self._p, None
        if p:
            try:
                p.stdin.close()
            except Exception:
                pass
            try:
                p.terminate()
            except Exception:
                pass

    def restart(self):
        self.stop()
        with self._lock:
            self._spawn()

    # ---- 调用 ----
    def call(self, tool, args=None, timeout=120, retry=True):
        """调一个工具，返回 (ok, result_or_error_str)。线程安全。"""
        with self._lock:
            if not self.alive():
                try:
                    self._spawn()
                except Exception as e:
                    self.last_error = "桥接进程起不来：%s" % e
                    return False, self.last_error

            self._seq += 1
            req = {"id": self._seq, "tool": tool, "args": args or {}}
            try:
                self._p.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                self._p.stdin.flush()
            except Exception as e:
                self.last_error = "写给桥接进程失败：%s" % e
                # 写不进去说明进程没了：起一个新的再试一次（只重试一次）
                if retry:
                    self.restart()
                    return self.call(tool, args, timeout, retry=False)
                return False, self.last_error

            line = None
            try:
                # Windows 上没法对 pipe 做可移植的超时读，交给 watchdog 线程兜底
                box = {}

                def _reader():
                    try:
                        box["line"] = self._p.stdout.readline()
                    except Exception:
                        box["line"] = None

                t = threading.Thread(target=_reader, daemon=True)
                t.start()
                t.join(timeout)
                if t.is_alive():
                    self.last_error = "超时（%ss），已重启桥接" % timeout
                    self.restart()
                    return False, self.last_error
                line = box.get("line")
            except Exception as e:
                self.last_error = "桥接进程没回应（可能已退出）：%s" % e
                if retry:
                    self.restart()
                    return self.call(tool, args, timeout, retry=False)
                return False, self.last_error

            if not line:
                self.last_error = "桥接进程没回应（可能已退出）"
                if retry:
                    self.restart()
                    return self.call(tool, args, timeout, retry=False)
                return False, self.last_error

            try:
                resp = json.loads(line)
            except Exception as e:
                return False, "桥接返回了非 JSON：%s" % (line[:200],)

            if resp.get("ok"):
                return True, resp.get("result")
            err = resp.get("error") or "未知错误"
            self.last_error = str(err)
            # 撞风控就把信号递出去，RiskGuard 会照着退避
            _feed_risk(err)
            return False, err

    def ping(self, timeout=20):
        return self.call("__ping__", {}, timeout=timeout)

    def tools(self, timeout=60):
        ok, r = self.call("__tools__", {}, timeout=timeout)
        if ok and isinstance(r, list):
            self._tools = r
        return self._tools or []


_B = None
_B_LOCK = threading.RLock()


def bridge():
    """单例。所有插件共用一个桥接进程。"""
    global _B
    with _B_LOCK:
        if _B is None:
            _B = BiliBridge()
        return _B


# ------------------------------------------------------------------ 风控闸门
_RISK = None
_RISK_RE = re.compile(r"(412|429|-352|验证码|风控|操作频繁)", re.I)


def _feed_risk(err):
    """桥接返回里出现风控信号就退避。"""
    s = str(err)
    if _RISK_RE.search(s):
        try:
            RiskGuard().note_risk("接口报 %s" % (_RISK_RE.search(s).group(1),))
        except Exception:
            pass


class RiskGuard(object):
    """读写预算 + 冷却 + 撞 412/429 就退避，越撞退得越久（5/10/20/30 分钟封顶）。

    默认值照抄 whale 配置里的 risk 段（很保守）：
    读每小时 600 / 每天 3000；写每小时 12 / 每天 30；写间隔 120 秒；私信间隔 60 秒。
    写操作**真的会被挡住**，不是写在文档里好看。
    """

    DEFAULTS = {
        "minGapMs": 1500, "readHourly": 600, "readDaily": 3000,
        "writeHourly": 12, "writeDaily": 30, "writeGapSec": 120, "dmGapSec": 60,
    }
    BACKOFF = [300, 600, 1200, 1800]     # 5 / 10 / 20 / 30 分钟

    def __init__(self, path=None):
        self.path = path or home("dfy_risk.json")
        self.cfg = dict(self.DEFAULTS)
        cfg = _read_json(WHALE_CFG_PATH, {}) or {}
        self.cfg.update(cfg.get("risk") or {})
        self.st = _read_json(self.path, {}) or {}
        if self.st.get("day") != today():
            # 跨天：日计数清零，小时计数按桶名自然失效
            self.st = {"day": today()}
        self.st.setdefault("strike", 0)

    # ---- 持久化 ----
    def _save(self):
        _write_json(self.path, self.st)

    def _hour_bucket(self):
        return time.strftime("%Y-%m-%d-%H")

    def _bump(self, key, n=1):
        b = self._hour_bucket()
        hk = key + "Hour"
        if self.st.get(hk + "Bucket") != b:
            self.st[hk + "Bucket"] = b
            self.st[hk] = 0
        self.st[hk] = int(self.st.get(hk) or 0) + n
        self.st[key + "Daily"] = int(self.st.get(key + "Daily") or 0) + n
        self._save()

    # ---- 熔断 ----
    def is_blocked(self):
        until = float(self.st.get("blockedUntil") or 0)
        if until > time.time():
            return True, int(until - time.time())
        if until:
            self.st["blockedUntil"] = 0
            self._save()
        return False, 0

    def note_risk(self, reason=""):
        """撞一次就退避一档，最多 30 分钟。"""
        s = int(self.st.get("strike") or 0)
        sec = self.BACKOFF[min(s, len(self.BACKOFF) - 1)]
        self.st["strike"] = s + 1
        self.st["blockedUntil"] = time.time() + sec
        self.st["lastReason"] = reason
        self.st["lastRiskAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._save()
        return sec

    def clear_block(self):
        self.st["blockedUntil"] = 0
        self.st["strike"] = 0
        self._save()
        return True

    # ---- 闸门 ----
    def can_read(self):
        blocked, left = self.is_blocked()
        if blocked:
            return False, "风控退避中，还有 %d 秒" % left
        b = self._hour_bucket()
        if self.st.get("readHourBucket") != b:
            self.st["readHour"] = 0
        if int(self.st.get("readHour") or 0) >= int(self.cfg.get("readHourly")):
            return False, "这一小时读预算用完"
        if int(self.st.get("readDaily") or 0) >= int(self.cfg.get("readDaily")):
            return False, "今天读预算用完"
        gap = float(self.cfg.get("minGapMs") or 0) / 1000.0
        last = float(self.st.get("lastReqAt") or 0)
        if gap and time.time() - last < gap:
            time.sleep(gap - (time.time() - last))
        self.st["lastReqAt"] = time.time()
        return True, ""

    def spend_read(self, n=1):
        self._bump("read", n)

    def can_write(self):
        blocked, left = self.is_blocked()
        if blocked:
            return False, "风控退避中，还有 %d 秒" % left
        b = self._hour_bucket()
        if self.st.get("writeHourBucket") != b:
            self.st["writeHour"] = 0
        if int(self.st.get("writeHour") or 0) >= int(self.cfg.get("writeHourly")):
            return False, "这一小时写预算用完"
        if int(self.st.get("writeDaily") or 0) >= int(self.cfg.get("writeDaily")):
            return False, "今天写预算用完"
        gap = float(self.cfg.get("writeGapSec") or 0)
        last = float(self.st.get("lastWriteAt") or 0)
        if gap and time.time() - last < gap:
            return False, "写冷却中，还有 %d 秒" % int(gap - (time.time() - last))
        return True, ""

    def spend_write(self, n=1):
        self.st["lastWriteAt"] = time.time()
        self._bump("write", n)

    def can_dm(self):
        blocked, left = self.is_blocked()
        if blocked:
            return False, "风控退避中，还有 %d 秒" % left
        gap = float(self.cfg.get("dmGapSec") or 0)
        last = float(self.st.get("lastDmAt") or 0)
        if gap and time.time() - last < gap:
            return False, "私信冷却中，还有 %d 秒" % int(gap - (time.time() - last))
        return True, ""

    def spend_dm(self, n=1):
        self.st["lastDmAt"] = time.time()
        self._save()

    def status(self):
        blocked, left = self.is_blocked()
        return {
            "blocked": blocked, "blockedLeftSec": left,
            "strike": int(self.st.get("strike") or 0),
            "lastReason": self.st.get("lastReason") or "",
            "read": {"hour": int(self.st.get("readHour") or 0),
                     "daily": int(self.st.get("readDaily") or 0),
                     "hourMax": self.cfg.get("readHourly"),
                     "dailyMax": self.cfg.get("readDaily")},
            "write": {"hour": int(self.st.get("writeHour") or 0),
                      "daily": int(self.st.get("writeDaily") or 0),
                      "hourMax": self.cfg.get("writeHourly"),
                      "dailyMax": self.cfg.get("writeDaily")},
        }


# ------------------------------------------------------------------ 判重表
class Seen(object):
    """判重表 seen.md —— 看过的视频第二轮直接跳过。"""

    def __init__(self, path=None):
        self.path = path or home("seen.md")

    def bvids(self):
        txt = read_text(self.path)
        out = []
        for m in _BV_RE.finditer(txt):
            if m.group(0) not in out:
                out.append(m.group(0))
        return out

    def has(self, bvid):
        return bool(bvid) and bvid in self.bvids()

    def add(self, bvid, line=None):
        txt = line or ("- %s · %s" % (today(), bvid))
        return append_text(self.path, txt.rstrip() + "\n")


# ------------------------------------------------------------------ 口味
class Taste(object):
    """你的口味。优先读 pond 工作区，没有就退回 dsh 里那份 whale 配置。"""

    def __init__(self, path=None):
        self.path = path or home("taste.json")
        self.like = []
        self.dislike = []
        self.keywords = []
        self.rules = ""
        self.criteria = ""

    def load(self):
        d = _read_json(self.path, None)
        if not isinstance(d, dict):
            cfg = _read_json(WHALE_CFG_PATH, {}) or {}
            d = cfg.get("preferences") or {}
        self.like = list(d.get("like") or d.get("likes") or [])
        self.dislike = list(d.get("dislike") or d.get("dislikes") or [])
        self.keywords = list(d.get("keywords") or [])
        self.rules = d.get("rules") or ""
        self.criteria = d.get("criteria") or ""
        return self


# ------------------------------------------------------------------ pond 配置
def cfg_all():
    return _read_json(home("config.json"), {}) or {}


def cfg_get(key, default=None):
    v = cfg_all().get(key)
    return default if v is None else v


def cfg_set(key, value):
    """改配置。

    pond 白名单里的键走 bili_setting_set（它有范围校验），
    白名单外的（我们新增的）直接写文件。
    """
    cfg = cfg_all()
    old = cfg.get(key)
    cfg[key] = value
    if not _write_json(home("config.json"), cfg):
        return {"ok": False, "error": "写不进 %s" % home("config.json")}
    # 顺手让桥接进程那边的内存配置也跟着变（失败了不影响文件）
    try:
        b = bridge()
        if b.alive():
            b.call("bili_setting_set", {"key": key, "value": value}, timeout=15, retry=False)
    except Exception:
        pass
    return {"ok": True, "key": key, "value": value, "old": old}


def whale_cfg():
    """dsh-bili-whale 的配置（风控预算、作息窗口、口味）。

    ⚠️ 它在 ~/.dsh/ 下，不在 bili-duty 工作区里——别读错地方。
    """
    return _read_json(WHALE_CFG_PATH, {}) or {}
