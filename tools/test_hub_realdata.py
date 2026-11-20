# -*- coding: utf-8 -*-
"""用真实插件清单跑一遍插件中心：25 条（10 Python + 15 市场），每条都要点得开。"""
import json, os, random, subprocess, sys, tempfile, time, urllib.request
import websocket

HTML = r"D:\大肥鱼AI\大肥鱼AI.html"
DATA = r"D:\大肥鱼缓存\_real_plugin_list.json"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = random.randint(9300, 9900)
PROFILE = tempfile.mkdtemp(prefix="realhub_")

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (("  " + str(extra)) if extra else ""))

rows = json.load(open(DATA, encoding="utf-8"))
PY = [r for r in rows if r.get("kind") != "dsh"]
DSH = [r for r in rows if r.get("kind") == "dsh"]

STUB = ("window.__errors=[];window.addEventListener('error',function(e){window.__errors.push(String(e.message))});"
        "var _REAL=" + json.dumps(rows, ensure_ascii=False) + ";"
        "var _k={plugin_list:async function(){return _REAL},plugin_toggle:async function(){return {ok:true}}};"
        "window.pywebview={api:new Proxy(_k,{get:function(t,k){return (k in t)?t[k]:async function(){return {ok:true}}}})};")

subprocess.Popen([EDGE, "--headless=new", "--remote-debugging-port=%d" % PORT,
                  "--user-data-dir=%s" % PROFILE, "--no-first-run", "--disable-gpu",
                  "--window-size=1600,1000", "file:///" + HTML.replace("\\", "/")],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
url = None
for _ in range(40):
    time.sleep(0.5)
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json" % PORT, timeout=2) as r:
            ts = json.loads(r.read().decode())
        page = [t for t in ts if t.get("type") == "page"]
        if page:
            url = page[0]["webSocketDebuggerUrl"]; break
    except Exception:
        pass

ws = websocket.create_connection(url, timeout=30)
_i = 0
def send(m, p=None):
    global _i
    _i += 1
    ws.send(json.dumps({"id": _i, "method": m, "params": p or {}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == _i:
            return msg.get("result", {})

def ev(expr):
    r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "userGesture": True})
    if r.get("exceptionDetails"):
        return "THROW"
    return r["result"].get("value")

send("Page.enable"); send("Runtime.enable")
send("Page.addScriptToEvaluateOnNewDocument", {"source": STUB})
send("Page.reload", {"ignoreCache": True})
time.sleep(4)

print("真实清单：%d 条（Python %d + 市场 %d）" % (len(rows), len(PY), len(DSH)))
print("\n== 1) 打开插件中心 ==")
ev("openPluginHub()"); time.sleep(2)
nav = ev("document.querySelectorAll('#hubNav .hub-item').length")
check("左栏条目 = 2(列表入口) + %d + %d" % (len(PY), len(DSH)), nav == 2 + len(PY) + len(DSH), nav)
secs = str(ev("Array.from(document.querySelectorAll('#hubNav .hub-sec')).map(function(e){return e.textContent}).join('|')"))
check("分组里有市场插件", "市场插件" in secs, secs)

print("\n== 2) 每条市场插件都能点开 ==")
bad = []
for r in DSH:
    pid = r["id"]
    ev("hubShow('%s')" % pid.replace("'", "\\'"))
    time.sleep(0.35)
    ok = (ev("getComputedStyle(document.getElementById('hubDetailHead')).display") == "flex"
          and ev("_hubPid") == pid
          and (ev("document.getElementById('hubBody').innerHTML.length") or 0) > 200)
    if not ok:
        bad.append(pid)
check("%d 个市场插件全部能打开" % len(DSH), not bad, bad if bad else "全部通过")

print("\n== 3) 状态标记是否对得上真实数据 ==")
miss = [r["pkg"] for r in DSH if r.get("present") is False]
if miss:
    check("识别出缺目录的包", True, miss)
    txt = str(ev("document.querySelector('#hubNav .hub-item[data-pid=\"dsh:%s\"]').textContent" % miss[0]))
    check("缺目录在左栏有标记", "缺目录" in txt, txt)
    ev("hubShow('dsh:%s')" % miss[0]); time.sleep(0.6)
    body = str(ev("document.getElementById('hubBody').innerHTML") or "")
    check("详情页写明包目录缺失", "包目录缺失" in body)
    check("详情页给重装入口", "重新安装" in body)
else:
    check("没有缺目录的包（全部已落地）", True)
    states = str(ev("Array.from(document.querySelectorAll('#hubNav .hub-item[data-pid^=\"dsh:\"]'))"
                    ".map(function(b){return b.textContent}).join('|')"))
    check("市场条目都带状态标记", ("已开" in states or "已关" in states) and "缺目录" not in states, states[:120])

print("\n== 4) Python 插件照旧 ==")
bad2 = []
for r in PY:
    ev("hubShow('%s')" % r["id"]); time.sleep(0.35)
    if not (ev("getComputedStyle(document.getElementById('hubDetailHead')).display") == "flex"
            and ev("_hubPid") == r["id"]):
        bad2.append(r["id"])
check("%d 个 Python 插件全部能打开" % len(PY), not bad2, bad2 if bad2 else "全部通过")

print("\n== 5) 页面报错 ==")
check("无未捕获错误", not ev("window.__errors"), ev("window.__errors"))

print("\n===== 真实数据验收：%d 通过 / %d 失败 =====" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
