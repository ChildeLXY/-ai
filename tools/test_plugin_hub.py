# -*- coding: utf-8 -*-
"""插件中心点击回归测试（真实 Edge + 假接口）。

覆盖用户实际遇到的现象：点了插件，页面马上弹回列表。
同时验证：刷新不回弹、切换插件、返回列表、单页报错不炸整页。
"""
import json
import os
import random
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket

import os
HTML = os.environ.get("DFY_HUB_HTML") or r"D:\大肥鱼AI\大肥鱼AI.html"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
# 每轮用全新的端口 + 全新的临时配置目录，避免旧缓存让测试假失败
PORT = random.randint(9300, 9900)
PROFILE = tempfile.mkdtemp(prefix="hubtest_")

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
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.i:
                return msg.get("result", {})

    def ev(self, expr, await_promise=False):
        r = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                           "awaitPromise": await_promise,
                                           "userGesture": True})
        if r.get("exceptionDetails"):
            return "THROW: " + str(r["exceptionDetails"].get("text"))
        return r["result"].get("value")


STUB = r"""
var _REAL=null;
window.__errors=[];
window.addEventListener('error',function(e){window.__errors.push(String(e.message)+' @'+e.lineno)});
var _P=[['bili_pond','B站鱼塘','📺'],['bili_whale','B站养鱼','🐳'],['deep_think','深度思考','🔬'],
['feiyu_mood','鱼的心情','🐟'],['live_cast','大肥鱼直播','🎙'],['nekowhale_stickers','大肥鱼表情包','🐱'],
['qq_chat','QQ 对话','🐧'],['qq_zone','QQ 空间','🌌'],['soul_md','灵魂记忆','🧠'],['speak_up','主动搭话','💬']]
.map(function(a){return {id:a[0],name:a[1],icon:a[2],version:'1.0.0',ok:true,enabled:true,kind:'py',present:true}});
// 市场插件：一个正常已加载、一个包目录丢了
_P.push({id:'dsh:dsh-soul-md',pkg:'dsh-soul-md',name:'dsh-soul-md',icon:'🐋',version:'0.12.5',
         ok:true,enabled:true,kind:'dsh',present:true,note:'',desc:'灵魂记忆市场包'});
_P.push({id:'dsh:dsh-ops',pkg:'dsh-ops',name:'dsh-ops',icon:'🐋',version:'',
         ok:false,enabled:true,kind:'dsh',present:false,note:'包目录丢了（package.json 里还记着），点一下重新安装就能修好'});
var _k={plugin_list:async function(){return _REAL||_P},plugin_toggle:async function(){return {ok:true}}};
window.pywebview={api:new Proxy(_k,{get:function(t,k){return (k in t)?t[k]:async function(){return {ok:true,items:[],list:[],rows:[]}}}})};
"""

# 可选：用真实后端清单替换假数据
REAL = os.environ.get("DFY_HUB_DATA")
if REAL:
    with open(REAL, encoding="utf-8") as f:
        STUB = ("var _REAL=" + f.read() + ";\n" + STUB)

def main():
    ws = launch()
    c = CDP(ws)
    try:
        c.send("Page.enable"); c.send("Runtime.enable")
        c.send("Page.addScriptToEvaluateOnNewDocument", {"source": STUB})
        c.send("Page.reload", {"ignoreCache": True})
        time.sleep(3.5)

        print("== 1) 打开插件中心 ==")
        c.ev("openPluginHub()"); time.sleep(1.8)
        n = c.ev("document.querySelectorAll('#hubNav .hub-item').length")
        check("侧栏有 14 个条目（已安装/市场 + 10 插件 + 2 市场）", n == 14, n)
        check("初始停在列表页", c.ev("getComputedStyle(document.getElementById('hubListHead')).display") == "flex")

        print("\n== 2) 点 B站鱼塘（复现用户操作） ==")
        c.ev("document.querySelector('#hubNav .hub-item[data-pid=\"bili_pond\"]').click()")
        time.sleep(1.2)
        check("详情页打开", c.ev("getComputedStyle(document.getElementById('hubDetailHead')).display") == "flex")
        check("列表页已隐藏", c.ev("getComputedStyle(document.getElementById('hubListHead')).display") == "none")
        check("标题正确", c.ev("document.getElementById('hubName').textContent") == "B站鱼塘")
        blen = c.ev("document.getElementById('hubBody').innerHTML.length")
        check("正文有内容（不是空白）", isinstance(blen, int) and blen > 200, blen)

        print("\n== 3) 关键回归：详情页调 hubRefresh() 不能弹回列表 ==")
        c.ev("Promise.resolve(hubRefresh())", await_promise=True)
        time.sleep(0.8)
        check("刷新后仍在详情页", c.ev("getComputedStyle(document.getElementById('hubDetailHead')).display") == "flex")
        check("刷新后列表仍隐藏", c.ev("getComputedStyle(document.getElementById('hubListHead')).display") == "none")
        check("刷新后 _hubTab 仍是 detail", c.ev("_hubTab") == "detail", c.ev("_hubTab"))

        print("\n== 4) 逐个切换 10 个插件，每个都要真的打开 ==")
        ids = ["bili_pond", "bili_whale", "deep_think", "feiyu_mood", "live_cast",
               "nekowhale_stickers", "qq_chat", "qq_zone", "soul_md", "speak_up"]
        bad = []
        for pid in ids:
            c.ev("hubShow('%s')" % pid)
            time.sleep(0.9)
            ok = (c.ev("getComputedStyle(document.getElementById('hubDetailHead')).display") == "flex"
                  and c.ev("_hubPid") == pid
                  and (c.ev("document.getElementById('hubBody').innerHTML.length") or 0) > 100)
            if not ok:
                bad.append(pid)
        check("10 个插件全部能打开", not bad, bad if bad else "全部通过")

        print("\n== 5) 返回列表 ==")
        c.ev("hubShow('installed')"); time.sleep(1)
        check("返回列表成功", c.ev("getComputedStyle(document.getElementById('hubListHead')).display") == "flex")

        print("\n== 6) 某个插件页面抛异常时，不能把整个面板搞空白 ==")
        c.ev("_hubViews.bili_pond.mount=function(){throw new Error('模拟页面炸了')}")
        c.ev("hubShow('bili_pond')"); time.sleep(1)
        body = c.ev("document.getElementById('hubBody').innerHTML") or ""
        check("仍停在详情页", c.ev("getComputedStyle(document.getElementById('hubDetailHead')).display") == "flex")
        check("给出了出错说明而不是空白", "页面加载出错" in str(body), str(body)[:80])

        print("\n== 7) 页面级报错 ==")
        errs = c.ev("window.__errors")
        check("运行期间无未捕获错误", not errs, errs)

        print("\n== 8) 市场插件必须出现在左栏（同步问题的核心） ==")
        c.ev("openPluginHub()"); time.sleep(1.5)
        n2 = c.ev("document.querySelectorAll('#hubNav .hub-item').length")
        check("左栏条目数 = 12 基础 + 2 市场", n2 == 14, n2)
        sec = c.ev("Array.from(document.querySelectorAll('#hubNav .hub-sec')).map(e=>e.textContent).join('|')")
        check("有「市场插件」分组", "市场插件" in str(sec), sec)
        dsh_nav = c.ev("document.querySelectorAll('#hubNav .hub-item[data-pid^=\"dsh:\"]').length")
        check("市场插件各有条目", dsh_nav == 2, dsh_nav)
        check("缺目录的打了「缺目录」标记",
              "缺目录" in str(c.ev("document.querySelector('#hubNav .hub-item[data-pid=\"dsh:dsh-ops\"]').textContent")))

        print("\n== 9) 点市场插件要进得去（以前只弹提示） ==")
        c.ev("document.querySelector('#hubNav .hub-item[data-pid=\"dsh:dsh-soul-md\"]').click()")
        time.sleep(1.2)
        check("进到详情页", c.ev("getComputedStyle(document.getElementById('hubDetailHead')).display") == "flex")
        check("标题是包名", c.ev("document.getElementById('hubName').textContent") == "dsh-soul-md",
              c.ev("document.getElementById('hubName').textContent"))
        check("副标题标了市场插件", "市场插件" in str(c.ev("document.getElementById('hubSub').textContent")))
        blen2 = c.ev("document.getElementById('hubBody').innerHTML.length") or 0
        check("详情页有内容", blen2 > 300, blen2)
        check("三个操作按钮都在", c.ev("!!document.getElementById('mktSwBtn')")
              and c.ev("!!document.getElementById('mktRepairBtn')")
              and c.ev("!!document.getElementById('mktOpenBtn')"))

        print("\n== 10) 缺目录的也要能点进去看原因 ==")
        c.ev("hubShow('dsh:dsh-ops')"); time.sleep(0.8)
        check("缺目录的也能打开", c.ev("getComputedStyle(document.getElementById('hubDetailHead')).display") == "flex")
        body3 = str(c.ev("document.getElementById('hubBody').innerHTML") or "")
        check("页面里写明包目录缺失", "包目录缺失" in body3)
        check("给出重装指引", "重新安装" in body3)

        print("\n== 11) 已安装列表里点市场插件也进详情 ==")
        c.ev("hubShow('installed')"); time.sleep(1)
        c.ev("(function(){var rows=document.querySelectorAll('#hubBody .hub-row');"
             "for(var i=0;i<rows.length;i++){if(rows[i].textContent.indexOf('dsh-soul-md')>=0){rows[i].click();return 'clicked'}}return 'notfound'})()")
        time.sleep(1)
        check("从列表点市场插件也进详情页",
              c.ev("getComputedStyle(document.getElementById('hubDetailHead')).display") == "flex"
              and c.ev("_hubPid") == "dsh:dsh-soul-md", c.ev("_hubPid"))
    finally:
        try:
            c.ws.close()
        except Exception:
            pass

    print("\n===== 结果：%d 项通过，%d 项失败 =====" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项:", FAIL)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
