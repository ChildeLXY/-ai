# -*- coding: utf-8 -*-
"""把打包副本里的写死路径改成可移植解析（只改 E:\dfy_build\app，不动 D:\大肥鱼AI）。"""
import io
import os
import sys

ROOT = r"E:\dfy_build\app"
ok, fail = [], []


def sub(rel, old, new, required=True):
    p = os.path.join(ROOT, rel)
    with io.open(p, encoding="utf-8") as f:
        s = f.read()
    if old not in s:
        if required:
            fail.append("%s : 未找到待替换片段 → %s" % (rel, old.splitlines()[0][:70]))
        return False
    s = s.replace(old, new, 1)
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s)
    ok.append("%s : %s" % (rel, old.splitlines()[0][:60]))
    return True


# ---------------------------------------------------------------- 主程序
sub("大肥鱼AI.py",
    'os.environ.setdefault("DSH_HOME", r"D:\\本地部署大肥鱼环境\\dsh")',
    '''import dfy_paths  # 路径统一解析：安装版落 %APPDATA%，作者本机继续复用 D 盘老目录

# 打包自检：DFY_SELFTEST=1 时只验证依赖能不能加载，不开窗口、不起任何后台线程
if os.environ.get("DFY_SELFTEST") == "1":
    _bad = []
    for _m in ("api_base", "llm_params", "dfy_core", "dfy_dsh", "dfy_market",
               "dfy_qzone", "qq_bridge", "dfy_tray", "plugins"):
        try:
            __import__(_m)
        except Exception as _e:
            _bad.append("%s: %r" % (_m, _e))
    print("[selftest] data=%s" % dfy_paths.DATA)
    print("[selftest] cache=%s" % dfy_paths.CACHE)
    print("[selftest] %s" % ("OK" if not _bad else "FAIL " + " | ".join(_bad)))
    sys.exit(0 if not _bad else 2)''')

sub("大肥鱼AI.py",
    'os.path.join(os.environ.get("DFY_DATA_DIR") or r"D:\\本地部署大肥鱼环境\\data", "chats.json")',
    'os.path.join(dfy_paths.DATA, "chats.json")')
sub("大肥鱼AI.py",
    'os.path.join(os.environ.get("DFY_DATA_DIR") or r"D:\\本地部署大肥鱼环境\\data", "memory.json")',
    'os.path.join(dfy_paths.DATA, "memory.json")')
sub("大肥鱼AI.py",
    'cache_dir = os.environ.get("DFY_CACHE_DIR") or r"D:\\大肥鱼缓存"',
    'cache_dir = dfy_paths.CACHE')
sub("大肥鱼AI.py",
    'DATA_DIR = os.environ.get("DFY_DATA_DIR") or r"D:\\本地部署大肥鱼环境\\data"',
    'DATA_DIR = dfy_paths.DATA')
sub("大肥鱼AI.py",
    'kb_dir = r"D:\\本地部署大肥鱼环境\\知识库"',
    'kb_dir = dfy_paths.KB_DIR')
sub("大肥鱼AI.py",
    'with open(r"D:\\大肥鱼缓存\\qzone.log", "a", encoding="utf-8") as _f:',
    'with open(os.path.join(cache_dir, "qzone.log"), "a", encoding="utf-8") as _f:')
sub("大肥鱼AI.py",
    'webview.start(storage_path=os.environ.get("DFY_WV2_PROFILE") or "D:\\\\大肥鱼缓存\\\\wv2_profile")',
    'webview.start(storage_path=dfy_paths.WV2_PROFILE)')

# ---------------------------------------------------------------- dfy_core
sub("dfy_core.py",
    'DATA_DIR = os.environ.get("DFY_DATA_DIR") or r"D:\\本地部署大肥鱼环境\\data"',
    'import dfy_paths\nDATA_DIR = dfy_paths.DATA')
sub("dfy_core.py",
    'CACHE_DIR = os.environ.get("DFY_CACHE_DIR") or r"D:\\大肥鱼缓存"',
    'CACHE_DIR = dfy_paths.CACHE')
sub("dfy_core.py",
    'kb_dir = r"D:\\本地部署大肥鱼环境\\知识库"',
    'kb_dir = dfy_paths.KB_DIR')
sub("dfy_core.py",
    '''    for c in (r"D:\\本地部署大肥鱼环境\\venv312\\Scripts\\python.exe",
              r"D:\\本地部署大肥鱼环境\\venv312\\Scripts\\pythonw.exe"):
        if os.path.exists(c):
            return c
    return sys.executable''',
    '''    for c in (os.environ.get("DFY_PYTHON") or "",
              r"D:\\本地部署大肥鱼环境\\venv312\\Scripts\\python.exe",
              r"D:\\本地部署大肥鱼环境\\venv312\\Scripts\\pythonw.exe"):
        if c and os.path.exists(c):
            return c
    # 冻结后 sys.executable 就是本程序，拿它去跑 -c 会把整个应用再启一遍，
    # 所以这里明确返回空，交给调用方提示"没有本机 Python"，绝不误启动自己。
    return ""''')
sub("dfy_core.py",
    '''def run_python(code):
    try:
        proc = subprocess.run(
            [_py_exe(), "-c", code],''',
    '''def run_python(code):
    py = _py_exe()
    if not py:
        return "[这台电脑没有可用的 Python，执行代码功能需要先装 Python（勾选 PATH）]"
    try:
        proc = subprocess.run(
            [py, "-c", code],''')

# ---------------------------------------------------------------- dfy_market
sub("dfy_market.py",
    '''HOST_DIR = r"D:\\大肥鱼AI\\dsh_host"
CACHE_DIR = r"D:\\大肥鱼缓存\\dsh_market"''',
    '''import dfy_paths
HOST_DIR = dfy_paths.HOST_DIR
CACHE_DIR = os.path.join(dfy_paths.CACHE, "dsh_market")''')
sub("dfy_market.py",
    'DSH_HOME = os.environ.get("DSH_HOME") or r"E:\\DSH-home"',
    'DSH_HOME = dfy_paths.DSH_HOME')

# ---------------------------------------------------------------- dfy_dsh
sub("dfy_dsh.py",
    '''HOST_DIR = r"D:\\大肥鱼AI\\dsh_host"''',
    '''import dfy_paths
HOST_DIR = dfy_paths.HOST_DIR''')
sub("dfy_dsh.py",
    '''# 插件源码所在的 DSH profile（只读引用，不往里写东西）
PROFILE_DIR = r"E:\\DSH-home\\profiles\\desktop"''',
    '''# 插件源码所在的 DSH profile（只读引用，不往里写东西）
# 别的电脑上通常没有这套 DSH 运行时，缺了就只用自己的插件目录
PROFILE_DIR = os.environ.get("DFY_DSH_PROFILE") or os.path.join(dfy_paths.DSH_HOME, "profiles", "desktop")''')
sub("dfy_dsh.py",
    'DSH_HOME = os.environ.get("DSH_HOME") or r"E:\\DSH-home"',
    'DSH_HOME = dfy_paths.DSH_HOME')
sub("dfy_dsh.py",
    'CACHE_DIR = r"D:\\大肥鱼缓存"', 'CACHE_DIR = dfy_paths.CACHE')
sub("dfy_dsh.py",
    '_SETTINGS = r"D:\\大肥鱼AI\\ds图片\\settings.json"', '_SETTINGS = dfy_paths.SETTINGS')

# ---------------------------------------------------------------- dfy_qzone
sub("dfy_qzone.py",
    '''LOG_PATH = r"D:\\大肥鱼缓存\\qzone.log"
HIST_PATH = r"D:\\大肥鱼缓存\\qzone_hist.json"
STATE_PATH = r"D:\\大肥鱼缓存\\qzone_state.json"
SETTINGS = r"D:\\大肥鱼AI\\ds图片\\settings.json"
STICKER_INDEX = r"D:\\本地部署大肥鱼环境\\data\\plugins\\nekowhale_stickers\\index.json"
GIF_DIR = r"D:\\本地部署大肥鱼环境\\data\\plugins\\nekowhale_stickers\\gif"''',
    '''import dfy_paths
LOG_PATH = os.path.join(dfy_paths.CACHE, "qzone.log")
HIST_PATH = os.path.join(dfy_paths.CACHE, "qzone_hist.json")
STATE_PATH = os.path.join(dfy_paths.CACHE, "qzone_state.json")
SETTINGS = dfy_paths.SETTINGS
STICKER_INDEX = os.path.join(dfy_paths.DATA, "plugins", "nekowhale_stickers", "index.json")
GIF_DIR = os.path.join(dfy_paths.DATA, "plugins", "nekowhale_stickers", "gif")''')

# ---------------------------------------------------------------- qq_bridge
sub("qq_bridge.py",
    '''DATA_DIR = os.environ.get("DFY_DATA_DIR") or r"D:\\本地部署大肥鱼环境\\data"
CACHE = r"D:\\大肥鱼缓存"
LOG_PATH = os.path.join(r"D:\\大肥鱼缓存", "qqbot.log")
QQBOT_DIR = r"D:\\大肥鱼QQ"''',
    '''import dfy_paths
DATA_DIR = dfy_paths.DATA
CACHE = dfy_paths.CACHE
LOG_PATH = os.path.join(CACHE, "qqbot.log")
QQBOT_DIR = os.environ.get("DFY_QQBOT_DIR") or os.path.join(os.path.dirname(dfy_paths.APP_DIR), "大肥鱼QQ")''')

# ---------------------------------------------------------------- qq_launcher
sub("qq_launcher.py",
    '''    "qq_exe": r"D:\\QQ\\QQ.exe",
    "napcat_dir": r"D:\\NapCat",''',
    '''    "qq_exe": os.environ.get("DFY_QQ_EXE") or r"D:\\QQ\\QQ.exe",
    "napcat_dir": os.environ.get("DFY_NAPCAT_DIR") or r"D:\\NapCat",''')
sub("qq_launcher.py",
    'PID_FILE = os.path.join(r"D:\\大肥鱼缓存", "qq_bot_pids.json")',
    'import dfy_paths\nPID_FILE = os.path.join(dfy_paths.CACHE, "qq_bot_pids.json")')

# ---------------------------------------------------------------- dfy_buddy
sub("dfy_buddy.py",
    '''BUDDY_DIR = r"D:\\DeepBuddy"''',
    '''import dfy_paths
BUDDY_DIR = os.environ.get("DFY_BUDDY_DIR") or r"D:\\DeepBuddy"''')
sub("dfy_buddy.py",
    '_CACHE_DIR = r"D:\\大肥鱼缓存"', '_CACHE_DIR = dfy_paths.CACHE')

# ---------------------------------------------------------------- 其他小模块
sub("pet_mutter_watcher.py",
    '''_DATA_DIR = os.environ.get("DFY_DATA_DIR") or r"D:\\本地部署大肥鱼环境\\data"
_CACHE_DIR = os.environ.get("DFY_CACHE_DIR") or r"D:\\大肥鱼缓存"''',
    '''import dfy_paths
_DATA_DIR = dfy_paths.DATA
_CACHE_DIR = dfy_paths.CACHE''')
sub("dfy_market_ui.py",
    'storage_path=os.path.join(r"D:\\大肥鱼缓存", "wv2_market"))',
    'storage_path=os.path.join(dfy_paths.CACHE, "wv2_market"))')
sub("dfy_market_ui.py",
    'import webview', 'import webview\nimport dfy_paths', required=False)
sub("cloud_proxy.py",
    'with open(r"D:\\大肥鱼缓存\\_proxy_dbg.log", "a", encoding="utf-8") as f:',
    'with open(os.path.join(os.environ.get("DFY_CACHE_DIR") or os.environ.get("TEMP") or ".", "_proxy_dbg.log"), "a", encoding="utf-8") as f:')
sub("web_client.py",
    'DATA_DIR = os.environ.get("DFY_DATA_DIR") or r"D:\\本地部署大肥鱼环境\\data"',
    'import dfy_paths\nDATA_DIR = dfy_paths.DATA')
sub("plugins/soul_md.py",
    'LEGACY_MEMORY = r"D:\\本地部署大肥鱼环境\\data\\memory.json"',
    'from dfy_paths import DATA as _DFY_DATA\nLEGACY_MEMORY = os.path.join(_DFY_DATA, "memory.json")')
sub("plugins/qq_zone.py",
    '''STICKER_DIR = r"D:\\本地部署大肥鱼环境\\data\\plugins\\nekowhale_stickers"''',
    '''from dfy_paths import DATA as _DFY_DATA
STICKER_DIR = os.path.join(_DFY_DATA, "plugins", "nekowhale_stickers")''')
sub("plugins/qq_zone.py",
    'MEMORY_FILE = r"D:\\本地部署大肥鱼环境\\data\\memory.json"',
    'MEMORY_FILE = os.path.join(_DFY_DATA, "memory.json")')
sub("llm_params.py",
    '''    paths += [os.path.join(here, "ds图片", "settings.json"),
              r"D:\\本地部署大肥鱼环境\\data\\settings.json"]''',
    '''    paths += [os.path.join(here, "ds图片", "settings.json"),
              os.path.join(os.environ.get("DFY_DATA_DIR") or here, "settings.json")]''')

print("成功 %d 项" % len(ok))
for x in ok:
    print("  +", x)
if fail:
    print("失败 %d 项：" % len(fail))
    for x in fail:
        print("  !", x)
    sys.exit(1)
