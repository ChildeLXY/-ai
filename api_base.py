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
    # 可选的云端模型清单。同一个 Key 下多个模型并存时用：
    #   [{"id": "glm-4.7-flash", "vision": false, "thinking": true}, ...]
    # 有了这份清单，/api/tags 会把它们全列出来，前端模型下拉就能直接切换，
    # 代理按请求里的 model 字段分发到对应上游模型，而不是死认 config.model。
    # 清单为空时保持老行为：只认 model 一个。
    "models": [],
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


def cloud_models(c=None):
    """归一化云端模型清单。

    返回 [{id, vision, thinking}, ...]；清单为空时退回单模型模式，
    把 config.model 变成只有一条、且沿用顶层 vision/thinking 开关的清单。

    为什么要归一化：models 里可能缺字段、可能写成字符串、可能 id 为空。
    代理和前端都要按同一份结构理解，统一在这里处理，别处只管用。
    """
    c = c if c is not None else config()
    raw = c.get("models") or []
    out = []
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.replace(",", " ").split() if x.strip()]
    if not isinstance(raw, (list, tuple)):
        raw = []
    for item in raw:
        if isinstance(item, str):
            item = {"id": item}
        if not isinstance(item, dict):
            continue
        mid = str(item.get("id") or item.get("model") or "").strip()
        if not mid:
            continue
        out.append({
            "id": mid,
            "vision": bool(item.get("vision", c.get("vision", False))),
            "thinking": bool(item.get("thinking", c.get("thinking", False))),
            "context": _guess_context(mid, item.get("context")),
        })
    if not out:
        mid = str(c.get("model") or "").strip()
        if mid:
            out.append({
                "id": mid,
                "vision": bool(c.get("vision", False)),
                "thinking": bool(c.get("thinking", False)),
                "context": _guess_context(mid, None),
            })
    return out


def _guess_context(model_id, explicit=None):
    """推断云端模型的上下文窗口上限（tokens）。

    优先用配置里显式给的 context 字段；否则按模型名猜测常见值。
    这只是给前端面板显示"分母"用的，不影响实际请求——云端窗口由厂商决定。
    """
    if explicit:
        try:
            return int(explicit)
        except (TypeError, ValueError):
            pass
    m = str(model_id or "").lower()
    # 长上下文型号
    for kw, n in (("longcontext", 131072), ("128k", 131072), ("200k", 200000),
                  ("1m", 1048576), ("256k", 262144)):
        if kw in m:
            return n
    if "deepseek" in m:
        return 65536
    if "glm-4" in m or "glm-4." in m:
        return 131072          # 智谱 GLM-4 系列普遍 128K
    if "qwen" in m:
        return 131072
    if "gpt-4" in m or "gpt-4o" in m:
        return 128000
    if "claude" in m:
        return 200000
    if "gemini" in m:
        return 1048576
    return 32768               # 保守默认


def cloud_model_ids(c=None):
    """云端模型 id 列表（给 /api/tags 和前端下拉用）。"""
    return [m["id"] for m in cloud_models(c)]


# 各厂商"官方标注免费"的模型目录。
#
# 为什么不能只靠厂商的 /models 接口：实测智谱的 /api/paas/v4/models 只返回
# 12 个付费模型（glm-4.5 / glm-4.6 / glm-4.7 / glm-5 系列），
# **一个免费模型都没有** —— 免费模型是「不限量但不在清单里」的，
# 照着接口返回的清单填，用户就永远看不到免费那批。
# 所以拉取厂商清单时，必须再并上这份内置目录。
#
# 能力标注（vision/thinking）是逐个实测出来的：
#   · glm-4.7-flash  支持思维链，不支持看图（发 image part 报 1210）
#   · glm-4.6v-flash 支持思维链，也支持看图
#   · glm-4.5-flash  支持思维链，不支持看图
#   · glm-4-flash / glm-4-flashx  都不支持思维链、不支持看图
FREE_MODEL_CATALOG = {
    "bigmodel.cn": [
        {"id": "glm-4.7-flash", "vision": False, "thinking": True,
         "note": "官方免费 · 文本 · 200K 上下文"},
        {"id": "glm-4.6v-flash", "vision": True, "thinking": True,
         "note": "官方免费 · 支持看图"},
        {"id": "glm-4.5-flash", "vision": False, "thinking": True,
         "note": "免费 · 文本 · 128K"},
        {"id": "glm-4-flash", "vision": False, "thinking": False,
         "note": "老牌免费 · 轻量"},
        {"id": "glm-4-flashx", "vision": False, "thinking": False,
         "note": "老牌免费 · 更快"},
    ],
}


def free_models_for(base_url):
    """这个接口地址对应厂商的免费模型清单（没有就给空表）。"""
    b = (base_url or "").lower()
    for frag, items in FREE_MODEL_CATALOG.items():
        if frag in b:
            return [dict(x) for x in items]
    return []


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
