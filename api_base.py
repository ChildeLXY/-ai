# -*- coding: utf-8 -*-
"""模型服务地址的唯一来源。

以前各处直接写死 http://localhost:11434，接入云端 API 时得改十几个地方。
现在统一走这里：

  · 云端关闭（默认）→ 直连本机 Ollama 11434，行为和以前完全一样，不依赖任何新进程
  · 云端开启      → 指向本机兼容代理 11435，由 cloud_proxy.py 翻译成 OpenAI 协议

注意：不管哪种模式，请求目标都在 **本机**，所以一律绕过系统代理
（用户级 http_proxy=127.0.0.1:7897，Clash 没开时 urllib 会把 localhost 也塞进代理变 502）。
出公网那一跳由 cloud_proxy.py 自己处理。
"""
import json
import os
import socket
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "cloud_config.json")

LOCAL_BASE = "http://127.0.0.1:11434"
PROXY_BASE = "http://127.0.0.1:11435"

DEFAULTS = {
    "enabled": False,
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "",
    "model": "deepseek-chat",
    "vision": False,
    "thinking": False,
    "proxy": "",              # 出公网用的代理，如 http://127.0.0.1:7897；留空直连
    "max_tokens": 4096,
    # 云端限流(429)/服务端故障(5xx)/网络不通时，自动改走本机 Ollama 继续对话，
    # 而不是把一句报错甩给用户。401/403 不回退——key 错了要说清楚，回退会掩盖问题。
    "fallback_local": True,
}

# 本机请求恒定绕过系统代理
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def config():
    """读配置，永远返回完整字段（缺的用默认值补）。"""
    c = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            c.update(json.load(f) or {})
    except Exception:
        pass
    return c


def save_config(c):
    """写配置（只允许 DEFAULTS 里的键，避免塞进奇怪的东西）。

    空 api_key 视为"不修改"：前端换渠道时会把 Key 框清空，
    如果直接写进去就会把已存的 Key 抹掉，下次请求报 401 更难查。
    """
    old = config()
    for k in DEFAULTS:
        if k in c:
            if k == "api_key" and not str(c[k] or "").strip():
                continue
            old[k] = c[k]
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(old, f, ensure_ascii=False, indent=2)
    return old


def enabled():
    return bool(config().get("enabled"))


# 代理端口探测结果缓存，避免每轮对话探十几次（本地回环本身极快，1 秒足够新鲜）
_PROBE = {"t": 0.0, "ok": False}


def proxy_alive(timeout=0.25):
    """11435 上有没有进程在听。"""
    now = time.time()
    if now - _PROBE["t"] < 1.0:
        return _PROBE["ok"]
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", 11435))
        ok = True
    except Exception:
        ok = False
    finally:
        try:
            s.close()
        except Exception:
            pass
    _PROBE.update(t=now, ok=ok)
    return ok


def base():
    """当前该把请求打到哪里。

    云端开着但兼容代理没跑起来（开机自启时 pythonw 先启动、或者用户手动
    杀了代理窗口）时，**不能**把请求丢给一个没人听的端口——那样整个程序
    会变成"什么都发不出去，还退不回本地"。这里先尝试把代理拉起来，
    拉不起来才退回 11434，至少能说话，设置页会显示"代理未就绪"。
    """
    if not enabled():
        return LOCAL_BASE
    if proxy_alive():
        return PROXY_BASE
    if ensure_proxy():
        return PROXY_BASE
    return LOCAL_BASE


# 自愈节流：代理挂了才尝试重启，20 秒内最多试一次，别把请求卡住
_HEAL = {"t": 0.0}


def ensure_proxy(wait=1.2):
    """云端模式下代理没在听 → 自己把它拉起来（后台 pythonw，无窗口）。

    以前只在"保存配置"和程序启动那一刻拉一次，代理中途崩了就一直没人管：
    插件（选表情包、判心情、记记忆）全部悄悄打到 11434，云端模型名在那里
    是 404，表现就是"云端能聊天，但不发表情包了"。
    """
    now = time.time()
    if now - _HEAL["t"] < 20.0:
        return proxy_alive()
    _HEAL["t"] = now
    try:
        import subprocess
        import sys
        p = os.path.join(HERE, "cloud_proxy.py")
        if not os.path.exists(p):
            return False
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen([sys.executable, p], cwd=HERE, creationflags=flags)
        deadline = time.time() + wait
        while time.time() < deadline:
            time.sleep(0.15)
            _PROBE["t"] = 0.0          # 让 proxy_alive 重新探
            if proxy_alive():
                return True
    except Exception:
        pass
    _PROBE["t"] = 0.0
    return proxy_alive()


def url(path):
    """path 形如 '/api/chat'。"""
    if not path.startswith("/"):
        path = "/" + path
    return base() + path


def opener():
    return _LOCAL_OPENER
