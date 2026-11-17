# -*- coding: utf-8 -*-
"""
生成「鱼在碗里」动画插画的 SVG（内联片段），供主站使用。

输入: design-source/bowl-full.png（从录屏裁出的插画）
输出: design-source/bowl.svg

分层:
  #bowl-static  面板 / 黑碗 / 虚线弧 / 灰色水花 / 小方块（不动）
  #bowl-whale   鲸鱼身体 / 腹部条纹 / 尾鳍（整体上下浮动）
  #bowl-tail    尾鳍（额外轻微摆动）
"""
import os

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "design-source", "bowl-full.png")
OUT = os.path.join(ROOT, "design-source", "bowl.svg")

BLUE = np.array([0x50, 0x63, 0xF8])
BLACK = np.array([0x11, 0x16, 0x1C])
PANEL = np.array([0xE9, 0xEC, 0xFE])

C_WHALE = "#5063f8"
C_INK = "#11161c"
C_PANEL = "#e9ecfe"
C_LIGHT = "#f7f8fb"
C_DOT = "#cecfd2"


def imread_unicode(path):
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def mask_of(rgb, target, tol):
    d = np.abs(rgb.astype(np.int16) - target.astype(np.int16)).sum(axis=2)
    return d < tol


def smooth(points, closed=True, tension=0.9):
    pts = [tuple(map(float, p)) for p in points]
    if len(pts) < 3:
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    ext = ([pts[-1]] + pts + [pts[0], pts[1]]) if closed else ([pts[0]] + pts + [pts[-1]])
    d = [f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"]
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6 * tension, p1[1] + (p2[1] - p0[1]) / 6 * tension)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6 * tension, p2[1] - (p3[1] - p1[1]) / 6 * tension)
        d.append(f"C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}")
    if closed:
        d.append("Z")
    return " ".join(d)


def contours_of(mask, eps=1.0, min_area=20):
    m = mask.astype(np.uint8) * 255
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
        if cv2.contourArea(approx, oriented=True) < 0:
            pts = pts[::-1]
        out.append({
            "pts": pts,
            "area": area,
            "hole": hier[i][3] != -1,
            "bbox": (int(pts[:, 0].min()), int(pts[:, 1].min()),
                     int(pts[:, 0].max()), int(pts[:, 1].max())),
        })
    return out


def path_of(items):
    segs = [smooth(it["pts"], True) for it in items]
    return " ".join(segs)


def main():
    bgr = imread_unicode(SRC)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w, _ = rgb.shape

    bc = contours_of(mask_of(rgb, BLUE, 70))
    kc = contours_of(mask_of(rgb, BLACK, 80))
    pc = contours_of(mask_of(rgb, PANEL, 16), eps=1.5, min_area=300)

    # 蓝色分类
    whale_body = [c for c in bc if c["bbox"][0] < 60 and 55 < c["bbox"][1] < 170 and not c["hole"]]
    belly_blue = [c for c in bc if c["bbox"][1] > 170 and not c["hole"] and c["bbox"][0] < 250]
    tail = [c for c in bc if c["bbox"][0] > 290 and c["bbox"][1] < 130 and not c["hole"]]
    star = [c for c in bc if c["hole"] and c["bbox"][1] > 70 and c["bbox"][1] < 180]
    streak = [c for c in bc if c["hole"] and c["bbox"][1] < 120 and c["bbox"][0] > 300]
    deco_sq = [c for c in bc if c["bbox"][0] > 470 and c["bbox"][1] > 140]
    page_sq = [c for c in bc if c["bbox"][1] < 20]      # 页面上的小方块，不属于插画

    # 黑色分类：碗 / 底座 vs 鲸鱼腹部的黑条纹
    bowl_black = [c for c in kc if c["bbox"][3] >= 240]
    stripe_black = [c for c in kc if c["bbox"][3] < 240]

    print("身体", len(whale_body), "腹蓝条", len(belly_blue), "尾", len(tail),
          "星", len(star), "高光", len(streak), "碗", len(bowl_black),
          "黑条", len(stripe_black), "方块", len(deco_sq), "页面方块", len(page_sq))

    svg = []
    svg.append(f'<svg class="bowl-svg" viewBox="0 0 {w} {h}" '
               f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="一条鲸鱼养在碗里">')

    # 静止层：面板 + 黑碗
    svg.append(f'  <g class="bowl-static">')
    svg.append(f'    <path fill="{C_PANEL}" d="{path_of(pc)}"/>')
    svg.append(f'    <path fill="{C_INK}" d="{path_of(bowl_black)}"/>')
    # 尾后虚线弧
    svg.append('    <path class="bowl-arc" d="M285,52 Q420,55 500,158" fill="none" '
               f'stroke="{C_DOT}" stroke-width="2" stroke-linecap="round" '
               'stroke-dasharray="1 9" stroke-dashoffset="0"/>')
    # 两道水花
    svg.append('    <g class="bowl-splash" stroke="#cfd0d3" stroke-width="4.5" '
               'stroke-linecap="round" fill="none">')
    svg.append('      <path d="M484,138 L489,203"/>')
    svg.append('      <path d="M496,153 L506,199"/>')
    svg.append('    </g>')
    svg.append(f'    <path fill="{C_WHALE}" d="{path_of(deco_sq)}"/>')
    # 面板右下角的小加号
    svg.append('    <g stroke="#c3c8d4" stroke-width="2.4" stroke-linecap="round">')
    svg.append('      <path d="M502,318 v13"/>')
    svg.append('      <path d="M495.5,324.5 h13"/>')
    svg.append('    </g>')
    svg.append('  </g>')

    # 鲸鱼层：整体浮动
    svg.append('  <g class="bowl-whale">')
    svg.append(f'    <path fill="{C_WHALE}" d="{path_of(whale_body)}"/>')
    svg.append(f'    <path fill="{C_WHALE}" d="{path_of(belly_blue)}"/>')
    svg.append(f'    <path fill="{C_INK}" d="{path_of(stripe_black)}"/>')
    # 尾巴单独一层，方便摆动
    svg.append('    <g class="bowl-tail">')
    svg.append(f'      <path fill="{C_WHALE}" d="{path_of(tail)}"/>')
    svg.append(f'      <path fill="{C_LIGHT}" d="{path_of(streak)}"/>')
    svg.append('    </g>')
    # 星芒
    svg.append(f'    <path fill="#ffffff" d="{path_of(star)}"/>')
    svg.append('  </g>')

    svg.append('</svg>')

    out = "\n".join(svg) + "\n"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(out)
    print("输出:", OUT, len(out), "字节")


if __name__ == "__main__":
    main()
