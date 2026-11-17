# -*- coding: utf-8 -*-
"""
把 AI 生成的「鲸鱼戏水盆」插画处理成主站用的两层资源：
  assets/bowl-whale.webp  水面以上的鲸鱼（含气泡与点状弧线），底部做「拉丝」延伸，
                          藏在水盆前沿下面，浮动时不会露出缝隙
  assets/bowl-cup.webp    水面以下的浅蓝水盆（不透明，压在鲸鱼上层）

用法:
    set PYTHONPATH=D:\\大肥鱼AI\\.tools
    python tools\\make_bowl.py
"""
import os

import cv2
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "design-source", "ai-gen",
                   "扁平矢量图标_透明背景_纯白背景_不要棋盘格__只用两种颜色_2026-11-12T16-14-47.png")
ASSETS = os.path.join(ROOT, "site-home", "assets")

SMUDGE = 20   # 向下拉丝像素
TW = 520      # 输出宽度


def cutout(im):
    """把纯白背景抠成透明：从四边泛洪，连不到画布外的白一律保留（保护白肚皮）"""
    a = np.array(im.convert("RGB"))
    white = a.min(axis=2) >= 246
    h, w = white.shape
    ff = white.astype(np.uint8) * 255
    for seed in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        if white[seed]:
            cv2.floodFill(ff, np.zeros((h + 2, w + 2), np.uint8), seed, 128)
    bg = ff == 128
    alpha = np.where(bg, 0, 255).astype(np.uint8)
    soft = cv2.GaussianBlur(alpha.astype(np.float32) / 255.0, (0, 0), 0.7)
    alpha = np.clip(soft * 255, 0, 255).astype(np.uint8)
    out = np.dstack([a, alpha])
    return Image.fromarray(out, "RGBA")


def trim(im, pad=4):
    a = np.array(im)
    ys, xs = np.where(a[:, :, 3] > 6)
    return im.crop((max(0, xs.min() - pad), max(0, ys.min() - pad),
                    min(im.width, xs.max() + pad + 1), min(im.height, ys.max() + pad + 1)))


def find_waterline(im):
    """水面那条横线：在画面中段找「蓝色像素最多」的一行"""
    a = np.array(im.convert("RGB")).astype(np.int16)
    h, w, _ = a.shape
    d = np.abs(a - np.array([47, 92, 255])).sum(axis=2)
    blue = (d < 150)
    mid = blue[:, int(w * 0.28):int(w * 0.72)]
    counts = mid.sum(axis=1)
    lo, hi = int(h * 0.35), int(h * 0.75)
    y = int(np.argmax(counts[lo:hi])) + lo
    print("水面线 y =", y, "该行蓝色像素", int(counts[y]), "/", mid.shape[1])
    return y


def main():
    im = Image.open(SRC).convert("RGB")
    im = cutout(im)
    im = trim(im)
    w, h = im.size
    print("裁切后:", im.size)

    line = find_waterline(im)

    # 水盆层：从水面线往下（不透明，压在鲸鱼上面）
    cup = im.crop((0, line, w, h))

    # 鲸鱼层：水面以上 + 底部拉丝
    head = im.crop((0, 0, w, line))
    arr = np.array(head)
    whale = Image.fromarray(np.concatenate([arr, arr[-SMUDGE:].copy()], axis=0), "RGBA")

    def dump(img, name):
        tw = TW
        th = round(img.height * tw / img.width)
        out = img.resize((tw, th), Image.LANCZOS)
        path = os.path.join(ASSETS, name)
        out.save(path, "WEBP", quality=90, method=6)
        print(f"  {name}: {out.size} {os.path.getsize(path)/1024:.0f} KB")
        return out

    dw = dump(whale, "bowl-whale.webp")
    dc = dump(cup, "bowl-cup.webp")

    # 预览（按两层的实际缩放比例叠）
    base = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    base.alpha_composite(whale, (0, 0))
    base.alpha_composite(cup, (0, line))
    base.convert("RGB").resize((w // 2, h // 2), Image.LANCZOS).save(
        os.path.join(ROOT, "design-source", "bowl-split-preview.png"))

    print("鲸鱼层 %dx%d  盆层 %dx%d" % (dw.size[0], dw.size[1], dc.size[0], dc.size[1]))
    print("容器宽高比 = %d / %d" % (dw.size[0], dw.size[1]))


if __name__ == "__main__":
    main()
