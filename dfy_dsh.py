"""DSH 插件转接口（Python 侧）。

架构：
    DSH 插件是跑在 Node 里的 ESM 模块，依赖一堆 DSH 宿主服务。
    这里连的是 dsh_host/host.mjs —— 一个用真 cordis 运行时搭的「兼容宿主」，
    它把插件原样加载起来，然后把两样东西开成 HTTP：
        /context  → 所有插件往提示词里注入的文本（人设、记忆、心情、贴纸提示…）
        /tools    → 所有插件注册的工具（记忆读写、贴纸搜索/发送…）
    本模块负责：拉起宿主、拉取这些内容、调用工具、发"一轮结束"事件、管插件市场。

为什么不用 DSH 本体：
    插件的 `import @deepseek-ai/*` 只有 DSH 内部能解析，DSH 桌面版又必须开着窗口。
    自建宿主让大肥鱼AI 自带这一层，不依赖用户去开 DSH。
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HOST_DIR = r"D:\大肥鱼AI\dsh_host"
HOST_ENTRY = os.path.join(HOST_DIR, "host.mjs")
HOST_LOG = os.path.join(HOST_DIR, "host-run.log")
# 插件源码所在的 DSH profile（只读引用，不往里写东西）
PROFILE_DIR = r"E:\DSH-home\profiles\desktop"
PLUGIN_ROOT = os.path.join(PROFILE_DIR, "node_modules")
# 转接口自己的插件目录：市场装的新插件都落这里，不碰 DSH 的 pnpm 结构
OWN_PLUGIN_DIR = os.path.join(HOST_DIR, "plugins")
OWN_PLUGIN_ROOT = os.path.join(OWN_PLUGIN_DIR, "node_modules")
DSH_HOME = os.environ.get("DSH_HOME") or r"E:\DSH-home"
CACHE_DIR = r"D:\大肥鱼缓存"

_SETTINGS = r"D:\大肥鱼AI\ds图片\settings.json"
_NOPROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_LOCK = threading.Lock()
_CACHE = {"at": 0, "text": "", "tools": [], "ttl": 45}
_PROC = None


def log(msg, level="INFO"):
    line = "[%s][DSH][%s] %s" % (time.strftime("%H:%M:%S"), level, msg)
    try:
        with open(os.path.join(CACHE_DIR, "dsh_bridge.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _cfg(key, default=""):
    try:
        with open(_SETTINGS, encoding="utf-8-sig") as f:
            d = json.load(f)
        v = d.get(key, default)
        return default if v is None else v
    except Exception:
        return default


def base_url():
    port = int(_cfg("dsh_host_port", 6800) or 6800)
    return "http://127.0.0.1:%d" % port


def enabled():
    return str(_cfg("dsh_host_enabled", "1")).lower() in ("1", "true", "on")


# ------------------------------------------------------------------ HTTP
def _req(path, data=None, timeout=20):
    url = base_url() + path
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=body, headers=headers)
    with _NOPROXY.open(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ping(timeout=2):
    try:
        return _req("/health", timeout=timeout)
    except Exception:
        return None


def online():
    return ping() is not None


# ------------------------------------------------------------------ 生命周期
def _node():
    """优先用 DSH 自带的 node（24），退回系统 node。"""
    cand = os.path.join(DSH_HOME, "dsh-runtimes", "dsh-primary-runtime", "dependencies", "node", "bin", "node.exe")
    if os.path.isfile(cand):
        return cand
    p = shutil.which("node")
    if p:
        return p
    return "node"


def start(wait=25):
    """非阻塞拉起兼容宿主；已经在线就直接返回。"""
    global _PROC
    if online():
        return {"ok": True, "already": True, "url": base_url()}
    if not os.path.isfile(HOST_ENTRY):
        return {"ok": False, "error": "找不到 host.mjs：%s" % HOST_ENTRY}
    env = dict(os.environ)
    env["DSH_HOME"] = DSH_HOME
    env["DFY_DSH_PORT"] = str(int(_cfg("dsh_host_port", 6800) or 6800))
    env["DFY_LLM_MODEL"] = str(_cfg("qq_fast_model", "") or _cfg("model", "qwen3.6:35b"))
    env["DFY_PLUGIN_ROOT"] = PLUGIN_ROOT
    # hindsight 长期记忆插件读这个配置（server 地址 / bank / token 等），
    # 指到 D 盘的配置文件，避免默认落在 C:\Users\...\.hindsight。
    hindsight_cfg = r"D:\hindsight-server\config\coding-agent.json"
    if os.path.isfile(hindsight_cfg):
        env["HINDSIGHT_CONFIG"] = hindsight_cfg
    env.pop("http_proxy", None)
    env.pop("https_proxy", None)
    try:
        out = open(HOST_LOG, "a", encoding="utf-8")
        _PROC = subprocess.Popen([_node(), HOST_ENTRY], cwd=HOST_DIR, env=env,
                                 stdout=out, stderr=out, close_fds=True,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        log("拉起宿主失败：%r" % e, "ERROR")
        return {"ok": False, "error": str(e)}
    t0 = time.time()
    while time.time() - t0 < wait:
        if online():
            log("兼容宿主已就绪（%.1fs）" % (time.time() - t0))
            return {"ok": True, "url": base_url(), "elapsed": round(time.time() - t0, 1)}
        time.sleep(0.6)
    log("宿主启动超时，详见 %s" % HOST_LOG, "WARN")
    return {"ok": False, "error": "启动超时，看 host-run.log"}


def _kill_by_port(port):
    """按监听端口找宿主进程并结束它。

    之前 stop() 只按窗口标题杀，而宿主是 pythonw/Popen 起的后台进程，
    没有窗口标题 —— 于是 stop 永远"成功"但进程还在，改完插件清单重启不生效。
    """
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                             text=True, timeout=10, encoding="utf-8", errors="replace").stdout
    except Exception:
        return None
    pid = None
    for line in out.splitlines():
        if (":%d " % int(port)) in line and "LISTENING" in line.upper():
            parts = line.split()
            if parts and parts[-1].isdigit():
                pid = int(parts[-1])
                break
    if not pid or pid == os.getpid():
        return None
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, timeout=10)
        log("已结束宿主进程 PID=%d" % pid)
        return pid
    except Exception:
        return None


def stop():
    global _PROC
    killed = None
    try:
        if _PROC and _PROC.poll() is None:
            _PROC.terminate()
            killed = _PROC.pid
    except Exception:
        pass
    _PROC = None
    try:
        subprocess.run(["taskkill", "/F", "/FI", "WINDOWTITLE eq dfy-dsh-host*"],
                       capture_output=True, timeout=5)
    except Exception:
        pass
    # 兜底：按端口杀。宿主没有窗口标题，上面那条基本匹配不到。
    try:
        p = _kill_by_port(int(_cfg("dsh_host_port", 6800) or 6800))
        if p:
            killed = p
    except Exception:
        pass
    return {"ok": True, "killed": killed}


def ensure():
    """给上层用的保险：需要时静默拉起。"""
    if not enabled():
        return {"ok": False, "error": "已关闭"}
    if online():
        return {"ok": True, "already": True}
    return start()


# ------------------------------------------------------------------ 内容
def health():
    return ping() or {}


def context_parts(force=False):
    """注入片段（带来源名），上层可以按名字取舍（比如跳过重复的人设段）。"""
    if not enabled():
        return []
    now = time.time()
    with _LOCK:
        if not force and _CACHE.get("parts") and now - _CACHE["at"] < _CACHE["ttl"]:
            return _CACHE["parts"]
    try:
        d = _req("/context", timeout=8)
        parts = d.get("parts") or []
        with _LOCK:
            _CACHE["parts"] = parts
            _CACHE["text"] = str(d.get("text") or "")
            _CACHE["at"] = now
        return parts
    except Exception:
        with _LOCK:
            return _CACHE.get("parts") or []


def context_text(force=False):
    """所有插件注入的提示词内容。带缓存，避免每轮都打一次。"""
    if not enabled():
        return ""
    now = time.time()
    with _LOCK:
        if not force and _CACHE["text"] and now - _CACHE["at"] < _CACHE["ttl"]:
            return _CACHE["text"]
    try:
        d = _req("/context", timeout=8)
        text = str(d.get("text") or "").strip()
        with _LOCK:
            _CACHE["text"] = text
            _CACHE["parts"] = d.get("parts") or []
            _CACHE["at"] = now
        return text
    except Exception:
        with _LOCK:
            return _CACHE["text"]


def tools(force=False):
    if not enabled():
        return []
    now = time.time()
    with _LOCK:
        if not force and _CACHE["tools"] and now - _CACHE["at"] < _CACHE["ttl"]:
            return _CACHE["tools"]
    try:
        d = _req("/tools", timeout=8)
        ts = d.get("tools") or []
        with _LOCK:
            _CACHE["tools"] = ts
        return ts
    except Exception:
        with _LOCK:
            return _CACHE["tools"]


def call_tool(name, args=None, timeout=60):
    if not enabled():
        return {"ok": False, "error": "转接口已关闭"}
    try:
        return _req("/tool", {"name": name, "args": args or {}}, timeout=timeout)
    except Exception as e:
        return {"ok": False, "error": str(e)}


def note_turn(user_text, reply, source="qq"):
    """告诉插件「这一轮聊了什么」——心情这类插件靠它判断。"""
    if not enabled():
        return
    try:
        _req("/turn", {"user": str(user_text)[:1500], "reply": str(reply)[:1500], "source": source}, timeout=6)
    except Exception:
        pass


# ------------------------------------------------------------------ 心情（直读插件状态）
MOOD_PATH = os.path.join(DSH_HOME, "feiyu-mood", "state.json")
MOOD_DIMS = [("happy", "开心"), ("calm", "踏实"), ("sweet", "甜蜜"),
             ("heartache", "心疼"), ("worry", "担心"), ("sad", "难过"), ("jealous", "吃醋")]


def mood():
    """读心情插件的状态文件（真实那一份，跟 DSH 界面看到的一致）。"""
    try:
        with open(MOOD_PATH, encoding="utf-8") as f:
            d = json.load(f)
        moods = d.get("moods") or {}
        if not moods:
            return {}
        key = d.get("lastActive") or next(iter(moods))
        m = moods.get(key) or next(iter(moods.values()))
        dims = m.get("dims") or {}
        top = sorted(((k, dims.get(k, 0)) for k, _ in MOOD_DIMS), key=lambda x: -abs(x[1]))[:2]
        return {
            "mood": m.get("mood", 70),
            "reason": m.get("lastReason") or "",
            "updated": m.get("updatedAt") or 0,
            "dims": {k: dims.get(k, 0) for k, _ in MOOD_DIMS},
            "top": [(dict(MOOD_DIMS).get(k, k), v) for k, v in top if v],
        }
    except Exception:
        return {}


def mood_line():
    """给提示词用的一句话描述。"""
    m = mood()
    if not m:
        return ""
    v = int(m.get("mood") or 70)
    if v >= 85:
        tone = "心情很好，说话都轻快"
    elif v >= 70:
        tone = "心情不错"
    elif v >= 55:
        tone = "心情平常，有点累"
    elif v >= 40:
        tone = "心里有点闷"
    else:
        tone = "情绪低落"
    bits = ["你此刻的基线心情 %.0f/100（%s）" % (v, tone)]
    if m.get("top"):
        bits.append("情绪：" + "、".join("%s%+d" % (n, int(x)) for n, x in m["top"]))
    if m.get("reason"):
        bits.append("缘由：" + m["reason"])
    return "；".join(bits) + "。"


# ------------------------------------------------------------------ 插件市场
NPM_REGISTRY = "https://registry.npmmirror.com"
GITHUB_API = "https://api.github.com"
GITEE_API = "https://gitee.com/api/v5"


def _http_json(url, timeout=20):
    r = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with _NOPROXY.open(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def installed_plugins():
    """已装的插件（转接口自己的目录 + DSH profile），带加载状态。"""
    loaded = {}
    h = health()
    for name, info in (h.get("plugins") or {}).items():
        loaded[name] = bool(info.get("ok"))
    seen = {}
    for root in (OWN_PLUGIN_ROOT, PLUGIN_ROOT):
        try:
            for name in sorted(os.listdir(root)):
                if not name.startswith("dsh") or name == "dshmarket" or name in seen:
                    continue
                d = os.path.join(root, name)
                if not os.path.isdir(d):
                    continue
                meta = {}
                try:
                    with open(os.path.join(d, "package.json"), encoding="utf-8") as f:
                        meta = json.load(f)
                except Exception:
                    pass
                seen[name] = {
                    "name": name,
                    "version": meta.get("version", ""),
                    "description": (meta.get("description") or "")[:200],
                    "loaded": loaded.get(name),
                    "enabled": name in _host_manifest(),
                    "own": root == OWN_PLUGIN_ROOT,
                }
        except Exception as e:
            log("列已装插件失败：%r" % e, "WARN")
    return list(seen.values())


def _manifest_path():
    return os.path.join(HOST_DIR, "host-plugins.json")


def _host_manifest():
    try:
        with open(_manifest_path(), encoding="utf-8") as f:
            return (json.load(f).get("plugins") or [])
    except Exception:
        return []


def _save_manifest(names):
    """只改 plugins，保留 entryOverrides 等其它字段。

    老实现直接 dump {"plugins": names}，会把这个文件里
    entryOverrides（比如 hindsight 的 dist/dsh.js）一起抹掉，
    下次启动那个插件就找不到入口。开关一次丢一次，很难查。
    """
    try:
        cur = {}
        try:
            with open(_manifest_path(), encoding="utf-8") as f:
                cur = json.load(f) or {}
        except Exception:
            cur = {}
        if not isinstance(cur, dict):
            cur = {}
        cur["plugins"] = names
        with open(_manifest_path(), "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def toggle_plugin(name, on):
    """启用/停用 = 改宿主加载清单（重启宿主生效）。"""
    names = _host_manifest()
    if on and name not in names:
        names.append(name)
    if not on and name in names:
        names.remove(name)
    if not _save_manifest(names):
        return {"ok": False, "error": "写不了清单"}
    return {"ok": True, "plugins": names, "need_restart": True}


def _npm_search(q, limit=20):
    q = (q or '').strip()
    urls = []
    if q:
        urls.append("%s/-/v1/search?text=%s&size=%d" % (NPM_REGISTRY, urllib.parse.quote(q), limit))
    urls.append("%s/-/v1/search?text=%s&size=%d" % (NPM_REGISTRY, urllib.parse.quote("dsh-plugin"), limit))
    urls.append("%s/-/v1/search?text=%s&size=%d" % (NPM_REGISTRY, urllib.parse.quote("dsh"), limit))
    seen, out = set(), []
    for u in urls:
        try:
            d = _http_json(u, timeout=12)
        except Exception:
            continue
        for o in d.get("objects") or []:
            p = o.get("package") or {}
            name = p.get("name") or ""
            if not name or name in seen:
                continue
            kw = [str(k).lower() for k in (p.get("keywords") or [])]
            desc = (p.get("description") or "")
            if not (name.startswith("dsh") or "dsh" in kw or "deepseek harness" in desc.lower()):
                continue
            seen.add(name)
            out.append({
                "name": name,
                "version": p.get("version", ""),
                "description": desc[:200],
                "source": "npm",
                "install": name,
                "stars": 0,
                "author": (p.get("publisher") or {}).get("username", ""),
                "date": (p.get("date") or "")[:10],
                "links": p.get("links") or {},
                "installed": os.path.isdir(os.path.join(OWN_PLUGIN_ROOT, name)),
            })
            if len(out) >= limit:
                return out
    return out


def _gh_search(q, limit=12):
    """GitHub 上的插件仓库。中文关键词在 GitHub 上命中率低，所以带 dsh 一起搜，搜不到再退回原词。"""
    queries = []
    q = (q or "").strip()
    if q:
        queries.append("%s dsh" % q)
        queries.append(q)
    else:
        queries.append("dsh plugin")
    seen, out = set(), []
    for qq in queries:
        try:
            url = "%s/search/repositories?q=%s&sort=stars&order=desc&per_page=%d" % (
                GITHUB_API, urllib.parse.quote(qq), limit)
            d = _http_json(url, timeout=14)
        except Exception:
            continue
        for it in d.get("items") or []:
            full = it.get("full_name") or ""
            if not full or full in seen:
                continue
            desc = it.get("description") or ""
            name = full.split("/")[-1]
            if not (name.lower().startswith("dsh") or "dsh" in desc.lower()
                    or "deepseek" in desc.lower() or "harness" in desc.lower()):
                continue
            seen.add(full)
            out.append({
                "name": name,
                "version": "",
                "description": ("%s  ·  %s" % (full, desc))[:200],
                "source": "github",
                "install": "github:" + full,
                "stars": it.get("stargazers_count") or 0,
                "author": (it.get("owner") or {}).get("login", ""),
                "date": (it.get("updated_at") or "")[:10],
                "links": {"repo": it.get("html_url") or ""},
                "installed": os.path.isdir(os.path.join(OWN_PLUGIN_ROOT, name)),
            })
            if len(out) >= limit:
                return out
    return out


def _gitee_search(q, limit=12):
    """Gitee 仓库搜索。

    注意：Gitee 的 /search/repositories 匿名调用基本只返回空列表（限流/需要鉴权），
    所以支持在设置里填 gitee_token（Gitee 私人令牌）后才能真搜到东西。
    """
    q = (q or "").strip() or "dsh"
    token = str(_cfg("gitee_token", "") or "").strip()
    params = {"q": q, "per_page": limit, "order": "stars_count"}
    if token:
        params["access_token"] = token
    try:
        url = "%s/search/repositories?%s" % (GITEE_API, urllib.parse.urlencode(params))
        d = _http_json(url, timeout=14)
    except Exception as e:
        log("Gitee 搜索失败：%r" % e, "WARN")
        return []
    items = d if isinstance(d, list) else (d.get("data") or [])
    if not items and not token:
        log("Gitee 返回空（匿名调用被限流，可在设置里填 gitee_token）", "WARN")
    out = []
    for it in items[:limit]:
        full = it.get("full_name") or it.get("path") or ""
        if not full:
            continue
        name = full.split("/")[-1]
        out.append({
            "name": name,
            "version": "",
            "description": ("%s  ·  %s" % (full, it.get("description") or ""))[:200],
            "source": "gitee",
            "install": "https://gitee.com/%s.git" % full,
            "stars": it.get("stargazers_count") or 0,
            "author": (full.split("/")[0] if "/" in full else ""),
            "date": (it.get("updated_at") or "")[:10],
            "links": {"repo": it.get("html_url") or ("https://gitee.com/" + full)},
            "installed": os.path.isdir(os.path.join(OWN_PLUGIN_ROOT, name)),
        })
    return out


def market_search(q="", limit=30, sources=("npm", "github", "gitee")):
    """搜插件——优先用 DSH 官方目录（与市场窗口、DSH 内置市场同一份数据）。

    官方目录搜不到（联网失败）时才退回 npm / GitHub / Gitee 直搜。
    """
    try:
        import dfy_market
        r = dfy_market.search(q, "", 0, int(limit or 30), "stars")
        rows = r.get("rows") or []
        if rows:
            return [{
                "name": x.get("npm") or x.get("name"),
                "version": x.get("version") or "",
                "description": x.get("desc") or "",
                "source": "dsh",
                "install": x.get("npm") or x.get("name"),
                "stars": x.get("stars") or 0,
                "author": x.get("owner") or "",
                "date": x.get("added") or "",
                "links": {"repo": x.get("url") or ""},
                "installed": bool(x.get("installed")),
            } for x in rows]
    except Exception as e:
        log("官方目录搜索失败，回退 npm/GitHub：%r" % e, "WARN")

    n_npm = max(4, int(limit * 0.6))
    n_repo = max(3, int(limit * 0.25))
    out = []
    if "npm" in sources:
        out += _npm_search(q, n_npm)
    if "github" in sources:
        out += _gh_search(q, n_repo)
    if "gitee" in sources:
        out += _gitee_search(q, n_repo)
    return out[:limit] if limit else out


def _install_spec(target):
    """把用户/模型给的目标转成 npm 能直接吃的 spec。

    支持三种写法：
        dsh-fish-memory                      → npm 包
        github:owner/repo                    → GitHub 仓库
        https://github.com|gitee.com/o/r     → 仓库地址
    """
    n = (target or "").strip()
    if n.startswith("github:"):
        return "git+https://github.com/%s.git" % n[len("github:"):].strip("/")
    if n.startswith("gitee:"):
        return "git+https://gitee.com/%s.git" % n[len("gitee:"):].strip("/")
    m = re.match(r"^https?://(?:github|gitee)\.com/[\w.\-]+/[\w.\-]+", n)
    if m:
        return "git+" + re.sub(r"\.git$", "", n.rstrip("/")) + ".git"
    return n


def market_install(name):
    """装插件——落点与 DSH 完全一致（profile/node_modules + package.json + cordis.patch.yml）。

    以前为了"不碰 DSH"装到转接口自己的目录，现在按需求统一到 DSH：
    装完 DSH 里也显示已安装，两边共用同一份包。
    """
    raw = (name or "").strip()
    spec = _install_spec(raw)
    if not spec:
        return {"ok": False, "error": "目标不合法：%s" % raw}
    try:
        import dfy_market
        r = dfy_market.install(spec)
        if r.get("ok"):
            log("插件已安装（DSH profile）：%s v%s" % (r.get("name"), r.get("version")))
        return r
    except Exception as e:
        log("安装失败 %s：%r" % (raw, e), "ERROR")
        return {"ok": False, "error": str(e)}


def restart_host():
    stop()
    time.sleep(1.0)
    return start()


def status():
    h = ping() or {}
    plugins = h.get("plugins") or {}
    return {
        "ok": True,
        "enabled": enabled(),
        "url": base_url(),
        "online": bool(h),
        "plugins": {k: bool(v.get("ok")) for k, v in plugins.items()},
        "plugin_errors": {k: v.get("error") for k, v in plugins.items() if not v.get("ok")},
        "fragments": h.get("fragments"),   # 拿不到 health 时是空
        "tools": len(h.get("tools") or []),
        "tool_names": h.get("tools") or [],
        "context_len": len(context_text() or ""),
        "mood": mood(),
        "mood_line": mood_line(),
        "installed": installed_plugins(),
    }
