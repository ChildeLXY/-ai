# -*- coding: utf-8 -*-
"""把 DeepBuddy 的模型下拉框展开成可视化对照图。

游戏是独占全屏窗口，会盖住 DeepBuddy，屏幕截图抓不到。
改走这条路：把 DeepBuddy 的 ui.html 真实渲染逻辑跑在无头 Chrome 里，
用真实模型清单（本地 7 个 + 云端 5 个）填充下拉框，
再把 select 展开成可视列表，截图给人看。

渲染用的就是 D:\\DeepBuddy\\ui.html 本身，mock 的只有 pywebview 后端接口，
UI 代码一行没改。
"""
import base64
import io
import json
import os
import re
import subprocess
import urllib.request

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
OUT_DIR = r"D:\大肥鱼AI\design-source\verify"
TMP_HTML = r"D:\大肥鱼AI\_dd_render.html"
OUT_PNG = os.path.join(OUT_DIR, "deepbuddy_models_render.png")

UI = r"D:\DeepBuddy\ui.html"


def fetch(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


# 真实模型清单：本地 Ollama + 云端代理
local = [m["name"] for m in fetch("http://127.0.0.1:11434/api/tags")["models"]]
cloud = [m["name"] for m in fetch("http://127.0.0.1:11435/api/tags")["models"]]
# DeepBuddy 的 cloud_models() 会给云端模型加 #cloud 后缀
models = local + [c + "#cloud" for c in cloud]
default = local[0] if local else models[0]
print("本地", len(local), "云端", len(cloud), "合计", len(models))

ui = open(UI, encoding="utf-8").read()

inject = """
<script>
window.__MOCK__ = %s;
window.pywebview = {api:{
  list_models:async()=>({models:window.__MOCK__.models,default:window.__MOCK__.default}),
  get_pref:async()=>'dark', set_pref:async()=>{}, set_titlebar:async()=>{},
  start:async()=>({}), poll:async()=>({}), cancel:async()=>{}, pick_files:async()=>[],
}};
</script>
""" % json.dumps({"models": models, "default": default})

ui = ui.replace("</head>", inject + "</head>", 1)
ui = ui.replace(
    "</body>",
    '<script>window.addEventListener("load",()=>setTimeout('
    '()=>window.dispatchEvent(new Event("pywebviewready")),200));</script></body>', 1)

# boot() 之后，把原生 select 展开成一块可视列表（原生下拉在 headless 里展不开）
expand = """
<script>
setTimeout(()=>{
  const sel=document.getElementById('model');
  if(!sel) return;
  const r=sel.getBoundingClientRect();
  const box=document.createElement('div');
  box.id='__expanded';
  const opts=[...sel.options];
  /* 空间不够就往上展开，保证 12 个选项全在视口内 */
  const below=window.innerHeight-r.bottom;
  const need=opts.length*27+16;
  const up=below<need;
  const top=up?Math.max(8,r.top-need-4):(r.bottom+4);
  box.style.cssText='position:fixed;left:'+Math.max(8,r.left-8)+'px;top:'+top+'px;'
    +'z-index:99999;background:var(--card,#151a22);border:1px solid var(--border,#2b3340);'
    +'border-radius:8px;box-shadow:0 12px 32px rgba(0,0,0,.55);padding:6px;'
    +'min-width:250px;max-height:none;font:13px/1.5 system-ui,"Microsoft YaHei",sans-serif;';
  opts.forEach(o=>{
    const d=document.createElement('div');
    d.style.cssText='padding:5px 10px;border-radius:5px;color:'+(o.selected?'#7fd1ff':'#d8dee9');
    if(o.selected) d.style.background='rgba(127,209,255,.10)';
    d.textContent=o.textContent;
    box.appendChild(d);
  });
  document.body.appendChild(box);
  document.documentElement.style.background='#0d1117';
}, 1500);
</script>
"""
ui = ui.replace("</body>", expand + "</body>", 1)
open(TMP_HTML, "w", encoding="utf-8").write(ui)

p = subprocess.run(
    [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
     "--window-size=1180,1120", "--virtual-time-budget=14000",
     "--screenshot=" + OUT_PNG, "file:///" + TMP_HTML.replace("\\", "/")],
    capture_output=True)
print(p.stdout.decode("utf-8", "ignore").strip()[-120:])
print("saved", OUT_PNG, os.path.getsize(OUT_PNG))

# 同时把渲染后的 option 文本 dump 出来做文字核对
p2 = subprocess.run(
    [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
     "--virtual-time-budget=12000", "--dump-dom", "file:///" + TMP_HTML.replace("\\", "/")],
    capture_output=True)
html = p2.stdout.decode("utf-8", "ignore")
m = re.search(r'<select[^>]*id="model"[^>]*>(.*?)</select>', html, re.S)
opts = re.findall(r"<option[^>]*>(.*?)</option>", m.group(1), re.S)
print("下拉选项 %d 个：" % len(opts))
for o in opts:
    print("  |", o.strip())
os.remove(TMP_HTML)
