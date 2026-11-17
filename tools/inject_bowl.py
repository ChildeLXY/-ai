# -*- coding: utf-8 -*-
"""
把 build_bowl_svg.py 生成的鱼碗 SVG 注入主站 index.html：
  - 去掉外层 <svg>，只保留内部内容
  - 套一层 transform 放进 hero 前景 SVG（与 deco 同一坐标系，保证贴着地面线）

用法:
    set PYTHONPATH=D:\\大肥鱼AI\\.tools
    python tools\\inject_bowl.py
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SVG = os.path.join(ROOT, "design-source", "bowl.svg")
HTML = os.path.join(ROOT, "site-home", "index.html")

MARK = "<!--BOWL-->"

# 在 deco 的 520x520 坐标系里：鱼碗宽约 150，底座贴着地面线 y=404
BLOCK = (
    "      <g class=\"bowl-fit\" transform=\"translate(31,292.4) scale(0.3)\">\n"
    "{inner}\n"
    "      </g>"
)


def main():
    svg = open(SVG, encoding="utf-8").read()
    m = re.search(r"<svg[^>]*>(.*)</svg>", svg, flags=re.S)
    inner = m.group(1).strip("\n")
    # 去掉根 svg 自带的缩进
    inner = "\n".join(line[2:] if line.startswith("  ") else line for line in inner.split("\n"))

    block = BLOCK.replace("{inner}", inner.rstrip())

    html = open(HTML, encoding="utf-8").read()
    if "<!--BOWL-BEGIN-->" not in html or "<!--BOWL-END-->" not in html:
        raise SystemExit("index.html 里找不到 <!--BOWL-BEGIN--> / <!--BOWL-END--> 标记")

    # 替换掉上一次注入的内容
    pat = re.compile(r"<!--BOWL-BEGIN-->.*?<!--BOWL-END-->", re.S)
    html = pat.sub(lambda m: "<!--BOWL-BEGIN-->\n" + block + "\n      <!--BOWL-END-->", html)
    open(HTML, "w", encoding="utf-8").write(html)
    print("已注入，index.html 现在", len(html), "字节")


if __name__ == "__main__":
    main()
