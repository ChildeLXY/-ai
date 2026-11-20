# -*- coding: utf-8 -*-
"""最小诊断：页面到底有没有加载成功，第一处报错是什么。"""
import json, subprocess, time, urllib.request, sys
import websocket

PORT = 9226
PROFILE = r"D:\大肥鱼缓存\_hub_diag_profile"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
HTML = sys.argv[1] if len(sys.argv) > 1 else r"D:\大肥鱼AI\大肥鱼AI.html"

subprocess.Popen([EDGE, "--headless=new", "--remote-debugging-port=%d" % PORT,
                  "--user-data-dir=%s" % PROFILE, "--no-first-run", "--disable-gpu",
                  "file:///" + HTML.replace("\\", "/")],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
url = None
for _ in range(40):
    time.sleep(0.5)
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json" % PORT, timeout=2) as r:
            ts = json.loads(r.read().decode())
        page = [t for t in ts if t.get("type") == "page"]
        if page:
            url = page[0]["webSocketDebuggerUrl"]
            break
    except Exception:
        pass

ws = websocket.create_connection(url, timeout=30)
i = 0
def send(m, p):
    global i
    i += 1
    ws.send(json.dumps({"id": i, "method": m, "params": p}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == i:
            if "error" in msg:
                raise RuntimeError(msg["error"])
            return msg.get("result", {})

def ev(expr):
    r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    if r.get("exceptionDetails"):
        return "THROW " + str(r["exceptionDetails"])[:200]
    return r["result"].get("value")

send("Page.enable", {})
send("Runtime.enable", {})
send("Page.addScriptToEvaluateOnNewDocument", {"source":
      "window.pywebview={api:new Proxy({}, {get:function(){return async function(){return {ok:true}}}})};"})
send("Page.reload", {"ignoreCache": True})
time.sleep(4)

print("body 长度:", ev("document.body.innerHTML.length"))
print("script 块数:", ev("document.querySelectorAll('script').length"))
print("openPluginHub:", ev("typeof openPluginHub"))
print("hubShow:", ev("typeof hubShow"))
print("_hubMktPage:", ev("typeof _hubMktPage"))
print("_hubViews 键数:", ev("typeof _hubViews==='object'&&_hubViews?Object.keys(_hubViews).length:-1"))
print("第1个脚本字符数:", ev("document.querySelectorAll('script')[0].textContent.length"))
print("脚本是否含 hubShow 字样:", ev("document.querySelectorAll('script')[0].textContent.indexOf('function hubShow')"))
print("页面 window.onerror 记录:", ev("window.__diagErrs||'(未安装捕获)'"))
