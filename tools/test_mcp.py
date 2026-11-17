# -*- coding: utf-8 -*-
"""
MCP 双向单元测试
================
直接运行：python tools/test_mcp.py
Server：handle_message 纯函数 + StringIO 内存流验证完整 stdio 链路。
Client：拉起真实子进程（临时 MCP server 脚本），做 initialize/list/call/close 端到端。
"""

import os
import sys
import io
import json
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from core import MCPServer, MCPClient, MCPError

PY = r"D:\本地部署大肥鱼环境\venv312\Scripts\python.exe"
_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  [FAIL]", label)


def build_server():
    s = MCPServer("unit", "2.0")
    s.tool("add", "加法", lambda a: str(a.get("x", 0) + a.get("y", 0)),
           inputSchema={"type": "object",
                        "properties": {"x": {"type": "number"}, "y": {"type": "number"}}})
    return s


# ============================================================ Server 纯函数
def test_initialize():
    s = build_server()
    r = s.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    res = r["result"]
    check(r["id"] == 1 and res["protocolVersion"], "initialize 返回协议版本")
    check(res["serverInfo"]["name"] == "unit", "initialize 返回 serverInfo")
    check("tools" in res["capabilities"], "initialize 声明 tools 能力")


def test_invalid_and_notification():
    s = build_server()
    r = s.handle_message({"foo": 1})
    check(r["error"]["code"] == -32600, "非法 JSON-RPC 报错")
    # 通知无 id，返回 None
    n = s.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
    check(n is None, "通知无响应")
    # ping
    p = s.handle_message({"jsonrpc": "2.0", "id": 9, "method": "ping"})
    check(p["result"] == {}, "ping 响应")


def test_tools():
    s = build_server()
    # list
    r = s.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = r["result"]["tools"]
    check(len(tools) == 1 and tools[0]["name"] == "add", "tools/list 发现工具")
    # call 成功
    r = s.handle_message({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                          "params": {"name": "add", "arguments": {"x": 2, "y": 3}}})
    text = r["result"]["content"][0]["text"]
    check(text == "5" and r["result"]["isError"] is False, "tools/call 执行正确")
    # 未知工具
    r = s.handle_message({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                          "params": {"name": "nope"}})
    check(r["error"]["code"] == -32601, "未知工具报错")
    # handler 异常
    s.tool("boom", "炸", lambda a: 1 / 0)
    r = s.handle_message({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                          "params": {"name": "boom"}})
    check(r["result"]["isError"] is True, "handler 异常返回 isError")
    # 未知方法
    r = s.handle_message({"jsonrpc": "2.0", "id": 6, "method": "xyz"})
    check(r["error"]["code"] == -32601, "未知方法报错")


def test_serve_stdio_memory():
    s = build_server()
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    inp = io.StringIO("\n".join(json.dumps(m) for m in msgs) + "\n")
    out = io.StringIO()
    s.serve_stdio(inp, out)
    lines = [json.loads(l) for l in out.getvalue().strip().splitlines()]
    check(len(lines) == 2 and lines[1]["result"]["tools"], "stdio 内存流完整链路")


# ============================================================ Client 端到端
SERVER_SCRIPT = """
import sys
sys.path.insert(0, %r)
from core import MCPServer
s = MCPServer("e2e-server", "1.0")
s.tool("echo", "回显", lambda a: "ECHO " + a.get("m", ""),
       inputSchema={"type":"object","properties":{"m":{"type":"string"}}})
s.serve_stdio()
""" % BASE


def test_client_end_to_end():
    d = tempfile.mkdtemp(prefix="mcp_")
    script = os.path.join(d, "mcp_server.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(SERVER_SCRIPT)

    client = MCPClient(PY, [script])
    try:
        init = client.start()
        check(init["serverInfo"]["name"] == "e2e-server", "client initialize 握手")
        tools = client.list_tools()
        check(len(tools) == 1 and tools[0]["name"] == "echo", "client 发现工具")
        r = client.call_tool("echo", {"m": "hi"})
        check(r["text"] == "ECHO hi" and r["isError"] is False,
              "client 调用工具并回收结果")
    finally:
        client.close()
    check(client.proc.poll() is not None, "client 关闭后子进程退出")


if __name__ == "__main__":
    test_initialize()
    test_invalid_and_notification()
    test_tools()
    test_serve_stdio_memory()
    test_client_end_to_end()
    print("\n================================")
    print("MCP 测试：通过 %d，失败 %d" % (_passed, _failed))
    print("================================")
    sys.exit(1 if _failed else 0)
