# -*- coding: utf-8 -*-
"""生成软著申请用的「源程序文档」（前 30 页 + 后 30 页 = 60 页）。

格式依据中国版权保护中心要求：
- 每页不少于 50 行
- 页眉中间标注「软件全称 + 版本号」
- 右上角标注页码
- 最后一页应为程序退出部分

文件顺序刻意把主程序放在最后，这样"后 30 页"自然落在主程序尾部，
末页正好是 webview.start()（程序退出/进入事件循环的地方）。
"""
import os
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

ROOT = r"D:\大肥鱼AI"
OUT_DIR = os.path.join(ROOT, "软著申请")
SOFT_NAME = "大肥鱼AI桌面助手软件"
VERSION = "V1.0"
LINES_PER_PAGE = 50
PAGES_EACH_SIDE = 30
FONT_PT = 8               # 8pt Consolas：一页塞得下 50 行且长行不易折行
WRAP_COLS = 96            # 我们自己折行，避免 Word 自动折行把页数撑乱（中文按 2 列算）

# 提交顺序：核心模块 → 插件 → 前端 → 主程序（放最后，保证末页是退出段）
FILES = [
    "dfy_core.py",
    "llm_params.py",
    "pet_mutter_watcher.py",
] + sorted(
    os.path.join("plugins", f) for f in os.listdir(os.path.join(ROOT, "plugins"))
    if f.endswith(".py")
) + [
    "大肥鱼AI.html",
    os.path.join("live", "live_screen.html"),
    "大肥鱼AI.py",
]


def _disp_width(s):
    """显示宽度：中日韩字符算 2 列，其余算 1 列。"""
    w = 0
    for ch in s:
        w += 2 if ord(ch) > 0x2E80 else 1
    return w


def wrap_line(line, limit=WRAP_COLS):
    """把超长行按显示宽度折开，续行加缩进标记。

    为什么要自己折：Word 自动折行会让"50 行/页"失效，实测多出 6 页（66 页），
    不符合"前后各 30 页"的格式要求。
    """
    if _disp_width(line) <= limit:
        return [line]
    parts = []
    cur = ""
    w = 0
    for ch in line:
        cw = 2 if ord(ch) > 0x2E80 else 1
        if w + cw > limit:
            parts.append(cur)
            cur = "      " + ch          # 续行缩进 6 格，便于识别
            w = 6 + cw
        else:
            cur += ch
            w += cw
    if cur:
        parts.append(cur)
    return parts


def load_lines():
    """把源码按文件拼成一串行，空行剔除（提高代码密度），带文件分隔标记。"""
    out = []
    for rel in FILES:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        raw = open(path, "r", encoding="utf-8-sig", errors="replace").read()
        lines = raw.splitlines()
        out.append("/* ===== 文件：%s（共 %d 行）===== */" % (rel.replace("\\", "/"), len(lines)))
        for ln in lines:
            ln = ln.rstrip()
            if not ln.strip():
                continue          # 去掉非代码空行
            ln = ln.replace("\t", "    ")
            out.extend(wrap_line(ln))
        out.append("")
    while out and not out[-1].strip():
        out.pop()
    return out


def add_page_field(paragraph):
    """在段落里插入 { PAGE } 域，让 Word 自动算页码。"""
    run = paragraph.add_run()
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    inner = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "1"
    inner.append(t)
    fld.append(inner)
    run._r.addnext(fld)


def set_header(section):
    header = section.header
    header.is_linked_to_previous = False
    p = header.paragraphs[0]
    p.text = ""
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    # 左：软件全称 + 版本号；右：第 X 页
    run = p.add_run("%s %s" % (SOFT_NAME, VERSION))
    run.font.size = Pt(9)
    run.font.name = "宋体"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    # 用制表位把页码推到右边
    from docx.enum.text import WD_TAB_ALIGNMENT
    p.paragraph_format.tab_stops.add_tab_stop(Cm(15.5), WD_TAB_ALIGNMENT.RIGHT)
    p.add_run("\t")

    r2 = p.add_run("第 ")
    r2.font.size = Pt(9)
    r2.font.name = "宋体"
    add_page_field(p)
    r3 = p.add_run(" 页")
    r3.font.size = Pt(9)
    r3.font.name = "宋体"


def add_code_line(doc, text, first):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    pf.line_spacing = Pt(13.2)
    pf.first_line_indent = Pt(0)
    run = p.add_run(text)
    run.font.size = Pt(FONT_PT)
    run.font.name = "Consolas"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    if first is False:
        pass


def build():
    lines = load_lines()
    total_pages = PAGES_EACH_SIDE * 2
    need = LINES_PER_PAGE * total_pages          # 3000 行

    if len(lines) <= need:
        body = lines
        cut_note = "代码共 %d 行，不足 60 页，按《登记办法》全部提交。" % len(lines)
    else:
        head_n = LINES_PER_PAGE * PAGES_EACH_SIDE      # 1500
        # 分隔标记占 1 行，所以尾段取 1499 行，保证总数正好 60 页
        tail_n = LINES_PER_PAGE * PAGES_EACH_SIDE - 1  # 1499
        head = lines[:head_n]
        tail = lines[-tail_n:]
        body = head + ["/* ===== 中间部分省略（前 30 页 + 后 30 页交存） ===== */"] + tail
        cut_note = ("代码共 %d 行，按一般交存方式提交前 %d 行与后 %d 行（各 30 页）。"
                    % (len(lines), head_n, tail_n))

    doc = Document()
    sec = doc.sections[0]
    sec.page_width = Cm(21.0)
    sec.page_height = Cm(29.7)
    sec.top_margin = Cm(2.2)
    sec.bottom_margin = Cm(1.8)
    sec.left_margin = Cm(2.2)
    sec.right_margin = Cm(2.0)
    sec.header_distance = Cm(1.0)
    set_header(sec)

    # 正文样式兜底
    style = doc.styles["Normal"]
    style.font.name = "Consolas"
    style.font.size = Pt(FONT_PT)
    style._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    page = 1
    count_in_page = 0
    for ln in body:
        if count_in_page == LINES_PER_PAGE:
            doc.add_page_break()
            page += 1
            count_in_page = 0
        add_code_line(doc, ln, count_in_page == 0)
        count_in_page += 1

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "源程序文档_%s%s.docx" % (SOFT_NAME, VERSION))
    doc.save(out)
    pages = (len(body) + LINES_PER_PAGE - 1) // LINES_PER_PAGE
    print(cut_note)
    print("总行数: %d  预计页数: %d" % (len(body), pages))
    print("已生成:", out, "%.1f KB" % (os.path.getsize(out) / 1024))
    return out


if __name__ == "__main__":
    build()
