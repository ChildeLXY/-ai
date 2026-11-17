#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主站内容调整：
1. 删除 #api 横幅（dabaifan.com 是别人的站，与本站无关）
2. 新增 #play 板块（陪她玩会儿）—— 不再整段都是 AI 能力介绍
3. #features 文案降 AI 味，突出"好玩、陪伴"的一面
4. 修掉 #sticker 应用截图空白（去掉 loading=lazy / decoding=async）
5. 导航同步
"""

import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "site-home", "index.html")

h = io.open(HTML, encoding="utf-8").read()
orig = h


def cut(start_marker, end_marker):
    """删掉 [start_marker, end_marker) 之间的内容"""
    global h
    i = h.find(start_marker)
    assert i >= 0, "not found: %s" % start_marker
    j = h.find(end_marker, i)
    assert j >= 0, "end not found: %s" % end_marker
    h = h[:i] + h[j:]


# ---------------------------------------------------------------- 1. 删掉 API 横幅
cut('  <section class="sec alt" id="api">', "</main>")

# CSS 里顺手删掉 .api-band 相关
h = re.sub(r"\.api-band[^{]*\{[^}]*\}", "", h)
h = re.sub(r"\.api-band h3\{[^}]*\}", "", h)
h = re.sub(r"\.api-band p\{[^}]*\}", "", h)
h = re.sub(r"\.api-band[^{]*\{[^}]*\}", "", h)
h = re.sub(r"\n{3,}", "\n\n", h)

# ---------------------------------------------------------------- 2. 修应用截图空白
h = h.replace(
    '<img src="assets/shot-sticker.webp" alt="大肥鱼AI 桌面助手的表情包面板" width="1600" height="1075" loading="lazy" decoding="async">',
    '<img src="assets/shot-sticker.webp" alt="大肥鱼AI 桌面助手的表情包面板" width="1600" height="1075">',
)

# ---------------------------------------------------------------- 3. features 文案
OLD_FEAT_HEAD = """          <h2 class="reveal">它能做什么</h2>
          <p class="reveal">不是套壳聊天窗，是一个住在本机的小家伙。</p>"""
NEW_FEAT_HEAD = """          <h2 class="reveal">它能做什么</h2>
          <p class="reveal">不是套壳聊天窗，是一个住在你本机、能陪你玩的小家伙。</p>"""
assert OLD_FEAT_HEAD in h
h = h.replace(OLD_FEAT_HEAD, NEW_FEAT_HEAD)

REPL = [
    # 本地推理 -> 说人话
    (
        """          <h3>本地推理</h3>
          <p>走本机 127.0.0.1 上的 Ollama，断网也照常说话。对话不经过任何云端 API，聊天记录只躺在你自己的硬盘里。</p>
          <span class="tagline">断网可用</span>""",
        """          <h3>不联网也照聊</h3>
          <p>装好之后拔了网线照样说话。它跑在你自己的电脑上，聊天记录只躺在你自己的硬盘里，谁也拿不走。</p>
          <span class="tagline">断网可用</span>""",
    ),
    # 长期记忆
    (
        """          <h3>长期记忆</h3>
          <p>分层记忆加按话题召回。它记得你上周说过什么，也会给每条消息标上真实发生的时间，隔天再聊不会串台。</p>
          <span class="tagline">跨会话不失忆</span>""",
        """          <h3>记得住你</h3>
          <p>上次提过的事它都记着，连消息是哪天说的都标得清清楚楚。隔几天再开，不会把你昨天的话当成今天。</p>
          <span class="tagline">跨会话不失忆</span>""",
    ),
    # 表情包
    (
        """          <h3>表情包</h3>
          <p>322 张手绘表情包按情绪分好类，开心、无奈、撒娇、阴阳怪气……它自己挑一张，合适的时机甩过来。</p>
          <span class="tagline">322 张</span>""",
        """          <h3>会发表情包</h3>
          <p>322 张手绘表情包按情绪分好类，开心、无奈、撒娇、阴阳怪气……它自己挑一张，看准时机就甩过来。</p>
          <span class="tagline">322 张</span>""",
    ),
    # 有心情
    (
        """          <h3>有心情</h3>
          <p>心情时间线会记下每次被安慰、被敷衍、被放鸽子的时刻。回你话的时候，语气也跟着变。</p>
          <span class="tagline">心情时间线</span>""",
        """          <h3>脾气会变</h3>
          <p>被安慰、被敷衍、被放鸽子，它都记在心里。下次回话的语气跟着变，不是复读机。</p>
          <span class="tagline">心情时间线</span>""",
    ),
    # 主动搭话
    (
        """          <h3>主动搭话</h3>
          <p>碎碎念守望者盯着桌面，你忙的时候它不打扰，闲下来就自己冒一句。桌宠也会满屋溜达、念念有词。</p>
          <span class="tagline">会自己开口</span>""",
        """          <h3>会自己冒泡</h3>
          <p>你忙的时候它不打扰，闲下来就自己冒一句。桌宠还会满屋溜达、念念有词，像家里多个人。</p>
          <span class="tagline">会自己开口</span>""",
    ),
    # 桌宠小剧场
    (
        """          <h3>桌宠小剧场</h3>
          <p>89 套动作、每套 12 帧。舞狮、宅舞、写代码、蹲着摆烂，手机端也能整套带进去。</p>
          <span class="tagline">89 套动作</span>""",
        """          <h3>会演小剧场</h3>
          <p>89 套动作、每套 12 帧。舞狮、宅舞、写代码、蹲着摆烂，你拽着她能满屏拖，手机端也整套带得走。</p>
          <span class="tagline">89 套动作</span>""",
    ),
]
for old, new in REPL:
    assert old in h, old[:40]
    h = h.replace(old, new)

# ---------------------------------------------------------------- 4. 新增 #play 板块
PLAY = """
  <section class="sec alt" id="play">
    <div class="sec-in">
      <div class="sec-head">
        <div>
          <h2 class="reveal">除了聊天，还能这么玩</h2>
          <p class="reveal">它不只是个输入框，后面还藏了一整套可以折腾的东西。</p>
        </div>
        <span class="count">03 / 05</span>
      </div>

      <div class="play-grid">
        <div class="play-card reveal">
          <span class="ico" aria-hidden="true"><svg width="19" height="19" viewBox="0 0 24 24" fill="none"><rect x="2.5" y="4" width="19" height="13" rx="2" stroke="currentColor" stroke-width="1.5"/><path d="M8 20.5h8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M7 8.5h4M7 11.5h7" stroke="currentColor" stroke-width="1.5" stroke-cap="round" stroke-linecap="round"/></svg></span>
          <h3>桌面版 / 网页版</h3>
          <p>同一套东西两种壳：Windows 上是独立小窗口，也可以直接开网页版，浏览器里待着。</p>
          <span class="tagline">两套前端</span>
        </div>
        <div class="play-card reveal">
          <span class="ico" aria-hidden="true"><svg width="19" height="19" viewBox="0 0 24 24" fill="none"><path d="M9 8.5L4.5 12 9 15.5M15 8.5L19.5 12 15 15.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
          <h3>插件中心</h3>
          <p>心情、表情包、主动搭话、灵魂记忆……每个插件一个独立页面，装不装、留几个，你自己定。</p>
          <span class="tagline">插件化</span>
        </div>
        <div class="play-card reveal">
          <span class="ico" aria-hidden="true"><svg width="19" height="19" viewBox="0 0 24 24" fill="none"><path d="M5 9.5c0-1.7 1.3-3 3-3s3 1.3 3 3-1.3 3-3 3-3-1.3-3-3z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M13 15.5c0-1.7 1.3-3 3-3s3 1.3 3 3-1.3 3-3 3-3-1.3-3-3z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M9.2 12.5l2.6 2.4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></span>
          <h3>B 站鱼塘</h3>
          <p>养鱼、投食、看它长大。无聊的时候打开喂两把，比刷视频有意思。</p>
          <span class="tagline">养鱼插件</span>
        </div>
        <div class="play-card reveal">
          <span class="ico" aria-hidden="true"><svg width="19" height="19" viewBox="0 0 24 24" fill="none"><path d="M11 4.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13z" stroke="currentColor" stroke-width="1.5"/><path d="M16 16l4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></span>
          <h3>源码随便拆</h3>
          <p>MIT 协议，代码全摊在仓库里。改人格卡、换表情包、加自己的插件，想拆就拆。</p>
          <span class="tagline">MIT 开源</span>
        </div>
        <div class="play-card reveal">
          <span class="ico" aria-hidden="true"><svg width="19" height="19" viewBox="0 0 24 24" fill="none"><path d="M4 10.5v3a1 1 0 001 1h3l5 4V5.5L8 9.5H5a1 1 0 00-1 1z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M16.5 9a4 4 0 010 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></span>
          <h3>能出声</h3>
          <p>接上 edge-tts 就能让她开口念。写完东西直接念给你听，懒得看屏幕的时候省事。</p>
          <span class="tagline">语音朗读</span>
        </div>
        <div class="play-card reveal">
          <span class="ico" aria-hidden="true"><svg width="19" height="19" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="8.5" stroke="currentColor" stroke-width="1.5"/><path d="M3.5 12h17M12 3.5c2.2 2.3 3.4 5.3 3.4 8.5S14.2 18.2 12 20.5c-2.2-2.3-3.4-5.3-3.4-8.5S9.8 5.8 12 3.5z" stroke="currentColor" stroke-width="1.4"/></svg></span>
          <h3>顺手查点资料</h3>
          <p>接上检索就能翻网页、找资料。不接也行，本地那点本事已经够用了。</p>
          <span class="tagline">可开关</span>
        </div>
      </div>

      <div class="play-cta reveal">
        <a class="btn btn-main" href="https://gitee.com/liu-xingyuec/dafeiyu-ai-local" target="_blank" rel="noopener">翻翻源码
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 8h9M8.5 4l4 4-4 4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </a>
        <a class="btn btn-line" href="https://game.dabaifan.cn/">先去看个游戏</a>
        <span class="note">MIT 协议 · 不用注册 · 数据不出本机</span>
      </div>
    </div>
  </section>

"""

# 插到 #sticker 之前（features 之后）
anchor = '  <section class="sec" id="sticker">'
assert anchor in h
h = h.replace(anchor, PLAY.lstrip("\n") + anchor)

# 计数编号顺延
h = h.replace('<span class="count">02 / 05</span>', '<span class="count">02 / 05</span>', 1)
h = h.replace('<span class="count">03 / 05</span>\n      </div>\n\n      <div class="req">',
              '<span class="count">04 / 05</span>\n      </div>\n\n      <div class="req">')
h = h.replace('<span class="count">04 / 05</span>\n      </div>\n      <div class="faq">',
              '<span class="count">05 / 05</span>\n      </div>\n      <div class="faq">')

# ---------------------------------------------------------------- 5. 导航
h = h.replace(
    '    <a href="#features">特色</a>\n    <a href="#faq">常见问题</a>',
    '    <a href="#features">特色</a>\n    <a href="#play">玩法</a>\n    <a href="#faq">常见问题</a>',
)

# ---------------------------------------------------------------- 6. play-grid 样式
CSS = """
.play-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.play-card{
  background:#fff;border:1px solid var(--line);border-radius:8px;padding:22px 20px;
  display:flex;flex-direction:column;gap:8px;
  transition:transform .18s ease,box-shadow .18s ease,border-color .18s ease;
}
.play-card:hover{transform:translateY(-3px);border-color:#c9d4ea;box-shadow:0 16px 34px -22px rgba(13,20,36,.35)}
.play-card .ico{width:34px;height:34px;border-radius:8px;background:#eef3ff;color:#2f6bff;display:flex;align-items:center;justify-content:center}
.play-card h3{font-size:15px;font-weight:700;letter-spacing:-.01em}
.play-card p{font-size:13px;line-height:1.75;color:#5b6577}
.play-card .tagline{
  margin-top:auto;align-self:flex-start;font-size:11px;color:#2f6bff;
  background:#eef3ff;border-radius:4px;padding:3px 8px;
}
.play-cta{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:26px}
.play-cta .note{font-size:12px;color:#8b94a5}
@media (max-width:1080px){.play-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:620px){.play-grid{grid-template-columns:1fr}}
"""
marker = ".fgrid{"
i = h.find(marker)
assert i >= 0
h = h[:i] + CSS.strip() + "\n\n" + h[i:]

# ---------------------------------------------------------------- 写回
assert h != orig
io.open(HTML, "w", encoding="utf-8", newline="").write(h)

print("OK  长度 %d -> %d" % (len(orig), len(h)))
for k in ["id=\"api\"", "api-band", "dabaifan.com", "id=\"play\"", "play-grid", "loading=\"lazy\""]:
    print("  %-18s x%d" % (k, h.count(k)))
