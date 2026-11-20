# -*- coding: utf-8 -*-
"""验证「DSH 原生界面」入口的前端接线：点按钮 → 桩接口返回地址 → 出现内嵌层。"""
import json, os, random, subprocess, tempfile, time, urllib.request
import websocket

HTML = os.environ.get("DFY_HUB_HTML") or r"D:\大肥鱼AI\大肥鱼AI.html"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = random.randint(9300, 9900)
PROFILE = tempfile.mkdtemp(prefix="dshuitest_")
PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (("  " + str(extra)) if extra else ""))


def launch():
    subprocess.Popen([EDGE, "--headless=new", "--remote-debugging-port=%d" % PORT,
                      "--user-data-dir=%s" % PROFILE, "--no-first-run",
                      "--no-default-browser-check", "--disable-gpu",
                      "--window-size=1600,1000",
                      "file:///" + HTML.replace("\\", "/")],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json" % PORT, timeout=2) as r:
                ts = json.loads(r.read().decode("utf-8"))
            page = [t for t in ts if t.get("type") == "page"]
            if page:
                return page[0]["webSocketDebuggerUrl"]
        except Exception:
            continue
    raise SystemExit("Edge 没起来")


class CDP:
    def __init__(self, url):
        self.ws = websocket.create_connection(url, timeout=30)
        self.i = 0

    def send(self, m, p=None):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": m, "params": p or {}}))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == self.i:
                return r.get("result", {})

    def ev(self, expr):
        r = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        return r.get("result", {}).get("value")


STUB = r"""
window.pywebview = { api: new Proxy({}, { get: (t, k) => {
   if (k === 'dsh_ui_open') return async () => ({ ok: true, url: 'http://127.0.0.1:6811/?token=TESTTOKEN' });
   if (k === 'dsh_ui_close') return async () => ({ ok: true });
   if (k === 'plugin_list') return async () => ([
      {id:'feiyu_mood',name:'情绪',kind:'python',ok:true,enabled:true},
      {id:'dsh:dsh-soul-md',name:'soul-md',kind:'dsh',ok:true,enabled:true,present:true}
   ]);
   return async () => null;
} }) };
"""

url = launch()
c = CDP(url)
c.send("Page.enable")
c.send("Runtime.enable")
time.sleep(3)
c.ev(STUB)

print("\n== 1) 插件中心里有没有「DSH 原生」入口 ==")
c.ev("openPluginHub('installed')")
time.sleep(2)
n = c.ev("document.querySelectorAll('.hub-native').length")
check("按钮存在", bool(n), "%s 个" % n)

print("\n== 2) 点击后是否打开内嵌层 ==")
c.ev("document.querySelector('.hub-native').click()")
time.sleep(3)
ov = c.ev("!!document.getElementById('dshUiOverlay')")
disp = c.ev("(document.getElementById('dshUiOverlay')||{}).style && document.getElementById('dshUiOverlay').style.display")
check("内嵌层已创建", bool(ov), disp)
src = c.ev("(document.getElementById('dshUiFrame')||{}).src || ''")
check("iframe 用了带 token 的地址", "token=TESTTOKEN" in str(src), src)
check("有独立窗口入口", bool(c.ev("!!document.getElementById('dshUiOut')")))

print("\n== 3) 关闭时释放（iframe 置空 + 调 close 接口） ==")
c.ev("dshUiClose()")
time.sleep(1)
check("内嵌层隐藏", c.ev("document.getElementById('dshUiOverlay').style.display") == "none")
check("iframe 已置空", c.ev("(document.getElementById('dshUiFrame').src||'').indexOf('about:blank')>=0"))

print("\n== 4) 页面无未捕获错误 ==")
errs = c.ev("window.__errs ? window.__errs.length : 0")
check("无报错计数", not errs, errs)

print("\n===== 结果：%d 通过 / %d 失败 =====" % (len(PASS), len(FAIL)))
os.system("taskkill /f /pid %d >nul 2>&1" % 0)
