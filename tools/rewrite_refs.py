# -*- coding: utf-8 -*-
"""
把 index.html 里的图片引用切到 WebP，并补上加载优先级/懒加载属性。
对 dist-site 和 site 两个目录各跑一次，每处替换必须命中且仅命中一次。
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PAIRS = [
    # og:image 换成 jpg，社交平台抓取 WebP 不稳
    ('content="https://dabaifan.cn/assets/char.png"',
     'content="https://dabaifan.cn/assets/og.jpg"'),
    # 首屏立绘：最高优先级，别让它排队
    ('<img class="char" src="assets/char.png" alt="大肥鱼">',
     '<img class="char" src="assets/char.webp" alt="大肥鱼" decoding="async" fetchpriority="high">'),
    # 首屏浮动小卡：显示才 186px，用缩略图版本
    ('<img src="assets/shot-sticker.png" alt="表情包面板">',
     '<img src="assets/shot-sticker-sm.webp" alt="表情包面板" decoding="async">'),
    ('<img src="assets/ciallo.jpg" alt="Ciallo">',
     '<img src="assets/ciallo.webp" alt="Ciallo" decoding="async">'),
    # 正文大图在首屏外，懒加载
    ('<img src="assets/shot-sticker.png" alt="表情包面板截图">',
     '<img src="assets/shot-sticker.webp" alt="表情包面板截图" loading="lazy" decoding="async">'),
    # 聊天模拟里的头像
    ("const AV='assets/char.png';", "const AV='assets/char.webp';"),
    ("${me?'assets/ciallo.jpg':AV}", "${me?'assets/ciallo.webp':AV}"),
]


def run(rel_path):
    path = os.path.join(BASE, rel_path)
    if not os.path.exists(path):
        print(f"[跳过] 不存在 {rel_path}")
        return True
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()

    ok = True
    for old, new in PAIRS:
        n = html.count(old)
        if n != 1:
            print(f"  [!] 命中 {n} 次（应为 1）：{old[:60]}")
            ok = False
            continue
        html = html.replace(old, new)

    if ok:
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  [√] {rel_path} 7 处引用全部替换")
    else:
        print(f"  [×] {rel_path} 有未命中项，未写入")
    return ok


if __name__ == "__main__":
    all_ok = True
    for rel in ["dist-site/index.html", "site/index.html"]:
        print(rel)
        all_ok &= run(rel)
    sys.exit(0 if all_ok else 1)
