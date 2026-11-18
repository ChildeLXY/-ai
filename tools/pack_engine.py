# -*- coding: utf-8 -*-
"""把引擎目录打包成可分发的压缩包（可选分卷），并生成下载清单。

用法：
    # 单文件（推荐，配合 OSS / 支持大文件的源）
    python pack_engine.py --engine "D:\\本地部署大肥鱼环境\\engine" --out D:\\dist

    # 分卷（绕开 100MB 单文件限制，如 Gitee Release）
    python pack_engine.py --engine "D:\\本地部署大肥鱼环境\\engine" --out D:\\dist --part-mb 95

    # 指定下载前缀（写进 manifest 的 URL）
    python pack_engine.py ... --base-url https://your-cdn.com/engine/

产出：
    dafeiyu-engine-<version>.tgz            单文件模式
    dafeiyu-engine-<version>.tgz.partNN     分卷模式（NN 从 01 起）
    manifest.json                           下载清单（含总大小 / SHA256 / 分卷 URL）

manifest.json 里的 sha256 是针对「合并后的整包」算的，下载器合并后校验。
每个分卷另外记了 sha256，用于单卷完整性校验（可选）。
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1024 * 512)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def read_version(engine_dir):
    p = os.path.join(engine_dir, "resources", "runtime",
                     "primary-runtime", "runtime.json")
    try:
        with open(p, encoding="utf-8") as f:
            return str(json.load(f).get("desktopVersion") or "unknown")
    except Exception:
        return "unknown"


def find_tar():
    for p in (r"C:\Windows\system32\tar.exe", shutil.which("tar") or ""):
        if p and os.path.isfile(p):
            return p
    return ""


def pack(engine_dir, out_dir, part_mb, base_url, archive="tgz"):
    eng = os.path.abspath(engine_dir)
    if not os.path.isfile(os.path.join(eng, "DaFeiyuEngine.exe")):
        raise SystemExit("引擎目录不对：找不到 DaFeiyuEngine.exe（%s）" % eng)
    out = os.path.abspath(out_dir)
    os.makedirs(out, exist_ok=True)
    version = read_version(eng)
    name = "dafeiyu-engine-%s.%s" % (version, archive)
    target = os.path.join(out, name)

    tar = find_tar()
    if not tar:
        raise SystemExit("找不到 tar，无法打包")

    print("打包 %s -> %s" % (eng, target))
    t0 = time.time()
    # -C 指向 engine 的父目录，包内顶层就是 engine/
    parent = os.path.dirname(eng)
    leaf = os.path.basename(eng)
    rc = subprocess.call([tar, "-czf", target, "-C", parent, leaf])
    if rc != 0:
        raise SystemExit("tar 打包失败，退出码 %d" % rc)
    size = os.path.getsize(target)
    print("打包完成 %.1f MB，用时 %.1fs" % (size / 1048576.0, time.time() - t0))

    print("计算 SHA256…")
    digest = sha256_file(target)
    print("SHA256 = %s" % digest)

    parts = []
    part_paths = []
    if part_mb and part_mb > 0:
        chunk = int(part_mb * 1024 * 1024)
        n = 0
        with open(target, "rb") as f:
            while True:
                buf = f.read(chunk)
                if not buf:
                    break
                n += 1
                pp = "%s.part%02d" % (target, n)
                with open(pp, "wb") as g:
                    g.write(buf)
                part_paths.append(pp)
                parts.append({
                    "file": os.path.basename(pp),
                    "url": (base_url.rstrip("/") + "/" + os.path.basename(pp)) if base_url else os.path.basename(pp),
                    "bytes": len(buf),
                    "sha256": hashlib.sha256(buf).hexdigest(),
                })
                print("  分卷 %02d: %.1f MB" % (n, len(buf) / 1048576.0))
        # 分卷完成后删掉整包，避免本地重复占空间（需要时用 --keep-whole 保留）
        print("已切分为 %d 卷" % n)
    else:
        parts.append({
            "file": name,
            "url": (base_url.rstrip("/") + "/" + name) if base_url else name,
            "bytes": size,
            "sha256": digest,
        })

    manifest = {
        "version": version,
        "archive": archive,
        "total_bytes": size,
        "sha256": digest,
        "min_disk_bytes": size * 3 + 300 * 1024 * 1024,
        "parts": parts,
    }
    mp = os.path.join(out, "manifest.json")
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("清单已写入 %s" % mp)
    print(json.dumps({k: manifest[k] for k in ("version", "total_bytes", "sha256")},
                     ensure_ascii=False, indent=2))
    print("分卷数: %d" % len(parts))
    return manifest, target, part_paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True, help="引擎目录")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--part-mb", type=int, default=0, help="分卷大小 MB；0 表示不分卷")
    ap.add_argument("--base-url", default="", help="写进清单的下载前缀 URL")
    ap.add_argument("--archive", default="tgz", choices=["tgz"], help="压缩格式")
    ap.add_argument("--keep-whole", action="store_true", help="分卷后保留整包")
    a = ap.parse_args()
    man, target, parts = pack(a.engine, a.out, a.part_mb, a.base_url, a.archive)
    if parts and not a.keep_whole:
        try:
            os.remove(target)
            print("已删除整包 %s（分卷模式）" % target)
        except Exception as e:
            print("删除整包失败（不影响分发）：%s" % e)


if __name__ == "__main__":
    sys.exit(main())
