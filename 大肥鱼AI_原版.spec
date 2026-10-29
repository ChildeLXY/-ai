# -*- mode: python ; coding: utf-8 -*-
# 大肥鱼AI 原版前端（pywebview + HTML）单文件打包
block_cipher = None

a = Analysis(
    ['大肥鱼AI.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('大肥鱼AI.html', '.'),
        ('ds图片', 'ds图片'),
    ],
    hiddenimports=[
        'webview.platforms.winforms',
        'clr_loader',
        'pythonnet',
        'certifi',
        'PIL',
        'PIL.Image',
        'PIL.ImageGrab',
        'edge_tts',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='大肥鱼AI',
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
    icon='ds图片\\avatar.ico',
)
