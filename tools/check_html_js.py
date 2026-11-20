# -*- coding: utf-8 -*-
"""把 大肥鱼AI.html 里的 <script> 块逐个抽出来，用 node --check 校验语法。
只要某块有语法错误，那一整块里的函数（包括点击回调）就全部不存在，
表现就是"点了没反应"。"""
import os
import re
import subprocess
import tempfile

HTML = r"D:\大肥鱼AI\大肥鱼AI.html"
NODE = r"D:\本地部署大肥鱼环境\engine\resources\runtime\primary-runtime\dependencies\node\bin\node.exe"

html = open(HTML, encoding="utf-8").read()
# 只取没有 src 的内联 script
blocks = []
for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S | re.I):
    start = m.start(1)
    line = html.count("\n", 0, start) + 1
    blocks.append((line, m.group(1)))

print("内联 script 块数量: %d" % len(blocks))
bad = 0
for i, (line, body) in enumerate(blocks):
    if not body.strip():
        continue
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(body)
        tmp = f.name
    r = subprocess.run([NODE, "--check", tmp], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        bad += 1
        print("\n[语法错误] 块 %d（HTML 第 %d 行起，%d 字符）" % (i, line, len(body)))
        # node 报的行号是块内相对行号，换算成 HTML 行号
        for ln in (r.stderr or "").splitlines()[:12]:
            print("   " + ln)
            mo = re.search(r":(\d+)$", ln.strip())
            if mo:
                print("   -> 对应 HTML 第 %d 行" % (line + int(mo.group(1)) - 1))
    else:
        print("块 %d（HTML 第 %d 行起）语法 OK，%d 字符" % (i, line, len(body)))
    os.unlink(tmp)
print("\n结论: %s" % ("存在语法错误 %d 处" % bad if bad else "全部块语法正常"))
