# -*- coding: utf-8 -*-
"""宿主 schemastery shim 回归测试。

背景：host 的 loader-hooks.mjs 把插件对 `@deepseek-ai/*` 的 import **无条件**重定向到
`dsh_host/node_modules/@deepseek-ai/<name>/index.js`。其中 schemastery 是我们写的精简
shim（只描形状、不真校验）。shim 少一个链式方法，插件整个挂载就会失败，外观表现就是
"插件装了但不生效"（/health 里 ok=False）。

判据：插件真实用到的链式方法里，凡是**真 schemastery 有、shim 没有**的，都算回归。
（中文注释、第三方库里的同名调用、zod 的 API 都不参与比较，靠"真库是否有这个方法"过滤。）
"""
import json
import os
import re
import subprocess
import sys

HOST_SHIM = r"D:\大肥鱼AI\dsh_host\node_modules\@deepseek-ai\schemastery\index.js"
REAL_PKG = r"D:\本地部署大肥鱼环境\dsh\profiles\desktop\node_modules\@deepseek-ai\schemastery\lib\index.mjs"
PLUGIN_ROOTS = [
    r"D:\本地部署大肥鱼环境\dsh\profiles\desktop\node_modules",
]
NODE = r"D:\本地部署大肥鱼环境\dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"

CTORS = r"(?:any|string|number|natural|percent|boolean|const|dict|array|tuple|union|intersect|object)"
IMPORT_RE = re.compile(r"from\s+['\"]@deepseek-ai/schemastery['\"]|import\(\s*['\"]@deepseek-ai/schemastery['\"]\s*\)")
CHAIN_RE = re.compile(r"\bz\." + CTORS + r"\b(?:[^;\n]{0,400}?)\.([A-Za-z_$][\w$]*)\s*\(")

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (("  " + str(extra)) if extra else ""))


# 1) 只扫真正 import 了 scoped schemastery 的插件文件
METHODS, FILES = set(), []
for root in PLUGIN_ROOTS:
    for dirpath, _, files in os.walk(root):
        if ".pnpm" in dirpath or ".ignored" in dirpath or "schemastery" in dirpath:
            continue
        for fn in files:
            if not fn.endswith((".js", ".mjs", ".cjs")):
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p, encoding="utf-8", errors="ignore") as f:
                    src = f.read()
            except Exception:
                continue
            if not IMPORT_RE.search(src):
                continue
            FILES.append(os.path.relpath(p, root))
            for m in CHAIN_RE.finditer(src):
                METHODS.add(m.group(1))

print("真正 import schemastery 的文件 %d 个（其中业务插件见下），链式方法 %d 个" % (len(FILES), len(METHODS)))
for f in sorted([x for x in FILES if not x.startswith("@deepseek-ai")]):
    print("  · " + f)

# 2) 用真库算出"合法方法集合"，再逐个数 shim
code = """
const methods = %s;
const real = (await import("file:///%s")).default;
const shim = (await import("file:///%s")).default;

const realOk = new Set();
for (const ctor of ["number","string","object","array","dict","union","any","natural","boolean","const","tuple","intersect","percent"]) {
  try {
    let s = real[ctor]();
    while (s) { for (const k of Object.getOwnPropertyNames(s)) realOk.add(k); s = Object.getPrototypeOf(s); if (!s || s === Object.prototype) break; }
  } catch (e) {}
}

const missing = [], notInReal = [];
for (const m of methods) {
  if (!realOk.has(m)) { notInReal.push(m); continue; }
  try {
    const s = shim.number();
    if (typeof s[m] !== "function") { missing.push(m); continue; }
    s[m]();
  } catch (e) { missing.push(m + "(" + e.message + ")"); }
}
console.log(JSON.stringify({ missing, notInReal }));
""" % (json.dumps(sorted(METHODS)), REAL_PKG.replace("\\", "/"), HOST_SHIM.replace("\\", "/"))

tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_t_shim.mjs")
with open(tmp, "w", encoding="utf-8") as f:
    f.write(code)
r = subprocess.run([NODE, tmp], capture_output=True, text=True, timeout=90)
os.remove(tmp)

print("\n== shim 覆盖检查 ==")
if r.returncode != 0:
    print(r.stdout + r.stderr)
    check("shim 能被加载", False, r.stderr.strip()[:200])
else:
    res = json.loads(r.stdout.strip().splitlines()[-1])
    missing, not_in_real = res["missing"], res["notInReal"]
    check("shim 能被加载", True)
    check("插件真实用到的链式方法 shim 全都有", not missing,
          ("缺: " + ", ".join(missing)) if missing else "全部可调用")
    check("deprecated() 可用（onebot 回归点）", "deprecated" not in missing)
    if not_in_real:
        print("  (跳过真库没有的方法: " + ", ".join(not_in_real) + ")")

print("\n===== 结果：%d 通过 / %d 失败 =====" % (len(PASS), len(FAIL)))
sys.exit(0 if not FAIL else 1)
