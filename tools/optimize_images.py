# -*- coding: utf-8 -*-
"""
大肥鱼AI 站点图片优化
把 PNG/JPG 转成 WebP，按页面实际显示尺寸裁剪分辨率，并单独产出 og 分享图。

用法：
    set PYTHONPATH=D:\\大肥鱼AI\\.tools
    python tools\\optimize_images.py
"""
import io
import os
import sys

from PIL import Image

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "dist-site", "assets")

# (源文件, 输出文件, 目标宽, quality, 说明)
JOBS = [
    # 首屏立绘：页面 min(92%,460px)，2x 屏需 920px，原图 910 刚好，不缩
    ("char.png",          "char.webp",          910,  88, "首屏立绘 460px@2x"),
    # 首屏小卡 .fc1：显示 186px，2x 需 372px
    ("shot-sticker.png",  "shot-sticker-sm.webp", 372, 80, "首屏小卡 186px@2x"),
    # 正文大图 .shot：容器最宽约 1100px，2x 需 2200，压到 1600 折中
    ("shot-sticker.png",  "shot-sticker.webp",   1600, 82, "正文大图 1100px@1.5x"),
    # 小卡 .fc2：显示 150px，2x 需 300px，给 480 留 3x 余量
    ("ciallo.jpg",        "ciallo.webp",         480,  82, "小卡 150px@3x"),
]

# 社交分享图单独出 JPG：微信/QQ 卡片对 WebP 支持不稳
OG = ("char.png", "og.jpg", 85, "og:image 分享图")


def human(n):
    return f"{n/1024:.0f} KB" if n < 1024 * 1024 else f"{n/1048576:.2f} MB"


def resize_to_width(im, target_w):
    if im.width <= target_w:
        return im
    h = round(im.height * target_w / im.width)
    return im.resize((target_w, h), Image.LANCZOS)


def main():
    total_before = 0
    total_after = 0
    rows = []

    for src_name, out_name, target_w, quality, note in JOBS:
        src_path = os.path.join(SRC, src_name)
        if not os.path.exists(src_path):
            print(f"[跳过] 找不到 {src_name}")
            continue
        before = os.path.getsize(src_path)
        im = Image.open(src_path)
        if im.mode in ("P", "LA"):
            im = im.convert("RGBA")
        im2 = resize_to_width(im, target_w)

        kwargs = dict(format="WEBP", quality=quality, method=6)
        if im2.mode == "RGBA":
            kwargs["alpha_quality"] = 100
            kwargs["exact"] = True  # 保留透明像素的原始 RGB，避免边缘发黑

        out_path = os.path.join(SRC, out_name)
        im2.save(out_path, **kwargs)
        after = os.path.getsize(out_path)

        total_before += before
        total_after += after
        rows.append((out_name, f"{im2.width}x{im2.height}", human(before), human(after),
                     f"-{100 - after * 100 // before}%", note))

    # og 分享图
    src_name, out_name, quality, note = OG
    src_path = os.path.join(SRC, src_name)
    if os.path.exists(src_path):
        im = Image.open(src_path)
        if im.mode != "RGB":
            bg = Image.new("RGB", im.size, (10, 14, 24))
            bg.paste(im, mask=im.split()[-1] if im.mode == "RGBA" else None)
            im = bg
        out_path = os.path.join(SRC, out_name)
        im.save(out_path, format="JPEG", quality=quality, optimize=True, progressive=True)
        rows.append((out_name, f"{im.width}x{im.height}", human(os.path.getsize(src_path)),
                     human(os.path.getsize(out_path)), "-", note))

    print(f"{'文件':24s}{'尺寸':12s}{'原始':>9s}{'压缩后':>9s}{'降幅':>7s}  用途")
    print("-" * 88)
    for r in rows:
        print(f"{r[0]:24s}{r[1]:12s}{r[2]:>9s}{r[3]:>9s}{r[4]:>7s}  {r[5]}")
    print("-" * 88)
    print(f"三张主图合计：{human(total_before)} → {human(total_after)}"
          f"（省 {100 - total_after * 100 // total_before}%）")


if __name__ == "__main__":
    main()
