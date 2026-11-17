# -*- coding: utf-8 -*-
"""插件市场独立窗口。

单独进程跑一个 pywebview 窗口，崩了也不影响主程序。
数据和安装逻辑都在 dfy_market 里，这里只做窗口与 JS 桥。
"""

import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import dfy_market as mk  # noqa: E402

HTML = os.path.join(HERE, "market.html")
ICON = os.path.join(HERE, "icon.ico")


class Api:
    """给前端调用的方法，返回值都会 JSON 序列化。"""

    def status(self):
        try:
            return mk.status()
        except Exception as e:
            return {"ok": False, "error": str(e), "total": 0}

    def refresh(self):
        try:
            r = mk.catalog(force=True)
            return mk.status()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def categories(self):
        try:
            return mk.categories()
        except Exception:
            return []

    def search(self, q="", category="all", page=0, size=30, sort="stars"):
        try:
            return mk.search(q, "" if category in (None, "all") else category,
                             int(page or 0), int(size or 30), sort or "stars")
        except Exception as e:
            return {"ok": False, "total": 0, "rows": [], "error": str(e)}

    def detail(self, key):
        try:
            return mk.detail(key)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def install(self, spec):
        try:
            return mk.install(spec)
        except Exception as e:
            return {"ok": False, "error": str(e), "trace": traceback.format_exc()[-500:]}

    def uninstall(self, name):
        try:
            return mk.uninstall(name)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_url(self, url):
        try:
            if not str(url).startswith("http"):
                return False
            os.startfile(url)
            return True
        except Exception:
            return False

    def installed(self):
        try:
            return list(mk.installed().values())
        except Exception:
            return []


def main():
    import webview

    mk.prime_async()

    kwargs = dict(title="DSH 插件市场 · 联网",
                  url=HTML, width=1120, height=790,
                  min_size=(880, 600),
                  background_color="#0d1117",
                  text_select=False,
                  confirm_close=False,
                  js_api=Api())
    if os.path.isfile(ICON):
        kwargs["icon"] = ICON

    win = webview.create_window(**kwargs)
    webview.start(debug=False, private_mode=False,
                  storage_path=os.path.join(r"D:\大肥鱼缓存", "wv2_market"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
