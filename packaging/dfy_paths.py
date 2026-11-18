# -*- coding: utf-8 -*-
"""大肥鱼AI · 可移植路径解析（安装版 / 源码版共用）

设计目标只有一个：**同一份代码，在作者这台 D 盘机器上和在别人电脑上都能跑**。

规则（按优先级）：
  1. 环境变量显式指定  → 以它为准（老用户可以用这个把数据放别处）
  2. 老机器上存在 D 盘目录 → 继续用（作者本机无缝迁移，聊天记录不丢）
  3. 都不满足（新电脑）  → 落到 %APPDATA%\\大肥鱼AI\\data、%LOCALAPPDATA%\\大肥鱼AI\\cache

绝对不往 C 盘装程序本体；数据落 %APPDATA% 属于用户配置区，卸载可保留。
"""

import os
import sys

# 程序目录：打包后是 exe 所在目录，源码模式是本文件目录
def _app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.argv[0]))
    return os.path.dirname(os.path.abspath(__file__))

APP_DIR = _app_dir()
# 打包后静态资源在 _MEIPASS（onedir 下即 _internal），源码模式在 APP_DIR
BUNDLE_DIR = getattr(sys, "_MEIPASS", APP_DIR)

_LEGACY_ROOT = r"D:\本地部署大肥鱼环境"
_LEGACY_DATA = os.path.join(_LEGACY_ROOT, "data")
_LEGACY_CACHE = r"D:\大肥鱼缓存"

_APPDATA = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
_LOCALAPPDATA = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")


def _pick(env_name, legacy, fallback):
    v = (os.environ.get(env_name) or "").strip()
    if v:
        return v
    if legacy and os.path.isdir(legacy):
        return legacy
    return fallback


DATA = _pick("DFY_DATA_DIR", _LEGACY_DATA, os.path.join(_APPDATA, "大肥鱼AI", "data"))
CACHE = _pick("DFY_CACHE_DIR", _LEGACY_CACHE, os.path.join(_LOCALAPPDATA, "大肥鱼AI", "cache"))

# 内置 Agent 引擎（基于 DeepSeek Harness，MIT）：随产品一起分发，用户无需另装 DSH。
# 优先级：环境变量 → 老机器 D 盘引擎 → 程序目录下的 engine（安装包形态）
_LEGACY_ENGINE = os.path.join(_LEGACY_ROOT, "engine")
_ENGINE_IN_BUNDLE = os.path.join(APP_DIR, "engine")
ENGINE = _pick(
    "DFY_ENGINE_DIR",
    _LEGACY_ENGINE if os.path.isdir(_LEGACY_ENGINE) else None,
    _ENGINE_IN_BUNDLE,
)
# 引擎的 CLI 启动器（用于插件安装、版本查询等原生能力）
ENGINE_CLI = os.path.join(ENGINE, "resources", "runtime", "cli", "bin", "dsh.cmd")

# 派生目录
KB_DIR = os.environ.get("DFY_KB_DIR") or os.path.join(DATA, "知识库")
HOST_DIR = os.path.join(DATA, "dsh_host")
SETTINGS = os.path.join(DATA, "settings.json")
WV2_PROFILE = os.environ.get("DFY_WV2_PROFILE") or os.path.join(CACHE, "wv2_profile")
DSH_HOME = os.environ.get("DSH_HOME") or os.path.join(DATA, "dsh")


def ensure_dirs():
    """把必须存在的目录先建出来；失败也不抛（只读安装目录时报个错就走）。"""
    for d in (DATA, CACHE, KB_DIR, DSH_HOME):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass


def export_env():
    """把自己算出来的结果写回环境变量，让各模块读到的都是同一份。"""
    os.environ["DFY_DATA_DIR"] = DATA
    os.environ["DFY_CACHE_DIR"] = CACHE
    os.environ["DFY_KB_DIR"] = KB_DIR
    os.environ["DSH_HOME"] = DSH_HOME
    os.environ["DFY_ENGINE_DIR"] = ENGINE
    os.environ["DFY_WV2_PROFILE"] = WV2_PROFILE


ensure_dirs()
export_env()
