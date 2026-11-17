# -*- coding: utf-8 -*-
"""
大肥鱼 · 扩展传感器（纯标准库 + ctypes，零安装、不碰 C 盘）
覆盖：屏幕亮度、磁盘状态、ICMP 延迟、网速、麦克风、摄像头、NTP 校时、播放器识别。
所有慢操作都有超时/节流/容错，单项失败返回 None，不影响其他项与主循环。
"""
import ctypes
import os
import socket
import struct
import subprocess
import time

CREATE_NO_WINDOW = 0x08000000
PING_HOST = "223.5.5.5"        # 阿里公共 DNS，稳定
NTP_HOST = "ntp.aliyun.com"
MEDIA_HINT = ("网易云", "qq音乐", "spotify", "windows media", "音乐", "播放器",
              "potplayer", "vlc", "kugou", "酷狗", "酷我")

_slow_cache = {}                # 慢查询节流缓存
_if_last = {"t": 0.0, "in": 0, "out": 0, "ready": False}


# ---------------- 屏幕亮度（独立进程里低频 WMI，失败返回 None） ----------------
def brightness():
    if _fresh("brightness", 300):
        return _slow_cache["brightness"]["v"]
    cmd = ("(Get-CimInstance -Namespace root\\WMI -ClassName WmiMonitorBrightness "
           "-ErrorAction SilentlyContinue).CurrentBrightness")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, timeout=8,
                           creationflags=CREATE_NO_WINDOW)
        v = r.stdout.strip()
        out = int(v) if v.isdigit() else None
    except Exception:
        out = None
    _store("brightness", out)
    return out


# ---------------- 磁盘：枚举 + 容量/剩余 ----------------
def disks():
    out = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if not (bitmask & (1 << i)):
            continue
        root = chr(65 + i) + ":\\"
        free = ctypes.c_ulonglong()
        total = ctypes.c_ulonglong()
        if ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                root, None, ctypes.byref(total), ctypes.byref(free)):
            out.append({"drive": chr(65 + i),
                        "totalGB": round(total.value / 2 ** 30),
                        "freeGB": round(free.value / 2 ** 30)})
    return out


# ---------------- ICMP 延迟 ----------------
class _IPOPT(ctypes.Structure):
    _fields_ = [("Ttl", ctypes.c_ubyte), ("Tos", ctypes.c_ubyte),
                ("Flags", ctypes.c_ubyte), ("OptionsSize", ctypes.c_ubyte),
                ("OptionsData", ctypes.c_void_p)]


class _ECHO(ctypes.Structure):
    _fields_ = [("Address", ctypes.c_ulong), ("Status", ctypes.c_ubyte),
                ("RoundTripTime", ctypes.c_ulong), ("DataSize", ctypes.c_ushort),
                ("Reserved", ctypes.c_ushort), ("Data", ctypes.c_void_p),
                ("Options", _IPOPT)]


def ping(host=PING_HOST):
    try:
        iphlp = ctypes.windll.iphlpapi
        ws2 = ctypes.windll.ws2_32
        # 64 位必须显式声明句柄/指针原型，否则参数被当 32 位截断 -> 访问违例
        ws2.inet_addr.restype = ctypes.c_ulong
        ws2.inet_addr.argtypes = [ctypes.c_char_p]
        iphlp.IcmpCreateFile.restype = ctypes.c_void_p
        iphlp.IcmpCreateFile.argtypes = []
        iphlp.IcmpSendEcho.restype = ctypes.c_ulong
        iphlp.IcmpSendEcho.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ushort,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
        iphlp.IcmpCloseHandle.argtypes = [ctypes.c_void_p]

        ip = ws2.inet_addr(host.encode("ascii"))
        h = iphlp.IcmpCreateFile()
        if not h:
            return -1
        sendbuf = ctypes.create_string_buffer(b"abcdefghijklmnopqrstuvwxyz012345")
        buf = ctypes.create_string_buffer(ctypes.sizeof(_ECHO) + sendbuf.value.__len__() + 8)
        n = iphlp.IcmpSendEcho(
            h, ip, sendbuf, len(sendbuf.value), None,
            buf, ctypes.sizeof(buf), 1500)
        iphlp.IcmpCloseHandle(h)
        if n == 0:
            return -1
        rep = ctypes.cast(buf, ctypes.POINTER(_ECHO)).contents
        return int(rep.RoundTripTime) if rep.Status == 0 else -1
    except Exception:
        return -1


# ---------------- 网速：PDH 性能计数器（避免 GetIfTable 结构布局问题） ----------------
PDH_FMT_DOUBLE = 0x00000200


class _PDHVAL(ctypes.Structure):
    _fields_ = [("CStatus", ctypes.c_ulong),
                ("pad", ctypes.c_ulong),
                ("DoubleValue", ctypes.c_double)]


_pdh = {"ready": False, "q": None, "c": None}


def _pdh_proto(pdh):
    pdh.PdhOpenQueryW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    pdh.PdhOpenQueryW.restype = ctypes.c_long
    pdh.PdhAddEnglishCounterW.argtypes = [
        ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p]
    pdh.PdhAddEnglishCounterW.restype = ctypes.c_long
    pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
    pdh.PdhCollectQueryData.restype = ctypes.c_long
    pdh.PdhGetFormattedCounterValue.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p]
    pdh.PdhGetFormattedCounterValue.restype = ctypes.c_long


def net_speed():
    """返回 (总速率KB/s, 0)：PDH 只给全接口总字节/秒，不区分上下行。首次建基线返回 0。"""
    try:
        pdh = ctypes.windll.pdh
        _pdh_proto(pdh)
        if not _pdh["ready"]:
            q = ctypes.c_void_p()
            if pdh.PdhOpenQueryW(None, None, ctypes.byref(q)) != 0:
                return 0.0, 0.0
            c = ctypes.c_void_p()
            path = r"\Network Interface(*)\Bytes Total/sec"
            if pdh.PdhAddEnglishCounterW(q, path, None, ctypes.byref(c)) != 0:
                return 0.0, 0.0
            _pdh.update(ready=True, q=q, c=c)
            pdh.PdhCollectQueryData(q)
            return 0.0, 0.0
        pdh.PdhCollectQueryData(_pdh["q"])
        val = _PDHVAL()
        typ = ctypes.c_ulong(0)
        rc = pdh.PdhGetFormattedCounterValue(
            _pdh["c"], PDH_FMT_DOUBLE, ctypes.byref(typ), ctypes.byref(val))
        if rc != 0 or val.CStatus != 0:
            return 0.0, 0.0
        return round(max(0, val.DoubleValue) / 1024.0, 1), 0.0
    except Exception:
        return 0.0, 0.0


# ---------------- 麦克风 / 摄像头：只数设备，不采集内容 ----------------
def mic_count():
    try:
        return int(ctypes.windll.winmm.waveInGetNumDevs())
    except Exception:
        return 0


def camera_count():
    try:
        n = 0
        for i in range(10):
            name = ctypes.create_unicode_buffer(256)
            ver = ctypes.create_unicode_buffer(256)
            if ctypes.windll.avicap32.capGetDriverDescriptionW(
                    i, name, 256, ver, 256):
                n += 1
        return n
    except Exception:
        return 0


# ---------------- NTP 校时：只算偏差，不自动改系统时间 ----------------
def ntp_offset(server=NTP_HOST):
    if _fresh("ntp", 1800):
        return _slow_cache["ntp"]["v"]
    try:
        pkt = b"\x1b" + b"\0" * 47
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(3)
        t0 = time.time()
        s.sendto(pkt, (server, 123))
        data = s.recv(48)
        t1 = time.time()
        s.close()
        if len(data) < 48:
            _store("ntp", None)
            return None
        secs, frac = struct.unpack("!II", data[40:48])
        server_t = secs - 2208988800 + frac / 2 ** 32
        out = round(server_t - (t0 + t1) / 2, 2)
    except Exception:
        out = None
    _store("ntp", out)
    return out


# ---------------- 播放器识别：基于活动窗口名（基础版） ----------------
def media_player(active_window=""):
    w = (active_window or "").lower()
    for k in MEDIA_HINT:
        if k in w:
            return active_window.strip()[:40]
    return ""


# ---------------- 节流工具 ----------------
def _fresh(key, ttl):
    e = _slow_cache.get(key)
    return bool(e and time.time() - e["t"] < ttl)


def _store(key, v):
    _slow_cache[key] = {"t": time.time(), "v": v}


def sample_extras(active_window=""):
    """一次性汇总全部扩展传感器。慢项各自节流，整体不阻塞。"""
    dk, uk = net_speed()
    return {
        "brightness": brightness(),
        "disks": disks(),
        "pingMs": ping(),
        "downKBs": dk,
        "upKBs": uk,
        "micDevices": mic_count(),
        "cameraDevices": camera_count(),
        "ntpOffsetSec": ntp_offset(),
        "media": media_player(active_window),
    }
