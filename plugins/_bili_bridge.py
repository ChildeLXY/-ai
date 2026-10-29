#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bili_bridge.py —— 大肥鱼 ↔ B站 MCP 服务器 的桥接进程。

为什么要这一层
--------------
pond 插件自带的 mcp_server.py（92KB，40 个 B站工具）跑在自己的 Python 3.13 + .venv 里，
依赖 bilibili_api / aiohttp / mcp 这些**大肥鱼本体没有**的包，而且它只提供 MCP over stdio。

两种接法：
  ① 走 MCP 协议（initialize → tools/list → tools/call）—— 受 MCP 版本差异影响，握手一变就全废。
  ② 直接 import 它、调函数 —— 不碰协议，版本升级也不受影响。✅ 本文件采用

所以它是个**长驻子进程**：大肥鱼起一次（import bilibili_api 要好几秒，不能每次现起），
之后靠 stdin/stdout 一行一个 JSON 来回说话。

协议（极简）
------------
请求（一行 JSON）:  {"id":1, "tool":"bili_search", "args":{"keyword":"DSH 插件","num":10}}
响应（一行 JSON）:  {"id":1, "ok":true, "result":"..."}   失败: {"id":1,"ok":false,"error":"..."}
心跳:              {"id":0,"tool":"__ping__","args":{}}   → {"id":0,"ok":true,"result":"pong"}

用法
----
  <bili-duty>/.venv/Scripts/python.exe bili_bridge.py
（cwd 任意；mcp_server 靠 __file__ 定位 bili_credential.json，不受影响）
"""
import asyncio
import json
import os
import sys
import traceback

# Windows 上管道里的中文/emoji 会按 GBK 编解码直接炸 —— 钉死 UTF-8
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ⚠️ pond 的 .venv 里 sitecustomize 会找临时目录；沙箱/受限环境下 %TEMP% 不可用会报错。
#    这里把临时目录钉到 mcp_server 所在目录的 tmp/ 下，保证 yt-dlp 转写等能落盘。
_HOME = os.environ.get("BILI_DUTY_HOME") or os.path.dirname(os.path.abspath(__file__))
_TMP = os.path.join(_HOME, "tmp")
try:
    os.makedirs(_TMP, exist_ok=True)
    for _k in ("TMPDIR", "TEMP", "TMP"):
        os.environ[_k] = _TMP
except Exception:
    pass

# mcp_server 在 bili-duty 目录里（BILI_DUTY_HOME 指定）
sys.path.insert(0, _HOME)

_mcp = None          # mcp_server 模块
_loop = None         # 常驻事件循环


def _load():
    """导入 mcp_server。失败原因要能传回主进程，否则那边只看到'子进程死了'。"""
    global _mcp
    if _mcp is None:
        import mcp_server  # noqa
        _mcp = mcp_server
    return _mcp


def _resolve(mod, name):
    """拿到可调用对象。

    FastMCP 的 @tool() 有的版本返回原函数、有的返回 FunctionTool 包装，
    这里两种都兼容，取不到就直接报错而不是静默失败。
    """
    fn = getattr(mod, name, None)
    if fn is None:
        raise AttributeError("mcp_server 里没有工具：%s" % name)
    # FunctionTool / Tool 包装对象：取出真正的函数
    for attr in ("fn", "func", "function", "__wrapped__"):
        inner = getattr(fn, attr, None)
        if callable(inner) and not isinstance(inner, type(fn)):
            return inner
    if not callable(fn):
        raise AttributeError("工具 %s 不是可调用对象（%r）" % (name, type(fn)))
    return fn


async def _call(name, args):
    mod = _load()
    fn = _resolve(mod, name)
    if asyncio.iscoroutinefunction(fn):
        return await fn(**(args or {}))
    # 同步工具（少数）：丢线程池，别卡住事件循环
    return await asyncio.get_event_loop().run_in_executor(None, lambda: fn(**(args or {})))


def main():
    # Windows 默认 ProactorEventLoop；子进程里用 Selector 更稳
    try:
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    except Exception:
        pass

    # 预热：先把 mcp_server 导进来，让主进程第一次调用不用等
    warm_err = None
    try:
        _load()
    except Exception as e:
        warm_err = "%s: %s" % (type(e).__name__, e)

    sys.stderr.write("[bili-bridge] ready (home=%s)\n" % _HOME)
    sys.stderr.flush()
    if warm_err:
        sys.stderr.write("[bili-bridge] 预热失败: %s\n" % warm_err)
        sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req_id, ok, payload = None, False, ""
        try:
            req = json.loads(line)
            req_id = req.get("id")
            tool = req.get("tool")
            args = req.get("args") or {}
            if tool == "__ping__":
                ok, payload = True, "pong"
            elif tool == "__tools__":
                mod = _load()
                names = sorted(
                    n for n in dir(mod)
                    if n.startswith("bili_") and callable(getattr(mod, n, None))
                )
                ok, payload = True, names
            else:
                out = asyncio.get_event_loop().run_until_complete(_call(tool, args))
                # 工具多数返回字符串（已是 JSON 或 Markdown），原样带回去
                if not isinstance(out, (str, int, float, bool, list, dict)) and out is not None:
                    out = str(out)
                ok, payload = True, out
        except Exception as e:
            ok, payload = False, "%s: %s" % (type(e).__name__, e)
            try:
                sys.stderr.write(traceback.format_exc() + "\n")
                sys.stderr.flush()
            except Exception:
                pass
        try:
            sys.stdout.write(json.dumps({"id": req_id, "ok": ok, "result": payload if ok else None,
                                         "error": None if ok else payload},
                                        ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception:
            break


if __name__ == "__main__":
    main()
