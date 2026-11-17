# -*- coding: utf-8 -*-
"""
把 AI 生成的「鱼在碗里」插画抠成透明背景、裁掉空白、压缩成 WebP。

用法:
    set PYTHONPATH=D:\\大肥鱼AI\\.tools
    python tools\\cutout_bowl.py
"""
import os

import cv2
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "design-source", "ai-gen",
                   "扁平矢量插画_flat_vector_illustratio_2026-11-12T16-09-05.png")
OUT = os.path.join(ROOT, "site-home", "assets", "bowl.webp")


def main():
    im = Image.open(SRC).convert("RGB")
    a = np.array(im)

    # 1. 找出接近纯白的像素
    white = (a.min(axis=2) >= 246)

    # 2. 从四边泛洪，只把「连到画布外」的白色当背景，避免误伤鲸鱼白肚皮
    h, w = white.shape
    flood = np.zeros((h + 2, w + 2), np.uint8)
    ff = (white.astype(np.uint8) * 255)
    m = np.zeros((h, w), np.uint8)
    for seed in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        if white[seed]:
            cv2.floodFill(ff, flood, seed, 128)
    bg = (ff == 128)

    # 3. 生成 alpha，并对边缘做 1px 羽化
    alpha = np.where(bg, 0, 255).astype(np.uint8)
    soft = cv2.GaussianBlur(alpha.astype(np.float32) / 255.0, (0, 0), 0.7)
    alpha = np.clip(soft * 255, 0, 255).astype(np.uint8)

    out = np.dstack([a, alpha])
    img = Image.fromarray(out, "RGBA")

    # 4. 裁到内容边界（留 6px 余量）
    arr = np.array(img)
    ys, xs = np.where(arr[:, :, 3] > 6)
    pad = 6
    box = (max(0, xs.min() - pad), max(0, ys.min() - pad),
           min(w, xs.max() + pad + 1), min(h, ys.max() + pad + 1))
    img = img.crop(box)
    print("裁切后:", img.size)

    # 5. 缩到 520 宽并存 WebP
    tw = 520
    th = round(img.height * tw / img.width)
    img = img.resize((tw, th), Image.LANCZOS)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    img.save(OUT, "WEBP", quality=90, method=6)
    print("输出:", OUT, img.size, f"{os.path.getsize(OUT)/1024:.0f} KB")

    # 留一张带 alpha 的 PNG，给 split_bowl.py 用
    img.save(os.path.join(ROOT, "design-source", "bowl-cut.png"))

    # 留一张预览
    img.resize((tw // 2, th // 2), Image.LANCZOS).convert("RGB").save(
        os.path.join(ROOT, "design-source", "bowl-ai-preview.png"))


if __name__ == "__main__":
    main()
