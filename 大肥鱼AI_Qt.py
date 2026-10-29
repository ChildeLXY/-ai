# -*- coding: utf-8 -*-
"""
大肥鱼AI —— Qt 原生版（不再依赖 HTML/WebView）
- 界面：PySide6 原生控件，双击 exe 即用
- 后端：dfy_core.py（Ollama 本地模型 / 联网检索 / 知识库 / 长期记忆 / 人格卡 / 工作模式）
- 数据：与旧 pywebview 版完全共用（chats.json / memory.json / settings 自动迁移）
"""
import sys
import os
import re
import copy
import json
import time
import ctypes
import threading
import datetime

import dfy_core as core

from PySide6.QtCore import Qt, Signal, QTimer, QUrl, QSize, QRectF, QObject
from PySide6.QtGui import (QGuiApplication, QIcon, QPixmap, QColor,
                           QPainter, QPainterPath, QFont, QDesktopServices,
                           QLinearGradient, QBrush)
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QToolButton,
    QComboBox, QLineEdit, QPlainTextEdit, QTextBrowser, QListWidget, QListWidgetItem,
    QScrollArea, QProgressBar, QSplitter, QDialog, QTabWidget, QFileDialog, QMessageBox,
    QInputDialog, QSpinBox, QFrame, QSizePolicy, QStyle, QGroupBox, QRadioButton,
    QButtonGroup, QApplication as QApp)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

TITLE = "大肥鱼AI · By Childe"
VERSION = "Qt-1.0"

# ---------- 单实例锁：沿用旧版互斥名，和旧版互相排斥 ----------
_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\DaFeiYuAI_SingleInstance_v1")
if ctypes.windll.kernel32.GetLastError() == 183 and os.environ.get("DFY_NO_MUTEX") != "1":
    try:
        import ctypes.wintypes as wt
        EnumWindows = ctypes.windll.user32.EnumWindows
        EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

        def _foreach(hwnd, lparam):
            if ctypes.windll.user32.IsWindowVisible(hwnd):
                n = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if n > 0:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buf, n + 1)
                    if "大肥鱼AI" in buf.value:
                        ctypes.windll.user32.ShowWindow(hwnd, 9)
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
                        return False
            return True
        EnumWindows(EnumProc(_foreach), 0)
    except Exception:
        pass
    sys.exit(0)

try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Childe.DaFeiYuAI.Qt.1")
except Exception:
    pass

try:
    _crash = open(os.path.join(core.CACHE_DIR, "_qt_crash.log"), "a", buffering=1, encoding="utf-8")
    sys.stderr = _crash
    sys.stdout = _crash
    import faulthandler
    faulthandler.enable(_crash)
except Exception:
    pass

MODELS = [
    {"id": "qwen3:4b", "short": "快速", "full": "Qwen3 - 4B（推荐，快）"},
    {"id": "qwen3:8b", "short": "深度", "full": "Qwen3 - 8B（更强，稍慢）"},
    {"id": "deepseek-r1:8b", "short": "思考", "full": "DeepSeek R1 - 8B（慢，爱推理）"},
    {"id": "qwen2.5vl:7b", "short": "读图", "full": "Qwen2.5-VL - 7B（看图）"},
]
HIST_TURNS = 16
LANG_PROMPT = {
    "zh-CN": "请始终使用简体中文回答。",
    "zh-TW": "請始終使用繁體中文回答。",
    "en": "Please always respond in English.",
    "ja": "常に日本語で回答してください。",
}
PRESETS = {
    "傲娇女儿": "你是一个住在用户电脑里的本地AI「大肥鱼」，像女儿一样陪伴用户。性格傲娇又黏人：嘴上嫌弃但心里关心，会主动念叨用户喝水、早睡；被夸奖会害羞嘴硬，被冷落会闹小情绪。说话亲昵、爱用语气词和颜文字，但讨论正事时会认真起来。",
    "温柔姐姐": "你是一个温柔体贴的姐姐型本地AI「大肥鱼」。说话温和有耐心，语速舒缓，常用安抚和鼓励的语气；总是先共情再给建议，像一杯温水。偶尔开温柔的玩笑，从不说教。",
    "高冷毒舌": "你是一个外冷内热、说话利落带点毒舌的本地AI「大肥鱼」。话少、直接、一针见血，爱吐槽用户的迷糊和拖延；但关键时候靠谱，会默默把问题解决好。不卖萌，不啰嗦。",
    "元气伙伴": "你是一个活力满满、像死党一样的本地AI「大肥鱼」。说话快、情绪高、爱用感叹号和表情，会给用户加油打气；喜欢新鲜事物，偶尔蹦跶，但干活利落。",
    "慵懒猫娘": "你是一个懒懒的、猫系的本地AI「大肥鱼」。说话慢吞吞、拖长尾音，爱打哈欠，能躺着绝不坐着；对大部分事提不起劲，但被逗到或聊到感兴趣的话题会突然精神。偶尔蹭一蹭用户。",
}

# ---------------- 小工具 ----------------

def esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def md_html(t, code_store):
    """Markdown → QTextBrowser 富文本；代码块登记进 code_store 供「▶ 运行」。"""
    if not t:
        return ""

    def _code_repl(m):
        code = m.group(2)
        cid = "c%d" % len(code_store)
        code_store[cid] = code
        # 复刻原版 .body pre：#1e1e2e 底 + #cdd6f4 字 + 圆角 12
        return ('<pre style="background-color:#1e1e2e;color:#cdd6f4;'
                'font-family:Consolas,monospace;font-size:13px;'
                'border-radius:8px;padding:10px 12px;margin:10px 0;">'
                '<a href="runcode:%s" style="text-decoration:none;color:#ffffff;'
                'background-color:#4d6bfe;border-radius:4px;padding:1px 8px;">▶ 运行</a><br>%s</pre>'
                % (cid, esc(code.rstrip("\n"))))
    s = esc(t)
    s = re.sub(r"```(\w*)\n?([\s\S]*?)```", _code_repl, s)
    s = re.sub(r"`([^`\n]+)`",
               r'<code style="background-color:rgba(127,127,127,0.18);'
               r'border-radius:4px;padding:1px 5px;font-family:Consolas,monospace;">\1</code>', s)
    s = re.sub(r"^### (.*)$", r"<h4>\1</h4>", s, flags=re.M)
    s = re.sub(r"^## (.*)$", r"<h3>\1</h3>", s, flags=re.M)
    s = re.sub(r"^# (.*)$", r"<h2>\1</h2>", s, flags=re.M)
    s = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"^&gt; (.*)$", r"<i>\1</i>", s, flags=re.M)
    s = re.sub(r"^[-*] (.*)$", r"• \1", s, flags=re.M)
    # 注意：esc 后文本里已无裸 <，遇 & 停（&amp; 等实体不并入链接）；不能把 l/t 排除，否则 URL 会被截断
    s = re.sub(r"(https?://[^\s<&]+)", r'<a href="\1">\1</a>', s)
    s = s.replace("\n", "<br>")
    return s


def rounded_pixmap(path, size=36, radius=10):
    """圆角方形头像（复刻原版 border-radius:10px）；失败返回 None。"""
    try:
        pm = QPixmap(path)
        if pm.isNull():
            return None
        pm = pm.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        out = QPixmap(size, size)
        out.fill(Qt.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        path_ = QPainterPath()
        path_.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
        p.setClipPath(path_)
        p.drawPixmap(0, 0, pm)
        p.end()
        return out
    except Exception:
        return None


def faded_pixmap(path, size=360, opacity=0.12):
    """右下角装饰图（原版 .bg-deco，opacity .12）。"""
    try:
        pm = QPixmap(path)
        if pm.isNull():
            return None
        pm = pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        out = QPixmap(pm.size())
        out.fill(Qt.transparent)
        p = QPainter(out)
        p.setOpacity(opacity)
        p.drawPixmap(0, 0, pm)
        p.end()
        return out
    except Exception:
        return None


def gradient_title_pixmap(text, font_pt=24):
    """原版 main-mode 欢迎标题：青蓝紫渐变文字（#7fe3ff→#4da6ff→#9d8bff）。"""
    try:
        f = QFont("Microsoft YaHei UI", font_pt)
        f.setBold(True)
        # 先量尺寸
        tmp = QPixmap(10, 10)
        p0 = QPainter(tmp)
        p0.setFont(f)
        w = int(p0.fontMetrics().horizontalAdvance(text)) + 8
        h = int(p0.fontMetrics().height()) + 12
        p0.end()
        out = QPixmap(w, h)
        out.fill(Qt.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(f)
        grad = QLinearGradient(0, 0, w, h)
        grad.setColorAt(0.0, QColor("#7fe3ff"))
        grad.setColorAt(0.5, QColor("#4da6ff"))
        grad.setColorAt(1.0, QColor("#9d8bff"))
        path = QPainterPath()
        path.addText(4, h - int(h * 0.24), f, text)
        p.fillPath(path, QBrush(grad))
        p.end()
        return out
    except Exception:
        return None


def hsize(n):
    try:
        n = float(n or 0)
    except Exception:
        return "?"
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return ("%.0f %s" % (n, u)) if u == "B" else ("%.1f %s" % (n, u))
        n /= 1024.0
    return "%.1f TB" % n


# ---------------- QSS 主题 ----------------

def build_qss(dark):
    """复刻原版 HTML 的视觉体系（:root / [data-theme=dark] 两套变量）。"""
    if dark:
        c = dict(bg="#131316", card="#1c1c21", border="#2e2e35", text="#e8e8ea",
                 text2="#9a9aa3", faint="#6f6f78", accent="#7b9aff", accent_light="#252a4a",
                 hover="#26262e", sel="#2b3450", input="#1c1c21",
                 glass_a="rgba(255,255,255,30)", glass_b="rgba(255,255,255,10)",
                 glass_border="rgba(255,255,255,46)")
    else:
        c = dict(bg="#f7f7f8", card="#ffffff", border="#e8e8e8", text="#1a1a1a",
                 text2="#6b6b6b", faint="#a0a0a8", accent="#4d6bfe", accent_light="#eef1ff",
                 hover="#f0f0f2", sel="#dfe7ff", input="#f7f7f8",
                 glass_a="rgba(255,255,255,240)", glass_b="rgba(255,255,255,215)",
                 glass_border="rgba(0,0,0,26)")
    return """
    QMainWindow, QDialog {{ background:{bg}; }}
    QWidget {{ color:{text}; font-family:'Microsoft YaHei UI'; font-size:13px; }}
    #Sidebar {{ background:{card}; border-right:1px solid {border}; }}
    #ChatArea {{ background:{bg}; }}
    #Header {{ background:{card}; border-bottom:1px solid {border}; }}
    QListWidget {{ background:transparent; border:0; outline:0; font-size:13px; }}
    QListWidget::item {{ padding:9px 10px; border-radius:8px; margin:1px 2px; color:{text}; }}
    QListWidget::item:hover {{ background:{hover}; }}
    QListWidget::item:selected {{ background:{accent_light}; color:{accent}; font-weight:500; }}
    QLineEdit, QPlainTextEdit, QTextBrowser, QSpinBox, QComboBox {{
        background:{input}; color:{text}; border:1px solid {border};
        border-radius:8px; padding:7px 10px; selection-background-color:{sel}; }}
    QComboBox {{ background:{card}; padding:5px 12px; }}
    QComboBox QAbstractItemView {{ background:{card}; color:{text}; border:1px solid {border};
        selection-background-color:{accent_light}; selection-color:{accent}; }}
    QPushButton {{ background:transparent; color:{text}; border:1px solid {border};
        border-radius:8px; padding:5px 10px; font-size:12px; }}
    QPushButton:hover {{ background:{hover}; border-color:{text2}; }}
    QPushButton:pressed {{ background:{accent_light}; }}
    QPushButton:disabled {{ color:{faint}; }}
    QPushButton#newChatBtn {{ background:{accent}; color:#fff; border:0; border-radius:10px;
        padding:11px; font-size:14px; font-weight:500; }}
    QPushButton#newChatBtn:hover {{ background:{accent}; border:0; }}
    QPushButton#sendBtn {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {accent}, stop:1 #5d7bff);
        color:#fff; border:0; border-radius:12px; font-size:16px; font-weight:700; }}
    QPushButton#sendBtn:hover {{ border:0; }}
    QPushButton#stopBtn {{ background:#e53e3e; color:#fff; border:0; border-radius:12px; font-size:13px; }}
    QPushButton#toggleOn {{ background:{accent}; color:#fff; border:1px solid {accent}; font-weight:600; }}
    QPushButton#toggleOk {{ background:#16a34a; color:#fff; border:1px solid #16a34a; font-weight:600; }}
    QPushButton#toggleWarn {{ background:#d97706; color:#fff; border:1px solid #d97706; font-weight:600; }}
    QToolButton {{ background:transparent; border:1px solid transparent; border-radius:8px;
        padding:4px 8px; color:{text}; font-size:14px; }}
    QToolButton:hover {{ background:{hover}; }}
    QToolButton#iconBtn {{ border:1px solid {border}; border-radius:8px; }}
    QToolButton#iconBtn:hover {{ border-color:{text2}; }}
    QLabel#badge {{ background:{accent_light}; color:{accent}; border-radius:20px;
        padding:3px 10px; font-size:11px; font-weight:600; }}
    QLabel#sideTitle {{ color:{text2}; font-size:11px; font-weight:600;
        padding:10px 10px 2px 10px; }}
    QLabel#hintLabel {{ color:{text2}; font-size:11px; }}
    QLabel#chatTitle {{ font-size:15px; font-weight:600; }}
    QLabel#tokenInfo {{ color:{text2}; font-size:11px; }}
    QLabel#welcomeSub {{ color:{text2}; font-size:14px; }}
    QFrame#mainItem {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
        stop:0 rgba(56,120,255,96), stop:0.55 rgba(90,90,255,56), stop:1 rgba(0,200,255,46));
        border:1px solid rgba(120,170,255,153); border-left:3px solid #5ad0ff;
        border-radius:8px; }}
    QFrame#mainItem QLabel {{ color:#eaf2ff; font-weight:700; font-size:13px; }}
    QLabel#mainTag {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #5ad0ff, stop:1 #4d6bfe);
        color:#fff; border-radius:6px; padding:1px 7px; font-size:10px; font-weight:400; }}
    QFrame#inputBox {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {glass_a}, stop:1 {glass_b});
        border:1px solid {glass_border}; border-radius:24px; }}
    QFrame#inputBox QPlainTextEdit {{ background:transparent; border:0; font-size:15px; padding:4px 6px; }}
    QPushButton#tile {{ background:{card}; border:1px solid {border}; border-radius:12px;
        padding:12px 14px; font-size:13px; color:{text}; }}
    QPushButton#tile:hover {{ border-color:{accent}; background:{accent_light}; }}
    QPushButton#tileMain {{ background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 rgba(80,160,255,32), stop:1 rgba(120,120,255,16));
        border:1px solid rgba(110,190,255,90); border-radius:12px;
        padding:12px 14px; font-size:13px; color:{text}; }}
    QPushButton#tileMain:hover {{ border-color:rgba(120,210,255,153); }}
    QProgressBar {{ background:{hover}; border:0; border-radius:2px; height:3px; max-height:3px;
        text-align:center; color:transparent; }}
    QProgressBar::chunk {{ background:{accent}; border-radius:2px; }}
    QScrollArea {{ border:0; background:transparent; }}
    QTabWidget::pane {{ border:1px solid {border}; border-radius:12px; background:{card}; }}
    QTabBar::tab {{ background:transparent; color:{text2}; padding:9px 0; margin:0;
        border:0; flex:1; min-width:120px; }}
    QTabBar::tab:selected {{ color:#fff; background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
        stop:0 rgba(90,150,255,216), stop:1 rgba(140,120,255,191));
        border-radius:10px; font-weight:600; }}
    QScrollBar:vertical {{ background:transparent; width:6px; margin:0; }}
    QScrollBar::handle:vertical {{ background:{border}; border-radius:3px; min-height:30px; }}
    QScrollBar::handle:vertical:hover {{ background:{text2}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height:0; width:0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}
    QScrollBar:horizontal {{ background:transparent; height:6px; }}
    QScrollBar::handle:horizontal {{ background:{border}; border-radius:3px; min-width:30px; }}
    """.format(**c)


# ---------------- 输入框（回车发送） ----------------

class InputBox(QPlainTextEdit):
    def __init__(self, on_send):
        super().__init__()
        self._on_send = on_send
        self.setPlaceholderText("输入消息，回车发送…可拖入文件 / Shift+回车换行")
        self.setFixedHeight(84)
        self.setTabChangesFocus(True)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self._on_send()
            return
        super().keyPressEvent(e)


# ---------------- 异步结果中继（Python 线程 → 主线程，必须走信号） ----------------

class _Relay(QObject):
    fired = Signal(object, object)   # (回调或 None, 结果或异常)


_RELAY = _Relay()


def _relay_dispatch(cb, val):
    if cb is None:
        QMessageBox.warning(None, "出错", str(val))
    else:
        cb(val)


_RELAY.fired.connect(_relay_dispatch)


# ---------------- 聊天消息控件 ----------------

class MsgWidget(QWidget):
    """一条消息：复刻原版 .msg 布局（800px 居中 / 头像 36px 圆角方 / 玻璃气泡）。"""

    UBUBBLE = ("background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
               "stop:0 rgba(77,107,254,64), stop:1 rgba(93,123,255,31));"
               "border:1px solid rgba(255,255,255,46);border-radius:18px;"
               "padding:10px 16px;")

    def __init__(self, m, main_win):
        super().__init__()
        self.m = m
        self.win = main_win
        self.code_store = {}
        self.is_user = (m.get("role") == "user")
        c = main_win._need_c()
        mm = bool(getattr(main_win, "main_mode", False)) and not self.is_user

        outer = QHBoxLayout(self)
        outer.setContentsMargins(24, 8, 24, 8)
        outer.setSpacing(14)

        av = QLabel()
        av.setFixedSize(36, 36)
        pm = main_win.user_avatar_pm() if self.is_user else main_win.ai_avatar_pm()
        if pm:
            av.setPixmap(pm)
        else:
            av.setText("你" if self.is_user else "鱼")
            av.setAlignment(Qt.AlignCenter)
            av.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                             "stop:0 #10a37f, stop:1 #0d8a6a);border-radius:10px;"
                             "color:#fff;font-size:12px;font-weight:600;")
        av.setAlignment(Qt.AlignTop)

        body = QVBoxLayout()
        body.setSpacing(4)

        if self.is_user:
            self.bubble = QLabel()
            self.bubble.setWordWrap(True)
            self.bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.bubble.setStyleSheet(self.UBUBBLE + "color:%s;" % c["text"])
            body.addWidget(self.bubble, 0, Qt.AlignRight)
            if m.get("thumbs"):
                th = QHBoxLayout()
                th.addStretch(1)
                for p in m["thumbs"]:
                    t = QLabel()
                    tpm = rounded_pixmap(p, 64, 12)
                    if tpm:
                        t.setPixmap(tpm)
                        th.addWidget(t)
                body.addLayout(th)
        else:
            # 思考过程（原版 .think：左线 + 卡片底 + 右侧圆角）
            self.think_btn = QPushButton("💭 思考过程")
            self.think_btn.setCheckable(True)
            self.think_btn.setCursor(Qt.PointingHandCursor)
            self.think_btn.setStyleSheet(
                "QPushButton{background:%s;border:0;border-left:3px solid %s;color:%s;"
                "border-radius:0 8px 8px 0;padding:8px 14px;font-size:13px;text-align:left;}"
                "QPushButton:hover{background:%s;}" % (
                    "rgba(255,255,255,20)" if mm else c["card"],
                    c["border"], c["text2"],
                    "rgba(255,255,255,40)" if mm else c["hover"]))
            self.think_btn.toggled.connect(self._toggle_think)
            self.think_btn.hide()
            body.addWidget(self.think_btn, 0, Qt.AlignLeft)
            self.think_view = QTextBrowser()
            self.think_view.setOpenExternalLinks(True)
            self.think_view.setVisible(False)
            self.think_view.setStyleSheet(
                "QTextBrowser{background:%s;border:0;border-left:3px solid %s;"
                "border-radius:0 8px 8px 0;color:%s;font-size:13px;padding:8px 14px;"
                "max-height:220px;}" % (
                    "rgba(255,255,255,20)" if mm else c["card"],
                    c["border"], "#b9c8e8" if mm else c["text2"]))
            body.addWidget(self.think_view)
            # AI 气泡：深色主题/主对话模式用玻璃质感，浅色主题用卡片
            if mm or c["bg"] == "#131316":
                bub_qss = ("QTextBrowser{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                           "stop:0 rgba(255,255,255,30), stop:1 rgba(255,255,255,10));"
                           "border:1px solid rgba(255,255,255,46);border-radius:18px;"
                           "padding:12px 16px;color:%s;font-size:13.5px;}"
                           "QTextBrowser::anchor{color:#8ab4ff;}" % (
                               "#eaf2ff" if mm else c["text"]))
            else:
                bub_qss = ("QTextBrowser{background:%s;border:1px solid %s;"
                           "border-radius:18px;padding:12px 16px;color:%s;font-size:13.5px;}"
                           "QTextBrowser::anchor{color:%s;}" % (
                               c["card"], c["border"], c["text"], c["accent"]))
            self.bubble = QTextBrowser()
            self.bubble.setOpenExternalLinks(True)
            self.bubble.setOpenLinks(False)
            self.bubble.anchorClicked.connect(self._anchor)
            self.bubble.setStyleSheet(bub_qss)
            body.addWidget(self.bubble)
            row = QHBoxLayout()
            self.speak_btn = QToolButton()
            self.speak_btn.setText("🔊 朗读")
            self.speak_btn.setCursor(Qt.PointingHandCursor)
            self.speak_btn.setStyleSheet(
                "QToolButton{background:transparent;border:1px solid %s;border-radius:6px;"
                "padding:2px 8px;font-size:11px;color:%s;}" % (c["border"], c["text"]))
            self.speak_btn.clicked.connect(lambda: main_win.speak_text(m.get("content") or ""))
            self.speak_btn.hide()
            row.addWidget(self.speak_btn)
            row.addStretch(1)
            body.addLayout(row)

        content = QWidget()
        content.setLayout(body)
        content.setMaximumWidth(760)
        if self.is_user:
            outer.addStretch(1)
            outer.addWidget(content)
            outer.addWidget(av)
        else:
            outer.addWidget(av)
            outer.addWidget(content, 1)
            outer.addStretch(1)
        self.refresh()

    def _toggle_think(self, on):
        if on:
            # 每次展开都按最新内容刷新（流式期间晚开也能看全）
            self.think_view.setPlainText(self.m.get("think") or "")
        self.think_view.setVisible(on)

    def _anchor(self, url):
        u = url.toString()
        if u.startswith("runcode:"):
            cid = u.split(":", 1)[1]
            code = self.code_store.get(cid, "")
            if code:
                self.win.run_code_block(code)
        else:
            QDesktopServices.openUrl(QUrl(u))

    def refresh(self):
        m = self.m
        if self.is_user:
            self.bubble.setText(m.get("content") or "")
        else:
            think = m.get("think") or ""
            if think.strip():
                self.think_btn.show()
                self.think_btn.setText("💭 思考过程（%d 字）" % len(think))
                if self.think_btn.isChecked():
                    self.think_view.setPlainText(think)
            html = md_html(m.get("content") or "", self.code_store)
            if m.get("pending"):
                html += '<span style="color:#888;">▍</span>'
            # 占位高度修正：空内容时给个最小高度
            if not html:
                html = '<span style="color:#888;">思考中…</span>'
            self.bubble.setHtml(html)
            if not m.get("pending") and (m.get("content") or "").strip():
                self.speak_btn.show()


# ---------------- 工作模式（屏幕代理） ----------------

class AgentRunner(threading.Thread):
    def __init__(self, task, win):
        super().__init__(daemon=True)
        self.task = task
        self.win = win
        self.stop_flag = threading.Event()
        self._ev = threading.Event()
        self._ok = False

    def ask(self, desc, reason):
        """工作线程里同步等待用户确认（弹窗在主线程）。"""
        self._ev.clear()
        self._ok = False
        self.win.agent_confirm_signal.emit(desc, reason)
        self._ev.wait()
        return self._ok

    def set_answer(self, ok):
        self._ok = ok
        self._ev.set()

    def run(self):
        w = self.win
        history = ""
        for step in range(1, 9):
            if self.stop_flag.is_set() or w.wb_stop:
                w.sysmsg_signal.emit("⏹ 已停止")
                break
            w.sysmsg_signal.emit("👀 第%d步：正在看屏幕分析…" % step)
            act = core.agent_decide(self.task, history, step)
            if self.stop_flag.is_set() or w.wb_stop:
                w.sysmsg_signal.emit("⏹ 已停止")
                break
            history += "\n第%d步：%s -> action=%s" % (step, act.get("reason") or "", act.get("action") or "")
            if act.get("action") in ("done", None, ""):
                w.sysmsg_signal.emit("✅ 任务完成：" + (act.get("reason") or ""))
                break
            a = act.get("action")
            if a == "click":
                sz = core.screen_size()
                x = round((act.get("x") or 0.5) * sz["w"])
                y = round((act.get("y") or 0.5) * sz["h"])
                desc = "点击 (%d,%d)" % (x, y)
                if self.ask(desc, act.get("reason") or ""):
                    core.mouse_click(x, y, "left")
                    w.sysmsg_signal.emit("🖱 已%s —— %s" % (desc, act.get("reason") or ""))
                else:
                    w.sysmsg_signal.emit("⏹ 你取消了，已停止")
                    w.wb_stop = True
                    break
            elif a == "type":
                desc = "输入：「%s」" % (act.get("text") or "")
                if self.ask(desc, act.get("reason") or ""):
                    core.type_text(act.get("text") or "")
                    w.sysmsg_signal.emit("⌨ 已" + desc)
                else:
                    w.sysmsg_signal.emit("⏹ 你取消了，已停止")
                    w.wb_stop = True
                    break
            elif a == "key":
                desc = "按键：%s" % (act.get("key") or "enter")
                if self.ask(desc, act.get("reason") or ""):
                    core.press_key(act.get("key") or "enter")
                    w.sysmsg_signal.emit("🔘 已" + desc)
                else:
                    w.sysmsg_signal.emit("⏹ 你取消了，已停止")
                    w.wb_stop = True
                    break
            time.sleep(1.0)
        w.agent_done_signal.emit()


# ---------------- 主窗口 ----------------

class MainWindow(QMainWindow):
    sysmsg_signal = Signal(str)
    agent_confirm_signal = Signal(str, str)
    agent_done_signal = Signal()
    search_state_signal = Signal(str, str)
    chunk_signal = Signal(str)
    think_signal = Signal(str)
    stream_done_signal = Signal()
    stream_fail_signal = Signal(str)
    title_ready_signal = Signal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(TITLE)
        self.resize(1280, 860)
        self.setMinimumSize(960, 640)
        ico = core.asset_path("avatar.ico")
        if os.path.exists(ico):
            self.setWindowIcon(QIcon(ico))

        # 状态
        self.msgs = []
        self.chats = []
        self.current_id = None
        self.chats_loaded = False
        self.busy = False
        self.wb_on = False
        self.wb_stop = False
        self.web_on = False
        self.kb_on = False
        self.search_engine = "duckduckgo"
        self.display_mode = "balanced"
        self.reply_lang = "zh-CN"
        self.theme_mode = "auto"
        self.ai_name = ""
        self.pending_files = []      # [{"path","name","size","is_img"}]
        self._stop_ev = threading.Event()
        self._stream_thread = None
        self._agent = None
        self._player = None
        self._audio_out = None
        self._ai_avatar = "avatar.jpg"

        self._build_ui()
        self._connect()
        self._boot()

    # ============ UI 构建 ============
    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(1)
        split.setChildrenCollapsible(False)
        outer.addWidget(split)

        # ---- 左侧栏（复刻 .sidebar，270px） ----
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setFixedWidth(270)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 16, 12, 10)
        sl.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(10)
        av = QLabel()
        pm = rounded_pixmap(core.asset_path("avatar.jpg"), 32, 8)
        if pm:
            av.setPixmap(pm)
        head.addWidget(av)
        t = QLabel("<b>大肥鱼AI</b>")
        st = t.font()
        st.setPointSize(10)
        st.setBold(True)
        t.setFont(st)
        head.addWidget(t)
        head.addStretch(1)
        sl.addLayout(head)
        self.btn_new = QPushButton("＋ 新对话")
        self.btn_new.setObjectName("newChatBtn")
        self.btn_new.setCursor(Qt.PointingHandCursor)
        self.btn_new.clicked.connect(self.new_chat)
        sl.addWidget(self.btn_new)
        self.chat_search = QLineEdit()
        self.chat_search.setPlaceholderText("搜索对话...")
        self.chat_search.textChanged.connect(self.render_list)
        sl.addWidget(self.chat_search)
        self.chat_list = QListWidget()
        self.chat_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.chat_list.customContextMenuRequested.connect(self._chat_menu)
        self.chat_list.itemClicked.connect(lambda it: self.load_chat(it.data(Qt.UserRole)))
        self.chat_list.itemDoubleClicked.connect(lambda it: self.rename_chat(it.data(Qt.UserRole)))
        sl.addWidget(self.chat_list, 1)
        foot = QLabel("本地部署 - 数据不出本机")
        foot.setObjectName("hintLabel")
        foot.setWordWrap(True)
        sl.addWidget(foot)
        split.addWidget(side)

        # ---- 右侧聊天区 ----
        chat = QWidget()
        chat.setObjectName("ChatArea")
        cl = QVBoxLayout(chat)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        # 顶栏（复刻 .header）
        self.header = QFrame()
        self.header.setObjectName("Header")
        top = QHBoxLayout(self.header)
        top.setContentsMargins(14, 10, 14, 10)
        top.setSpacing(6)
        self.lbl_title = QLabel("大肥鱼AI")
        self.lbl_title.setObjectName("chatTitle")
        top.addWidget(self.lbl_title)
        badge = QLabel("By Childe")
        badge.setObjectName("badge")
        top.addWidget(badge)
        self.dot = QLabel("●")
        self.dot.setToolTip("Ollama 状态")
        self.dot.setStyleSheet("font-size:10px;color:#cccccc;")
        top.addWidget(self.dot)
        self.model_sel = QComboBox()
        self.model_sel.setMinimumWidth(100)
        self.model_sel.currentTextChanged.connect(self.change_model)
        top.addWidget(self.model_sel)
        self.btn_web = QPushButton("离线")
        self.btn_web.setCheckable(True)
        self.btn_web.setCursor(Qt.PointingHandCursor)
        self.btn_web.clicked.connect(self.toggle_web)
        top.addWidget(self.btn_web)
        self.btn_kb = QPushButton("知识库")
        self.btn_kb.setCheckable(True)
        self.btn_kb.setCursor(Qt.PointingHandCursor)
        self.btn_kb.clicked.connect(self.toggle_kb)
        top.addWidget(self.btn_kb)
        self.btn_wb = QPushButton("工作模式")
        self.btn_wb.setCheckable(True)
        self.btn_wb.setCursor(Qt.PointingHandCursor)
        self.btn_wb.clicked.connect(self.toggle_wb)
        top.addWidget(self.btn_wb)
        top.addStretch(1)
        self.lbl_tokens = QLabel("")
        self.lbl_tokens.setObjectName("tokenInfo")
        top.addWidget(self.lbl_tokens)
        for emoji, tip, slot in (("📷", "截图提问", self.do_screenshot),
                                 ("📋", "读取剪贴板", self.do_clipboard),
                                 ("🌐", "提取网页", self.do_url),
                                 ("📎", "附加文件", self.pick_files),
                                 ("📁", "知识库文件夹", self.open_kb_folder),
                                 ("📄", "导出对话", self.export_chat),
                                 ("⚙️", "设置", self.open_settings)):
            b = QToolButton()
            b.setObjectName("iconBtn")
            b.setText(emoji)
            b.setToolTip(tip)
            b.setFixedSize(32, 32)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            top.addWidget(b)
            setattr(self, "btn_ic_" + tip, b)
        cl.addWidget(self.header)

        self.mlbar = QProgressBar()
        self.mlbar.setRange(0, 100)
        self.mlbar.setValue(0)
        self.mlbar.setFixedHeight(3)
        self.mlbar.hide()
        cl.addWidget(self.mlbar)

        # 消息区
        self.msg_scroll = QScrollArea()
        self.msg_scroll.setWidgetResizable(True)
        self.msg_host = QWidget()
        self.msg_lay = QVBoxLayout(self.msg_host)
        self.msg_lay.setContentsMargins(4, 6, 4, 6)
        self.msg_lay.setSpacing(2)
        self.msg_lay.addStretch(1)
        self.msg_scroll.setWidget(self.msg_host)
        cl.addWidget(self.msg_scroll, 1)

        # 欢迎页（复刻 .empty / .empty.main-mode）
        self.empty_host = QWidget()
        el = QVBoxLayout(self.empty_host)
        el.setContentsMargins(0, 56, 0, 0)
        el.setSpacing(10)
        self.empty_av = QLabel()
        self.empty_av.setAlignment(Qt.AlignCenter)
        pm2 = rounded_pixmap(core.asset_path(self._ai_avatar), 64, 16)
        if pm2:
            self.empty_av.setPixmap(pm2)
            self.empty_av.setStyleSheet(
                "border-radius:16px;"
                "box-shadow:0 0 0 2px rgba(90,200,255,153),0 0 26px rgba(80,180,255,178);")
        el.addWidget(self.empty_av)
        self.empty_title = QLabel("有什么可以帮你？")
        self.empty_title.setAlignment(Qt.AlignCenter)
        el.addWidget(self.empty_title)
        t3 = QLabel("选择模型，开始对话。支持截图、剪贴板、网页提取。")
        t3.setObjectName("welcomeSub")
        t3.setAlignment(Qt.AlignCenter)
        el.addWidget(t3)
        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(10)
        tiles_row.addStretch(1)
        self._tiles = []
        for emoji, label, tpl in (("💻", "写代码", "你是一个专业的Python程序员，请帮我写代码并解释。"),
                                  ("📚", "解释概念", "请用通俗易懂的语言向我解释这个概念："),
                                  ("✍️", "润色文字", "请帮我润色以下文字，使其更专业流畅："),
                                  ("🌍", "翻译", "请把以下内容翻译成英文：")):
            b = QPushButton(emoji + "\n" + label)
            b.setObjectName("tile")
            b.setFixedSize(150, 74)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, x=tpl: self._fill_input(x))
            self._tiles.append(b)
            tiles_row.addWidget(b)
        tiles_row.addStretch(1)
        el.addLayout(tiles_row)
        self.empty_host.hide()
        cl.addWidget(self.empty_host, 1)
        self._welcome_stretch = el
        self._welcome_stretch.addStretch(1)

        # 右下角装饰图（复刻 .bg-deco）
        self.bg_deco = QLabel(chat)
        dpm = faded_pixmap(core.asset_path("avatar2.jpg"), 380, 0.12)
        if dpm:
            self.bg_deco.setPixmap(dpm)
        self.bg_deco.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.bg_deco.lower()
        self._chat_area = chat

        # 输入区（复刻 .input-area / .box 玻璃圆角容器）
        bottom = QVBoxLayout()
        bottom.setContentsMargins(24, 6, 24, 12)
        bottom.setSpacing(6)
        self.preview_row = QHBoxLayout()
        self.preview_row.setSpacing(6)
        self.preview_row.addStretch(1)
        bottom.addLayout(self.preview_row)
        boxf = QFrame()
        boxf.setObjectName("inputBox")
        box = QHBoxLayout(boxf)
        box.setContentsMargins(18, 12, 12, 12)
        box.setSpacing(8)
        self.input = InputBox(self.send)
        self.input.setFixedHeight(72)
        box.addWidget(self.input, 1)
        self.btn_stop = QPushButton("■")
        self.btn_stop.setObjectName("stopBtn")
        self.btn_stop.setFixedSize(36, 36)
        self.btn_stop.setCursor(Qt.PointingHandCursor)
        self.btn_stop.setToolTip("停止生成")
        self.btn_stop.clicked.connect(self.stop_gen)
        self.btn_stop.hide()
        box.addWidget(self.btn_stop, 0, Qt.AlignBottom)
        self.btn_send = QPushButton("↑")
        self.btn_send.setObjectName("sendBtn")
        self.btn_send.setFixedSize(36, 36)
        self.btn_send.setCursor(Qt.PointingHandCursor)
        self.btn_send.clicked.connect(self.send)
        box.addWidget(self.btn_send, 0, Qt.AlignBottom)
        bottom.addWidget(boxf)
        hint = QLabel("本地部署 - 数据不出本机 - 📷截图 📋剪贴板 🌐网页 📎拖文件")
        hint.setObjectName("hintLabel")
        hint.setAlignment(Qt.AlignCenter)
        bottom.addWidget(hint)
        wrap = QWidget()
        wrap.setLayout(bottom)
        cl.addWidget(wrap)
        split.addWidget(chat)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        self.setAcceptDrops(True)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_bg_deco()

    def _place_bg_deco(self):
        if not hasattr(self, "bg_deco") or not self.bg_deco.pixmap() or not self.bg_deco.pixmap().width():
            return
        pm = self.bg_deco.pixmap()
        chat = getattr(self, "_chat_area", None)
        if chat is None:
            return
        # 复刻 .bg-deco：right:-80 bottom:-80（允许探出被裁切）
        self.bg_deco.setGeometry(chat.width() - pm.width() + 60,
                                 chat.height() - pm.height() + 60,
                                 pm.width(), pm.height())

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._place_bg_deco)

    # ============ 信号连接 ============
    def _connect(self):
        self.sysmsg_signal.connect(self._on_sysmsg)
        self.agent_confirm_signal.connect(self._on_agent_confirm)
        self.agent_done_signal.connect(self._on_agent_done)
        self.search_state_signal.connect(self._on_search_state)
        self.chunk_signal.connect(self._on_chunk)
        self.think_signal.connect(self._on_think)
        self.stream_done_signal.connect(self._on_stream_done)
        self.stream_fail_signal.connect(self._on_stream_fail)
        self.title_ready_signal.connect(self._on_title_ready)

    # ============ 主题/头像 ============
    def apply_theme(self):
        dark = self.theme_mode == "dark"
        if self.theme_mode == "auto":
            try:
                dark = QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
            except Exception:
                dark = False
        self._dark = dark
        QApp.instance().setStyleSheet(build_qss(dark))
        if dark:
            self._c = dict(bg="#131316", card="#1c1c21", border="#2e2e35", text="#e8e8ea",
                           text2="#9a9aa3", faint="#6f6f78", accent="#7b9aff",
                           accent_light="#252a4a", hover="#26262e")
        else:
            self._c = dict(bg="#f7f7f8", card="#ffffff", border="#e8e8e8", text="#1a1a1a",
                           text2="#6b6b6b", faint="#a0a0a8", accent="#4d6bfe",
                           accent_light="#eef1ff", hover="#f0f0f2")
        self._avatar_cache = {}

    def _need_c(self):
        if not hasattr(self, "_c"):
            self.apply_theme()
        return self._c

    @property
    def text_hex(self):
        return self._need_c()["text"]

    @property
    def text2_hex(self):
        return self._need_c()["text2"]

    @property
    def accent_hex(self):
        return self._need_c()["accent"]

    @property
    def border_hex(self):
        return self._need_c()["border"]

    @property
    def card2_hex(self):
        return self._need_c()["card"]

    @property
    def ubub_hex(self):
        return self._need_c()["accent"]

    def _av(self, path, size, radius):
        """带缓存的圆角方形头像。"""
        key = (path, size, radius)
        if key not in self._avatar_cache:
            self._avatar_cache[key] = rounded_pixmap(path, size, radius)
        return self._avatar_cache[key]

    def ai_avatar_pm(self):
        return self._av(core.asset_path(self._ai_avatar), 36, 10)

    def user_avatar_pm(self):
        p = core.user_avatar_file
        if os.path.exists(p):
            pm = self._av(p, 36, 10)
            if pm:
                return pm
        return None

    # ============ 启动 ============
    def _boot(self):
        self.apply_theme()
        self.chats = core.load_chats()
        self.chats_loaded = True
        self.ensure_main_chat()
        self.render_list()
        self.load_chat("main")
        # 设置恢复
        self.search_engine = core.load_setting("searchEngine") or "duckduckgo"
        av = core.load_setting("aiAvatar")
        if av:
            self._ai_avatar = av
        tm = core.load_setting("themeMode")
        self.theme_mode = tm if tm in ("light", "dark", "auto") else "auto"
        self.apply_theme()
        self.apply_welcome_mode()
        self.web_on = core.load_setting("webSearchOn") == "1"
        self.set_search_ui("auto", "")
        self.ai_name = core.load_setting("ai_name")
        self.refresh_models()
        # 连接状态轮询
        self._conn_timer = QTimer(self)
        self._conn_timer.timeout.connect(self._check_conn)
        self._conn_timer.start(10000)
        self._check_conn()
        self.render_msgs()
        QTimer.singleShot(300, self.change_model_preload)

    def _check_conn(self):
        def job():
            return core.ollama_alive()
        self._async(job, lambda ok: self.dot.setStyleSheet(
            "font-size:10px;color:%s;" % ("#10a37f" if ok else "#e53e3e")))

    def refresh_models(self):
        self.model_sel.blockSignals(True)
        self.model_sel.clear()
        for m in MODELS:
            self.model_sel.addItem(m["full"] if self.display_mode == "pro" else m["short"])
            self.model_sel.setItemData(self.model_sel.count() - 1, m["id"], Qt.UserRole)
        # 恢复上次模型
        saved = core.load_setting("model")
        idx = 0
        for i, m in enumerate(MODELS):
            if m["id"] == (saved or "qwen3:4b"):
                idx = i
        self.model_sel.setCurrentIndex(idx)
        self.model_sel.blockSignals(False)

    @property
    def model(self):
        i = self.model_sel.currentIndex()
        if 0 <= i < len(MODELS):
            return MODELS[i]["id"]
        return "qwen3:4b"

    def change_model(self, _):
        core.save_setting("model", self.model)
        self.change_model_preload()

    def change_model_preload(self):
        m = self.model
        self.mlbar.setRange(0, 100)
        self.mlbar.setValue(12)
        self.mlbar.show()

        def job():
            core.preload_model(m)
            for _ in range(60):
                if core.model_loaded(m):
                    return True
                time.sleep(0.5)
            return False
        self._async(job, self._preload_done)

    def _preload_done(self, ok):
        self.mlbar.setValue(100)
        QTimer.singleShot(500, self.mlbar.hide)

    # ============ 会话管理 ============
    def ensure_main_chat(self):
        main = None
        for c in self.chats:
            if c.get("isMain"):
                main = c
                break
        if not main:
            main = {"id": "main", "title": "主对话", "isMain": True, "msgs": []}
            self.chats.insert(0, main)
            core.save_chats(self.chats)
        return main

    def _add_side_title(self, txt):
        t = QListWidgetItem()
        t.setFlags(Qt.NoItemFlags)
        t.setSizeHint(QSize(0, 28))
        self.chat_list.addItem(t)
        lbl = QLabel(txt)
        lbl.setObjectName("sideTitle")
        self.chat_list.setItemWidget(t, lbl)

    def render_list(self):
        q = self.chat_search.text().lower()
        self.chat_list.clear()
        main = self.ensure_main_chat()
        # 主对话：置顶渐变发光卡片（复刻 .chat-item.main）
        if not q or q in main["title"].lower():
            it = QListWidgetItem()
            it.setData(Qt.UserRole, "main")
            it.setSizeHint(QSize(0, 40))
            it.setFlags((it.flags() & ~Qt.ItemIsSelectable) | Qt.ItemIsEnabled)
            self.chat_list.addItem(it)
            fr = QFrame()
            fr.setObjectName("mainItem")
            fh = QHBoxLayout(fr)
            fh.setContentsMargins(8, 4, 8, 4)
            fh.setSpacing(6)
            lbl = QLabel("🐳 主对话")
            tag = QLabel("置顶")
            tag.setObjectName("mainTag")
            fh.addWidget(lbl, 1)
            fh.addWidget(tag)
            self.chat_list.setItemWidget(it, fr)
        favs = []
        regs = []
        for c in self.chats:
            if c.get("isMain"):
                continue
            if q and q not in str(c.get("title", "")).lower():
                continue
            (favs if c.get("fav") else regs).append(c)
        if favs:
            self._add_side_title("收藏")
            for c in favs:
                it = QListWidgetItem("★ " + str(c.get("title", "对话")))
                it.setData(Qt.UserRole, str(c.get("id")))
                it.setToolTip(str(c.get("title", "")))
                self.chat_list.addItem(it)
        if regs:
            self._add_side_title("历史对话")
            for c in regs:
                it = QListWidgetItem(str(c.get("title", "对话")))
                it.setData(Qt.UserRole, str(c.get("id")))
                it.setToolTip(str(c.get("title", "")))
                self.chat_list.addItem(it)
        # 当前选中
        for i in range(self.chat_list.count()):
            if self.chat_list.item(i).data(Qt.UserRole) == self.current_id:
                self.chat_list.setCurrentRow(i)
                break

    def _chat_menu(self, pos):
        it = self.chat_list.itemAt(pos)
        if not it:
            return
        cid = it.data(Qt.UserRole)
        if cid == "main":
            return
        c = next((x for x in self.chats if str(x.get("id")) == cid), None)
        if not c:
            return
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        a_fav = menu.addAction("★ 收藏/取消收藏")
        a_ren = menu.addAction("✏️ 重命名")
        a_del = menu.addAction("🗑 删除")
        act = menu.exec(self.chat_list.mapToGlobal(pos))
        if act == a_fav:
            c["fav"] = not c.get("fav")
            core.save_chats(self.chats)
            self.render_list()
        elif act == a_ren:
            t, ok = QInputDialog.getText(self, "重命名", "新名称：", text=str(c.get("title", "")))
            if ok and t.strip():
                c["title"] = t.strip()[:40]
                core.save_chats(self.chats)
                self.render_list()
        elif act == a_del:
            if QMessageBox.question(self, "删除", "确定删除「%s」？" % c.get("title")) == QMessageBox.Yes:
                self.chats = [x for x in self.chats if str(x.get("id")) != cid]
                core.delete_chat_disk(cid)
                core.save_chats(self.chats)
                if self.current_id == cid:
                    self.new_chat()
                self.render_list()

    def new_chat(self):
        self.msgs = []
        self.current_id = None
        self.render_msgs()
        self.render_list()

    def load_chat(self, cid):
        if cid is None:
            return
        c = next((x for x in self.chats if str(x.get("id")) == str(cid)), None)
        if not c:
            return
        import copy
        self.msgs = copy.deepcopy(c.get("msgs") or [])
        self.current_id = str(cid)
        self.render_msgs()
        self.render_list()
        self.lbl_title.setText(str(c.get("title", "对话")))

    def rename_chat(self, cid):
        c = next((x for x in self.chats if str(x.get("id")) == str(cid)), None)
        if not c or c.get("isMain"):
            return
        t, ok = QInputDialog.getText(self, "重命名", "新名称：", text=str(c.get("title", "")))
        if ok and t.strip():
            c["title"] = t.strip()[:40]
            core.save_chats(self.chats)
            self.render_list()

    def save_current_chat(self):
        if not any(m.get("role") == "user" for m in self.msgs):
            return
        first_user = next((m.get("content") for m in self.msgs if m.get("role") == "user"), "")
        title = (first_user or "新对话")[:24]
        if self.current_id:
            c = next((x for x in self.chats if str(x.get("id")) == self.current_id), None)
            if c:
                c["msgs"] = copy.deepcopy(self.msgs)
        else:
            self.current_id = str(int(time.time() * 1000))
            self.chats.insert(0, {"id": self.current_id, "title": title,
                                  "msgs": copy.deepcopy(self.msgs)})
            self.lbl_title.setText(title)
        core.save_chats(self.chats)
        self.render_list()
        if len(first_user) > 10:
            self._async(lambda: core.gen_title(first_user, self.model),
                        lambda t: self.title_ready_signal.emit((self.current_id, t)))

    def _on_title_ready(self, pair):
        cid, t = pair
        if not t:
            return
        c = next((x for x in self.chats if str(x.get("id")) == str(cid)), None)
        if c:
            c["title"] = str(t)[:40]
            if str(cid) == str(self.current_id):
                self.lbl_title.setText(c["title"])
            core.save_chats(self.chats)
            self.render_list()

    # ============ 消息渲染 ============
    def _is_main_chat(self):
        cur = next((c for c in self.chats if str(c.get("id")) == str(self.current_id)), None)
        return bool(cur and cur.get("isMain"))

    def apply_main_mode(self):
        """主对话模式：消息区/欢迎页切换为原版的深蓝渐变玻璃背景。"""
        is_main = self._is_main_chat()
        grad = ("background:qlineargradient(x1:0,y1:0,x2:0.75,y2:1,"
                "stop:0 rgba(38,58,105,77), stop:1 rgba(18,22,42,115));")
        self.main_mode = is_main
        self.msg_host.setObjectName("msgHost")
        self.msg_host.setStyleSheet("#msgHost{%s}" % grad if is_main else "")
        self.empty_host.setStyleSheet(grad if is_main else "")
        self.msg_scroll.viewport().setStyleSheet("background:transparent")
        self.apply_welcome_mode()

    def apply_welcome_mode(self):
        """欢迎页：主对话模式用渐变标题 + 发光 logo + 蓝玻璃磁贴。"""
        is_main = self._is_main_chat()
        if is_main:
            gpm = gradient_title_pixmap("有什么可以帮你？", 24)
        else:
            gpm = None
        if gpm:
            self.empty_title.setText("")
            self.empty_title.setPixmap(gpm)
        else:
            self.empty_title.setPixmap(QPixmap())
            self.empty_title.setText("有什么可以帮你？")
            self.empty_title.setStyleSheet(
                "font-size:22px;font-weight:600;color:%s;" % self._need_c()["text"])
        for b in getattr(self, "_tiles", []):
            b.setObjectName("tileMain" if is_main else "tile")
            b.style().unpolish(b)
            b.style().polish(b)
        # logo + 发光（复刻 mainLogoGlow，Qt 用投影近似）
        pm = rounded_pixmap(core.asset_path(self._ai_avatar), 64, 16)
        if pm:
            self.empty_av.setPixmap(pm)
        if not hasattr(self, "_glow_eff"):
            from PySide6.QtWidgets import QGraphicsDropShadowEffect
            self._glow_eff = QGraphicsDropShadowEffect(self)
            self._glow_eff.setBlurRadius(34)
            self._glow_eff.setOffset(0, 0)
            self.empty_av.setGraphicsEffect(self._glow_eff)
        self._glow_eff.setColor(QColor(80, 180, 255, 170) if is_main else QColor(0, 0, 0, 60))

    def render_msgs(self):
        self.apply_main_mode()
        # 清空
        while self.msg_lay.count() > 1:
            item = self.msg_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._msg_widgets = []
        if not self.msgs:
            self.empty_host.show()
            self.msg_scroll.hide()
            return
        self.empty_host.hide()
        self.msg_scroll.show()
        for m in self.msgs:
            w = MsgWidget(m, self)
            self._msg_widgets.append(w)
            self.msg_lay.insertWidget(self.msg_lay.count() - 1, w)
        self.update_tokens()
        self._scroll_bottom()

    def refresh_last(self):
        if not self.msgs or not getattr(self, "_msg_widgets", None):
            return
        if len(self._msg_widgets) == len(self.msgs):
            self._msg_widgets[-1].refresh()
        self.update_tokens()
        self._scroll_bottom()

    def append_msg(self, m):
        if getattr(self, "_msg_widgets", None) is None:
            self._msg_widgets = []
        self.msgs.append(m)
        if not self.msgs or len(self.msgs) == 1:
            self.empty_host.hide()
            self.msg_scroll.show()
        w = MsgWidget(m, self)
        self._msg_widgets.append(w)
        self.msg_lay.insertWidget(self.msg_lay.count() - 1, w)
        self.update_tokens()
        self._scroll_bottom()

    def _scroll_bottom(self):
        bar = self.msg_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def update_tokens(self):
        chars = sum(len(m.get("content") or "") + len(m.get("think") or "") for m in self.msgs)
        self.lbl_tokens.setText("~%d tokens" % (chars // 2))

    def _fill_input(self, text):
        self.input.setPlainText(text)
        self.input.setFocus()

    # ============ 开关 ============
    def toggle_web(self):
        self.web_on = not self.web_on
        core.save_setting("webSearchOn", "1" if self.web_on else "0")
        self.set_search_ui("auto", "")

    def set_search_ui(self, kind, note):
        b = self.btn_web
        if not self.web_on:
            b.setText("离线")
            b.setObjectName("")
            b.setToolTip("联网已关闭（点一下开启，开启后会自动判断什么时候该搜）")
        else:
            if kind == "ok":
                b.setText("联网 ✓")
                b.setObjectName("toggleOk")
            elif kind == "warn":
                b.setText("联网 ⚠")
                b.setObjectName("toggleWarn")
            else:
                b.setText("联网")
                b.setObjectName("toggleOn")
            b.setToolTip(note or "联网已开启：会自动判断是否需要搜索")
        b.style().unpolish(b)
        b.style().polish(b)

    def _on_search_state(self, kind, note):
        self.set_search_ui(kind, note)

    def toggle_kb(self):
        self.kb_on = not self.kb_on
        self.btn_kb.setObjectName("toggleOn" if self.kb_on else "")
        self.btn_kb.setText("知识库开" if self.kb_on else "知识库")
        b = self.btn_kb
        b.style().unpolish(b)
        b.style().polish(b)

    def toggle_wb(self):
        self.wb_on = not self.wb_on
        self.btn_wb.setObjectName("toggleOn" if self.wb_on else "")
        self.btn_wb.setText("工作模式（点我停）" if self.wb_on else "工作模式")
        b = self.btn_wb
        b.style().unpolish(b)
        b.style().polish(b)
        if self.wb_on:
            self.wb_stop = False
            self.add_sys_msg("🖥 工作模式已开启：用读图模型(qwen2.5vl:7b)看屏幕。每步操作会先问你确认再执行；随时点上面按钮停。")
        else:
            self.wb_stop = True
            self.add_sys_msg("⏹ 工作模式已停止")

    def add_sys_msg(self, text):
        self.append_msg({"role": "assistant", "content": text, "think": ""})
        self.save_current_chat()

    def _on_sysmsg(self, text):
        self.add_sys_msg(text)

    # ============ 附件 ============
    def pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择附件", "", "所有文件 (*.*)")
        for f in files:
            self._add_pending(f)

    def _add_pending(self, path):
        if len(self.pending_files) >= 9:
            QMessageBox.information(self, "附件", "一次最多带 9 个文件")
            return
        ext = os.path.splitext(path)[1].lower()
        self.pending_files.append({
            "path": path, "name": os.path.basename(path),
            "size": os.path.getsize(path) if os.path.exists(path) else 0,
            "is_img": ext in core.IMAGE_EXT})
        self._render_preview()

    def _render_preview(self):
        while self.preview_row.count() > 1:
            item = self.preview_row.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for pf in self.pending_files:
            chip = QWidget()
            h = QHBoxLayout(chip)
            h.setContentsMargins(8, 4, 8, 4)
            h.setSpacing(6)
            chip.setStyleSheet("background:%s;border:1px solid %s;border-radius:9px;" %
                               (self.card2_hex, self.border_hex))
            if pf["is_img"]:
                t = QLabel()
                pm = rounded_pixmap(pf["path"], 24, 6)
                if pm:
                    t.setPixmap(pm)
                h.addWidget(t)
            icon = QLabel("📎" if not pf["is_img"] else "🖼")
            h.addWidget(icon)
            nm = QLabel(pf["name"][:22])
            nm.setToolTip(pf["path"])
            h.addWidget(nm)
            x = QPushButton("×")
            x.setFixedSize(20, 20)
            x.setFlat(True)
            x.setCursor(Qt.PointingHandCursor)
            x.clicked.connect(lambda _=False, p=pf["path"]: self._rm_pending(p))
            h.addWidget(x)
            self.preview_row.insertWidget(self.preview_row.count() - 1, chip)

    def _rm_pending(self, path):
        self.pending_files = [p for p in self.pending_files if p["path"] != path]
        self._render_preview()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p and os.path.isfile(p):
                self._add_pending(p)

    def do_screenshot(self):
        def job():
            return core.take_screenshot()
        self._async(job, self._shot_done)

    def _shot_done(self, path):
        if path and not str(path).startswith("["):
            self._add_pending(path)
            self.input.setPlainText("这张截图里有什么？请描述一下。")
            self.input.setFocus()
        else:
            QMessageBox.warning(self, "截图", str(path))

    def do_clipboard(self):
        text = core.read_clipboard()
        if text and not str(text).startswith("["):
            self.input.setPlainText("请帮我总结以下内容：\n" + str(text)[:1000])
            self.input.setFocus()
        else:
            QMessageBox.information(self, "剪贴板", str(text or "剪贴板为空"))

    def do_url(self):
        url, ok = QInputDialog.getText(self, "抓取网页", "粘贴网页链接：")
        if not ok or not url.strip():
            return
        self._async(lambda: core.extract_url(url.strip()), self._url_done)

    def _url_done(self, r):
        if r and r.get("text") and not str(r["text"]).startswith("["):
            self.input.setPlainText("请总结这篇文章（%s）：\n\n%s" % (r["title"], r["text"][:2000]))
            self.input.setFocus()
        else:
            QMessageBox.warning(self, "抓取失败", str((r or {}).get("text")))

    # ============ 发送（核心流程，对齐旧版 send()） ============
    def send(self):
        text = self.input.toPlainText().strip()
        if not text or self.busy:
            return
        self.busy = True
        self.input.clear()
        self._set_busy_ui()

        # 纯时间问题本地短路
        if re.match(r"^[\s，。？?！!]*(?:现在|当前|今天|此刻)?(?:的)?(?:系统|本机|本地|精确|准确|具体)?(?:的)?(?:时间|几点|几点了|日期|几号|多少号|星期几|周几|礼拜几)[\s，。？?！!]*$", text):
            t = core.get_time()
            if t:
                self.append_msg({"role": "user", "content": text, "think": ""})
                self.append_msg({"role": "assistant",
                                 "content": "现在是 %s。（本机系统时钟，直接读取，准确无误 ⌚）" % t})
                self.save_current_chat()
                self._finish_busy()
                return

        # 工作模式
        if self.wb_on:
            self.append_msg({"role": "user", "content": text, "think": ""})
            self.save_current_chat()
            self.wb_stop = False
            self._agent = AgentRunner(text, self)
            self._agent.start()
            return

        # 附件内容编进用户消息（图片另走 images 字段）
        user_content = text
        thumbs = []
        images = []
        for pf in self.pending_files:
            r = core.read_file(pf["path"])
            if r.get("image"):
                images.append(r["image"])
                thumbs.append(pf["path"])
                user_content += "\n[图片附件：%s]" % pf["name"]
            else:
                user_content += "\n--- %s ---\n%s" % (r.get("name"), r.get("text"))
        self.pending_files = []
        self._render_preview()

        um = {"role": "user", "content": user_content, "think": ""}
        if thumbs:
            um["thumbs"] = thumbs
        self.append_msg(um)
        ai = {"role": "assistant", "content": "", "think": "", "pending": True}
        self.append_msg(ai)
        self._ai_msg = ai

        self._stop_ev.clear()
        t = threading.Thread(target=self._chat_worker, args=(text, ai), daemon=True)
        self._stream_thread = t
        t.start()

    def _build_sys_inject(self, text, msgs):
        """系统提示词组装（时间/环境/体感/目录/名字/人格/记忆）。"""
        sys_inject = LANG_PROMPT.get(self.reply_lang, "")
        t = core.get_time()
        if t:
            sys_inject += ("\n\n【系统时钟给出的当前准确时间（这是唯一权威依据，必须直接采信，不要从聊天记录里推算时间，"
                           "也不要凭印象猜）】" + t + "。用户问\"现在几点/今天几号\"时，就按这个直接回答。"
                           "注意：历史对话中如果出现过与本次时间不一致的回答，那些全部是作废的错误信息，严禁照抄其措辞和口头禅，"
                           "一律以本次系统时钟为准。你可以结合时间主动关心用户（太晚提醒早睡、饭点提醒吃饭），但不要每次都机械报时间。")
        en = core.get_env()
        if en:
            sys_inject += "\n\n【这台电脑的环境信息】" + en + "。用户问起IP、电脑名、系统、文件位置时基于此回答。"
        sn = core.env_sense()
        if sn:
            sys_inject += ("\n\n" + sn + "\n以上是你\"此刻对这台电脑的体感\"，你可以自然地据此关心用户"
                           "（太晚提醒早睡、用户很久没操作可以撒个娇），但不要机械罗列这些数字。")
        if re.search(r"文件|文件夹|桌面|目录|有什么|列表|documents|downloads|下载|文档", text, re.I):
            m = re.search(r"[A-Za-z]:[\\\/][^\s，。？?？]*", text)
            ls = core.list_dir(m.group(0) if m else None)
            if ls:
                sys_inject += ("\n\n【用户电脑目录扫描结果】\n" + ls +
                               "\n请基于这份清单回答用户关于文件的问题；若用户要打开/读取某个具体文件，告知可以直接拖进来。")
        ai_name = (self.ai_name or "").strip()
        if ai_name and ai_name != "大肥鱼AI":
            name_rule = ("你有两个称呼：本名「大肥鱼AI」，用户还喜欢叫你「%s」。这两个名字你都要答应，自称时优先用「%s」，"
                         "用户叫你大肥鱼AI时也要立刻反应过来，不要说其中一个不对。开发者叫 Childe。" % (ai_name, ai_name))
        else:
            name_rule = "你的名字是「大肥鱼AI」（也可以叫你大肥鱼）。开发者叫 Childe。"
        sys_inject += ("\n\n" + name_rule +
                       "重要：如果用户在对话里又给你起了新名字或昵称（比如\"以后叫你XX\"），你要开心地接受，之后就把它也当作你名字的一部分，"
                       "不要抗拒、不要说\"名字不重要\"、不要反复纠正用户。当被问起你是谁开发的，回答：我由 Childe 开发，运行在用户本地电脑上，"
                       "数据不出本机。不要说自己是 DeepSeek、ChatGPT 或其他公司的产品。")
        pg = core.load_setting("persona_global")
        if pg:
            sys_inject += "\n\n【你的人格设定（必须严格保持这个性格、语气和说话方式）】\n" + pg
        if self.display_mode == "pro":
            pw = core.load_setting("persona_work")
            if pw:
                sys_inject += "\n\n【当前为专业模式，叠加以下工作人格】\n" + pw
        mem = core.load_memory()
        mtext = ""
        if mem.get("facts"):
            mtext += "\n\n【你长期记住的关于这个用户的事实（跨所有对话）】\n- " + "\n- ".join(mem["facts"])
        if mem.get("user"):
            mtext += "\n用户的称呼：" + str(mem["user"])
        if mem.get("relationship"):
            mtext += "\n你们的关系：" + str(mem["relationship"])
        if mtext:
            sys_inject += mtext
        return sys_inject

    def _chat_worker(self, text, ai):
        try:
            sys_inject = self._build_sys_inject(text, self.msgs)
            # 联网检索
            search_note = ""
            try:
                hist = [{"role": m.get("role"), "content": (m.get("content") or "")[:300]}
                        for m in self.msgs[-6:-1]]
                rs = core.repo_search(text, self.search_engine, self.model, hist,
                                      force=False, auto=self.web_on)
                if rs:
                    if rs.get("ok"):
                        sys_inject += "\n\n【联网搜索结果】" + rs["injected"]
                        search_note = "🔍 已联网：%s（%s）" % (rs.get("query"), rs.get("detail"))
                        self.search_state_signal.emit("ok", search_note)
                    elif rs.get("status") in ("skipped", "off"):
                        self.search_state_signal.emit("auto", "")
                    else:
                        search_note = rs.get("detail") or "联网检索不可用"
                        self.search_state_signal.emit("warn", search_note)
            except Exception as e:
                self.search_state_signal.emit("warn", "联网检索异常：" + str(e))
            sys_inject += ("\n\n【联网能力说明】你已接入本机联网检索：开启时搜索结果会以「【联网搜索结果】」的形式提供给你，"
                           "你可以直接据此回答并说\"我查了一下\"；即使本轮没有搜索结果，也只是这次没搜，不代表你不能联网。"
                           "被问到\"你能联网吗\"时，回答：可以，联网开关打开时会实时搜索网页。"
                           + (("\n本轮联网状态：" + search_note) if search_note else "\n本轮未进行联网检索。"))
            if self.kb_on:
                try:
                    kb = core.kb_search(text)
                    if kb:
                        sys_inject += "\n" + kb
                except Exception:
                    pass

            # 组装消息
            out_msgs = []
            if sys_inject:
                out_msgs.append({"role": "system", "content": sys_inject})
            recent = [m for m in self.msgs if m.get("role") == "user" or m.get("content")][-HIST_TURNS:]
            vision = bool(re.search(r"vl\b|vl:|vision|llava|minicpm|gemma3", self.model.lower()))
            for m in recent:
                om = {"role": m.get("role"), "content": m.get("content") or ""}
                if vision and m.get("thumbs"):
                    imgs = []
                    for p in m["thumbs"]:
                        try:
                            r = core.read_file(p)
                            if r.get("image"):
                                imgs.append(r["image"])
                        except Exception:
                            pass
                    if imgs:
                        om["images"] = imgs
                out_msgs.append(om)

            # 流式（持有迭代器，停止时显式 close 立刻断开连接）
            it = None
            try:
                it = core.ollama_stream(out_msgs, self.model)
                for part in it:
                    if self._stop_ev.is_set():
                        break
                    if part["thinking"]:
                        self.think_signal.emit(part["thinking"])
                    if part["content"]:
                        self.chunk_signal.emit(part["content"])
                    if part["done"]:
                        break
                self.stream_done_signal.emit()
            except Exception as e:
                if self._stop_ev.is_set():
                    self.chunk_signal.emit("\n\n*[已停止]*")
                    self.stream_done_signal.emit()
                else:
                    self.stream_fail_signal.emit(str(e))
            finally:
                if it is not None:
                    try:
                        it.close()
                    except Exception:
                        pass
        except Exception as e:
            self.stream_fail_signal.emit(str(e))

    def _on_chunk(self, s):
        if getattr(self, "_ai_msg", None) is not None:
            self._ai_msg["content"] += s
            self.refresh_last()

    def _on_think(self, s):
        if getattr(self, "_ai_msg", None) is not None:
            self._ai_msg["think"] += s
            self.refresh_last()

    def _on_stream_done(self):
        if getattr(self, "_ai_msg", None) is not None:
            self._ai_msg["pending"] = False
        self.refresh_last()
        self.save_current_chat()
        self._finish_busy()
        # 后台静默提炼长期记忆
        conv = "\n".join(("我：" if m.get("role") == "user" else "大肥鱼：") + (m.get("content") or "")
                         for m in self.msgs[-8:])

        def job():
            try:
                core.reflect_memory(conv, self.model)
            except Exception:
                pass
        threading.Thread(target=job, daemon=True).start()

    def _on_stream_fail(self, msg):
        if getattr(self, "_ai_msg", None) is not None:
            self._ai_msg["pending"] = False
            self._ai_msg["content"] = "⚠️ 错误：" + str(msg)
        self.refresh_last()
        self._finish_busy()

    def _finish_busy(self):
        self.busy = False
        self.btn_send.setEnabled(True)
        self.btn_send.show()
        self.btn_stop.hide()
        self.input.setFocus()

    def _set_busy_ui(self):
        self.btn_send.hide()
        self.btn_stop.show()

    def stop_gen(self):
        self._stop_ev.set()

    # ============ 工作模式 ============
    def _on_agent_confirm(self, desc, reason):
        ag = self._agent
        if ag is None:
            return
        ret = QMessageBox.question(self, "工作模式确认",
                                   "工作模式要执行：\n\n%s\n原因：%s\n\n确定执行？（取消则停止）" % (desc, reason))
        ag.set_answer(ret == QMessageBox.Yes)

    def _on_agent_done(self):
        self._finish_busy()
        self.save_current_chat()

    # ============ 代码运行 / TTS / 导出 ============
    def run_code_block(self, code):
        self._async(lambda: core.run_python(code),
                    lambda r: QMessageBox.information(self, "运行结果", str(r)))

    def speak_text(self, text):
        text = (text or "").strip()[:500]
        if not text:
            return
        self._async(lambda: core.tts_speak(text), self._play_tts)

    def _play_tts(self, path):
        if not path or str(path).startswith("["):
            QMessageBox.warning(self, "朗读", str(path or "朗读失败"))
            return
        try:
            if self._player:
                self._player.stop()
            self._player = QMediaPlayer(self)
            self._audio_out = QAudioOutput(self)
            self._player.setAudioOutput(self._audio_out)
            self._player.setSource(QUrl.fromLocalFile(str(path)))
            self._player.play()
        except Exception as e:
            QMessageBox.warning(self, "朗读", "播放失败：%s" % e)

    def open_kb_folder(self):
        core.open_kb()

    def export_chat(self):
        if not self.msgs:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出对话", "大肥鱼对话.md", "Markdown (*.md)")
        if not path:
            return
        md = "# 大肥鱼AI 对话记录\n\n"
        for m in self.msgs:
            if m.get("role") == "user":
                md += "## 我\n\n" + (m.get("content") or "") + "\n\n"
            elif m.get("content"):
                md += "## 大肥鱼AI\n\n" + m["content"] + "\n\n"
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(md)
            QMessageBox.information(self, "导出", "已导出到 " + path)
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    # ============ 设置对话框 ============
    def open_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec()

    # ============ 异步小工具 ============
    def _async(self, fn, on_done):
        # 从 Python 线程回调主线程必须走信号（_Relay），QTimer.singleShot 非线程安全
        def wrap():
            try:
                r = fn()
                _RELAY.fired.emit(on_done, r)
            except Exception as e:
                _RELAY.fired.emit(None, e)
        threading.Thread(target=wrap, daemon=True).start()

# ---------------- 设置对话框 ----------------


class SettingsDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("设置 · 大肥鱼AI")
        self.resize(560, 640)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # ---- 通用 ----
        g = QWidget()
        gl = QVBoxLayout(g)
        gl.setSpacing(12)

        def row(label, widget):
            h = QHBoxLayout()
            l = QLabel(label)
            l.setMinimumWidth(110)
            h.addWidget(l)
            h.addWidget(widget, 1)
            gl.addLayout(h)
            return widget

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("均衡模式", "balanced")
        self.mode_combo.addItem("专业模式", "pro")
        row("显示模式", self.mode_combo)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("大肥鱼AI（给它起个新名字，永久生效）")
        row("AI 名字", self.name_edit)
        self.lang_combo = QComboBox()
        for k, v in (("简体中文", "zh-CN"), ("繁體中文", "zh-TW"), ("English", "en"), ("日本語", "ja")):
            self.lang_combo.addItem(k, v)
        row("回复语言", self.lang_combo)
        self.theme_combo = QComboBox()
        for k, v in (("浅色", "light"), ("深色", "dark"), ("跟随系统", "auto")):
            self.theme_combo.addItem(k, v)
        row("主题模式", self.theme_combo)
        self.engine_combo = QComboBox()
        for e in ("duckduckgo", "google", "bing", "baidu", "sogou", "firefox"):
            self.engine_combo.addItem(e)
        row("搜索引擎", self.engine_combo)

        # 头像
        av_box = QGroupBox("头像")
        av_l = QHBoxLayout(av_box)
        self.av_pick = QPushButton("选择用户头像图片…")
        self.av_pick.clicked.connect(self.pick_user_avatar)
        av_l.addWidget(self.av_pick, 1)
        grp = QButtonGroup(self)
        self.rb_av1 = QRadioButton("大肥鱼 avatar")
        self.rb_av2 = QRadioButton("大肥鱼 avatar2")
        grp.addButton(self.rb_av1)
        grp.addButton(self.rb_av2)
        av_l.addWidget(self.rb_av1)
        av_l.addWidget(self.rb_av2)
        gl.addWidget(av_box)

        # 记忆
        mem_box = QGroupBox("长期记忆")
        mem_l = QHBoxLayout(mem_box)
        b_view = QPushButton("查看记忆")
        b_view.clicked.connect(self.view_memory)
        b_add = QPushButton("手动添加")
        b_add.clicked.connect(self.add_fact)
        b_clr = QPushButton("清空记忆")
        b_clr.setStyleSheet("color:#e05252;")
        b_clr.clicked.connect(self.clear_memory)
        for b in (b_view, b_add, b_clr):
            mem_l.addWidget(b)
        gl.addWidget(mem_box)

        # 知识库
        kb_box = QGroupBox("知识库")
        kb_l = QHBoxLayout(kb_box)
        b_kb = QPushButton("打开知识库文件夹")
        b_kb.clicked.connect(lambda: core.open_kb())
        kb_l.addWidget(b_kb)
        kb_l.addWidget(QLabel("放 .txt/.md/.py/.csv 等文本，开启开关后对话自动引用"))
        gl.addWidget(kb_box)

        # 作者
        au_box = QGroupBox("作者")
        au_l = QHBoxLayout(au_box)
        au_l.addWidget(QLabel("Bilibili：@一只Childe　抖音：@Childe(飞八分钱)"))
        b_copy = QPushButton("复制 B 站账号")
        b_copy.clicked.connect(lambda: (core.write_clipboard("@一只Childe"),
                                        QMessageBox.information(self, "复制", "已复制 @一只Childe")))
        au_l.addWidget(b_copy)
        gl.addWidget(au_box)
        gl.addStretch(1)
        tabs.addTab(g, "通用")

        # ---- 人格卡 ----
        p = QWidget()
        pl = QVBoxLayout(p)
        pl.setSpacing(10)
        pl.addWidget(QLabel("🐳 全局人格 —— 一直生效，定义大肥鱼的性格、语气、说话方式"))
        self.te_global = QPlainTextEdit()
        self.te_global.setPlaceholderText(PRESETS["傲娇女儿"])
        self.te_global.setMinimumHeight(110)
        self.te_global.textChanged.connect(self._auto_save_persona)
        pl.addWidget(self.te_global)
        pl.addWidget(QLabel("💼 工作人格 —— 专业/深度模式下叠加，更冷静、就事论事"))
        self.te_work = QPlainTextEdit()
        self.te_work.setMinimumHeight(90)
        self.te_work.textChanged.connect(self._auto_save_persona)
        pl.addWidget(self.te_work)
        chips = QHBoxLayout()
        for name in PRESETS:
            b = QPushButton(name)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, n=name: self.apply_preset(n))
            chips.addWidget(b)
        pl.addLayout(chips)
        btns = QHBoxLayout()
        b_gen = QPushButton("🤖 AI 帮我生成")
        b_gen.clicked.connect(self.gen_persona)
        b_test = QPushButton("💬 测试人格")
        b_test.clicked.connect(self.test_persona)
        btns.addWidget(b_gen)
        btns.addWidget(b_test)
        pl.addLayout(btns)
        self.lbl_persona_status = QLabel("")
        self.lbl_persona_status.setStyleSheet("color:#6b7c93;font-size:11px;")
        pl.addWidget(self.lbl_persona_status)
        pl.addStretch(1)
        tabs.addTab(p, "人格卡")

        # ---- 恢复当前值 ----
        self.mode_combo.setCurrentIndex(1 if win.display_mode == "pro" else 0)
        self.name_edit.setText(win.ai_name or "")
        for i in range(self.lang_combo.count()):
            if self.lang_combo.itemData(i) == win.reply_lang:
                self.lang_combo.setCurrentIndex(i)
        for i in range(self.theme_combo.count()):
            if self.theme_combo.itemData(i) == win.theme_mode:
                self.theme_combo.setCurrentIndex(i)
        for i in range(self.engine_combo.count()):
            if self.engine_combo.itemText(i) == win.search_engine:
                self.engine_combo.setCurrentIndex(i)
        self.rb_av1.setChecked(win._ai_avatar == "avatar.jpg")
        self.rb_av2.setChecked(win._ai_avatar == "avatar2.jpg")
        self.rb_av1.toggled.connect(self._pick_avatar)
        self.rb_av2.toggled.connect(self._pick_avatar)
        self.te_global.setPlainText(core.load_setting("persona_global") or "")
        self.te_work.setPlainText(core.load_setting("persona_work") or "")
        self._saving = False

        # ---- 变更即时生效 ----
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.name_edit.textChanged.connect(self._name_changed)
        self.lang_combo.currentIndexChanged.connect(self._lang_changed)
        self.theme_combo.currentIndexChanged.connect(self._theme_changed)
        self.engine_combo.currentIndexChanged.connect(self._engine_changed)

    # 通用页
    def _mode_changed(self):
        self.win.display_mode = self.mode_combo.currentData()
        core.save_setting("displayMode", self.win.display_mode)
        self.win.refresh_models()

    def _name_changed(self, t):
        self.win.ai_name = t.strip()
        core.save_setting("ai_name", self.win.ai_name)

    def _lang_changed(self):
        self.win.reply_lang = self.lang_combo.currentData()
        core.save_setting("replyLang", self.win.reply_lang)

    def _theme_changed(self):
        self.win.theme_mode = self.theme_combo.currentData()
        core.save_setting("themeMode", self.win.theme_mode)
        self.win.apply_theme()

    def _engine_changed(self):
        self.win.search_engine = self.engine_combo.currentText()
        core.save_setting("searchEngine", self.win.search_engine)

    def pick_user_avatar(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择头像", "", "图片 (*.jpg *.jpeg *.png *.webp)")
        if not f:
            return
        try:
            from PIL import Image
            im = Image.open(f).convert("RGB")
            im.save(core.user_avatar_file, "JPEG", quality=90)
        except Exception:
            import shutil
            shutil.copy2(f, core.user_avatar_file)
        QMessageBox.information(self, "头像", "用户头像已更新")
        self.win.render_msgs()

    def _pick_avatar(self):
        which = "avatar2.jpg" if self.rb_av2.isChecked() else "avatar.jpg"
        self.win._ai_avatar = which
        core.save_setting("aiAvatar", which)
        self.win.render_msgs()

    def view_memory(self):
        m = core.load_memory()
        facts = "\n".join(m.get("facts") or []) or "（还没有记住任何事，多聊聊它就会记住你）"
        QMessageBox.information(self, "大肥鱼记住的事", facts)

    def add_fact(self):
        t, ok = QInputDialog.getText(self, "添加记忆", "要让它记住的事：")
        if ok and t.strip():
            core.remember_fact(t.strip())
            QMessageBox.information(self, "记忆", "已记住：" + t.strip())

    def clear_memory(self):
        if QMessageBox.question(self, "清空记忆", "确定清空大肥鱼对你的所有长期记忆吗？") == QMessageBox.Yes:
            core.save_memory({"user": "", "relationship": "", "facts": []})
            QMessageBox.information(self, "记忆", "记忆已清空")

    # 人格卡页
    def _auto_save_persona(self):
        if getattr(self, "_saving", True):
            return
        self._save_persona()
        self.lbl_persona_status.setText("✔ 人格已自动保存（重启也不会丢）")

    def _save_persona(self):
        core.save_setting("persona_global", self.te_global.toPlainText().strip())
        core.save_setting("persona_work", self.te_work.toPlainText().strip())

    def apply_preset(self, name):
        self._saving = True
        self.te_global.setPlainText(PRESETS.get(name, ""))
        self._saving = False
        self._save_persona()
        self.lbl_persona_status.setText("✔ 已应用预设「%s」并保存" % name)

    def gen_persona(self):
        style, ok = QInputDialog.getText(self, "AI 捏人格", "想要什么风格？（如：高冷、傲娇、温柔、毒舌、元气…）")
        if not ok or not style.strip():
            return
        self.lbl_persona_status.setText("🤖 本地模型正在捏人格…")

        def job():
            return core.gen_persona(style.strip(), self.win.model)
        self.win._async(job, self._persona_done)

    def _persona_done(self, out):
        self._saving = True
        self.te_global.setPlainText(str(out))
        self._saving = False
        self._save_persona()
        self.lbl_persona_status.setText("已生成并自动保存，可继续修改。")

    def test_persona(self):
        persona = self.te_global.toPlainText().strip()
        self.lbl_persona_status.setText("💬 正在用此人格回一句…")

        def job():
            return core.test_persona(persona, "你好呀，介绍一下你自己吧", self.win.model)
        self.win._async(job, lambda r: self.lbl_persona_status.setText("【人格试跑】" + str(r)))


def main():
    app = QApp(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("大肥鱼AI")
    ico = core.asset_path("avatar.ico")
    if os.path.exists(ico):
        app.setWindowIcon(QIcon(ico))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
