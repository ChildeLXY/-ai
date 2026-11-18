# -*- coding: utf-8 -*-
"""引擎首次启动下载 / 安装 / 校验。

设计前提（用户拍板）：引擎不随安装包分发，第一次启动时按需下载。
所以这里必须做到：断点续传、SHA256 校验、失败重试、进度可上报、
以及「下载→解压→校验安装」全链路可回滚（失败不留下半残引擎）。

对外就两件事：
    is_installed()      判断引擎是否已就绪
    ensure(progress)    没装就装上，装好返回结果

清单（manifest）格式：
{
  "version": "0.2.2-rc.2",
  "archive": "tgz",
  "total_bytes": 394000000,
  "sha256": "<合并后整包的 sha256>",
  "min_disk_bytes": 1300000000,
  "parts": [
     {"url": "https://.../p1", "bytes": 104857600},
     {"url": "https://.../p2", "bytes": 104857600}
  ]
}

parts 只有一项时等于单文件直下。分卷是为了绕开分发平台单文件体积限制。
"""
import hashlib
import json
import os
import re
import shutil
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from urllib.parse import urljoin

# 默认清单地址：安装时由打包脚本写入 packaging/engine_manifest_url.txt，
# 也可用环境变量 DFY_ENGINE_MANIFEST_URL 覆盖。
ENV_MANIFEST = "DFY_ENGINE_MANIFEST_URL"
ENV_MANIFEST_FILE = "DFY_ENGINE_MANIFEST_FILE"

CHUNK = 1024 * 256          # 256KB 一片，兼顾速度与进度粒度
RETRIES = 3                 # 每个分卷的重试次数
TIMEOUT = 30                # 单次连接超时（秒）

# 引擎是否可用的判定依据（相对引擎根目录），必须和 dfy_engine 的认识一致
CORE_RELS = [
    "DaFeiyuEngine.exe",
    os.path.join("resources", "app.asar"),
    os.path.join("resources", "runtime", "cli", "bin", "dsh.cmd"),
]


def _log(msg):
    print("[engine-bootstrap] " + msg, flush=True)


class BootstrapError(RuntimeError):
    pass


# ---------------------------------------------------------------- 工具


def _fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%.1f GB" % n


def _no_proxy_opener():
    """本地回环和显式 URL 都别走系统代理，否则公司网络下会莫名失败。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _sha256_of(path, progress=None, label=""):
    h = hashlib.sha256()
    total = os.path.getsize(path)
    done = 0
    with open(path, "rb") as f:
        while True:
            buf = f.read(CHUNK * 4)
            if not buf:
                break
            h.update(buf)
            done += len(buf)
            if progress and total:
                progress("hashing", percent=done * 100.0 / total,
                         bytes_done=done, bytes_total=total, label=label)
    return h.hexdigest()


# ---------------------------------------------------------------- 主体


class EngineBootstrap(object):
    """负责把引擎装到本机。线程安全（内部只允许一个安装流程在跑）。"""

    def __init__(self, data_root, manifest_url=None, progress=None,
                 strict_local=False):
        """
        data_root: 数据根目录，默认装到 <data_root>/engine
        manifest_url: 清单地址（http/https/file 均可）；None 时按环境变量或内置文件解析
        progress: 回调 fn(phase, **info)
        strict_local: True 时只认 data_root/engine，不扫全局候选。
                      测试和多实例隔离用；产品默认走候选列表，这样已经
                      装在别处的引擎不会被判成"没装"而重复下载 381MB。
        """
        self.data_root = os.path.abspath(data_root)
        self.engine_dir = os.path.join(self.data_root, "engine")
        self.tmp_dir = os.path.join(self.data_root, ".engine_download")
        self.manifest_url = manifest_url or self._resolve_manifest_url()
        self.progress = progress or (lambda *a, **k: None)
        self.strict_local = bool(strict_local)
        self._lock = threading.Lock()
        self._installing = False

    # ---------------- 清单定位

    def _resolve_manifest_url(self):
        v = (os.environ.get(ENV_MANIFEST) or "").strip()
        if v:
            return v
        p = (os.environ.get(ENV_MANIFEST_FILE) or "").strip()
        candidates = [p] if p else []
        here = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(here, "engine_manifest_url.txt"))
        candidates.append(os.path.join(here, "packaging", "engine_manifest_url.txt"))
        for c in candidates:
            if c and os.path.isfile(c):
                try:
                    with open(c, encoding="utf-8") as f:
                        line = f.read().strip()
                    if line and not line.startswith("#"):
                        return line
                except Exception:
                    pass
        return ""

    def load_manifest(self):
        if not self.manifest_url:
            raise BootstrapError(
                "没有配置引擎下载清单地址。请设置环境变量 %s，"
                "或在程序目录放 packaging/engine_manifest_url.txt。" % ENV_MANIFEST)
        url = self.manifest_url
        try:
            if url.startswith("file://") or os.path.isfile(url):
                path = url[7:] if url.startswith("file://") else url
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            with _no_proxy_opener().open(
                    urllib.request.Request(url, headers={"User-Agent": "dfy-bootstrap"}),
                    timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            raise BootstrapError("读取清单失败（%s）：%s" % (url, e))

    # ---------------- 安装判定

    def _core_files(self):
        """判断引擎是否真的可用，不是只看目录存在。"""
        return [os.path.join(self.engine_dir, r) for r in CORE_RELS]

    def _is_engine_at(self, path):
        return all(os.path.isfile(os.path.join(path, r)) for r in CORE_RELS)

    def candidates(self):
        """和 dfy_engine.engine_candidates() 保持同一套顺序。

        两边必须一致，否则会「装到 A、去 B 找」。这里末尾额外加上
        自身 data_root 下的 engine，保证自定义数据根也能被识别。
        """
        if self.strict_local:
            return [self.engine_dir]
        out = []
        env = (os.environ.get("DFY_ENGINE_DIR") or "").strip()
        if env:
            out.append(os.path.abspath(env))
        here = os.path.dirname(os.path.abspath(__file__))
        out.append(os.path.join(here, "engine"))
        out.append(r"D:\本地部署大肥鱼环境\engine")
        out.append(os.path.join(self.data_root, "engine"))
        # 去重但保持顺序
        seen, uniq = set(), []
        for c in out:
            k = os.path.normcase(os.path.abspath(c))
            if k not in seen:
                seen.add(k)
                uniq.append(c)
        return uniq

    def find_installed(self):
        """返回实际已装引擎的目录；都没有返回空串。"""
        for c in self.candidates():
            if self._is_engine_at(c):
                return c
        return ""

    def is_installed(self):
        return bool(self.find_installed())

    def installed_version(self):
        """从 runtime.json 读版本，读不到返回空串。"""
        base = self.find_installed() or self.engine_dir
        p = os.path.join(base, "resources", "runtime",
                         "primary-runtime", "runtime.json")
        try:
            with open(p, encoding="utf-8") as f:
                return str(json.load(f).get("desktopVersion") or "")
        except Exception:
            return ""

    # ---------------- 主流程

    def _resolve_part_url(self, url):
        """清单里的 part url 可能是相对路径，要按清单地址解析成可下载的地址。

        这样分发方只需改 manifest 的位置，part 文件跟着走，不用改内容。
        """
        if re.match(r"(https?|file)://", url):
            return url
        m = self.manifest_url or ""
        if m.startswith(("http://", "https://", "file://")):
            return urljoin(m, url)
        # 清单本身是本地路径
        return os.path.join(os.path.dirname(os.path.abspath(m)), url)

    def ensure(self, force=False):
        if not self._lock.acquire(blocking=False):
            raise BootstrapError("已有一个安装流程在进行中")
        self._installing = True
        try:
            if self.is_installed() and not force:
                v = self.installed_version()
                where = self.find_installed() or self.engine_dir
                self.progress("done", ok=True, already=True, version=v,
                              engine=where)
                return {"ok": True, "already": True, "version": v,
                        "engine": where}

            # 强制重装时，装回原来的位置，不要在同一台机器上留两份引擎
            if force:
                got = self.find_installed()
                if got:
                    self.engine_dir = got
                    _log("强制重装，目标沿用已装位置：%s" % got)

            self.progress("checking")
            man = self.load_manifest()
            version = str(man.get("version") or "")
            total = int(man.get("total_bytes") or 0)
            parts = man.get("parts") or []
            if not parts:
                raise BootstrapError("清单里没有 parts")

            # 磁盘空间（先确保数据根存在，否则 disk_usage 会抛 FileNotFoundError）
            os.makedirs(self.data_root, exist_ok=True)
            need = int(man.get("min_disk_bytes") or (total * 3 + 200 * 1024 * 1024))
            try:
                free = shutil.disk_usage(self.data_root).free
            except Exception as e:
                raise BootstrapError("无法读取磁盘剩余空间（%s）：%s"
                                     % (self.data_root, e))
            if free < need:
                raise BootstrapError(
                    "磁盘空间不足：需要 %s，可用 %s（目录 %s）"
                    % (_fmt_size(need), _fmt_size(free), self.data_root))

            os.makedirs(self.tmp_dir, exist_ok=True)

            # 1) 下分卷
            part_paths = []
            for i, part in enumerate(parts, 1):
                url = part.get("url")
                if not url:
                    raise BootstrapError("第 %d 个分卷没有 url" % i)
                url = self._resolve_part_url(url)
                dest = os.path.join(self.tmp_dir, "part%02d" % i)
                self._download_part(url, dest, int(part.get("bytes") or 0),
                                    i, len(parts), total)
                part_paths.append(dest)

            # 2) 合并
            merged = os.path.join(self.tmp_dir,
                                  "engine." + str(man.get("archive") or "tgz"))
            self._merge(part_paths, merged)

            # 3) 校验整包
            expect = str(man.get("sha256") or "").lower()
            if expect:
                got = _sha256_of(merged, self.progress, "整包")
                if got != expect:
                    raise BootstrapError(
                        "整包校验失败：期望 %s… 实际 %s…" % (expect[:16], got[:16]))
                _log("整包 SHA256 校验通过")
            else:
                _log("清单未提供 sha256，跳过整包校验")

            # 4) 解压
            self.progress("extracting", percent=0, label="解压引擎")
            self._extract(merged, self.data_root)

            # 5) 校验安装
            self.progress("verifying")
            missing = [p for p in self._core_files() if not os.path.isfile(p)]
            if missing:
                raise BootstrapError("解压后引擎不完整，缺失：" + ", ".join(missing))

            # 6) 清理
            for p in part_paths + [merged]:
                try:
                    os.remove(p)
                except Exception:
                    pass
            try:
                shutil.rmtree(self.tmp_dir, ignore_errors=True)
            except Exception:
                pass

            v = self.installed_version()
            self.progress("done", ok=True, version=v, engine=self.engine_dir)
            return {"ok": True, "already": False, "version": v,
                    "engine": self.engine_dir}
        except Exception as e:
            self.progress("error", error=str(e))
            raise
        finally:
            self._installing = False
            self._lock.release()

    def ensure_async(self, force=False, on_done=None):
        """后台线程装，主程序不阻塞。"""
        def run():
            try:
                r = self.ensure(force=force)
                if on_done:
                    on_done(True, r)
            except Exception as e:
                if on_done:
                    on_done(False, {"ok": False, "error": str(e)})

        t = threading.Thread(target=run, name="engine-bootstrap", daemon=True)
        t.start()
        return t

    # ---------------- 子步骤

    def _download_part(self, url, dest, expect_bytes, idx, nparts, total):
        """带断点续传与重试的单卷下载。"""
        part_label = "分卷 %d/%d" % (idx, nparts)

        def report(bytes_done):
            self.progress("downloading", percent=bytes_done * 100.0 / total if total else 0,
                          bytes_done=bytes_done, bytes_total=total,
                          part=idx, parts=nparts, label=part_label)

        for attempt in range(1, RETRIES + 1):
            try:
                have = os.path.getsize(dest) if os.path.isfile(dest) else 0
                if expect_bytes and have >= expect_bytes:
                    _log("%s 已完整（%s）" % (part_label, _fmt_size(have)))
                    # 汇总所有已下载分卷上报一次，避免进度条回退
                    agg = 0
                    for j in range(1, nparts + 1):
                        pj = os.path.join(self.tmp_dir, "part%02d" % j)
                        if os.path.isfile(pj):
                            agg += os.path.getsize(pj)
                    report(agg)
                    return

                req = urllib.request.Request(url, headers={"User-Agent": "dfy-bootstrap"})
                if have:
                    req.add_header("Range", "bytes=%d-" % have)
                    _log("%s 续传，从 %s 开始" % (part_label, _fmt_size(have)))

                opener = _no_proxy_opener()
                with opener.open(req, timeout=TIMEOUT) as r:
                    if have and r.status == 206:
                        mode = "ab"
                    elif have and r.status == 200:
                        # 服务器不支持 Range，从头来
                        mode = "wb"
                        have = 0
                    else:
                        mode = "ab" if have else "wb"
                    with open(dest, mode) as f:
                        while True:
                            buf = r.read(CHUNK)
                            if not buf:
                                break
                            f.write(buf)
                            have += len(buf)
                            report(have)

                got = os.path.getsize(dest)
                if expect_bytes and got != expect_bytes:
                    raise BootstrapError(
                        "%s 大小不符：期望 %s 实际 %s"
                        % (part_label, _fmt_size(expect_bytes), _fmt_size(got)))
                _log("%s 下载完成（%s）" % (part_label, _fmt_size(got)))
                return
            except Exception as e:
                _log("%s 第 %d/%d 次失败：%s" % (part_label, attempt, RETRIES, e))
                if attempt == RETRIES:
                    raise BootstrapError("%s 下载失败：%s" % (part_label, e))
                time.sleep(2 ** attempt)      # 2s, 4s, 8s

    def _merge(self, parts, dest):
        if os.path.isfile(dest):
            os.remove(dest)
        total = sum(os.path.getsize(p) for p in parts)
        done = 0
        with open(dest, "wb") as out:
            for p in parts:
                with open(p, "rb") as f:
                    while True:
                        buf = f.read(CHUNK * 4)
                        if not buf:
                            break
                        out.write(buf)
                        done += len(buf)
                        self.progress("extracting", percent=done * 100.0 / total,
                                      bytes_done=done, bytes_total=total,
                                      label="合并分卷")
        _log("合并完成：%s" % _fmt_size(total))

    def _extract(self, archive, dest):
        """解压到 dest。会先清理 dest/engine，避免和旧残件混在一起。"""
        os.makedirs(dest, exist_ok=True)
        tmp_x = os.path.join(self.tmp_dir, "extract")
        if os.path.isdir(tmp_x):
            shutil.rmtree(tmp_x, ignore_errors=True)
        os.makedirs(tmp_x, exist_ok=True)

        if archive.lower().endswith((".tgz", ".tar.gz", ".tar")):
            with tarfile.open(archive, "r:*") as t:
                members = t.getmembers()
                total = max(1, len(members))
                # 安全检查：拒绝绝对路径与 ../ 越界
                for m in members:
                    name = m.name.replace("\\", "/")
                    if name.startswith("/") or ".." in name.split("/"):
                        raise BootstrapError("压缩包内含越界路径：" + m.name)
                for i, m in enumerate(members, 1):
                    t.extract(m, tmp_x)
                    if i % 500 == 0 or i == total:
                        self.progress("extracting", percent=i * 100.0 / total,
                                      label="解压 %d/%d" % (i, total))
        elif archive.lower().endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                names = z.namelist()
                total = max(1, len(names))
                for i, n in enumerate(names, 1):
                    nm = n.replace("\\", "/")
                    if nm.startswith("/") or ".." in nm.split("/"):
                        raise BootstrapError("压缩包内含越界路径：" + n)
                for i, n in enumerate(names, 1):
                    z.extract(n, tmp_x)
                    if i % 500 == 0 or i == total:
                        self.progress("extracting", percent=i * 100.0 / total,
                                      label="解压 %d/%d" % (i, total))
        else:
            raise BootstrapError("不支持的压缩格式：" + archive)

        src_engine = os.path.join(tmp_x, "engine")
        if not os.path.isdir(src_engine):
            # 兼容包内没有顶层 engine 目录的情况：找唯一含 exe 的目录
            hit = None
            for root, dirs, files in os.walk(tmp_x):
                if "DaFeiyuEngine.exe" in files:
                    hit = root
                    break
            if not hit:
                raise BootstrapError("压缩包里找不到引擎目录")
            src_engine = hit

        # 原子替换：先把旧引擎挪开，再移入新的，成功后删旧
        old = self.engine_dir + ".old"
        if os.path.isdir(old):
            shutil.rmtree(old, ignore_errors=True)
        if os.path.isdir(self.engine_dir):
            try:
                os.rename(self.engine_dir, old)
            except Exception:
                shutil.rmtree(self.engine_dir, ignore_errors=True)
        try:
            shutil.move(src_engine, self.engine_dir)
        except Exception as e:
            if os.path.isdir(old) and not os.path.isdir(self.engine_dir):
                try:
                    os.rename(old, self.engine_dir)
                except Exception:
                    pass
            raise BootstrapError("安装引擎目录失败：%s" % e)
        if os.path.isdir(old):
            shutil.rmtree(old, ignore_errors=True)
        shutil.rmtree(tmp_x, ignore_errors=True)
        _log("引擎已安装到 %s" % self.engine_dir)


# ---------------------------------------------------------------- 便捷入口


def default_data_root():
    """与 dfy_engine / dfy_market 保持同一套数据根解析。"""
    v = (os.environ.get("DFY_DATA_DIR") or "").strip()
    if v:
        return v
    legacy = r"D:\本地部署大肥鱼环境\data"
    if os.path.isdir(legacy):
        return legacy
    appdata = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"),
                                                        "AppData", "Roaming")
    return os.path.join(appdata, "大肥鱼AI", "data")


def is_engine_installed(data_root=None, engine_dir=None):
    """轻量判断，给启动流程和 UI 用，不触发任何下载。"""
    if engine_dir:
        b = EngineBootstrap(os.path.dirname(os.path.abspath(engine_dir)))
        b.engine_dir = os.path.abspath(engine_dir)
        return b.is_installed()
    return EngineBootstrap(data_root or default_data_root()).is_installed()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        print("引擎已安装:", is_engine_installed())
        if is_engine_installed():
            print("版本:", EngineBootstrap(default_data_root()).installed_version())
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "install":
        def _p(ph, **kw):
            detail = kw.get("label") or ""
            if "percent" in kw and kw["percent"] is not None:
                detail = (detail + " " if detail else "") + "%.1f%%" % kw["percent"]
            print("  %-12s %s" % (ph, detail))

        bp = EngineBootstrap(default_data_root(), progress=_p)
        print(json.dumps(bp.ensure(), ensure_ascii=False, indent=2))
        sys.exit(0)
    print("用法: python dfy_engine_bootstrap.py {check|install}")
