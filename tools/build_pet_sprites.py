# -*- coding: utf-8 -*-
"""
把 petpack 的动作帧合成横向精灵图（sprite sheet），供网页用 CSS steps() 播放。

用法:
    set PYTHONPATH=D:\\大肥鱼AI\\.tools
    python tools\\build_pet_sprites.py
    python tools\\build_pet_sprites.py r75 r17 r86
"""
import os
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(ROOT, "downloads", "petpack")
OUT = os.path.join(ROOT, "site-home", "assets")

# 默认要合成的动作：舞狮头(主)、可爱宅舞、轻快摇摆舞
DEFAULT_IDS = ["r75", "r17", "r86"]


def build(action_id):
    src_dir = os.path.join(PACK, action_id)
    if not os.path.isdir(src_dir):
        print(f"[跳过] 没有动作目录 {action_id}")
        return None

    frames = []
    for i in range(12):
        p = os.path.join(src_dir, "%02d.webp" % i)
        if not os.path.exists(p):
            print(f"[跳过] {action_id} 缺帧 {i}")
            return None
        im = Image.open(p).convert("RGBA")
        frames.append(im)

    w, h = frames[0].size
    sheet = Image.new("RGBA", (w * len(frames), h), (0, 0, 0, 0))
    for i, f in enumerate(frames):
        sheet.paste(f, (i * w, 0), f)

    os.makedirs(OUT, exist_ok=True)
    out_path = os.path.join(OUT, "pet-%s.webp" % action_id)
    sheet.save(out_path, "WEBP", quality=88, method=6)
    size = os.path.getsize(out_path)
    print(f"[完成] pet-{action_id}.webp  {sheet.size[0]}x{sheet.size[1]}  {size/1024:.0f} KB")
    return out_path


def main():
    ids = sys.argv[1:] or DEFAULT_IDS
    for aid in ids:
        build(aid)


if __name__ == "__main__":
    main()
