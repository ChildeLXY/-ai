#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把本地站点目录推到阿里云 OSS。

用法:
    python tools/publish.py --site home            # 上传门户 -> dabaifan-main
    python tools/publish.py --site ai              # 上传 AI 站 -> dabaifan-ai
    python tools/publish.py --site all             # 两个都传
    python tools/publish.py --site home --dry-run   # 只列出要传什么，不真传
    python tools/publish.py --site home --force     # 忽略本地缓存，全部重传

配置: deploy/oss_config.json（首次运行会生成模板）
依赖: oss2（已装在 D:/大肥鱼AI/.tools）
"""

import argparse
import json
import mimetypes
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(ROOT, "deploy", "oss_config.json")

SITES = {
    "home": {"dir": "site-home", "bucket_key": "home", "label": "门户页"},
    "ai": {"dir": "dist-site", "bucket_key": "ai", "label": "AI 站"},
    "game": {"dir": "site-game", "bucket_key": "game", "label": "游戏站"},
}

# 文件 -> (Content-Type, Cache-Control)
# index/404 不缓存，保证改完立刻生效；带指纹的静态资源长缓存
TYPE_MAP = {
    ".html": ("text/html; charset=utf-8", "no-cache, must-revalidate"),
    ".txt": ("text/plain; charset=utf-8", "public, max-age=3600"),
    ".webp": ("image/webp", "public, max-age=604800"),
    ".png": ("image/png", "public, max-age=604800"),
    ".jpg": ("image/jpeg", "public, max-age=604800"),
    ".jpeg": ("image/jpeg", "public, max-age=604800"),
    ".ico": ("image/x-icon", "public, max-age=604800"),
    ".svg": ("image/svg+xml", "public, max-age=604800"),
    ".css": ("text/css; charset=utf-8", "public, max-age=604800"),
    ".js": ("application/javascript; charset=utf-8", "public, max-age=604800"),
    ".exe": ("application/octet-stream", "public, max-age=3600"),
    ".zip": ("application/zip", "public, max-age=3600"),
}

CONF_TEMPLATE = {
    "access_key_id": "",
    "access_key_secret": "",
    "endpoint": "oss-cn-hangzhou.aliyuncs.com",
    "region": "cn-hangzhou",
    "buckets": {
        "home": "dabaifan-main",
        "ai": "dabaifan-ai",
        "game": "dabaifan-game",
    },
}


def load_conf(allow_empty=False):
    if not os.path.exists(CONF):
        os.makedirs(os.path.dirname(CONF), exist_ok=True)
        with open(CONF, "w", encoding="utf-8") as f:
            json.dump(CONF_TEMPLATE, f, ensure_ascii=False, indent=2)
        if allow_empty:
            return dict(CONF_TEMPLATE)
        sys.exit(
            "已生成配置模板：deploy/oss_config.json\n"
            "请把 access_key_id / access_key_secret 填进去（建议用只授权 OSS 的 RAM 子账号），然后重新运行。"
        )
    with open(CONF, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if not allow_empty and (not cfg.get("access_key_id") or not cfg.get("access_key_secret")):
        sys.exit("deploy/oss_config.json 里的 AK 还是空的，填完再跑。")
    return cfg


def collect(local_dir):
    """返回 [(绝对路径, OSS 对象名)]，跳过隐藏文件与临时文件。"""
    items = []
    for base, dirs, files in os.walk(local_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for name in files:
            if name.startswith(".") or name.endswith((".pyc", ".tmp", ".bak")):
                continue
            full = os.path.join(base, name)
            key = os.path.relpath(full, local_dir).replace(os.sep, "/")
            items.append((full, key))
    items.sort(key=lambda x: x[1])
    return items


def set_website(bucket, dry_run):
    """开启静态网站托管：首页 index.html、404 页 404.html。"""
    import oss2

    if dry_run:
        print("  [dry] 将开启静态网站托管（index.html / 404.html）")
        return
    try:
        bucket.put_bucket_website(oss2.models.BucketWebsite("index.html", "404.html"))
        print("  静态网站托管已开启（首页 index.html / 404 页 404.html）")
    except Exception as e:
        print(f"  ! 静态托管设置失败（可稍后在控制台手动开）：{e}")


def upload(cfg, site_key, force, dry_run):
    import oss2

    site = SITES[site_key]
    local_dir = os.path.join(ROOT, site["dir"])
    if not os.path.isdir(local_dir):
        print(f"跳过 {site['label']}：目录不存在 {local_dir}")
        return

    bucket_name = cfg["buckets"][site["bucket_key"]]
    endpoint = cfg["endpoint"]
    print(f"\n=== {site['label']} -> oss://{bucket_name} ===")

    bucket = None
    if not dry_run:
        bucket = oss2.Bucket(oss2.Auth(cfg["access_key_id"], cfg["access_key_secret"]), endpoint, bucket_name)
        try:
            bucket.get_bucket_info()
        except oss2.exceptions.NoSuchBucket:
            bucket.create_bucket(
                oss2.BUCKET_ACL_PUBLIC_READ,
                headers={
                    "x-oss-storage-class": oss2.BUCKET_STORAGE_CLASS_STANDARD,
                    "x-oss-data-redundancy": oss2.BUCKET_DATA_REDUNDANCY_TYPE_LRS,
                },
            )
            print(f"  已创建 Bucket {bucket_name}（标准存储 / 本地冗余 / 公共读）")
        set_website(bucket, False)

    files = collect(local_dir)
    total = sum(os.path.getsize(f) for f, _ in files)
    print(f"  共 {len(files)} 个文件，{total / 1024:.1f} KB")

    sent = skipped = 0
    for full, key in files:
        size = os.path.getsize(full)
        ext = os.path.splitext(key)[1].lower()
        ctype, cache = TYPE_MAP.get(ext, (mimetypes.guess_type(key)[0] or "application/octet-stream", "public, max-age=3600"))
        headers = {"Content-Type": ctype, "Cache-Control": cache}

        if bucket and not force:
            try:
                meta = bucket.head_object(key)
                if meta.content_length == size:
                    skipped += 1
                    continue
            except Exception:
                pass

        if dry_run:
            print(f"  [dry] {key}  {size} B  {ctype}")
            sent += 1
            continue

        with open(full, "rb") as fh:
            bucket.put_object(key, fh, headers=headers)
        print(f"  ✓ {key}  {size} B")
        sent += 1

    print(f"  完成：上传 {sent}，跳过未变 {skipped}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", choices=["home", "ai", "game", "all"], default="all")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    cfg = load_conf(allow_empty=args.dry_run)
    targets = ["home", "ai", "game"] if args.site == "all" else [args.site]
    if args.dry_run:
        print("DRY RUN：只列清单，不碰云端\n")

    for key in targets:
        upload(cfg, key, args.force, args.dry_run)

    print("\n全部结束。接下来：绑自定义域名 -> 加 CDN 加速域名 -> 改 DNS。")


if __name__ == "__main__":
    main()
