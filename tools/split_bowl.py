# -*- coding: utf-8 -*-
"""
把抠好透明背景的「鱼在碗里」拆成上下两层，让鲸鱼能在碗里上下浮动：
  assets/bowl-whale.webp  碗沿以上的鲸鱼（含气泡与虚线弧），底部做「拉丝」延伸，
                          藏在碗沿下面，浮动时不会露出缝隙
  assets/bowl-cup.webp    碗沿以下的碗身（不透明，压在鲸鱼上层）

用法:
    set PYTHONPATH=D:\\大肥鱼AI\\.tools
    python tools\\cutout_bowl.py && python tools\\split_bowl.py
"""
import os

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "design-source", "bowl-cut.png")
ASSETS = os.path.join(ROOT, "site-home", "assets")

SMUDGE = 18   # 向下拉丝多少像素
TW = 520      # 输出宽度


def content_bbox(img):
    a = np.array(img)
    ys, xs = np.where(a[:, :, 3] > 6)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def find_rim(img):
    """找碗口那条横线的 y：从画面中间的列往下扫，第一片深色就是碗沿"""
    a = np.array(img.convert("RGB"))
    h, w, _ = a.shape
    x = w // 2
    for y in range(int(h * 0.3), int(h * 0.75)):
        r, g, b = a[y, x]
        if r < 90 and g < 95 and b < 105:
            return y
    return int(h * 0.5)


def main():
    im = Image.open(SRC).convert("RGBA")
    w, h = im.size
    cx0, cy0, cx1, cy1 = content_bbox(im)
    print("内容范围:", (cx0, cy0, cx1, cy1), "画布:", im.size)

    # 裁到内容（左右留 4px 余量）
    im = im.crop((max(0, cx0 - 4), max(0, cy0 - 4),
                  min(w, cx1 + 5), min(h, cy1 + 5)))
    w, h = im.size
    rim = find_rim(im)
    print("碗沿 y =", rim, "/", h)

    # 碗（含碗沿以下）
    cup = im.crop((0, rim, w, h))

    # 鲸鱼层：碗沿以上 + 底部拉丝（藏在碗沿下，浮动时不露缝）
    head = im.crop((0, 0, w, rim))
    arr = np.array(head)
    tail = arr[-SMUDGE:].copy()
    whale = Image.fromarray(np.concatenate([arr, tail], axis=0), "RGBA")

    def dump(img, name):
        tw = TW
        th = round(img.height * tw / img.width)
        out = img.resize((tw, th), Image.LANCZOS)
        path = os.path.join(ASSETS, name)
        out.save(path, "WEBP", quality=90, method=6)
        print(f"  {name}: {out.size} {os.path.getsize(path)/1024:.0f} KB")
        return out

    dump(whale, "bowl-whale.webp")
    dump(cup, "bowl-cup.webp")

    # 叠一张预览
    base = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    base.alpha_composite(whale, (0, 0))
    base.alpha_composite(cup, (0, rim))
    base.convert("RGB").resize((w // 2, h // 2), Image.LANCZOS).save(
        os.path.join(ROOT, "design-source", "bowl-split-preview.png"))

    print("容器宽高比 = 1 / %.4f" % (h / w))


if __name__ == "__main__":
    main()
