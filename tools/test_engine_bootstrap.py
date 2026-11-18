# -*- coding: utf-8 -*-
"""引擎首次启动下载的端到端测试。

真实场景复刻：本地起一个支持 Range 的 HTTP 服务器，模拟分发源，
然后在**空目录**里从零把引擎装起来，最后验证装出来的引擎真能跑。

覆盖：
  1. 未装 → 安装 → 可用（真引擎 381.7 MB / 5 分卷）
  2. 分卷下载与合并
  3. 断点续传（预置半截分卷）
  4. 整包 SHA256 校验（含篡改必拦）
  5. 解压与核心文件校验
  6. 幂等（已装再 ensure 返回 already）
  7. 磁盘空间不足时报错
  8. 清单缺失时报错
  9. 装出来的引擎能 --version
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, r"D:\大肥鱼AI")
import dfy_engine_bootstrap as bp_mod

DIST = r"D:\大肥鱼缓存\engine_dist"
VENV_PY = r"D:\本地部署大肥鱼环境\venv312\Scripts\python.exe"
PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name
          + (("  -> " + str(detail)) if detail and not cond else ""))


def skip(name, why=""):
    SKIP.append(name)
    print("  [SKIP] " + name + (("  -> " + why) if why else ""))


# ---------------------------------------------------------------- 测试服务器

class RangeHandler(BaseHTTPRequestHandler):
    root = ""

    def do_GET(self):
        rel = self.path.split("?")[0].lstrip("/")
        p = os.path.join(self.root, rel.replace("/", os.sep))
        if not os.path.isfile(p):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        size = os.path.getsize(p)
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d+)-(\d*)", rng) if rng else None
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
            if start >= size:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.end_headers()
                return
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            remain = end - start + 1
            with open(p, "rb") as f:
                f.seek(start)
                while remain > 0:
                    b = f.read(min(65536, remain))
                    if not b:
                        break
                    self.wfile.write(b)
                    remain -= len(b)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(p, "rb") as f:
                shutil.copyfileobj(f, self.wfile, 65536)

    def log_message(self, *a):
        pass


def start_server(root):
    h = RangeHandler
    h.root = root
    srv = ThreadingHTTPServer(("127.0.0.1", 0), h)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, "http://127.0.0.1:%d/" % srv.server_address[1]


# ---------------------------------------------------------------- 构造微型包

def make_mini(root):
    """造一个目录结构正确、内容随机的小包，用来快速测异常分支。"""
    eng = os.path.join(root, "mini_src", "engine")
    for rel in ("resources/runtime/cli/bin/dsh.cmd",):
        p = os.path.join(eng, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("@echo off\r\necho mini\r\n")
    with open(os.path.join(eng, "DaFeiyuEngine.exe"), "wb") as f:
        f.write(os.urandom(8 * 1024 * 1024))
    with open(os.path.join(eng, "resources", "app.asar"), "wb") as f:
        f.write(os.urandom(8 * 1024 * 1024))
    out = os.path.join(root, "mini_pkg")
    os.makedirs(out, exist_ok=True)
    tgz = os.path.join(out, "mini.tgz")
    subprocess.check_call([r"C:\Windows\system32\tar.exe", "-czf", tgz,
                           "-C", os.path.join(root, "mini_src"), "engine"])
    h = hashlib.sha256(open(tgz, "rb").read()).hexdigest()
    man = {"version": "mini", "archive": "tgz",
           "total_bytes": os.path.getsize(tgz), "sha256": h,
           "min_disk_bytes": 10 * 1024 * 1024,
           "parts": [{"file": "mini.tgz", "url": "mini.tgz",
                      "bytes": os.path.getsize(tgz)}]}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f)
    return out, tgz, h


# ---------------------------------------------------------------- 用例

def main():
    print("== 0. 前置检查 ==")
    man_path = os.path.join(DIST, "manifest.json")
    check("分发清单存在", os.path.isfile(man_path), man_path)
    if not os.path.isfile(man_path):
        return 1
    man = json.load(open(man_path, encoding="utf-8"))
    n_parts = len(man["parts"])
    print("  分卷数 %d，整包 %.1f MB" % (n_parts, man["total_bytes"] / 1048576.0))
    check("分卷数与文件一致",
          all(os.path.isfile(os.path.join(DIST, p["file"])) for p in man["parts"]))

    base = tempfile.mkdtemp(prefix="dfy_boot_", dir="D:\\大肥鱼缓存")
    print("  测试工作区:", base)
    srv, baseurl = start_server(DIST)
    print("  模拟分发源:", baseurl)

    try:
        # ---------------- 1. 预置半截分卷，验证断点续传
        print("\n== 1. 从零安装（含断点续传）==")
        target = os.path.join(base, "inst")
        os.makedirs(target, exist_ok=True)
        tmp = os.path.join(target, ".engine_download")
        os.makedirs(tmp, exist_ok=True)
        p1 = os.path.join(DIST, man["parts"][0]["file"])
        half = os.path.join(tmp, "part01")
        with open(p1, "rb") as f, open(half, "wb") as g:
            g.write(f.read(40 * 1024 * 1024))     # 先放 40MB，剩下 55MB 靠续传
        print("  预置 part01 前 %.0f MB，期望续传剩余部分" % 40)

        phases = []
        def prog(phase, **kw):
            phases.append((phase, kw.get("percent"), kw.get("label")))

        b = bp_mod.EngineBootstrap(target, manifest_url=baseurl + "manifest.json",
                                  progress=prog, strict_local=True)
        check("安装前判定为未装", not b.is_installed())
        r = b.ensure()
        print("  ensure ->", {k: r.get(k) for k in ("ok", "already", "version")})
        check("安装成功", r.get("ok") is True, r)
        check("不是 already（确实装了）", r.get("already") is False)
        check("版本正确", r.get("version") == "0.2.2-rc.2", r.get("version"))
        check("安装后判定为已装", b.is_installed())
        check("下载阶段有上报", any(p[0] == "downloading" for p in phases), phases[:6])
        check("校验阶段有上报", any(p[0] in ("hashing", "verifying") for p in phases))
        check("完成阶段有上报", any(p[0] == "done" for p in phases))
        # 核心文件
        for rel in ("DaFeiyuEngine.exe", "resources/app.asar",
                    "resources/runtime/cli/bin/dsh.cmd"):
            check("核心文件 " + rel,
                  os.path.isfile(os.path.join(target, "engine", rel.replace("/", os.sep))))

        # ---------------- 2. 装出来的引擎真能跑
        print("\n== 2. 装出来的引擎能启动 ==")
        cli = os.path.join(target, "engine", "resources", "runtime", "cli", "bin", "dsh.cmd")
        env = dict(os.environ)
        env["DSH_HOME"] = os.path.join(base, "dsh_home")
        try:
            out = subprocess.run(["cmd", "/c", cli, "--version"], capture_output=True,
                                 text=True, timeout=180, env=env, encoding="utf-8",
                                 errors="replace")
            txt = (out.stdout or "") + (out.stderr or "")
            print("  版本输出:", txt.strip()[:80])
            check("--version 退出码 0", out.returncode == 0, out.returncode)
            check("输出含版本号", bool(re.search(r"\d+\.\d+\.\d+", txt)), txt[:100])
        except Exception as e:
            check("--version 退出码 0", False, e)

        # ---------------- 3. 幂等
        print("\n== 3. 已装状态下再 ensure ==")
        b2 = bp_mod.EngineBootstrap(target, manifest_url=baseurl + "manifest.json", strict_local=True)
        r2 = b2.ensure()
        check("返回 already", r2.get("already") is True, r2)
        check("不重复下载", r2.get("ok") is True)

        # ---------------- 4. 清单缺失
        print("\n== 4. 清单地址缺失时报错 ==")
        b3 = bp_mod.EngineBootstrap(os.path.join(base, "x1"),
                                    manifest_url=baseurl + "nope.json", strict_local=True)
        try:
            b3.ensure()
            check("应抛错", False)
        except bp_mod.BootstrapError as e:
            check("应抛错", True)
            print("  错误信息:", str(e)[:90])

        # ---------------- 5. 篡改 SHA256 必拦
        print("\n== 5. 整包 SHA256 被篡改必须拒绝 ==")
        mini_out, mini_tgz, mini_sha = make_mini(base)
        bad_man = json.load(open(os.path.join(mini_out, "manifest.json"), encoding="utf-8"))
        bad_man["sha256"] = "0" * 64
        with open(os.path.join(mini_out, "manifest_bad.json"), "w", encoding="utf-8") as f:
            json.dump(bad_man, f)
        srv2, url2 = start_server(mini_out)
        try:
            b4 = bp_mod.EngineBootstrap(os.path.join(base, "x2"),
                                        manifest_url=url2 + "manifest_bad.json", strict_local=True)
            try:
                b4.ensure()
                check("篡改应被拒", False)
            except bp_mod.BootstrapError as e:
                check("篡改应被拒", "校验失败" in str(e), str(e)[:90])
            check("失败后未留下安装目录", not b4.is_installed())

            # ---------------- 6. 正确 SHA 应成功（证明上一条不是误杀）
            print("\n== 6. 同一包用正确 SHA 能装成功 ==")
            b5 = bp_mod.EngineBootstrap(os.path.join(base, "x3"),
                                        manifest_url=url2 + "manifest.json", strict_local=True)
            r5 = b5.ensure()
            check("微型包安装成功", r5.get("ok") is True, r5)
            check("微型包判定已装", b5.is_installed())
        finally:
            srv2.shutdown()

        # ---------------- 7. 磁盘空间不足
        print("\n== 7. 磁盘空间不足必须提前拦住 ==")
        big = json.load(open(man_path, encoding="utf-8"))
        big["min_disk_bytes"] = 10 ** 18
        big_dir = os.path.join(base, "bigman")
        os.makedirs(big_dir, exist_ok=True)
        shutil.copy(man_path, os.path.join(big_dir, "manifest.json"))
        with open(os.path.join(big_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(big, f)
        for p in man["parts"]:
            shutil.copy(os.path.join(DIST, p["file"]), os.path.join(big_dir, p["file"]))
        srv3, url3 = start_server(big_dir)
        try:
            b6 = bp_mod.EngineBootstrap(os.path.join(base, "x4"),
                                        manifest_url=url3 + "manifest.json", strict_local=True)
            try:
                b6.ensure()
                check("空间不足应被拒", False)
            except bp_mod.BootstrapError as e:
                check("空间不足应被拒", "磁盘空间不足" in str(e), str(e)[:90])
        finally:
            srv3.shutdown()

    finally:
        srv.shutdown()

    print("\n" + "=" * 58)
    print("通过 %d 项，失败 %d 项，跳过 %d 项" % (len(PASS), len(FAIL), len(SKIP)))
    for f in FAIL:
        print("  失败:", f)
    for s in SKIP:
        print("  跳过:", s)
    # 清理测试工作区
    try:
        shutil.rmtree(base, ignore_errors=True)
        print("测试工作区已清理")
    except Exception as e:
        print("清理失败（可手动删）:", e)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
