# -*- coding: utf-8 -*-
"""用真实 Edge(CDP) 打开大肥鱼前端，注入假的 pywebview 接口，
然后模拟"点左侧插件"这一步，看点击链路到底在哪断。

用法: python probe_hub.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket   # websocket-client

HTML = r"D:\大肥鱼AI\大肥鱼AI.html"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9224
PROFILE = r"D:\大肥鱼缓存\_hub_probe_profile"


def launch():
    args = [EDGE,
            "--headless=new",
            "--remote-debugging-port=%d" % PORT,
            "--user-data-dir=%s" % PROFILE,
            "--no-first-run", "--no-default-browser-check",
            "--disable-gpu", "--window-size=1600,1000",
            "file:///" + HTML.replace("\\", "/")]
    p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    targets = None
    for _ in range(40):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json" % PORT, timeout=2) as r:
                targets = json.loads(r.read().decode("utf-8"))
            if targets:
                break
        except Exception:
            continue
    if not targets:
        p.terminate()
        raise SystemExit("Edge 没起来")
    page = [t for t in targets if t.get("type") == "page"]
    return p, page[0]["webSocketDebuggerUrl"]


class CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self.i = 0

    def send(self, method, params=None):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.i:
                if "error" in msg:
                    raise RuntimeError("%s -> %s" % (method, msg["error"]))
                return msg.get("result", {})
            # 其它事件先丢掉

    def eval(self, expr, await_promise=False):
        r = self.send("Runtime.evaluate", {
            "expression": expr, "returnByValue": True,
            "awaitPromise": await_promise, "userGesture": True})
        if r.get("exceptionDetails"):
            return {"__exception": r["exceptionDetails"].get("text"),
                    "detail": str(r["exceptionDetails"])[:300]}
        return r.get("result", {}).get("value")


STUB = r"""
window.__errors = [];
window.addEventListener('error', function(e){
  window.__errors.push('ERROR: ' + (e.message||'') + ' @' + (e.filename||'') + ':' + (e.lineno||0));
});
window.addEventListener('unhandledrejection', function(e){
  window.__errors.push('REJECT: ' + String((e.reason&&e.reason.message)||e.reason));
});

var _PLUGINS = [
  {id:'bili_pond', name:'B站鱼塘', icon:'📺', version:'1.0.0', ok:true, enabled:true, kind:'py'},
  {id:'bili_whale', name:'B站养鱼', icon:'🐳', version:'1.0.0', ok:true, enabled:true, kind:'py'},
  {id:'deep_think', name:'深度思考', icon:'🔬', version:'1.0.0', ok:true, enabled:true, kind:'py'},
  {id:'feiyu_mood', name:'鱼的心情', icon:'🐟', version:'1.0.0', ok:true, enabled:true, kind:'py'},
  {id:'live_cast', name:'大肥鱼直播', icon:'🎙', version:'0.1.0', ok:true, enabled:true, kind:'py'},
  {id:'nekowhale_stickers', name:'大肥鱼表情包', icon:'🐱', version:'1.0.0', ok:true, enabled:true, kind:'py'},
  {id:'qq_chat', name:'QQ 对话', icon:'🐧', version:'1.0.0', ok:true, enabled:true, kind:'py'},
  {id:'qq_zone', name:'QQ 空间', icon:'🌌', version:'1.0.0', ok:true, enabled:true, kind:'py'},
  {id:'soul_md', name:'灵魂记忆', icon:'🧠', version:'1.0.0', ok:true, enabled:true, kind:'py'},
  {id:'speak_up', name:'主动搭话', icon:'💬', version:'1.0.0', ok:true, enabled:true, kind:'py'}
];

var _known = {
  plugin_list: async function(){ return _PLUGINS; },
  plugin_toggle: async function(id, on){ return {ok:true}; },
  plugin_detail: async function(id){ return {ok:true}; },
  mood_get: async function(){ return {face:'🙂', mood:'平静', dims:{}, timeline:[]}; },
  bili_status: async function(){ return {ok:true}; },
  bili_whale_status: async function(){ return {ok:true}; },
  qq_status: async function(){ return {ok:true}; },
  liquid_get: async function(){ return {}; }
};
var _proxy = new Proxy(_known, {
  get: function(t, k){
    if (k in t) return t[k];
    return async function(){ return {ok:true}; };
  },
  has: function(){ return true; }
});
window.pywebview = { api: _proxy, token: 'stub' };
"""


def main():
    proc, ws = launch()
    try:
        c = CDP(ws)
        c.send("Page.enable")
        c.send("Runtime.enable")
        c.send("Page.addScriptToEvaluateOnNewDocument", {"source": STUB})
        c.send("Page.reload", {"ignoreCache": True})
        time.sleep(3.5)

        print("=== 1) 基础环境 ===")
        print("pywebview 就绪:", c.eval("!!(window.pywebview&&window.pywebview.api)"))
        print("openPluginHub 存在:", c.eval("typeof openPluginHub"))
        print("hubShow 存在:", c.eval("typeof hubShow"))
        print("_hubViews 键数:", c.eval("Object.keys(_hubViews||{}).length"))

        print("\n=== 2) 打开插件中心并等清单加载 ===")
        c.eval("openPluginHub()")
        time.sleep(2)
        print("侧栏条目数:", c.eval("document.querySelectorAll('#hubNav .hub-item').length"))
        print("侧栏前几个:", c.eval("Array.from(document.querySelectorAll('#hubNav .hub-item')).map(b=>b.dataset.pid).join(', ')"))
        print("列表头部可见:", c.eval("getComputedStyle(document.getElementById('hubListHead')).display"))
        print("详情头部可见:", c.eval("getComputedStyle(document.getElementById('hubDetailHead')).display"))
        print("已加载 _HUBLIST 条数:", c.eval("(typeof _HUBLIST!=='undefined'&&_HUBLIST)?_HUBLIST.length:-1"))

        print("\n=== 3) 模拟点击 'B站鱼塘'（和用户操作一致）===")
        c.eval("(function(){var b=document.querySelector('#hubNav .hub-item[data-pid=\"bili_pond\"]'); if(b) b.click(); return !!b;})()")
        time.sleep(2)
        print("点击后 _hubPid:", c.eval("typeof _hubPid==='undefined'? '(未定义)': String(_hubPid)"))
        print("列表头部:", c.eval("getComputedStyle(document.getElementById('hubListHead')).display"))
        print("详情头部:", c.eval("getComputedStyle(document.getElementById('hubDetailHead')).display"))
        print("详情标题:", c.eval("document.getElementById('hubName').textContent"))
        print("详情副标题:", c.eval("document.getElementById('hubSub').textContent"))
        print("正文长度:", c.eval("document.getElementById('hubBody').innerHTML.length"))

        print("\n=== 4) 页面内捕获到的错误 ===")
        errs = c.eval("window.__errors")
        if errs:
            for e in errs[-15:]:
                print("  ", e)
        else:
            print("  无")

        print("\n=== 5) 直调 hubShow 看是否抛异常 ===")
        r = c.eval("(function(){try{hubShow('bili_pond');return 'ok';}catch(e){return 'THROW: '+e+' | '+e.stack;}})()")
        print("结果:", r)
        if r == "ok":
            time.sleep(1.5)
            print("直调后详情标题:", c.eval("document.getElementById('hubName').textContent"))
            print("直调后正文长度:", c.eval("document.getElementById('hubBody').innerHTML.length"))
            print("直调后详情头部:", c.eval("getComputedStyle(document.getElementById('hubDetailHead')).display"))
            errs2 = c.eval("window.__errors")
            if errs2:
                print("直调期间错误:")
                for e in errs2[-10:]:
                    print("  ", e)
    finally:
        try:
            proc.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    main()
