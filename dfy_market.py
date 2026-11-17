# -*- coding: utf-8 -*-
"""DSH 插件市场（原样复刻）。

数据来自 DSH 官方市场目录 https://awesome-dsh-plugin.com/plugins.json，
也就是 DSH 内置市场页看的那一份，所以列表、分类、描述、星标、下载量
完全一致。安装落点刻意与 DSH 相同：

  1. 包装进 E:/DSH-home/profiles/desktop/node_modules
  2. profile 的 package.json dependencies 记一行
  3. cordis.patch.yml 里登记启用

这样装完的东西在 DSH 里也是"已安装"，两边共用一份。

联网是硬要求：本模块不提供任何离线开关，拉不到目录就明确报错，
不会拿旧数据冒充最新（DSH 自己的实现也是这个原则）。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------- 路径

HOST_DIR = r"D:\大肥鱼AI\dsh_host"
CACHE_DIR = r"D:\大肥鱼缓存\dsh_market"
STAGE_DIR = os.path.join(HOST_DIR, "pkgstage")
DSH_HOME = os.environ.get("DSH_HOME") or r"E:\DSH-home"
PROFILE_DIR = os.path.join(DSH_HOME, "profiles", "desktop")
PROFILE_MODULES = os.path.join(PROFILE_DIR, "node_modules")
PROFILE_PKG = os.path.join(PROFILE_DIR, "package.json")
PATCH_FILE = os.path.join(PROFILE_DIR, "cordis.patch.yml")

# 兼容宿主真正用来决定"加载哪些插件"的名单。
# 市场原先只写 profile/package.json + cordis.patch.yml，而 dsh_host/host.mjs
# 读的是 host-plugins.json —— 这个文件没人维护，结果就是插件装进了
# node_modules、profile 也记了账，但宿主启动时压根不看它，等于装了不生效。
HOST_MANIFEST = os.path.join(HOST_DIR, "host-plugins.json")

CATALOG_URL = "https://awesome-dsh-plugin.com/plugins.json"
NPM_REGISTRY = "https://registry.npmmirror.com"
LOG_PATH = os.path.join(CACHE_DIR, "market.log")

# npm 可执行文件定位：不依赖父进程 PATH（pythonw 启动时往往没有 node/npm）。
# 优先用我们装在 D 盘的正式 Node，其次 DSH 自带 pnpm，最后才回退 PATH 里的 npm。
_NPM_CANDIDATES = [
    r"D:\nodejs\npm.cmd",
    r"D:\nodejs\node_modules\npm\bin\npm-cli.js",  # 若走 node 直接执行，见下方 _find_npm
]


def _find_npm():
    """返回 ([可执行文件, ...前置参数]) 形式的 npm 启动命令。"""
    # 1) 固定路径的 npm.cmd（最可靠）
    for p in _NPM_CANDIDATES:
        if p.lower().endswith(".cmd") and os.path.isfile(p):
            return [p]
    # 2) PATH 里能找到的 npm（exe/cmd/bat 都算）
    found = shutil.which("npm")
    if found:
        return [found]
    # 3) 用 DSH 自带的 node 直接跑 npm-cli.js
    npm_js = r"D:\nodejs\node_modules\npm\bin\npm-cli.js"
    node_exe = r"E:\DSH-home\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
    if os.path.isfile(npm_js) and os.path.isfile(node_exe):
        return [node_exe, npm_js]
    # 4) 兜底：交给系统 PATH（可能失败，但保留原行为）
    return ["npm"]


# 探测宿主用的 node（不走 npm，直接跑 host.mjs）
HOST_ENTRY_MJS = os.path.join(HOST_DIR, "host.mjs")
_NODE_EXE_CANDIDATES = [
    r"E:\DSH-home\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe",
    r"D:\nodejs\node.exe",
]


def _find_node():
    for p in _NODE_EXE_CANDIDATES:
        if os.path.isfile(p):
            return p
    return shutil.which("node") or "node"


CATALOG_FILE = os.path.join(CACHE_DIR, "catalog.json")
META_FILE = os.path.join(CACHE_DIR, "meta.json")
PID_FILE = os.path.join(CACHE_DIR, "ui.pid")

_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_LOCK = threading.Lock()
_CAT = {"data": None, "at": 0.0, "fetching": False, "error": "", "total": 0}


def log(msg, level="INFO"):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        line = "[%s][%s] %s\n" % (time.strftime("%m-%d %H:%M:%S"), level, msg)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception as e:
        log("写文件失败 %s: %r" % (path, e), "WARN")
        return False


# ---------------------------------------------------------------- 目录

def _http(url, headers=None, timeout=120):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with _NO_PROXY.open(req, timeout=timeout) as r:
        return r.status, dict(r.headers), r.read()


def _load_disk_catalog():
    """磁盘缓存：只用于启动瞬间先出画面，随后一定会被联网结果替换。"""
    d = _read_json(CATALOG_FILE)
    if isinstance(d, dict) and d.get("plugins"):
        return d
    return None


def fetch_catalog(force=False, timeout=180):
    """联网拉目录。永远真联网，304 才允许复用上次结果。"""
    meta = _read_json(META_FILE, {}) or {}
    headers = {"User-Agent": "Mozilla/5.0"}
    if not force:
        if meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]
    try:
        code, hdrs, body = _http(CATALOG_URL, headers, timeout)
    except urllib.error.HTTPError as e:
        # 304 是 urllib 当作异常抛出来的"没变化"，不是失败
        if e.code == 304:
            d = _load_disk_catalog()
            if d:
                log("目录未变化（304），沿用上次结果")
                return {"ok": True, "data": d, "not_modified": True}
            return fetch_catalog(force=True, timeout=timeout)
        log("拉取目录失败：HTTP %s" % e.code, "ERROR")
        return {"ok": False, "error": "联网获取插件目录失败：HTTP %s" % e.code}
    except Exception as e:
        log("拉取目录失败：%r" % e, "ERROR")
        return {"ok": False, "error": "联网获取插件目录失败：%s" % e}

    if code == 304:
        d = _load_disk_catalog()
        if d:
            log("目录未变化（304），沿用上次结果")
            return {"ok": True, "data": d, "not_modified": True}
        # 304 但没有本地副本：无条件重拉
        return fetch_catalog(force=True, timeout=timeout)

    try:
        d = json.loads(body.decode("utf-8"))
        if not isinstance(d.get("plugins"), list) or not d["plugins"]:
            raise ValueError("目录为空")
    except Exception as e:
        log("目录解析失败：%r" % e, "ERROR")
        return {"ok": False, "error": "插件目录格式异常：%s" % e}

    for p in d["plugins"]:
        if isinstance(p.get("description"), dict):
            pass
        elif isinstance(p.get("description"), str):
            p["description"] = {"zh": p["description"], "en": p["description"]}

    _write_json(CATALOG_FILE, d)
    _write_json(META_FILE, {
        "etag": hdrs.get("ETag") or hdrs.get("etag") or "",
        "last_modified": hdrs.get("Last-Modified") or hdrs.get("last-modified") or "",
        "fetched_at": int(time.time()),
        "count": len(d["plugins"]),
    })
    log("目录已更新：%d 个插件" % len(d["plugins"]))
    return {"ok": True, "data": d}


def prime_async(force=False):
    """启动时先给旧画面，再后台换新（不阻塞窗口）。"""
    disk = _load_disk_catalog()
    meta = _read_json(META_FILE, {}) or {}
    fetched = int(meta.get("fetched_at") or 0)
    if disk:
        with _LOCK:
            _CAT["data"] = disk
            # 用真实的拉取时间，否则会被当成"刚刚更新过"而永不刷新
            _CAT["at"] = fetched or (time.time() - 7200)
            _CAT["total"] = len(disk["plugins"])
    if _CAT["fetching"]:
        return
    if not force and time.time() - fetched < 120:
        return

    def work():
        with _LOCK:
            _CAT["fetching"] = True
        try:
            r = fetch_catalog()
            if r.get("ok"):
                with _LOCK:
                    _CAT["data"] = r["data"]
                    _CAT["at"] = time.time()
                    _CAT["total"] = len(r["data"]["plugins"])
                    _CAT["error"] = ""
            else:
                with _LOCK:
                    _CAT["error"] = r.get("error", "")
        finally:
            with _LOCK:
                _CAT["fetching"] = False

    threading.Thread(target=work, daemon=True, name="market-catalog").start()


def catalog(force=False, wait=False):
    """取目录。wait=True 时同步等联网结果（搜索/分类首次调用用）。"""
    with _LOCK:
        if force:
            r = fetch_catalog(force=True)
            if r.get("ok"):
                _CAT["data"] = r["data"]
                _CAT["at"] = time.time()
                _CAT["total"] = len(r["data"]["plugins"])
                _CAT["error"] = ""
            else:
                _CAT["error"] = r.get("error", "")
        data = _CAT["data"]

    if data and not force:
        # 有旧数据就直接用，后台悄悄刷新
        if time.time() - _CAT["at"] > 600:
            prime_async()
        return data

    if not data and wait:
        r = fetch_catalog()
        if r.get("ok"):
            with _LOCK:
                _CAT["data"] = r["data"]
                _CAT["at"] = time.time()
                _CAT["total"] = len(r["data"]["plugins"])
            return r["data"]
        with _LOCK:
            _CAT["error"] = r.get("error", "联网失败")
        return _load_disk_catalog() or {"plugins": [], "categories": {}}
    return data or _load_disk_catalog() or {"plugins": [], "categories": {}}


def status():
    with _LOCK:
        data = _CAT["data"]
        total = _CAT["total"]
        at = _CAT["at"]
        out = {
            "ok": True,
            "total": total or len((data or {}).get("plugins") or []),
            "loaded": bool(data),
            "fetching": _CAT["fetching"],
            "age": int(time.time() - at) if at else None,
            "error": _CAT["error"],
            "online_only": True,
            "catalog_url": CATALOG_URL,
            "profile": PROFILE_DIR,
        }
    if not out["total"]:
        meta = _read_json(META_FILE, {}) or {}
        out["total"] = int(meta.get("count") or 0)
        if meta.get("fetched_at") and out["age"] is None:
            out["age"] = int(time.time() - meta["fetched_at"])
    return out


def categories():
    d = catalog()
    cats = d.get("categories") or {}
    counts = {}
    for p in d.get("plugins") or []:
        c = p.get("category") or ""
        if isinstance(c, list):
            for x in c:
                counts[x] = counts.get(x, 0) + 1
        else:
            counts[c] = counts.get(c, 0) + 1
    out = []
    for k, v in cats.items():
        out.append({"id": k, "zh": (v or {}).get("zh") or k,
                    "en": (v or {}).get("en") or k, "count": counts.get(k, 0)})
    out.sort(key=lambda x: -x["count"])
    return out


# ---------------------------------------------------------------- 已装

def _profile_deps():
    d = _read_json(PROFILE_PKG, {}) or {}
    return dict(d.get("dependencies") or {})


def _patch_names():
    try:
        with open(PATCH_FILE, encoding="utf-8") as f:
            s = f.read()
    except Exception:
        return {}
    names = {}
    for m in re.finditer(r"^- id:\s*(\S+)\s*\n\s*name:\s*[\"']?([^\"'\n]+)", s, re.M):
        names[m.group(2).strip()] = m.group(1).strip()
    return names


def host_manifest():
    """读宿主的加载名单。宿主、市场和界面共用这一份，避免三处各说各话。"""
    d = _read_json(HOST_MANIFEST, None)
    if not isinstance(d, dict):
        d = {}
    lst = d.get("plugins")
    if not isinstance(lst, list):
        d["plugins"] = []
    d.setdefault("entryOverrides", {})
    return d


def host_enable(name, on=True):
    """把插件加入/移出宿主的加载名单 —— 这一步才真正决定它跑不跑。"""
    name = (name or "").strip()
    if not name:
        return False
    d = host_manifest()
    lst = [x for x in (d.get("plugins") or []) if isinstance(x, str)]
    if on:
        if name not in lst:
            lst.append(name)
    else:
        lst = [x for x in lst if x != name]
        ov = d.get("entryOverrides") or {}
        if isinstance(ov, dict):
            ov.pop(name, None)
    d["plugins"] = lst
    try:
        _write_json(HOST_MANIFEST, d)
    except Exception as e:
        log("写宿主名单失败：%r" % e, "WARN")
        return False
    log("宿主名单%s：%s" % ("加入" if on else "移除", name))
    return (name in lst) if on else (name not in lst)


def host_loaded(ttl=5):
    """宿主自报的加载结果（ok / error），用来区分「名单里有但加载失败」和「压根没登记」。"""
    now = time.time()
    if now - _LIVE_CACHE["t"] < ttl:
        return _LIVE_CACHE["d"]
    d = {}
    try:
        import urllib.request as _u
        with _u.urlopen("http://127.0.0.1:%d/health" % _host_port(), timeout=1.5) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        p = j.get("plugins")
        if isinstance(p, dict):
            d = p
    except Exception:
        d = {}
    _LIVE_CACHE["t"], _LIVE_CACHE["d"] = now, d
    return d


def _host_port():
    port = 6800
    try:
        p = os.path.join(os.environ.get("APPDATA") or "", "大肥鱼AI", "settings.json")
        with open(p, encoding="utf-8") as f:
            port = int((json.load(f) or {}).get("dsh_host_port") or 6800)
    except Exception:
        pass
    return port


_LIVE_CACHE = {"t": 0.0, "d": {}}


def sync_host_manifest(enable_all=True):
    """把 profile 里已装但没进名单的插件一次性补齐。

    历史上市场装过的插件（比如 dsh-bili-scroll）都卡在这一步：
    node_modules 里有、profile 记了账，但宿主名单没它，所以从来没被加载。
    """
    deps = _profile_deps()
    have = set(host_manifest().get("plugins") or [])
    added, missing = [], []
    for name in deps:
        if not name.startswith(("dsh", "@")):
            continue
        if not os.path.isdir(os.path.join(PROFILE_MODULES, *name.split("/"))):
            missing.append(name)
            continue
        if name not in have:
            if enable_all and host_enable(name, True):
                added.append(name)
    return {"ok": True, "added": added, "missing": missing,
            "plugins": host_manifest().get("plugins") or []}


def installed():
    """已装插件：以 profile 的 package.json 为准（DSH 也看这个）。"""
    deps = _profile_deps()
    patch = _patch_names()
    host = set(host_manifest().get("plugins") or [])
    live = host_loaded()
    out = {}
    for name, spec in deps.items():
        if not name.startswith(("dsh", "@")):
            continue
        path = os.path.join(PROFILE_MODULES, *name.split("/"))
        meta = _read_json(os.path.join(path, "package.json"), {}) or {}
        st = live.get(name) or {}
        out[name] = {
            "name": name,
            "version": meta.get("version") or "",
            "spec": spec,
            "present": os.path.isdir(path),
            "enabled": name in patch,
            "patch_id": patch.get(name, ""),
            # 这三个字段是这次补的：装完到底有没有真被宿主加载起来
            "host_enabled": name in host,
            "loaded": bool(st.get("ok")),
            "load_error": (st.get("error") or "")[:300] if st else "",
            "description": (meta.get("description") or "")[:200],
        }
    return out


# ---------------------------------------------------------------- 搜索

def _sort_key(sort):
    if sort == "stars":
        return lambda p: -(p.get("stars") or 0)
    if sort == "new":
        return lambda p: str(p.get("added") or "")
    if sort == "downloads":
        return lambda p: -(p.get("downloads") or 0)
    return lambda p: (-(p.get("stars") or 0), str(p.get("name") or ""))


def search(q="", category="", page=0, size=30, sort="stars", lang="zh"):
    """本地过滤官方目录（11332 条，过滤是毫秒级，不用打网络）。"""
    d = catalog(wait=True)
    plugins = d.get("plugins") or []
    q = (q or "").strip().lower()
    rows = []
    for p in plugins:
        if category and category != "all":
            c = p.get("category")
            cs = c if isinstance(c, list) else [c]
            if category not in cs:
                continue
        if q:
            desc = p.get("description") or {}
            hay = " ".join([
                str(p.get("name") or ""), str(p.get("owner") or ""),
                str(desc.get("zh") or ""), str(desc.get("en") or ""),
                " ".join(p.get("capabilities") or [] if isinstance(p.get("capabilities"), list) else []),
            ]).lower()
            if q not in hay:
                continue
        rows.append(p)

    rows.sort(key=_sort_key(sort))
    total = len(rows)
    start = max(0, int(page)) * int(size)
    page_rows = rows[start:start + int(size)]
    have = installed()

    out = []
    for p in page_rows:
        desc = p.get("description") or {}
        npm = p.get("npm") or ""
        name = npm or p.get("name") or ""
        inst = have.get(name)
        out.append({
            "name": p.get("name") or "",
            "npm": npm,
            "owner": p.get("owner") or "",
            "url": p.get("url") or "",
            "page": p.get("page") or "",
            "category": p.get("category"),
            "desc": (desc.get(lang) or desc.get("zh") or desc.get("en") or "")[:600],
            "desc_alt": (desc.get("en") if lang == "zh" else desc.get("zh")) or "",
            "version": p.get("version") or "",
            "stars": p.get("stars") or 0,
            "downloads": p.get("downloads") or 0,
            "added": (p.get("added") or "")[:10],
            "install": p.get("install") or "",
            "icon": p.get("icon") or "",
            "screenshots": (p.get("screenshots") or [])[:4],
            "installed": bool(inst and inst.get("present")),
            "installed_version": (inst or {}).get("version") or "",
            "enabled": bool(inst and inst.get("enabled")),
        })
    return {"ok": True, "total": total, "page": page, "size": size, "rows": out}


def detail(key):
    """按名称或 npm 名取一条详情。"""
    d = catalog()
    for p in d.get("plugins") or []:
        if key in (p.get("name"), p.get("npm")):
            desc = p.get("description") or {}
            have = installed()
            name = p.get("npm") or p.get("name") or ""
            return {"ok": True, "plugin": dict(p, description=desc,
                                               installed=bool(have.get(name, {}).get("present")))}
    return {"ok": False, "error": "目录里没有这一条"}


# ---------------------------------------------------------------- 安装

def _spec_of(item):
    """把 install 命令字符串还原成 npm 可用的 spec。"""
    ins = (item.get("install") or "").strip()
    m = re.search(r"\badd\s+(.+)$", ins)
    spec = (m.group(1).strip() if m else "") or item.get("npm") or ""
    spec = spec.strip().strip("\"'")
    if not spec and item.get("url"):
        spec = "github:" + (item.get("url") or "").split("github.com/")[-1].strip("/")
    if spec.startswith("github:") and "#" in spec:
        spec = spec.split("#")[0]
    return spec


def _guarded_spec(spec):
    spec = (spec or "").strip()
    ok = (
        re.match(r"^(@[a-z0-9-._~]+/)[a-z0-9-._~]+(@[^\s]+)?$", spec, re.I)
        or re.match(r"^[a-z0-9-._~]+(@[^\s]+)?$", spec, re.I)
        or re.match(r"^github:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(#[\w./-]+)?$", spec)
        or re.match(r"^https://(github\.com|gitee\.com)/[A-Za-z0-9_.\-/]+?(\.git)?(#[\w./-]+)?$", spec)
        or re.match(r"^git\+https://[^\s]+$", spec)
    )
    if not ok:
        return None
    if spec.startswith("https://"):
        spec = "git+" + spec
    return spec


def _norm_top(name):
    """node_modules 顶层条目名（scoped 包返回 @scope/name）。"""
    return name


def _list_modules(root):
    out = set()
    try:
        for n in os.listdir(root):
            if n.startswith("."):
                continue
            if n.startswith("@"):
                sub = os.path.join(root, n)
                if os.path.isdir(sub):
                    for s in os.listdir(sub):
                        if not s.startswith("."):
                            out.add(n + "/" + s)
            else:
                out.add(n)
    except Exception:
        pass
    return out


def _link_into_profile(src_root, names):
    """把 stage 里的包挂进 profile/node_modules。

    已存在的真实目录一律不动——宁可少装一个，也不能把用户现有安装换成
    指向 stage 的链接（stage 一清，DSH 就打不开了）。
    """
    linked, skipped, failed = [], [], []
    for n in sorted(names):
        src = os.path.join(src_root, *n.split("/"))
        dst = os.path.join(PROFILE_MODULES, *n.split("/"))
        if not os.path.isdir(src):
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.lexists(dst):
                # 之前由我们挂的链接可以安全替换；真实目录保持原样
                if os.path.islink(dst):
                    os.unlink(dst)
                else:
                    skipped.append(n)
                    continue
            r = subprocess.run(["cmd", "/c", "mklink", "/J", dst, src],
                               capture_output=True, text=True, timeout=60,
                               encoding="gbk", errors="replace")
            if r.returncode != 0 or not os.path.isdir(dst):
                shutil.copytree(src, dst, symlinks=True)
            linked.append(n)
        except Exception as e:
            log("挂载 %s 失败：%r" % (n, e), "WARN")
            failed.append(n)
    return linked, skipped, failed


def _write_profile_dep(name, version, spec):
    d = _read_json(PROFILE_PKG, {}) or {}
    deps = dict(d.get("dependencies") or {})
    if spec.startswith(("github:", "git+")) or "://" in spec:
        deps[name] = spec
    else:
        deps[name] = "^" + version if version and re.match(r"^\d", version) else (version or "latest")
    d["dependencies"] = deps
    _write_json(PROFILE_PKG, d)


def _register_patch(name):
    """登记为启用。

    绝大多数 DSH 插件自带 `dsh.bundle.patch`（包里的 cordis.patch.yml），
    DSH 加载时会自己读，不需要我们在 profile 里再写一条——多写反而可能让
    DSH 启动时找不到插件而报错。只有确实没有自带 patch 的包才手动登记，
    方便它在 DSH 里出现。
    """
    meta = _read_json(os.path.join(PROFILE_MODULES, *name.split("/"), "package.json"), {}) or {}
    bundle = ((meta.get("dsh") or {}).get("bundle") or {})
    if bundle.get("patch"):
        return "auto"

    try:
        with open(PATCH_FILE, encoding="utf-8") as f:
            s = f.read()
    except Exception:
        return False
    if re.search(r"^\s*name:\s*[\"']?%s[\"']?\s*$" % re.escape(name), s, re.M):
        return True
    short = name.split("/")[-1]
    pid = re.sub(r"[^a-z0-9-]+", "-", short.lower()).strip("-") or short
    existing = set(re.findall(r"^- id:\s*(\S+)", s, re.M))
    base = pid
    i = 2
    while pid in existing:
        pid = "%s-%d" % (base, i)
        i += 1
    if not s.endswith("\n"):
        s += "\n"
    s += "- id: %s\n  name: %s\n" % (pid, name)
    try:
        with open(PATCH_FILE, "w", encoding="utf-8") as f:
            f.write(s)
        return True
    except Exception as e:
        log("写 cordis.patch.yml 失败：%r" % e, "ERROR")
        return False


def _stage_pkgjson():
    os.makedirs(STAGE_DIR, exist_ok=True)
    p = os.path.join(STAGE_DIR, "package.json")
    if not os.path.isfile(p):
        _write_json(p, {"name": "dfy-market-stage", "private": True, "version": "0.0.0"})
    return p


def _remove_stage_pkg(name):
    """探测失败时把这次装进 stage 的包清掉，避免残留被下次安装带回来。"""
    p = os.path.join(STAGE_DIR, "node_modules", *name.split("/"))
    if os.path.isdir(p):
        shutil.rmtree(p, ignore_errors=True)
    sp = os.path.join(STAGE_DIR, "package.json")
    d = _read_json(sp, {}) or {}
    deps = dict(d.get("dependencies") or {})
    if deps.pop(name, None) is not None:
        d["dependencies"] = deps
        _write_json(sp, d)


def probe_plugin(main_name):
    """在独立临时宿主里把插件真加载一次，不碰真实 profile。

    这是市场安装前的自测：以前装完不检查，要等重启真实宿主才发现
    （比如 dsh-ops 的 inject 依赖）。现在让一个临时 host 只加载它，
    结果写进 JSON 文件，进程退出后读回来判定。
    """
    import tempfile
    import subprocess as _sp
    if not main_name:
        return {"ok": False, "error": "没有插件名，无法探测"}
    fd, result_path = tempfile.mkstemp(suffix=".json", prefix="dfyprobe_", dir=CACHE_DIR)
    os.close(fd)
    env = dict(os.environ)
    env.update({
        "DFY_PROBE": "1",
        "DFY_PROBE_PLUGIN": main_name,
        "DFY_PROBE_ROOT": os.path.join(STAGE_DIR, "node_modules"),
        "DFY_PROBE_RESULT": result_path,
    })
    try:
        r = _sp.run([_find_node(), HOST_ENTRY_MJS], env=env,
                    capture_output=True, text=True, timeout=60,
                    cwd=HOST_DIR, encoding="utf-8", errors="replace")
    except Exception as e:
        try:
            os.remove(result_path)
        except Exception:
            pass
        return {"ok": False, "error": "探测进程没跑起来：%s" % e}
    d = _read_json(result_path, None)
    try:
        os.remove(result_path)
    except Exception:
        pass
    if not isinstance(d, dict):
        tail = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()
        return {"ok": False, "error": "探测没有返回结果（退出码 %d）：%s"
                % (r.returncode, tail[-1] if tail else "未知")}
    if not d.get("ok"):
        return {"ok": False, "incompatible": True,
                "error": (d.get("error") or "插件加载失败").splitlines()[0][:200]}
    return {"ok": True, "main": d.get("main") or ""}


def install(spec, name_hint=""):
    """装一个插件，落点与 DSH 相同。联网下载，不允许离线。"""
    spec = _guarded_spec(spec)
    if not spec:
        return {"ok": False, "error": "安装目标不合法：%s" % (spec or "(空)")}

    _stage_pkgjson()
    before = _list_modules(os.path.join(STAGE_DIR, "node_modules"))

    cmd = _find_npm() + ["install", spec, "--prefix", STAGE_DIR, "--save",
                         "--no-audit", "--no-fund", "--legacy-peer-deps",
                         "--registry=" + NPM_REGISTRY]
    log("安装：%s" % spec)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                           cwd=STAGE_DIR, encoding="utf-8", errors="replace")
    except Exception as e:
        return {"ok": False, "error": "执行 npm 失败：%s" % e}

    tail = ((r.stdout or "") + (r.stderr or ""))[-800:]
    if r.returncode != 0:
        log("安装失败 %s：%s" % (spec, tail[-300:]), "ERROR")
        return {"ok": False, "error": tail or "npm 返回非零", "spec": spec}

    after = _list_modules(os.path.join(STAGE_DIR, "node_modules"))
    added = after - before

    # 找出这次装的主体包名
    bare = spec.replace("git+", "").replace("github:", "")
    candidate = ""
    if spec.startswith(("github:", "git+", "https://")):
        candidate = re.sub(r"\.git.*$", "", bare).rstrip("/").split("/")[-1]
    else:
        candidate = spec.split("@")[0] if not spec.startswith("@") else "@" + spec[1:].split("@")[0]
    stage_modules = os.path.join(STAGE_DIR, "node_modules")
    main_name = ""
    if candidate and os.path.isdir(os.path.join(stage_modules, *candidate.split("/"))):
        main_name = candidate
    else:
        deps = (_read_json(os.path.join(STAGE_DIR, "package.json"), {}) or {}).get("dependencies") or {}
        for k in deps:
            if os.path.isdir(os.path.join(stage_modules, *k.split("/"))):
                main_name = k
    if not main_name and name_hint:
        main_name = name_hint
    if not main_name:
        return {"ok": False, "error": "装完了但找不到包目录，请检查 npm 输出", "tail": tail}

    meta = _read_json(os.path.join(stage_modules, *main_name.split("/"), "package.json"), {}) or {}
    version = meta.get("version") or ""

    # 安装前自测：临时宿主只加载这一个，过了才允许进真实 profile。
    # 不通过就清理 stage 并中止，真实名单和真实 profile 一个字都不会写。
    probe = probe_plugin(main_name)
    if not probe.get("ok"):
        try:
            _remove_stage_pkg(main_name)
        except Exception:
            pass
        log("安装被自测拦下 %s：%s" % (main_name, probe.get("error")), "WARN")
        return {"ok": False, "incompatible": bool(probe.get("incompatible")),
                "name": main_name, "spec": spec,
                "error": "插件没通过加载自测：" + probe.get("error", "")}

    link_names = added | {main_name}
    linked, skipped, failed = _link_into_profile(stage_modules, link_names)
    _write_profile_dep(main_name, version, spec)
    patched = _register_patch(main_name)
    # 关键一步：写进宿主的加载名单，否则 node_modules 里有、profile 也记了账，
    # 但 dsh_host 启动时不会加载它，等于白装。
    host_on = host_enable(main_name, True)

    log("安装完成 %s v%s（挂载 %d，跳过 %d，%.1fs）"
        % (main_name, version, len(linked), len(skipped), time.time() - t0))
    return {
        "ok": True,
        "name": main_name,
        "version": version,
        "linked": len(linked),
        "skipped": skipped,
        "failed": failed,
        "patch": patched,
        "host_enabled": host_on,
        "elapsed": round(time.time() - t0, 1),
        "need_restart": True,
        "tail": tail[-400:],
    }


def uninstall(name):
    """从 profile 撤掉一个插件（package.json + node_modules + 启用条目）。"""
    name = (name or "").strip()
    if not name or not re.match(r"^(@[a-z0-9-._~]+/)?[a-z0-9-._~]+$", name, re.I):
        return {"ok": False, "error": "包名不合法"}

    d = _read_json(PROFILE_PKG, {}) or {}
    deps = dict(d.get("dependencies") or {})
    deps.pop(name, None)
    d["dependencies"] = deps
    _write_json(PROFILE_PKG, d)

    dst = os.path.join(PROFILE_MODULES, *name.split("/"))
    try:
        if os.path.islink(dst):
            os.unlink(dst)
        elif os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
    except Exception as e:
        log("移除目录失败：%r" % e, "WARN")

    # 只删我们自己追加的「裸条目」（没有 config 的那种）；带 config 的
    # 是用户在 DSH 里配过的，绝不能动
    try:
        with open(PATCH_FILE, encoding="utf-8") as f:
            s = f.read()
        s2 = re.sub(r"^- id:\s*\S+\n\s*name:\s*[\"']?%s[\"']?\s*\n(?=^- id:|\Z)"
                    % re.escape(name), "", s, flags=re.M)
        if s2 != s:
            with open(PATCH_FILE, "w", encoding="utf-8") as f:
                f.write(s2)
    except Exception as e:
        log("清理启用条目失败：%r" % e, "WARN")

    # stage 里也去掉，避免下次安装时被旧依赖带回来
    try:
        sp = os.path.join(STAGE_DIR, "package.json")
        sd = _read_json(sp, {}) or {}
        sdeps = dict(sd.get("dependencies") or {})
        if sdeps.pop(name, None) is not None:
            sd["dependencies"] = sdeps
            _write_json(sp, sd)
    except Exception:
        pass

    # 从宿主加载名单里摘掉，否则重启后它会继续加载一个已经被删的目录
    host_enable(name, False)

    log("已卸载：%s" % name)
    return {"ok": True, "name": name, "need_restart": True}


WINDOW_TITLE = "DSH 插件市场 · 联网"


def bring_window():
    """把已开着的市场窗口拉到前台。"""
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.FindWindowW.restype = wintypes.HWND
        u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        h = u.FindWindowW(None, WINDOW_TITLE)
        if not h:
            return False
        u.ShowWindow(h, 9)   # SW_RESTORE
        u.SetForegroundWindow(h)
        return True
    except Exception:
        return False


def open_window():
    """弹市场窗口（单实例）。返回 {ok, pid, already}。"""
    pid = window_pid()
    if pid:
        bring_window()
        return {"ok": True, "pid": pid, "already": True}
    exe = os.path.join(os.path.dirname(sys.executable), "python.exe")
    if not os.path.isfile(exe):
        exe = sys.executable
    ui = os.path.join(HOST_DIR, "..", "dfy_market_ui.py")
    ui = os.path.normpath(ui)
    if not os.path.isfile(ui):
        return {"ok": False, "error": "找不到市场窗口脚本：%s" % ui}
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        # 给子进程一个真实的 stdout（pywebview 在没有标准输出时会初始化失败），
        # 写到日志里，出问题能查
        logf = open(os.path.join(CACHE_DIR, "ui.log"), "a", encoding="utf-8", errors="replace")
        p = subprocess.Popen(
            [exe, ui], cwd=os.path.dirname(ui),
            creationflags=0x00000008 | 0x00000200,  # DETACHED | NEW_GROUP
            stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
            close_fds=True)
        try:
            logf.close()
        except Exception:
            pass
    except Exception as e:
        return {"ok": False, "error": str(e)}
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(p.pid))
    except Exception:
        pass
    log("市场窗口已启动 pid=%s" % p.pid)
    return {"ok": True, "pid": p.pid, "already": False}


def _find_window():
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.FindWindowW.restype = wintypes.HWND
        u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        return u.FindWindowW(None, WINDOW_TITLE)
    except Exception:
        return 0


def window_pid():
    """进程活着 **而且窗口真的在** 才算数——只凭 pid 会把僵死实例算成已开。"""
    try:
        with open(PID_FILE, encoding="utf-8") as f:
            pid = int((f.read() or "0").strip() or 0)
    except Exception:
        pid = 0
    if not pid:
        return 0
    try:
        import psutil
        p = psutil.Process(pid)
        ok = "dfy_market_ui" in " ".join(p.cmdline() or [])
    except Exception:
        ok = False
    if ok and _find_window():
        return pid
    # 进程在但窗口没了（或根本不在了）：清掉记录，让下次能重新拉起
    try:
        os.remove(PID_FILE)
    except Exception:
        pass
    return 0


def close_window():
    pid = window_pid()
    if not pid:
        return {"ok": True, "closed": False}
    try:
        import psutil
        psutil.Process(pid).terminate()
        return {"ok": True, "closed": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    print(json.dumps(status(), ensure_ascii=False, indent=1))
    print("total:", len(catalog(wait=True).get("plugins") or []))
