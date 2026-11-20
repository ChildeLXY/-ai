# -*- coding: utf-8 -*-
"""DSH 自愈体检（doctor）：把这次踩过的坑变成可自动修复的固定项。

用法：
    python dfy_dsh_doctor.py            体检，只报告
    python dfy_dsh_doctor.py fix        体检并自动修复可修项
    python dfy_dsh_doctor.py --json     机器可读输出

六项检查：
    1 proxy     Clash 代理 7897 是否在监听（GitHub 插件全靠它）
    2 pnpm      DSH 子进程用到的 pnpm 是否锁定为运行时 11.28.5
    3 store     storeDir 三处一致（workspace yaml / .npmrc / .modules.yaml）
    4 allowBuilds  lockfile 里每个 git 包 commit 都要在白名单里
    5 modules   14 个业务插件目录齐全（缺了优先从 .ignored 移回）
    6 ports     6800 归 host.mjs、6811 归 dfyweb，防止串台
"""
import json
import os
import re
import socket
import subprocess
import sys
import time

BASE = r"D:\大肥鱼AI"
# 内置 DSH 的固定 home（dfy_dshui / 6811 用这套）
DEFAULT_DSH_HOME = r"D:\本地部署大肥鱼环境\dsh"
# 真正在跑的那套：host.mjs 读的是环境变量 DSH_HOME。
# 本机实测 env DSH_HOME=E:\DSH-home，两套 home 的插件清单并不一致 ——
# 只看 D 盘那套会漏掉实际加载的插件（dsh-ops 悬空链接就是这么漏的）。
LIVE_DSH_HOME = os.path.normpath(os.environ.get("DSH_HOME") or DEFAULT_DSH_HOME)
HOMES = sorted({DEFAULT_DSH_HOME, LIVE_DSH_HOME})
# 体检的「配置类」检查（pnpm/store/allowBuilds/dfyweb）锚定内置 home：
# dfy_dshui / 6811 用的就是这套，dfyweb profile 只存在于这里。
# 插件目录检查则两套 home 都扫（见 check_modules）——正在跑的 host.mjs 读的是
# 环境变量 DSH_HOME，实测指向 E:\DSH-home，只看一套必然漏。
DSH_HOME = DEFAULT_DSH_HOME
RUNTIME = os.path.join(DSH_HOME, "dsh-runtimes", "dsh-primary-runtime")
NODE = os.path.join(RUNTIME, "dependencies", "node", "bin", "node.exe")
PNPM_CJS = os.path.join(RUNTIME, "dependencies", "pnpm", "bin", "pnpm.cjs")
PNPM_VERSION_FILE = os.path.join(RUNTIME, "runtime.json")

PROFILE = os.path.join(DSH_HOME, "profiles", "dfyweb")
DESKTOP = os.path.join(DSH_HOME, "profiles", "desktop")
NM = os.path.join(DESKTOP, "node_modules")
IGNORED = os.path.join(NM, ".ignored")
MODULES_YAML = os.path.join(NM, ".modules.yaml")
WS_YAML = os.path.join(PROFILE, "pnpm-workspace.yaml")
NPMRC = os.path.join(PROFILE, ".npmrc")
LOCK = os.path.join(PROFILE, "pnpm-lock.yaml")

STORE = r"C:\Users\lxy\AppData\Local\pnpm\store\v11"
SHIM_DIR = os.path.join(BASE, "dsh_host", "bin")
# 运行时目录里有中文（本地部署大肥鱼环境），而 .cmd 内容必须全 ASCII：
# cmd.exe 按 OEM 代码页解码批处理正文，UTF-8 写的中文路径会变成乱码 →
# "系统找不到指定的路径"。用目录联接给出纯 ASCII 路径。
RT_LINK = r"D:\dfy-rt"
RT_DEPS = os.path.join(RUNTIME, "dependencies")
CLASH_EXE = r"C:\Program Files\Clash Verge\clash-verge.exe"
PROXY_HOST, PROXY_PORT = "127.0.0.1", 7897

BIZ_PLUGINS = [
    "dsh-bili-pond", "dsh-bili-scroll", "dsh-bili-whale", "dsh-feiyu-mood",
    "dsh-live-compact", "dsh-local-ai", "dsh-nekowhale-stickers", "dsh-onebot-qq",
    "dsh-pet", "dsh-qq-bridge", "dsh-soul-md", "dsh-speak-up", "dshmarket",
    "dsh-qrcode",
]

PROBE_JS = os.path.join(BASE, "dsh_host", "probe_pnpm.mjs")

# 测试注入点：doctor_gate 需要在临时目录里模拟破损的 node_modules，
# 而不动真实环境。这两个钩子让「多 home」逻辑仍然可测。
_HOMES_OVERRIDE = None
_NM_OVERRIDE = None


def _homes():
    return list(_HOMES_OVERRIDE) if _HOMES_OVERRIDE else list(HOMES)


# ---------- 基础工具 ----------

def _run(args, cwd=None, timeout=40, env=None):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, env=env)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except Exception as e:
        return -1, "", repr(e)


def _port_open(port, host="127.0.0.1", wait=0.4):
    s = socket.socket()
    s.settimeout(wait)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _runtime_pnpm_version():
    try:
        with open(PNPM_VERSION_FILE, "r", encoding="utf-8") as f:
            return (json.load(f).get("pnpmVersion") or "").strip()
    except Exception:
        return ""


PINNED_PNPM = _runtime_pnpm_version() or "11.28.5"


def _shim_exe(name):
    return os.path.join(SHIM_DIR, name + ".cmd")


# ---------- 1 代理 ----------

def check_proxy():
    ok = _port_open(PROXY_PORT)
    return {
        "id": "proxy", "name": "Clash 代理 7897",
        "ok": ok, "fixable": True,
        "detail": "监听中" if ok else "未监听；GitHub 源插件会装不上",
    }


def fix_proxy():
    if _port_open(PROXY_PORT):
        return True, "已在监听"
    if not os.path.isfile(CLASH_EXE):
        return False, "找不到 clash-verge.exe"
    try:
        subprocess.Popen([CLASH_EXE], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    except Exception as e:
        return False, "启动失败: %r" % (e,)
    deadline = time.time() + 40
    while time.time() < deadline:
        if _port_open(PROXY_PORT):
            return True, "已拉起并监听"
        time.sleep(1)
    return False, "拉起后 40 秒仍未监听（可能未登录或订阅失效）"


# ---------- 2 pnpm ----------

def _ensure_rt_link():
    """保证 D:\\dfy-rt 联接存在，给垫片一条纯 ASCII 的运行路径。"""
    if os.path.isdir(os.path.join(RT_LINK, "node")):
        return True
    try:
        rc, out, err = _run(["cmd", "/d", "/s", "/c",
                             'mklink /J "%s" "%s"' % (RT_LINK, RT_DEPS)], timeout=30)
        return rc == 0 and os.path.isdir(os.path.join(RT_LINK, "node"))
    except Exception:
        return False


def _write_shims():
    """在 dsh_host\\bin 生成 pnpm 垫片，指向运行时 node + pnpm.cjs。

    Windows 下 cmd.exe 读批处理正文时按 OEM 代码页解码：正文里出现中文路径
    就会乱码成"系统找不到指定的路径"。所以正文必须全 ASCII —— 通过目录联接
    把运行时的中文路径换成 D:\\dfy-rt\\...。
    """
    os.makedirs(SHIM_DIR, exist_ok=True)
    use_link = _ensure_rt_link()
    if use_link:
        node = RT_LINK + r"\node\bin\node.exe"
        cjs = RT_LINK + r"\pnpm\bin\pnpm.cjs"
    else:
        node, cjs = NODE, PNPM_CJS
    cmd_path = _shim_exe("pnpm")
    ps1_path = os.path.join(SHIM_DIR, "pnpm.ps1")
    _write(cmd_path, '@echo off\r\n"%s" "%s" %%*\r\n' % (node, cjs))
    _write(ps1_path, '& "%s" "%s" @args\r\n' % (node, cjs))
    return use_link


def _is_ascii(path):
    try:
        with open(path, "rb") as f:
            return all(b < 128 for b in f.read())
    except Exception:
        return False


def check_pnpm():
    problems = []
    shim = _shim_exe("pnpm")
    if not os.path.isfile(shim):
        problems.append("缺少 dsh_host\\bin\\pnpm.cmd 垫片")
    elif not _is_ascii(shim):
        # 关键：含非 ASCII 的 .cmd 在 cmd.exe 里会被读坏，市场探针必失败
        problems.append("垫片正文含非 ASCII 路径（cmd 会解码失败）")
    if not os.path.isdir(os.path.join(RT_LINK, "node")):
        problems.append("缺少 %s 联接（运行时中文路径无法进 .cmd）" % RT_LINK)
    # 垫片跑出来的版本
    shim_ver = ""
    if os.path.isfile(shim):
        rc, out, err = _run([shim, "--version"], timeout=30)
        shim_ver = (out.strip().splitlines() or [""])[0].strip()
        if rc != 0 or not shim_ver:
            problems.append("垫片执行失败: %s" % (err.strip()[:120] or rc))
        elif shim_ver != PINNED_PNPM:
            problems.append("垫片版本 %s != 运行时 %s" % (shim_ver, PINNED_PNPM))
    # 市场探针走的是 cmd /c "pnpm --version"（裸名解析），必须也通
    probe_env = dict(os.environ)
    probe_env["PATH"] = SHIM_DIR + os.pathsep + probe_env.get("PATH", "")
    try:
        p = subprocess.run(["cmd", "/d", "/s", "/c", "pnpm --version"],
                           capture_output=True, text=True, timeout=30, env=probe_env)
        probe_out = (p.stdout or "").strip().splitlines()
        probe_ver = probe_out[0].strip() if probe_out else ""
        if p.returncode != 0 or probe_ver != PINNED_PNPM:
            problems.append("cmd 裸名解析 pnpm 失败（市场探针会判 false）: %r"
                            % ((p.stderr or p.stdout or "").strip()[:120]))
    except Exception as e:
        probe_ver = ""
        problems.append("cmd 裸名解析异常: %r" % (e,))
    # PATH 上当前解析到的（仅作参考，不进结论）
    rc, out, _ = _run(["where", "pnpm"], timeout=20)
    where_list = [x.strip() for x in out.splitlines() if x.strip()]
    return {
        "id": "pnpm", "name": "pnpm 版本锁定（含 cmd 裸名解析）",
        "ok": not problems, "fixable": True,
        "detail": ("已锁定 %s" % PINNED_PNPM) if not problems else "；".join(problems),
        "extra": {"shimVersion": shim_ver, "cmdProbe": probe_ver,
                  "pathResolved": where_list[:3]},
    }


def fix_pnpm():
    use_link = _write_shims()
    rc, out, err = _run([_shim_exe("pnpm"), "--version"], timeout=30)
    ver = (out.strip().splitlines() or [""])[0].strip()
    if ver != PINNED_PNPM:
        return False, "垫片重建后版本仍为 %r (%s)" % (ver, err.strip()[:120])
    if not use_link:
        return False, "垫片版本 %s，但 %s 联接建不出来（cmd 裸名解析可能仍失败）" % (ver, RT_LINK)
    return True, "垫片已重建（ASCII 路径），版本 %s" % ver


# ---------- 3 store ----------

def _parse_kv(text, key):
    """读 yaml / ini / json 里的同名键，允许带引号（.modules.yaml 是 JSON）。"""
    m = re.search(r'(?m)^\s*"?%s"?\s*[:=]\s*[\'"]?([^\'"\r\n]+)' % re.escape(key), text)
    return m.group(1).strip() if m else ""


def _modules_meta():
    """把 .modules.yaml 当 JSON 读（它本来就是 JSON 语法）。"""
    try:
        with open(MODULES_YAML, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def check_store():
    ws_store = _parse_kv(_read(WS_YAML), "storeDir")
    rc_store = _parse_kv(_read(NPMRC), "store-dir")
    meta = _modules_meta()
    mod_store = (meta.get("storeDir") or "").strip()
    mod_vsd = (meta.get("virtualStoreDir") or "").strip()
    rc, out, _ = _run([NODE, PNPM_CJS, "store", "path"], cwd=PROFILE, timeout=40)
    live_store = out.strip().splitlines()[0].strip() if out.strip() else ""

    def same(a, b):
        # dfyweb\node_modules 是指向 desktop 的目录联接，两个写法指向同一处，
        # 必须按真实路径比，否则会把等价路径误判为漂移。
        ka = os.path.normcase(os.path.realpath(a))
        kb = os.path.normcase(os.path.realpath(b))
        return ka == kb

    problems = []
    for label, val in (("workspace", ws_store), (".npmrc", rc_store),
                       (".modules.yaml", mod_store), ("pnpm 实回", live_store)):
        if not val:
            problems.append("%s 未配置" % label)
        elif not same(val, STORE):
            problems.append("%s=%s" % (label, val))
    want_vsd = os.path.join(DESKTOP, "node_modules", ".pnpm")
    # pnpm 是按字面串比较 .modules.yaml 里的 virtualStoreDir 的，
    # 写成等价的 dfyweb 路径也会报 ERR_PNPM_UNEXPECTED_VIRTUAL_STORE，
    # 所以这里必须字面对齐，不能只比 realpath。
    def literal_same(a, b):
        return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))

    if mod_vsd and not literal_same(mod_vsd, want_vsd):
        problems.append("virtualStoreDir 指向 %s" % mod_vsd)

    return {
        "id": "store", "name": "store 一致性",
        "ok": not problems, "fixable": True,
        "detail": ("统一在 %s" % STORE) if not problems else "；".join(problems),
    }


def fix_store():
    changed = []
    text = _read(WS_YAML)
    if text:
        new = re.sub(r"(?m)^\s*storeDir\s*:.*$", lambda m: "storeDir: '%s'" % STORE, text, count=1)
        if new != text:
            _write(WS_YAML, new)
            changed.append("pnpm-workspace.yaml")
    text = _read(NPMRC)
    if text:
        new = re.sub(r"(?m)^\s*store-dir\s*=.*$", lambda m: "store-dir=%s" % STORE, text, count=1)
        if new != text:
            _write(NPMRC, new)
            changed.append(".npmrc")
    # .modules.yaml 的 virtualStoreDir 若被别的 profile 写过，改回 desktop
    meta = _modules_meta()
    want_vsd = os.path.join(DESKTOP, "node_modules", ".pnpm")
    cur_vsd = (meta.get("virtualStoreDir") or "").strip()
    if cur_vsd and os.path.normcase(os.path.normpath(cur_vsd)) != os.path.normcase(os.path.normpath(want_vsd)):
        text = _read(MODULES_YAML)
        new = re.sub(r'(?m)^(\s*"virtualStoreDir"\s*:\s*)"[^"]*"',
                     lambda m: m.group(1) + json.dumps(want_vsd, ensure_ascii=False), text, count=1)
        _write(MODULES_YAML, new)
        changed.append(".modules.yaml")
    return True, ("已修正: " + "、".join(changed)) if changed else "无需修改"


# ---------- 4 allowBuilds ----------

GIT_HOSTED_RE = re.compile(
    r"(?m)^\s{2}([^\s:]+)@(https://codeload\.github\.com/[^\s:]+)\s*:")


def _needs_build(name):
    """该包是否真的声明了构建脚本。只有这些才需要 allowBuilds 授权。"""
    pj = os.path.join(NM, name, "package.json")
    try:
        with open(pj, "r", encoding="utf-8") as f:
            scripts = (json.load(f) or {}).get("scripts") or {}
    except Exception:
        return False
    return any(k in scripts for k in
               ("preinstall", "install", "postinstall", "prepare", "prepack"))


def _git_hosted_keys():
    """lockfile 里 commit 已钉死、且确实需要跑构建脚本的 git 包。"""
    text = _read(LOCK)
    pairs = set()
    for m in GIT_HOSTED_RE.finditer(text):
        url = m.group(2)
        if re.search(r"/[0-9a-f]{40}$", url):
            pairs.add((m.group(1), url))
    return {"%s@%s" % (name, url) for name, url in pairs if _needs_build(name)}


def _allow_keys():
    text = _read(WS_YAML)
    keys = set()
    in_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("allowBuilds:"):
            in_block = True
            continue
        if in_block:
            if not stripped:
                continue
            if not line.startswith((" ", "\t")):
                break
            # 形如 "  包名@https://...: true"，键里含冒号，必须按末尾的布尔值切
            m = re.match(r"^\s+(\S.*?)\s*:\s*(?:true|false)\s*$", line)
            if m:
                keys.add(m.group(1).strip())
    return keys


def check_allowbuilds():
    need = _git_hosted_keys()
    have = _allow_keys()
    missing = sorted(need - have)
    return {
        "id": "allowbuilds", "name": "allowBuilds 白名单",
        "ok": not missing, "fixable": True,
        "detail": ("%d 个 git 包全部已授权" % len(need)) if not missing
        else "缺少 %d 项: %s" % (len(missing), ", ".join(missing)),
        "extra": {"need": len(need), "missing": missing},
    }


def fix_allowbuilds():
    need = _git_hosted_keys()
    have = _allow_keys()
    missing = sorted(need - have)
    if not missing:
        return True, "无需修改"
    text = _read(WS_YAML)
    if "allowBuilds:" in text:
        # 在 allowBuilds 块末尾追加
        lines = text.splitlines()
        idx = next(i for i, l in enumerate(lines) if l.strip().startswith("allowBuilds:"))
        insert_at = idx + 1
        while insert_at < len(lines) and (lines[insert_at].startswith((" ", "\t"))) and lines[insert_at].strip():
            insert_at += 1
        pad = ["  %s: true" % k for k in missing]
        lines[insert_at:insert_at] = pad
        _write(WS_YAML, "\n".join(lines) + "\n")
    else:
        _write(WS_YAML, text.rstrip("\n") + "\nallowBuilds:\n" +
               "\n".join("  %s: true" % k for k in missing) + "\n")
    return True, "已补 %d 项" % len(missing)


# ---------- 5 插件目录 ----------

def _nm_of(home):
    if _NM_OVERRIDE:
        return _NM_OVERRIDE
    return os.path.join(home, "profiles", "desktop", "node_modules")


def _expected_plugins(home=None):
    """期望在 node_modules 里能看到的插件 = profile 的 dependencies ∪ bundles ∪ 核心名单。

    关键：**不再按名字前缀过滤**。原先只收 ``dshmarket`` / ``dsh-*``，
    导致 ``cc-safety-net``、``@vectorize-io/hindsight-coding-agents`` 这类
    非 dsh- 前缀的插件被整条链路忽略——目录被删、守卫失效，doctor 照样全绿。
    现在凡是 package.json 里声明的依赖或启用的 bundle，一律纳入体检。
    """
    home = home or DSH_HOME
    names = set(BIZ_PLUGINS)
    for prof in (os.path.join(home, "profiles", "desktop"),
                 os.path.join(home, "profiles", "dfyweb")):
        try:
            with open(os.path.join(prof, "package.json"), "r", encoding="utf-8") as f:
                pkg = json.load(f)
        except Exception:
            continue
        for key in ("dependencies", "devDependencies", "optionalDependencies"):
            for name in (pkg.get(key) or {}):
                names.add(name)
        # UNIFY_BUNDLES_AWARE: bundles 是「启用」的真源，也要纳入体检
        for name in ((pkg.get("dsh") or {}).get("profile") or {}).get("bundles", []):
            names.add(name)
    # 过滤掉明显不是插件的运行时包（DSH 自身的 GUI/构建依赖）
    skip = {"react", "react-dom", "typescript", "eslint", "vite", "axios"}
    return sorted(n for n in names if n not in skip)


def _plugin_dir(name, home=None):
    return os.path.join(_nm_of(home or DSH_HOME), name.replace("/", os.sep))


def _entry_target(pkg):
    """从 package.json 的 main / exports 里取第一个可解析的入口相对路径。"""
    main = pkg.get("main")
    if isinstance(main, str) and main:
        return main
    exp = pkg.get("exports")
    if isinstance(exp, str):
        return exp
    if isinstance(exp, dict):
        for k in ("." , "import", "default", "require"):
            v = exp.get(k)
            if isinstance(v, str):
                return v
            if isinstance(v, dict):
                for kk in ("import", "default", "require"):
                    if isinstance(v.get(kk), str):
                        return v[kk]
    return None


def _plugin_problems(name, home=None):
    """插件「能不能真正被加载」的静态体检，返回问题列表（空 = 健康）。

    只判断目录是不够的：目录在、代码坏了 / package.json 没了 / 入口文件缺失 /
    连 dsh 插件标记都没有，运行时都会拒载或报错，而旧版 doctor 一律判「齐全」。
    """
    d = _plugin_dir(name, home)
    probs = []
    if not os.path.isdir(d):
        if os.path.lexists(d):
            return ["悬空链接（目标目录已不存在）"]
        return ["目录缺失"]
    pj = os.path.join(d, "package.json")
    if not os.path.isfile(pj):
        return ["缺少 package.json"]
    try:
        with open(pj, "r", encoding="utf-8") as f:
            pkg = json.load(f)
    except Exception as e:
        return ["package.json 无法解析：%s" % e]
    if pkg.get("name") != name:
        probs.append("包名不符（声明 %r）" % pkg.get("name"))
    if not isinstance(pkg.get("dsh"), (dict, str)):
        probs.append("缺少 dsh 插件标记")
    entry = _entry_target(pkg)
    if entry:
        if not os.path.isfile(os.path.join(d, entry.replace("/", os.sep))):
            probs.append("入口文件缺失：%s" % entry)
    else:
        probs.append("未声明入口（main/exports 皆空）")
    return probs


def _scan_home(home):
    """扫一套 home：返回 (expect, missing, restorable, broken)。"""
    expect = _expected_plugins(home)
    nm = _nm_of(home)
    ignored = os.path.join(nm, ".ignored")
    missing, restorable, broken = [], [], {}
    for p in expect:
        d = os.path.join(nm, p.replace("/", os.sep))
        if not os.path.lexists(d):
            missing.append(p)
            if os.path.isdir(os.path.join(ignored, p.replace("/", os.sep))):
                restorable.append(p)
            continue
        probs = _plugin_problems(p, home)
        if probs:
            broken[p] = probs
    return expect, missing, restorable, broken


def check_modules():
    expect_all = []
    missing_all, restorable_all, broken_all = [], [], {}
    per_home = {}
    for home in _homes():
        expect, missing, restorable, broken = _scan_home(home)
        expect_all.extend(expect)
        missing_all.extend("%s@%s" % (p, home) for p in missing)
        restorable_all.extend(restorable)
        for k, v in broken.items():
            broken_all["%s@%s" % (k, home)] = v
        per_home[home] = {"expected": len(expect), "missing": missing,
                          "broken": list(broken)}
    ok = not missing_all and not broken_all
    if ok:
        detail = "；".join("%s：%d 个插件齐全且可加载" % (h, per_home[h]["expected"])
                          for h in HOMES)
    else:
        seg = []
        if missing_all:
            seg.append("缺 %d 个: %s%s" % (
                len(missing_all), ", ".join(missing_all),
                ("（可从 .ignored 恢复 %d 个）" % len(restorable_all)) if restorable_all
                else "（.ignored 里也没有）"))
        if broken_all:
            seg.append("%d 个目录在但加载不了: %s" % (
                len(broken_all),
                "; ".join("%s(%s)" % (k, "/".join(v[:2])) for k, v in broken_all.items())))
        detail = "；".join(seg)
    return {
        "id": "modules", "name": "业务插件目录",
        "ok": ok, "fixable": bool(restorable_all),
        "detail": detail,
        "extra": {"homes": _homes(), "perHome": per_home,
                  "missing": missing_all, "restorable": restorable_all,
                  "broken": broken_all},
    }


def fix_modules():
    moved, stuck, broken_left = [], [], {}
    for home in _homes():
        expect, missing, _restorable, broken = _scan_home(home)
        broken_left.update({"%s@%s" % (k, home): v for k, v in broken.items()})
        nm = _nm_of(home)
        ignored = os.path.join(nm, ".ignored")
        for p in missing:
            src = os.path.join(ignored, p.replace("/", os.sep))
            dst = os.path.join(nm, p.replace("/", os.sep))
            if not os.path.isdir(src):
                stuck.append("%s@%s" % (p, home))
                continue
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                import shutil
                shutil.move(src, dst)
                moved.append("%s@%s" % (p, home))
            except Exception:
                stuck.append("%s@%s" % (p, home))
    if broken_left:
        return False, ("已恢复 %d 个；仍有 %d 个加载不了（悬空链接/坏包，需要重装）: %s"
                       % (len(moved), len(broken_left), ", ".join(broken_left)))
    if stuck:
        return False, "已恢复 %d 个，仍缺 %s" % (len(moved), ", ".join(stuck))
    return True, ("已恢复 %d 个: %s" % (len(moved), ", ".join(moved)) if moved else "无需修改")


# ---------- 6 端口归属 ----------

def _port_owner_cmdline(port):
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
             "(Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue |"
             " Select-Object -First 1 -ExpandProperty OwningProcess)" % port],
            capture_output=True, text=True, timeout=20)
    except Exception:
        return ""
    pid = (out.stdout or "").strip()
    if not pid.isdigit():
        return ""
    out2 = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
         "(Get-CimInstance Win32_Process -Filter 'ProcessId=%s').CommandLine" % pid],
        capture_output=True, text=True, timeout=20)
    return (out2.stdout or "").strip()


def check_ports():
    issues, notes = [], []
    c6800 = _port_owner_cmdline(6800)
    if c6800 and "host.mjs" not in c6800.lower():
        issues.append("6800 被非 host.mjs 进程占用")
    elif not c6800:
        notes.append("6800 未监听（主程序未启动）")
    c6811 = _port_owner_cmdline(6811)
    if c6811 and "dfyweb" not in c6811.lower():
        issues.append("6811 被非 dfyweb 进程占用")
    elif not c6811:
        # 6811 是按需启停的：空闲不代表异常，但「全绿」也不代表 DSH 当前可用
        notes.append("6811 空闲（DSH 未在运行，按需启动）")
    detail = "；".join(issues) if issues else ("端口归属正常" + ("，" + "，".join(notes) if notes else ""))
    return {
        "id": "ports", "name": "端口归属",
        "ok": not issues, "fixable": False,
        "detail": detail,
        "extra": {"p6800": bool(c6800), "p6811": bool(c6811),
                  "dsh_running": bool(c6811)},
    }


def check_builtin():
    """内置修改版 DSH 的产物是否齐（dist / host / node 二进制 / 品牌覆盖层）。

    旧版 doctor 六项全绿时也可能完全没有内置 DSH（比如 dist 被 restore 掉），
    这里补一项，让「全绿」起码等价于「内置入口是完整的」。
    """
    dist = os.path.join(BASE, "dsh_host", "dfy-dsh", "dist")
    host = os.path.join(BASE, "dsh_host", "host.mjs")
    missing = []
    if not os.path.isdir(dist):
        missing.append("dist 目录")
    else:
        for f in ("index.html", "dfy-override.js", "dfy-override.css"):
            if not os.path.isfile(os.path.join(dist, f)):
                missing.append("dist/" + f)
    if not os.path.isfile(host):
        missing.append("host.mjs")
    if not os.path.isfile(NODE):
        missing.append("node.exe")
    return {
        "id": "builtin", "name": "内置 DSH 产物",
        "ok": not missing, "fixable": False,
        "detail": "内置入口完整" if not missing else "缺 %d 项: %s" % (len(missing), ", ".join(missing)),
        "extra": {"missing": missing},
    }


# ---------- 安装链路自洽性（node_modules 与 store 配置是否同一套） ----------

def _ws_store(home):
    ws = os.path.join(home, "profiles", "desktop", "pnpm-workspace.yaml")
    return (_parse_kv(_read(ws), "storeDir") or "").strip()


def _ws_vsd(home):
    ws = os.path.join(home, "profiles", "desktop", "pnpm-workspace.yaml")
    return (_parse_kv(_read(ws), "virtualStoreDir") or "").strip()


def _mod_meta(home):
    p = os.path.join(home, "profiles", "desktop", "node_modules", ".modules.yaml")
    text = _read(p)
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        return {}


def _p(v):
    return os.path.normcase(os.path.normpath(os.path.expanduser(v or ""))) if v else ""


def check_install_path():
    """node_modules 记录的 store/virtualStore 必须和 workspace 配置同一套。

    pnpm 是按这两个值做「你这堆依赖是从哪个仓库链出来的」比对的，不一致就直接
    ERR_PNPM_UNEXPECTED_STORE 拒绝安装——市场装插件会挂，但报错信息完全看不出
    根因。这一项把它摊开：谁不一致、该怎么修。
    这一项不可自动修：真对齐要重装 node_modules，必须在主程序关闭时做。
    """
    issues, notes = [], []
    for home in _homes():
        ws_store, ws_vsd = _ws_store(home), _ws_vsd(home)
        meta = _mod_meta(home)
        mod_store = (meta.get("storeDir") or "").strip()
        mod_vsd = (meta.get("virtualStoreDir") or "").strip()
        nm = _nm_of(home)
        if not os.path.isdir(nm):
            notes.append("%s：无 node_modules" % home)
            continue
        if not meta:
            issues.append("%s：缺 .modules.yaml，pnpm 无法判断依赖来源" % home)
            continue
        if mod_store and ws_store and _p(mod_store) != _p(ws_store):
            issues.append("%s：依赖链自 %s，配置写的是 %s" % (
                home, os.path.dirname(os.path.dirname(mod_store)), ws_store))
        if mod_vsd and ws_vsd and _p(mod_vsd) != _p(ws_vsd):
            issues.append("%s：virtualStore 指向 %s（配置是 %s）" % (home, mod_vsd, ws_vsd))
    ok = not issues
    detail = "两套 home 的 store/virtualStore 与依赖来源一致" if ok else (
        "；".join(issues) +
        "。修法：关闭大肥鱼后在该 home 的 profiles\\desktop 里跑一次 pnpm install"
        "（会按 workspace 配置重新链接 node_modules）")
    return {
        "id": "installpath", "name": "安装链路自洽",
        "ok": ok, "fixable": False,
        "detail": detail,
        "extra": {"homes": _homes(), "issues": issues, "notes": notes},
    }


# ---------- 主流程 ----------

CHECKS = [check_proxy, check_pnpm, check_store, check_allowbuilds,
          check_modules, check_install_path, check_ports, check_builtin]
FIXERS = {"proxy": fix_proxy, "pnpm": fix_pnpm, "store": fix_store,
          "allowbuilds": fix_allowbuilds, "modules": fix_modules}


def run_checks():
    return [fn() for fn in CHECKS]


def report(results, as_json=False):
    if as_json:
        print(json.dumps({"ok": all(r["ok"] for r in results), "checks": results},
                         ensure_ascii=False, indent=2))
        return
    print("DSH 自愈体检  —  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("-" * 62)
    for r in results:
        mark = "OK  " if r["ok"] else "FAIL"
        print("[%s] %-14s %s" % (mark, r["id"], r["detail"]))
    bad = [r for r in results if not r["ok"]]
    print("-" * 62)
    print("结论: %s" % ("全部通过" if not bad else "%d 项待修" % len(bad)))


def probe_market_pnpm():
    """用市场自己的探针（probePnpm）判一次。

    为什么不能拿 `pnpm --version` 当判据：市场的探针叠了 host 包管理器那一层，
    且"进程内缓存"意味着它一旦失败就会一直 false，直到重启 dsh。
    这个子命令存在的意义就是**在不重启的情况下**知道市场现在到底认不认 pnpm。
    """
    env = dict(os.environ)
    env["PATH"] = SHIM_DIR + os.pathsep + env.get("PATH", "")
    env["PNPM_HOME"] = SHIM_DIR
    env["DSH_HOME"] = DSH_HOME
    rc, out, err = _run([NODE, PROBE_JS], timeout=90, env=env)
    try:
        data = json.loads(out[out.find("{"):]) if "{" in out else {}
    except Exception:
        data = {}
    if not data:
        data = {"ok": False, "failure": {"kind": "probe-crashed",
                                         "output": (err or out or "")[:300]}}
    data["exit"] = rc
    return data


def main():
    args = [a for a in sys.argv[1:]]
    as_json = "--json" in args
    do_fix = ("fix" in args) or ("--fix" in args)
    # --safe：只修配置类问题，不启动任何外部程序（代理留给用户/显式 fix）
    safe = "--safe" in args
    skip = {"proxy"} if safe else set()

    if "probe" in args:
        data = probe_market_pnpm()
        if do_fix and not data.get("ok"):
            good, msg = fix_pnpm()
            data["fix"] = msg
            data = probe_market_pnpm()
            data["fix"] = msg
        if as_json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            ok = bool(data.get("ok"))
            print("市场探针: %s" % ("可用" if ok else "不可用"))
            print("  pnpm 版本: %s" % (data.get("pnpmVersion") or "未知"))
            if not ok:
                f = data.get("failure") or {}
                print("  失败类型: %s" % (f.get("kind") or "unknown"))
                print("  失败输出: %s" % (f.get("output") or data.get("error") or ""))
                print("  处置: 先 `fix` 重建垫片；仍 false 就重启 DSH"
                      "（探针结果按进程常驻缓存，不重启不会重探）")
        sys.exit(0 if data.get("ok") else 1)

    results = run_checks()
    if do_fix:
        fixed_any = False
        for r in results:
            if r["ok"]:
                continue
            fixer = FIXERS.get(r["id"])
            if fixer is None or r["id"] in skip:
                continue
            good, msg = fixer()
            if not as_json:
                print("[FIX ] %-14s %s" % (r["id"], msg))
            fixed_any = True
        results = run_checks()
        if as_json:
            print(json.dumps({"fixed": fixed_any, "ok": all(x["ok"] for x in results),
                              "skipped": sorted(skip), "checks": results},
                             ensure_ascii=False, indent=2))
        else:
            print()
            report(results, as_json=False)
    else:
        report(results, as_json=as_json)

    remaining = [r for r in results if not r["ok"] and r["id"] not in skip]
    sys.exit(0 if not remaining else 1)


if __name__ == "__main__":
    main()
