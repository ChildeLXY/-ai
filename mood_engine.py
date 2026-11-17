# -*- coding: utf-8 -*-
"""
大肥鱼 · 情绪引擎 + 事件触发器
- 纯 Python 标准库 + ctypes，无第三方依赖，不写 C 盘。
- 情绪：疲惫 / 兴奋 / 无聊 / 专注（0~100），带惯性平滑，持久化到 mood.json。
- 事件：边沿检测（离开/回来/深夜/高负载/锁屏解锁/断网联网），带冷却，
  触发时返回事件名，由 watcher 写桌宠气泡。
被 mood_watcher 周期调用；主程序只读 mood.json，不直接跑引擎。
"""
import ctypes
import os
import json
import time
import datetime
import random
import threading

import sensors_extra

DATA_DIR = os.environ.get("DFY_DATA_DIR") or r"D:\本地部署大肥鱼环境\data"
CACHE_DIR = os.environ.get("DFY_CACHE_DIR") or r"D:\大肥鱼缓存"
MOOD_FILE = os.path.join(DATA_DIR, "mood.json")
SAY_FILE = os.path.join(CACHE_DIR, "pet_say.txt")

LEAVE_SEC = 300        # 5 分钟无操作 = 离开
BACK_SEC = 30          # 空闲回到 30 秒内 = 回来
HIGH_CPU = 85          # CPU 高负载阈值
HIGH_CPU_TIMES = 3     # 连续 3 次高才触发
FIRE_COOLDOWN = 600    # 同类事件至少隔 10 分钟
SMOOTH = 0.65          # 惯性：新=旧*0.65+目标*0.35

WORK_HINT = ("code", "visual studio", "pycharm", "intellij", "idea", "terminal",
             "powershell", "cursor", "webstorm", "工程", "编辑器", "开发", "py")
PLAY_HINT = ("cs2", "csgo", "steam", "游戏", "bilibili", "哔哩", "视频",
             "player", "media", "网易云", "音乐", "游戏")

SAY = {
    "user_leave": ["主人走掉了…那我自己玩会儿。", "（趴在窗口等你回来）", "你不在，大肥鱼有点无聊。"],
    "user_back": ["你回来啦！我一直在等你呢。", "欢迎回来～刚才想死你了。", "（甩尾巴）你终于想起我啦！"],
    "late_night": ["都这么晚了，早点睡嘛，身体要紧。", "夜深了，大肥鱼陪你，但别熬太久。", "快去睡觉，剩下的明天再说啦。"],
    "high_load": ["电脑跑得好卖力，注意别让它太累哦。", "风扇在吼了，我帮你盯着，你也歇口气。"],
    "high_ping": ["网络有点卡，延迟好高，我先慢慢等。", "网好像不太顺畅，别急。"],
    "lock": ["（锁屏了，我乖乖守着）", "你去忙，我在这儿看家。"],
    "unlock": ["你回来啦，家里一切正常。", "（探头）解锁成功，大肥鱼报告平安！"],
    "offline": ["咦，网络好像断了。", "没网了…我先在本地陪你。"],
    "online": ["网络恢复啦！", "有网了，又能一起冲浪了。"],
    "desktop_add": ["你桌面上多了点东西，我帮你留意着呢。", "（探头）桌面有新文件，需要我帮你整理吗？"],
    "desktop_remove": ["桌面上少了个文件，我记下来啦。", "有东西被清走了，桌面清爽一点。"],
}

# ---------- ctypes 结构体模块级（避免函数内定义跨线程段错误） ----------
_LOCK = threading.RLock()


class _FT(ctypes.Structure):
    _fields_ = [("idle", ctypes.c_ulonglong),
                ("kernel", ctypes.c_ulonglong),
                ("user", ctypes.c_ulonglong)]


class _LI(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_uint), ("dw", ctypes.c_uint)]


_ft = _FT()
_li = _LI()
_last_cpu = {"idle": 0, "total": 0, "ready": False}


def _idle_seconds():
    with _LOCK:
        _li.cb = ctypes.sizeof(_LI)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(_li))
        return int((ctypes.windll.kernel32.GetTickCount64() - _li.dw) / 1000)


def _cpu_percent():
    """GetSystemTimes 差值；首次无基线返回 0。"""
    with _LOCK:
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(_ft), ctypes.byref(_ft), ctypes.byref(_ft))
        idle, total = _ft.idle, _ft.kernel + _ft.user
        if not _last_cpu["ready"]:
            _last_cpu.update(idle=idle, total=total, ready=True)
            return 0
        di, dt = idle - _last_cpu["idle"], total - _last_cpu["total"]
        _last_cpu.update(idle=idle, total=total)
        if dt <= 0:
            return 0
        return max(0, min(100, int(100 * (1 - di / dt))))


def _active_window():
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
        return buf.value.strip()
    except Exception:
        return ""


def _session_locked():
    """WTS_SESSIONSTATE：0=锁定，1=活跃。取不到就当未锁定。"""
    try:
        WTS_CURRENT_SESSION = 0xFFFFFFFF
        WTS_SESSIONSTATE = 24
        buf = ctypes.pointer(ctypes.c_uint())
        n = ctypes.c_uint()
        ok = ctypes.windll.wtsapi32.WTSQuerySessionInformationW(
            0, WTS_CURRENT_SESSION, WTS_SESSIONSTATE,
            ctypes.byref(buf), ctypes.byref(n))
        if not ok:
            return False
        val = buf.contents.value
        ctypes.windll.wtsapi32.WTSFreeMemory(buf)
        return val == 0      # WTS_SESSIONSTATE_LOCK=0
    except Exception:
        return False


def _online():
    """wininet 联网状态，轻量；失败默认在线，避免误报断网。"""
    try:
        flags = ctypes.c_ulong(0)
        ok = ctypes.windll.wininet.InternetGetConnectedState(ctypes.byref(flags), 0)
        return bool(ok)
    except Exception:
        return True


def _up_seconds():
    return ctypes.windll.kernel32.GetTickCount64() // 1000


def _clipboard_kind():
    """只判断剪贴板里是什么类型，不读具体内容（保护隐私）。"""
    try:
        u = ctypes.windll.user32
        if not u.OpenClipboard(None):
            return "未知"
        try:
            if u.IsClipboardFormatAvailable(15):    # CF_HDROP 文件
                return "文件"
            if u.IsClipboardFormatAvailable(8):     # CF_DIB 图片
                return "图片"
            if u.IsClipboardFormatAvailable(13):    # CF_UNICODETEXT 文本
                return "文本"
            return "空"
        finally:
            u.CloseClipboard()
    except Exception:
        return "未知"


def _desktop_dir():
    """取真实桌面目录，兼容 OneDrive 接管。"""
    home = os.path.expanduser("~")
    cands = [os.path.join(home, "Desktop"),
             os.path.join(home, "OneDrive", "Desktop"),
             os.path.join(home, "OneDrive", "桌面"),
             os.path.join(home, "桌面")]
    for c in cands:
        if os.path.isdir(c):
            return c
    return cands[0]


def _desktop_snapshot():
    """桌面当前顶层条目名集合。"""
    try:
        return set(os.listdir(_desktop_dir()))
    except Exception:
        return set()


# ---------- 持久化 ----------
def _default_state():
    return {
        "mood": {"fatigue": 20, "excitement": 30, "boredom": 20, "focus": 35},
        "label": "平静",
        "prev": {"away": False, "late": False, "locked": False, "online": True},
        "high_cpu_count": 0,
        "high_ping_count": 0,
        "desktop_files": [],
        "clip_kind": "",
        "extras": {},
        "last_fire": {},
        "updated": "",
    }


def load_state():
    try:
        if os.path.exists(MOOD_FILE):
            with open(MOOD_FILE, "r", encoding="utf-8-sig") as f:
                st = json.load(f)
            st.setdefault("mood", _default_state()["mood"])
            st.setdefault("prev", _default_state()["prev"])
            st.setdefault("last_fire", {})
            st.setdefault("high_cpu_count", 0)
            st.setdefault("desktop_files", [])
            st.setdefault("clip_kind", "")
            st.setdefault("extras", {})
            st.setdefault("high_ping_count", 0)
            return st
    except Exception:
        pass
    return _default_state()


def save_state(st):
    os.makedirs(os.path.dirname(MOOD_FILE), exist_ok=True)
    st["updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tmp = MOOD_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MOOD_FILE)


def _label(mood):
    if mood["fatigue"] >= 65:
        return "犯困"
    if mood["boredom"] >= 65:
        return "无聊想你"
    if mood["excitement"] >= 60:
        return "开心兴奋"
    if mood["focus"] >= 60:
        return "专注陪你"
    return "平静"


def _smooth(old, target):
    return round(old * SMOOTH + target * (1 - SMOOTH), 1)


def _fire(st, event, now_ts):
    """冷却通过则登记并返回事件，否则 None。"""
    last = st["last_fire"].get(event, 0)
    if now_ts - last < FIRE_COOLDOWN:
        return None
    st["last_fire"][event] = now_ts
    return event


def tick():
    """采样一次，更新情绪、检测事件；返回事件名列表（可能为空）。"""
    with _LOCK:
        st = load_state()
        now = datetime.datetime.now()
        h, idle = now.hour, _idle_seconds()
        cpu, win = _cpu_percent(), _active_window().lower()
        locked, online = _session_locked(), _online()
        up_h = _up_seconds() / 3600.0
        away = idle > LEAVE_SEC
        late = h >= 23 or h < 6
        wl = win.lower()
        is_work = any(k in wl for k in WORK_HINT)
        is_play = any(k in wl for k in PLAY_HINT)

        # ---- 目标情绪 ----
        t_fatigue = 20
        if late:
            t_fatigue = 75 if h >= 23 or h < 2 else 55
        t_fatigue = min(95, t_fatigue + up_h * 1.5)          # 开机越久越累
        t_excite = 25
        if idle < BACK_SEC and st["prev"]["away"]:
            t_excite = 85                                     # 用户刚回来
        elif idle < BACK_SEC:
            t_excite = 50
        t_bored = 80 if away else (55 if idle > 120 else 20)
        t_focus = 75 if is_work else (20 if is_play else 40)
        if cpu >= HIGH_CPU:
            t_focus = max(t_focus, 70)

        m = st["mood"]
        m["fatigue"] = max(0, min(100, _smooth(m["fatigue"], t_fatigue)))
        m["excitement"] = max(0, min(100, _smooth(m["excitement"], t_excite)))
        m["boredom"] = max(0, min(100, _smooth(m["boredom"], t_bored)))
        m["focus"] = max(0, min(100, _smooth(m["focus"], t_focus)))
        st["label"] = _label(m)

        # ---- 事件边沿检测 ----
        p, events, now_ts = st["prev"], [], int(time.time())
        if away and not p["away"]:
            events.append(_fire(st, "user_leave", now_ts))
        if (not away) and p["away"] and idle < BACK_SEC:
            events.append(_fire(st, "user_back", now_ts))
        if late and not p["late"]:
            events.append(_fire(st, "late_night", now_ts))
        if locked and not p["locked"]:
            events.append(_fire(st, "lock", now_ts))
        if (not locked) and p["locked"]:
            events.append(_fire(st, "unlock", now_ts))
        if (not online) and p["online"]:
            events.append(_fire(st, "offline", now_ts))
        if online and not p["online"]:
            events.append(_fire(st, "online", now_ts))
        # 高负载：连续计数
        if cpu >= HIGH_CPU:
            st["high_cpu_count"] += 1
            if st["high_cpu_count"] == HIGH_CPU_TIMES:
                events.append(_fire(st, "high_load", now_ts))
        else:
            st["high_cpu_count"] = 0

        p.update(away=away, late=late, locked=locked, online=online)

        # 剪贴板：只记录类型，不读内容、不弹气泡（具体内容需用户同意后由 read_clipboard 取）
        st["clip_kind"] = _clipboard_kind()

        # 扩展传感器：亮度/磁盘/延迟/网速/麦克风/摄像头/NTP校时/播放器
        extras = sensors_extra.sample_extras(_active_window())
        st["extras"] = extras
        # 高延迟：连续 3 次 ping>300ms 才提示（-1 读取失败不计，避免误报）
        pm = extras.get("pingMs", -1)
        if pm > 300:
            st["high_ping_count"] += 1
            if st["high_ping_count"] == 3:
                events.append(_fire(st, "high_ping", now_ts))
        elif pm >= 0:
            st["high_ping_count"] = 0

        # 桌面文件变动：首次只建基线，之后对比新增/删除并触发事件
        snap = _desktop_snapshot()
        old = set(st.get("desktop_files") or [])
        if old:
            if snap - old:
                events.append(_fire(st, "desktop_add", now_ts))
            if old - snap:
                events.append(_fire(st, "desktop_remove", now_ts))
        st["desktop_files"] = sorted(snap)

        save_state(st)
        return [e for e in events if e]


def say_for(event):
    return random.choice(SAY.get(event, [""]))


def deliver(line):
    try:
        os.makedirs(os.path.dirname(SAY_FILE), exist_ok=True)
        with open(SAY_FILE, "w", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def read_mood():
    """供主程序/前端：返回当前情绪摘要。"""
    st = load_state()
    m = st["mood"]
    return {"label": st["label"], "mood": m, "clip_kind": st.get("clip_kind", ""),
            "desktop_count": len(st.get("desktop_files", [])),
            "extras": st.get("extras", {}), "updated": st.get("updated", "")}
