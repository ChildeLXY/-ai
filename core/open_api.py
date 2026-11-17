# -*- coding: utf-8 -*-
"""
大肥鱼 AI · L2 · 开放 API（架构 P8）
====================================
把 agent 能力通过 HTTP REST 暴露：/health、/api/tools、/api/chat、/api/status。
含 token 鉴权与简单限流。

核心请求处理 :meth:`OpenAPI.handle` 为纯函数（不绑定 socket），可直接单测；
:class:`OpenAPIServer` 是基于 http.server 的薄封装。
"""

import json
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

__all__ = ["OpenAPI", "OpenAPIServer"]


class RateLimiter(object):
    def __init__(self, max_per_window=60, window=60):
        self.max = max_per_window
        self.window = window
        self._hits = []
        self._lock = threading.Lock()

    def allow(self):
        now = time.time()
        with self._lock:
            self._hits = [t for t in self._hits if now - t < self.window]
            if len(self._hits) >= self.max:
                return False
            self._hits.append(now)
            return True


class OpenAPI(object):
    def __init__(self, chat_handler, tools_provider=None,
                 token=None, rate_limiter=None):
        # chat_handler(payload:dict)->result dict
        self.chat_handler = chat_handler
        self.tools_provider = tools_provider or (lambda: [])
        self.token = token
        self.rl = rate_limiter or RateLimiter()

    def _json(self, status, body):
        return status, body

    def handle(self, method, path, headers, body):
        # 健康检查不鉴权
        if path == "/health" and method == "GET":
            return self._json(200, {"ok": True, "service": "dafeiyu-openapi"})

        # 鉴权
        if self.token:
            auth = headers.get("Authorization", "")
            if auth != "Bearer " + self.token:
                return self._json(401, {"error": "未授权：token 无效"})

        # 限流
        if not self.rl.allow():
            return self._json(429, {"error": "请求过于频繁，已限流"})

        if path == "/api/tools" and method == "GET":
            return self._json(200, {"tools": self.tools_provider()})
        if path == "/api/status" and method == "GET":
            return self._json(200, {"status": "running", "ts": time.time()})
        if path == "/api/chat" and method == "POST":
            try:
                payload = json.loads(body) if body else {}
            except Exception:
                return self._json(400, {"error": "请求体不是合法 JSON"})
            if not payload.get("message"):
                return self._json(400, {"error": "缺少 message 字段"})
            try:
                result = self.chat_handler(payload)
                return self._json(200, result)
            except Exception as e:
                return self._json(500, {"error": "agent 执行失败: %r" % e})
        return self._json(404, {"error": "路径不存在"})


# ============================================================ HTTP 薄封装
class _Handler(BaseHTTPRequestHandler):
    api = None

    def log_message(self, *a):
        pass

    def _serve(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        headers = {k: v for k, v in self.headers.items()}
        status, resp = self.api.handle(self.command, self.path, headers, body)
        data = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._serve()

    def do_POST(self):
        self._serve()


class OpenAPIServer(object):
    def __init__(self, api, host="127.0.0.1", port=8899):
        self.api = api
        self.host = host
        self.port = port
        self._httpd = None
        self._thread = None

    def start(self):
        _Handler.api = self.api
        self._httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return {"host": self.host, "port": self.port}

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
