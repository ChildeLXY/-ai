# -*- mode: python ; coding: utf-8 -*-
"""大肥鱼AI · 安装版打包（onedir）

与老 spec 的区别：
  1. onedir 而不是 onefile —— 启动不用每次解压 200MB，杀软误报也少
  2. 把 plugins/、ds图片/、market.html、web_ui.html 全带上（老 spec 漏了，插件和头像全丢）
  3. 补齐动态 import 的模块（qq_bridge / dfy_* / cloud_proxy 都是函数里才 import 的）
  4. 排掉 Qt 版和语音那套（PySide6 / onnxruntime / ctranslate2 / faster_whisper / av），主程序用不到
"""
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hidden = [
    'webview.platforms.winforms',
    'clr_loader',
    'pythonnet',
    'certifi',
    'PIL',
    'PIL.Image',
    'PIL.ImageGrab',
    # 主程序里是在函数内部 import 的，静态分析抓不到
    'api_base', 'llm_params', 'dfy_paths',
    'dfy_core', 'dfy_dsh', 'dfy_market', 'dfy_market_ui', 'dfy_buddy',
    'dfy_qzone', 'dfy_mem_guard', 'dfy_tray',
    'qq_bridge', 'qq_launcher', 'qq_autoboot',
    'mood_engine', 'mood_watcher', 'pet_mutter_watcher', 'sensors_extra',
    'cloud_proxy', 'web_client', 'plugins',
]
hidden += collect_submodules('webview')

a = Analysis(
    ['大肥鱼AI.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('大肥鱼AI.html', '.'),
        ('market.html', '.'),
        ('web_ui.html', '.'),
        ('ds图片', 'ds图片'),
        ('plugins', 'plugins'),
        ('cloud_config.example.json', '.'),
        ('README.md', '.'),
    ],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PySide6', 'PySide2', 'PyQt5', 'PyQt6', 'shiboken6',
        'onnxruntime', 'ctranslate2', 'faster_whisper', 'av', 'av.libs',
        'matplotlib', 'pandas', 'scipy', 'notebook', 'IPython',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='大肥鱼AI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='ds图片\\avatar.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='大肥鱼AI',
)
