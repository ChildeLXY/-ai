# -*- mode: python ; coding: utf-8 -*-
"""大肥鱼AI Qt 版打包配置（单文件 exe，无控制台窗口）。"""

a = Analysis(
    ["大肥鱼AI_Qt.py"],
    pathex=[],
    binaries=[],
    datas=[("ds图片", "ds图片")],
    hiddenimports=[
        "dfy_core",
        "edge_tts",
        "PIL", "PIL.Image", "PIL.ImageGrab",
        "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "webview", "pypdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="大肥鱼AI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="ds图片/avatar.ico",
)
