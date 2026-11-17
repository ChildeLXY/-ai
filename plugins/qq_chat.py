# -*- coding: utf-8 -*-
"""QQ 对话插件（内嵌版）================================================

让大肥鱼AI 通过 QQ 收发消息、自动配表情包。
真正的桥接逻辑在主程序的 qq_bridge.py（内嵌在主进程），本插件负责：
  · 在插件中心 / 左侧 dock 注册「QQ 对话 🐧」入口
  · 专属页面在前端 _hubViews.qq_chat 里渲染（状态/收发/配置）

API（qq_status / qq_start / qq_stop / qq_send / qq_reload / qq_set_autostart）
由主程序 大肥鱼AI.py 直接暴露，这里不重复注册，避免重名覆盖。
"""

PLUGIN_ID = "qq_chat"
PLUGIN_NAME = "QQ 对话"
PLUGIN_ICON = "🐧"
PLUGIN_VERSION = "1.0.0"

_CTX = None


def setup(ctx):
    global _CTX
    _CTX = ctx
    try:
        ctx.log(PLUGIN_ID, "QQ 对话插件已注册（桥接在主进程 qq_bridge.py）")
    except Exception:
        pass


def apis():
    # 不重复暴露 API——主程序已 expose qq_* 系列
    return []
