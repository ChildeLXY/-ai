# -*- coding: utf-8 -*-
"""
开放 API 单元测试
=================
直接运行：python tools/test_open_api.py
纯函数 handle 测试 + 真实 HTTP server 端到端。
"""

import os
import sys
import json
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from core import OpenAPI, OpenAPIServer, RateLimiter

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  [FAIL]", label)


def chat_handler(payload):
    return {"reply": "回答:" + payload["message"]}


def build_api(**kw):
    base = dict(chat_handler=chat_handler,
                tools_provider=lambda: [{"name": "list_dir"}],
                token="secret123")
    base.update(kw)
    return OpenAPI(**base)


H = {"Authorization": "Bearer secret123"}


def test_health_and_auth():
    api = build_api()
    s, b = api.handle("GET", "/health", {}, "")
    check(s == 200 and b["ok"], "health 无需鉴权")
    # 错误/缺失 token
    s, b = api.handle("GET", "/api/tools", {}, "")
    check(s == 401, "无 token 拒绝")
    s, b = api.handle("GET", "/api/tools",
                      {"Authorization": "Bearer wrong"}, "")
    check(s == 401, "错误 token 拒绝")
    # 正确 token
    s, b = api.handle("GET", "/api/tools", H, "")
    check(s == 200 and b["tools"][0]["name"] == "list_dir", "正确 token 放行")


def test_rate_limit():
    rl = RateLimiter(max_per_window=2, window=60)
    api = build_api(rate_limiter=rl)
    statuses = [api.handle("GET", "/api/tools", H, "")[0] for _ in range(3)]
    check(statuses == [200, 200, 429], "超出限流返回 429")


def test_chat_and_errors():
    api = build_api(token=None)   # 不鉴权，专注 chat 逻辑
    body = json.dumps({"message": "你好"})
    s, b = api.handle("POST", "/api/chat", {}, body)
    check(s == 200 and b["reply"] == "回答:你好", "chat 成功")
    # 非法 JSON
    s, b = api.handle("POST", "/api/chat", {}, "notjson")
    check(s == 400, "非法 JSON 400")
    # 缺 message
    s, b = api.handle("POST", "/api/chat", {}, json.dumps({"x": 1}))
    check(s == 400, "缺 message 400")
    # handler 异常
    bad_api = OpenAPI(chat_handler=lambda p: 1 / 0, token=None)
    s, b = bad_api.handle("POST", "/api/chat", {}, body)
    check(s == 500, "handler 异常 500")
    # status / 404
    check(api.handle("GET", "/api/status", {}, "")[0] == 200, "status 200")
    check(api.handle("GET", "/nope", {}, "")[0] == 404, "404")


def test_http_end_to_end():
    api = build_api(token=None)
    server = OpenAPIServer(api, "127.0.0.1", 8931)
    info = server.start()
    try:
        # 真实 HTTP 请求
        req = urllib.request.Request(
            "http://127.0.0.1:%d/api/chat" % info["port"],
            data=json.dumps({"message": "端到端"}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            out = json.loads(r.read().decode("utf-8"))
        check(out["reply"] == "回答:端到端", "HTTP server 端到端调用")
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/health" % info["port"], timeout=10) as r:
            check(r.status == 200, "HTTP health 端到端")
    finally:
        server.stop()


if __name__ == "__main__":
    test_health_and_auth()
    test_rate_limit()
    test_chat_and_errors()
    test_http_end_to_end()
    print("\n================================")
    print("开放 API 测试：通过 %d，失败 %d" % (_passed, _failed))
    print("================================")
    sys.exit(1 if _failed else 0)
