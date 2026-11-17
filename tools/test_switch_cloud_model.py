# -*- coding: utf-8 -*-
"""验证大肥鱼AI 主站「切换云端模型」这条链路。

跑的是真实的 大肥鱼AI.html，只 mock pywebview 后端接口。
要验证四件事：
  1. 云端模式下，下拉框里确实列出了全部 5 个智谱免费模型（带 ☁ 标识）
  2. 切换下拉框后，MODEL 变量真的跟着变了
  3. 切换后发出去的请求 model 字段是新的那个（不是旧的）
  4. 切模型不会触发预热请求（云端不需要，白烧额度）
"""
import json
import os
import re
import subprocess
import urllib.request

CHROME = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
HTML = r"D:\大肥鱼AI\大肥鱼AI.html"
TMP = r"D:\大肥鱼AI\_sw_test.html"

# 真实后端数据
cfg = json.load(open(r"D:\大肥鱼AI\cloud_config.json", encoding="utf-8"))
models = cfg["models"]

mock = {
    "cloud_config_get": {
        "enabled": True,
        "base": "http://127.0.0.1:11435",
        "model": cfg["model"],
        "models": models,
        "proxy_alive": True,
        "api_key": "*" * 8,
    },
    "model_caps": {},   # 按模型名填
    "load_setting": {},
    "save_setting": {},
}

# model_caps 要按主站后端 model_caps() 的真实返回结构来（顶层有 thinking/vision）
CAPS = {
    "glm-4.7-flash": {"ok": True, "caps": ["completion", "thinking"], "thinking": True, "vision": False},
    "glm-4.6v-flash": {"ok": True, "caps": ["completion", "vision", "thinking"], "thinking": True, "vision": True},
    "glm-4.5-flash": {"ok": True, "caps": ["completion", "thinking"], "thinking": True, "vision": False},
    "glm-4-flash": {"ok": True, "caps": ["completion"], "thinking": False, "vision": False},
    "glm-4-flashx": {"ok": True, "caps": ["completion"], "thinking": False, "vision": False},
}

html = open(HTML, encoding="utf-8").read()

inject = """
<script>
window.__MOCK__ = %s;
window.__CAPS__ = %s;
window.__CALLS__ = [];
window.pywebview = {api:{
  cloud_config_get:async()=>window.__MOCK__.cloud_config_get,
  model_caps:async(m)=>{window.__CALLS__.push(['model_caps',m]);return window.__CAPS__[m]||{ok:false};},
  load_setting:async(k)=>{window.__CALLS__.push(['load_setting',k]);return window.__MOCK__.load_setting[k]||null;},
  save_setting:async(k,v)=>{window.__CALLS__.push(['save_setting',k,v]);window.__MOCK__.save_setting[k]=v;},
  deep_think_state:async()=>({ok:false,on:false}),
  get_env:async()=>'', mood_context:async()=>'', soul_context:async()=>'',
  sticker_hint:async()=>'', kb_search:async()=>null,
  gen_title:async()=>'', load_chats:async()=>[], gen_persona:async()=>'',
  pick_files:async()=>[], thumb:async()=>'', save_dropped:async()=>'',
  set_pref:async()=>{}, set_titlebar:async()=>{}, open_work_dir:async()=>{},
  sticker_pick:async()=>[], reflect_memory:async()=>{},
}};
</script>
""" % (json.dumps(mock, ensure_ascii=False), json.dumps(CAPS, ensure_ascii=False))

# 记录前端所有发往 API 的请求，用来验证"切模型后请求的 model 是不是新的"
record = """
<script>
window.__REQ__ = [];
(function(){
  const of=window.fetch;
  window.fetch=function(url,opts){
    try{
      if(opts&&opts.body){
        const b=JSON.parse(opts.body);
        window.__REQ__.push({url:String(url),model:b.model,stream:!!b.stream,
                            messages:(b.messages||[]).length});
      }else{
        window.__REQ__.push({url:String(url),model:null});
      }
    }catch(e){}
    return of.apply(this,arguments);
  };
})();
</script>
"""
html = html.replace("</head>", inject + record + "</head>", 1)

# 测完把结果吐到一个隐藏元素里
probe = """
<script>
setTimeout(async()=>{
  const sel=document.getElementById('modelSel');
  const out={opts:[...sel.options].map(o=>o.textContent),
             values:[...sel.options].map(o=>o.value),
             before:MODEL};
  // 模拟切换：选一个非当前模型
  const target=[...sel.options].find(o=>o.value==='glm-4.6v-flash');
  if(target){
    sel.value=target.value;
    sel.dispatchEvent(new Event('change'));
    await new Promise(r=>setTimeout(r,900));
    out.after=MODEL;
    out.changed=(MODEL==='glm-4.6v-flash');
  }else{ out.changed=false; }
  // 模拟真正发一条消息，看请求里的 model 是不是切过之后的那个
  window.__REQ__.length=0;
  try{
    const ta=document.getElementById('input');
    if(ta){ ta.value='测试切换'; send(); }   // 不 await：send 会等整轮流式结束
  }catch(e){ out.sendErr=String(e); }
  await new Promise(r=>setTimeout(r,2500));
  out.requests=window.__REQ__;
  out.calls=window.__CALLS__;
  out.cloudEnabled=cloudEnabled;
  out.API=API;
  const d=document.createElement('div');d.id='__RESULT__';
  d.textContent=JSON.stringify(out);
  d.style.display='none';
  document.body.appendChild(d);
},2200);
</script>
"""
html = html.replace("</body>", probe + "</body>", 1)
open(TMP, "w", encoding="utf-8").write(html)

p = subprocess.run(
    [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
     "--virtual-time-budget=15000", "--dump-dom", "file:///" + TMP.replace("\\", "/")],
    capture_output=True)
dom = p.stdout.decode("utf-8", "ignore")

m = re.search(r'<div id="__RESULT__"[^>]*>(.*?)</div>', dom, re.S)
if not m:
    print("没拿到结果，DOM 长度", len(dom))
    raise SystemExit(1)

r = json.loads(m.group(1))
os.remove(TMP)

print("=" * 60)
print("云端模式下的模型下拉框")
for t, v in zip(r["opts"], r["values"]):
    print("   %-34s value=%s" % (t, v))
print()
print("云端模式标志 cloudEnabled :", r["cloudEnabled"])
print("前端请求目标 API           :", r["API"])
print("切换前 MODEL               :", r["before"])
print("切换后 MODEL               :", r.get("after"))
print("切换是否生效               :", r.get("changed"))
print()
print("前端发出的请求：")
for q in r["requests"]:
    print("   ", q)
print()
print("后端调用：")
for c in r["calls"]:
    print("   ", c)

print("=" * 60)
ok = True
checks = [
    ("下拉列出 5 个云端模型", len([v for v in r["values"] if v.startswith("glm-")]) == 5),
    ("每个都带 ☁ 标识", all(t.startswith("☁") for t, v in zip(r["opts"], r["values"]) if v.startswith("glm-"))),
    ("切换后 MODEL 变了", r.get("changed") is True),
    ("切换后请求发的是新模型", any(q.get("model") == "glm-4.6v-flash" for q in r["requests"])),
    ("切换没触发预热请求", not any(q.get("model") and q.get("messages") == 1 and q.get("stream") is False for q in r["requests"])),
    ("消息走的是代理 11435", any("11435" in q.get("url","") for q in r["requests"])),
    ("API 指向本机代理", r["API"].endswith("11435")),
]
for name, good in checks:
    print(("  ✅ " if good else "  ❌ ") + name)
    ok = ok and good
print("=" * 60)
print("全部通过 ✅" if ok else "有失败项 ❌")
