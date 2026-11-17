# -*- coding: utf-8 -*-
"""
把录屏里的「鱼在碗里」插画描成矢量路径，输出独立 SVG。

输入: design-source/bowl-full.png（从录屏裁出的插画）
输出: design-source/bowl-traced.svg

做法:
  1. 按主色分层（鲸鱼蓝 / 墨黑 / 浅蓝面板）
  2. cv2.findContours 取轮廓 + approxPolyDP 简化
  3. Catmull-Rom 转三次贝塞尔，得到平滑曲线
  4. 分组输出：whale（会动的）/ bowl（静止的）/ deco（虚线、方块等）
"""
import os

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "design-source", "bowl-full.png")
OUT = os.path.join(ROOT, "design-source", "bowl-traced.svg")

BLUE = np.array([0x50, 0x63, 0xF8])
BLACK = np.array([0x11, 0x16, 0x1C])
PANEL = np.array([0xE9, 0xEC, 0xFE])


def mask_of(rgb, target, tol):
    d = np.abs(rgb.astype(np.int16) - target.astype(np.int16)).sum(axis=2)
    return d < tol


def smooth(points, closed=True, tension=0.92):
    """Catmull-Rom -> 三次贝塞尔"""
    pts = [tuple(map(float, p)) for p in points]
    n = len(pts)
    if n < 3:
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    if closed:
        ext = [pts[-1]] + pts + [pts[0], pts[1]]
    else:
        ext = [pts[0]] + pts + [pts[-1]]
    d = [f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"]
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6 * tension, p1[1] + (p2[1] - p0[1]) / 6 * tension)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6 * tension, p2[1] - (p3[1] - p1[1]) / 6 * tension)
        d.append(f"C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}")
    if closed:
        d.append("Z")
    return " ".join(d)


def contours_of(mask, eps=1.1, min_area=24):
    m = (mask.astype(np.uint8)) * 255
    cnts, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hier is None:
        return []
    hier = hier[0]
    out = []
    for i, c in enumerate(cnts):
        area = cv2.contourArea(c)
        if area < min_area:
            continue
        approx = cv2.approxPolyDP(c, eps, True)
        pts = approx.reshape(-1, 2)
        # 归一化方向：外轮廓顺时针
        if cv2.contourArea(approx, oriented=True) < 0:
            pts = pts[::-1]
        is_hole = hier[i][3] != -1
        out.append({
            "pts": pts,
            "area": area,
            "hole": is_hole,
            "bbox": (int(pts[:, 0].min()), int(pts[:, 1].min()),
                     int(pts[:, 0].max()), int(pts[:, 1].max())),
        })
    return out


def path_of(items):
    """把一组轮廓合成一条 path（外轮廓 + 洞用 evenodd）"""
    outers = [it for it in items if not it["hole"]]
    holes = [it for it in items if it["hole"]]
    segs = []
    for o in outers:
        segs.append(smooth(o["pts"], True))
    for h in holes:
        segs.append(smooth(h["pts"], True))
    if not segs:
        return ""
    return " ".join(segs)


def imread_unicode(path):
    """OpenCV 在 Windows 上读不了含中文的路径，走 fromfile + imdecode"""
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def main():
    bgr = imread_unicode(SRC)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w, _ = rgb.shape

    blue = mask_of(rgb, BLUE, 60)
    black = mask_of(rgb, BLACK, 70)
    panel = mask_of(rgb, PANEL, 14)

    bc = contours_of(blue)
    kc = contours_of(black)
    pc = contours_of(panel, eps=1.5, min_area=200)

    print("蓝轮廓:", [(round(c["area"]), c["bbox"], "hole" if c["hole"] else "") for c in bc])
    print("黑轮廓:", [(round(c["area"]), c["bbox"], "hole" if c["hole"] else "") for c in kc])
    print("面板:", [(round(c["area"]), c["bbox"]) for c in pc])

    # 蓝色：去掉右下角那个独立小方块（静止装饰）
    whale_blue = [c for c in bc if not (c["bbox"][0] > 470 and c["bbox"][1] > 140)]
    deco_blue = [c for c in bc if (c["bbox"][0] > 470 and c["bbox"][1] > 140)]

    # 黑色：鲸鱼身上的条纹（y < 245）随鲸鱼动，碗（y >= 235）静止
    whale_black = [c for c in kc if c["bbox"][3] < 245]
    bowl_black = [c for c in kc if c["bbox"][3] >= 235]

    print("鲸鱼蓝:", len(whale_blue), "鲸鱼黑条:", len(whale_black),
          "碗黑:", len(bowl_black), "装饰蓝:", len(deco_blue))

    parts = []
    parts.append(f'<g id="whale" fill="#5063f8" fill-rule="evenodd">'
                 f'<path d="{path_of(whale_blue)}"/></g>')
    parts.append(f'<g id="whale-stripes" fill="#11161c" fill-rule="evenodd">'
                 f'<path d="{path_of(whale_black)}"/></g>')
    parts.append(f'<g id="bowl" fill="#11161c" fill-rule="evenodd">'
                 f'<path d="{path_of(bowl_black)}"/></g>')
    parts.append(f'<g id="panel" fill="#e9ecfe">'
                 f'<path d="{path_of(pc)}"/></g>')
    parts.append(f'<g id="deco" fill="#5063f8">'
                 f'<path d="{path_of(deco_blue)}"/></g>')

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">\n'
           + "\n".join(parts) + "\n</svg>\n")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(svg)
    print("输出:", OUT, len(svg), "字节")


if __name__ == "__main__":
    main()
