# -*- coding: utf-8 -*-
"""
大肥鱼 AI · L2 · MCP 双向（架构 P6）
=====================================
Model Context Protocol（JSON-RPC 2.0，stdio，newline-delimited JSON）。

- :class:`MCPServer` 把自身工具暴露给外部 agent（Claude Desktop / Claude Code 等），
  处理 initialize / tools/list / tools/call。
- :class:`MCPClient` 拉起外部 MCP server，自动发现并调用其工具——外部 MCP 生态
  直接变成可用工具，不靠自己造。

纯标准库。核心消息处理与传输解耦（handle_message 纯函数），便于单测。
"""

import os
import sys
import json
import shutil
import threading
import subprocess

__all__ = ["MCPServer", "MCPClient", "MCPError", "PROTOCOL_VERSION"]

PROTOCOL_VERSION = "2025-06-18"
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# JSON-RPC 标准错误码
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class MCPError(Exception):
    pass


# ------------------------------------------------------------ 消息构造
def _request(id, method, params=None):
    m = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        m["params"] = params
    return m


def _response(id, result):
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _error(id, code, message):
    return {"jsonrpc": "2.0", "id": id,
            "error": {"code": code, "message": message}}


def _notification(method, params=None):
    m = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        m["params"] = params
    return m


# ============================================================ Server
class MCPServer(object):
    def __init__(self, name="dafeiyu-mcp", version="1.0.0"):
        self.name = name
        self.version = version
        self._tools = {}        # name -> {description, inputSchema, handler}

    def tool(self, name, description, handler, inputSchema=None):
        """注册一个工具。handler(arguments:dict)->str 或 (text, is_error)。"""
        self._tools[name] = {
            "description": description,
            "inputSchema": inputSchema or {"type": "object", "properties": {}},
            "handler": handler,
        }
        return self

    def _list_tools_result(self):
        return {"tools": [
            {"name": n, "description": t["description"],
             "inputSchema": t["inputSchema"]}
            for n, t in self._tools.items()]}

    def _call_tool(self, params):
        name = (params or {}).get("name")
        if name not in self._tools:
            return _error(None, METHOD_NOT_FOUND, "未知工具 %r" % name)
        args = (params or {}).get("arguments") or {}
        try:
            out = self._tools[name]["handler"](args)
            is_error = False
            if isinstance(out, tuple):
                text, is_error = out
            else:
                text = out
            return {"content": [{"type": "text", "text": str(text)}],
                    "isError": bool(is_error)}
        except Exception as e:
            return {"content": [{"type": "text", "text": "工具执行失败: %r" % e}],
                    "isError": True}

    def handle_message(self, msg):
        """处理一条 JSON-RPC 消息，返回响应 dict；通知返回 None。"""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return _error(msg.get("id") if isinstance(msg, dict) else None,
                          INVALID_REQUEST, "非法 JSON-RPC 消息")
        id = msg.get("id")
        method = msg.get("method")

        # 通知（无 id）无需响应
        if id is None:
            return None

        if method == "initialize":
            return _response(id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": self.name, "version": self.version}})
        if method == "ping":
            return _response(id, {})
        if method == "tools/list":
            return _response(id, self._list_tools_result())
        if method == "tools/call":
            result = self._call_tool(msg.get("params"))
            if "error" in result:
                return _error(id, result["error"]["code"],
                              result["error"]["message"])
            return _response(id, result)
        return _error(id, METHOD_NOT_FOUND, "方法 %r 未实现" % method)

    def serve_stdio(self, istream=None, ostream=None):
        """通过 stdin/stdout 提供 MCP 服务（每行一个 JSON）。"""
        istream = istream or sys.stdin
        ostream = ostream or sys.stdout
        for line in istream:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                out = _error(None, PARSE_ERROR, "JSON 解析失败")
                ostream.write(json.dumps(out) + "\n")
                ostream.flush()
                continue
            resp = self.handle_message(msg)
            if resp is not None:
                ostream.write(json.dumps(resp, ensure_ascii=False) + "\n")
                ostream.flush()


# ============================================================ Client
class MCPClient(object):
    def __init__(self, command, args=None, cwd=None, env=None):
        self.command = command
        self.args = args or []
        self.cwd = cwd
        self.env = env
        self.proc = None
        self._id = 0
        self._lock = threading.RLock()
        self._tools_cache = None

    def _next_id(self):
        self._id += 1
        return self._id

    def start(self, client_name="dafeiyu-client"):
        """拉起子进程并完成 initialize 握手。"""
        cmd = shutil.which(self.command) or self.command
        self.proc = subprocess.Popen(
            [cmd] + self.args, cwd=self.cwd, env=self.env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        result = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": client_name, "version": "1.0.0"}})
        self._write(_notification("notifications/initialized"))
        return result

    def _write(self, msg):
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def _read_line(self):
        line = self.proc.stdout.readline()
        if not line:
            raise MCPError("MCP server 已关闭连接")
        return json.loads(line.decode("utf-8"))

    def _request(self, method, params=None):
        with self._lock:
            id = self._next_id()
            self._write(_request(id, method, params))
            while True:
                msg = self._read_line()
                if msg.get("id") == id:
                    if "error" in msg:
                        raise MCPError("server 错误: %r" % msg["error"])
                    return msg.get("result")

    def list_tools(self, refresh=False):
        if self._tools_cache is None or refresh:
            self._tools_cache = self._request("tools/list")["tools"]
        return self._tools_cache

    def call_tool(self, name, arguments=None):
        result = self._request("tools/call",
                               {"name": name, "arguments": arguments or {}})
        # 拼接文本内容
        texts = [c.get("text", "") for c in result.get("content", [])
                 if c.get("type") == "text"]
        return {"text": "\n".join(texts),
                "isError": result.get("isError", False), "raw": result}

    def close(self):
        try:
            if self.proc:
                self.proc.stdin.close()
                try:
                    self.proc.wait(timeout=5)
                except Exception:
                    self.proc.terminate()
        except Exception:
            pass
