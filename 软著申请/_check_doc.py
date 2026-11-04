# -*- coding: utf-8 -*-
"""用本机 Word 打开生成的文档，实测页数、页眉和末页内容，验证是否符合交存要求。"""
import os
import win32com.client

DOC = r"D:\大肥鱼AI\软著申请\源程序文档_大肥鱼AI桌面助手软件V1.0.docx"

word = win32com.client.DispatchEx("Word.Application")
word.Visible = False
word.DisplayAlerts = 0
try:
    doc = word.Documents.Open(DOC, ReadOnly=True)
    pages = doc.ComputeStatistics(2)          # 2 = wdStatisticPages
    words = doc.ComputeStatistics(0)
    print("实际页数:", pages)
    print("段落数:", doc.Paragraphs.Count)

    # 页眉内容（第 1 节）
    for i in range(1, doc.Sections(1).Headers.Count + 1):
        h = doc.Sections(1).Headers(i)
        if h.Exists:
            print("页眉[%d]:" % i, repr(h.Range.Text.strip()))

    # 最后一页前几行，确认是程序退出部分
    rng = doc.Content
    rng.Collapse(0)                            # 折叠到末尾
    rng.Start = max(0, doc.Content.End - 800)
    tail = rng.Text
    print("--- 文档末尾 300 字 ---")
    print(tail[-300:])
    doc.Close(False)
finally:
    word.Quit()
