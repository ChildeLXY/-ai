# -*- coding: utf-8 -*-
"""构建「大肥鱼内置修改版 DSH」前端。

做法（用户拍板：直接改官方 DSH 前端源码，由大肥鱼内置托管，不再起独立 dsh web）：
  1. 把官方 @deepseek-ai/dsh-web-frontend 的构建产物 dist 整份复制到 dsh_host/dfy-dsh/dist；
  2. 改 index.html / manifest 的标题与品牌名，并注入 dfy-override.js + dfy-override.css；
  3. 运行时覆盖层把侧栏品牌（data-slot=sidebar.brand.mark/name）替换成大肥鱼品牌，
     并对残留 DeepSeek 文案做兜底替换。
  4. 另含 patch_web_app()：让 dsh-web-app 的 resolveDistIndex 支持 DFY_DSH_DIST 环境变量，
     node 进程据此托管本目录，而不是官方 dist。

幂等：可反复运行，产物目录每次重建；web-app 补丁只打一次（带备份与标记）。
"""
import os
import shutil
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
SRC_DIST = os.path.join(
    BASE, "dsh_web", "node_modules", "@deepseek-ai",
    "dsh-web-frontend", "dist")
OUT_ROOT = os.path.join(BASE, "dsh_host", "dfy-dsh")
OUT_DIST = os.path.join(OUT_ROOT, "dist")

WEB_APP_JS = os.path.join(
    BASE, "dsh_web", "node_modules", "@deepseek-ai",
    "dsh-web-app", "lib", "index.js")
WEB_APP_BAK = WEB_APP_JS + ".bak-dfy"

FULL_NAME = "大肥鱼 · 内置 DSH"
SHORT_NAME = "大肥鱼 DSH"

OVERRIDE_JS = r"""(function(){
  'use strict';
  var FULL_NAME='大肥鱼 · 内置 DSH';
  function keepTitle(){
    try{
      if(document.title!==FULL_NAME && /deepseek|harness|^$/i.test(document.title)){
        document.title=FULL_NAME;
      }
    }catch(e){}
  }
  keepTitle();

  function setMark(){
    var m=document.querySelector('[data-slot="sidebar.brand.mark"]');
    if(m && !m.dataset.dfyMark){
      m.dataset.dfyMark='1';
      m.textContent='';
      var fish=document.createElement('span');
      fish.className='dfy-fish'; fish.textContent='\uD83D\uDC1F';
      m.appendChild(fish);
    }
    var n=document.querySelector('[data-slot="sidebar.brand.name"]');
    if(n && !n.dataset.dfyName){
      n.dataset.dfyName='1';
      n.textContent='';
      var w=document.createElement('span'); w.className='dfy-word'; w.textContent='大肥鱼';
      var s=document.createElement('span'); s.className='dfy-sub'; s.textContent='DSH';
      n.appendChild(w); n.appendChild(s);
    }
  }

  function replaceText(root){
    var walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT,{
      acceptNode:function(node){
        var p=node.parentElement; if(!p) return NodeFilter.FILTER_REJECT;
        var tag=p.tagName;
        if(tag==='SCRIPT'||tag==='STYLE'||tag==='TEXTAREA') return NodeFilter.FILTER_REJECT;
        return /deepseek/i.test(node.textContent)?NodeFilter.FILTER_ACCEPT:NodeFilter.FILTER_REJECT;
      }
    });
    var nodes=[],t;
    while(t=walker.nextNode()) nodes.push(t);
    nodes.forEach(function(tn){
      tn.textContent=tn.textContent
        .replace(/DeepSeek Harness/g,'大肥鱼 DSH')
        .replace(/deepseek harness/gi,'大肥鱼 DSH')
        .replace(/DeepSeek/g,'大肥鱼')
        .replace(/deepseek/g,'大肥鱼');
    });
  }

  function tick(){ try{ setMark(); }catch(e){} }
  function tick(){ try{ setMark(); }catch(e){} }
  tick();
  var obs=new MutationObserver(tick);
  obs.observe(document.documentElement,{childList:true,subtree:true});
  setInterval(keepTitle,1000);
  setTimeout(function(){ try{ replaceText(document.body); }catch(e){} },1200);
  setInterval(function(){ try{ replaceText(document.body); }catch(e){} },8000);

  /* ---- 首屏 hero：去掉官方鲸鱼 logo / 预览版徽标 / 官方文案 ---- */
  var HERO_TITLE='大肥鱼AI';
  function fixHero(){
    try{
      // 官方鲸鱼矢量图：直接隐藏（CSS 也已处理，这里兜底）
      document.querySelectorAll('.pXSMma_fishHitbox, [data-slot="conversation.hero.brand.mark"] svg')
        .forEach(function(el){ el.style.display='none'; });
      // 「预览版」徽标
      document.querySelectorAll('.pXSMma_previewBadge').forEach(function(el){
        el.style.display='none';
      });
      // 标题文案（React 会重渲染，用文本节点改写 + 定时守护）
      var tg=document.querySelectorAll('.pXSMma_titleGroup > span');
      tg.forEach(function(el){
        if(el.children.length===0 && el.textContent.trim()==='探索未至之境'){
          el.textContent=HERO_TITLE;
        }
      });
    }catch(e){}
  }
  fixHero();
  setInterval(fixHero,1200);

  /* ---- 账户区：剔除登录/退出一类入口（其余设置项保留） ---- */
  var AUTH_TEXT=/^(退出登录|登出|注销|切换账号|登录|Log ?out|Sign ?out|Sign ?in|Log ?in)$/i;
  function isAuthEntry(el){
    var t=(el.innerText||'').trim();
    if(!t) return false;
    // 菜单项通常自成一行，取第一行判断，避免整块菜单误伤
    var first=t.split('\n')[0].trim();
    return AUTH_TEXT.test(first);
  }
  function hideAuth(){
    try{
      document.querySelectorAll('button, a, [role="menuitem"], [role="button"]').forEach(function(el){
        if(el.dataset.dfyAuthHidden) return;
        if(isAuthEntry(el)){
          el.style.display='none';
          el.dataset.dfyAuthHidden='1';
        }
      });
    }catch(e){}
  }
  hideAuth();
  var authObs=new MutationObserver(hideAuth);
  authObs.observe(document.documentElement,{childList:true,subtree:true});
  setInterval(hideAuth,1000);
})();
"""

OVERRIDE_CSS = """/* 大肥鱼内置修改版 DSH：品牌覆盖样式（明暗主题自适应） */
[data-slot="sidebar.brand.mark"] .dfy-fish{
  display:inline-flex; align-items:center; justify-content:center;
  font-size:19px; line-height:1;
}
[data-slot="sidebar.brand.name"]{
  display:inline-flex !important; align-items:center; gap:6px;
}
[data-slot="sidebar.brand.name"] .dfy-word{
  font-weight:700; font-size:16px; letter-spacing:.3px; color:inherit; line-height:1;
}
[data-slot="sidebar.brand.name"] .dfy-sub{
  font-size:10px; font-weight:700; letter-spacing:.4px;
  color:#fff; background:#2f6bff;
  padding:1px 5px; border-radius:5px; line-height:1.2;
}

/* 首屏 hero：隐藏官方鲸鱼 logo 与「预览版」徽标 */
.pXSMma_fishHitbox{display:none !important;}
.pXSMma_previewBadge{display:none !important;}
"""


def copy_dist():
    if not os.path.isdir(SRC_DIST):
        raise SystemExit("官方前端 dist 不存在：%s" % SRC_DIST)
    if os.path.isdir(OUT_DIST):
        shutil.rmtree(OUT_DIST)
    os.makedirs(OUT_ROOT, exist_ok=True)
    shutil.copytree(SRC_DIST, OUT_DIST)


def patch_index():
    p = os.path.join(OUT_DIST, "index.html")
    t = open(p, encoding="utf-8").read()
    t = t.replace("<title>DeepSeek Harness</title>", "<title>%s</title>" % FULL_NAME)
    inject = ('<link rel="stylesheet" href="./dfy-override.css">\n'
              '    <script src="./dfy-override.js" defer></script>\n  </head>')
    if "dfy-override.js" not in t:
        t = t.replace("</head>", inject)
    open(p, "w", encoding="utf-8", newline="").write(t)


def patch_manifest():
    p = os.path.join(OUT_DIST, "manifest.webmanifest")
    t = open(p, encoding="utf-8").read()
    t = t.replace('"name": "DeepSeek Harness"', '"name": "%s"' % FULL_NAME)
    t = t.replace('"short_name": "DSH"', '"short_name": "%s"' % SHORT_NAME)
    open(p, "w", encoding="utf-8", newline="").write(t)


def write_overrides():
    open(os.path.join(OUT_DIST, "dfy-override.js"), "w",
         encoding="utf-8", newline="\n").write(OVERRIDE_JS)
    open(os.path.join(OUT_DIST, "dfy-override.css"), "w",
         encoding="utf-8", newline="\n").write(OVERRIDE_CSS)


def patch_web_app():
    """让 dsh-web-app 支持 DFY_DSH_DIST 覆盖 dist 目录（幂等，带备份）。"""
    t = open(WEB_APP_JS, encoding="utf-8").read()
    if "DFY_DSH_DIST" in t:
        return "already"
    if not os.path.isfile(WEB_APP_BAK):
        shutil.copy2(WEB_APP_JS, WEB_APP_BAK)
    needle = ('function resolveDistIndex() {\n'
              '\tconst require = createRequire(import.meta.url);')
    repl = ('function resolveDistIndex() {\n'
            '\tif (process.env.DFY_DSH_DIST) '
            'return join(process.env.DFY_DSH_DIST, "index.html");\n'
            '\tconst require = createRequire(import.meta.url);')
    if needle not in t:
        raise SystemExit("web-app 补丁锚点没找到，需人工确认（文件可能已升级）")
    open(WEB_APP_JS, "w", encoding="utf-8", newline="").write(t.replace(needle, repl))
    return "patched"


def restore_web_app():
    if os.path.isfile(WEB_APP_BAK):
        shutil.copy2(WEB_APP_BAK, WEB_APP_JS)
        print("已还原 dsh-web-app 官方文件")


def main():
    if "--restore" in sys.argv:
        restore_web_app()
        return
    copy_dist()
    patch_index()
    patch_manifest()
    write_overrides()
    wa = patch_web_app()
    n_files = sum(len(f) for _, _, f in os.walk(OUT_DIST))
    print("OK  修改版前端 -> %s" % OUT_DIST)
    print("    文件数=%d  web-app 补丁=%s" % (n_files, wa))
    print("    标题=%s" % FULL_NAME)


if __name__ == "__main__":
    main()
