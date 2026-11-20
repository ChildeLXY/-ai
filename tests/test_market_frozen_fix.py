# -*- coding: utf-8 -*-
"""验证 #104 修复：frozen 安装版 open_window 不再起子进程，返回 embedded 标记。"""
import sys
import os

sys.path.insert(0, r"D:\大肥鱼AI")
import dfy_market


class _FakePopen(object):
    def __init__(self, *a, **k):
        self.pid = 99999


def test_frozen_embedded_no_subprocess():
    calls = []

    class _SpyPopen(_FakePopen):
        def __init__(self, *a, **k):
            calls.append((a, k))
            super().__init__(*a, **k)

    orig_popen = dfy_market.subprocess.Popen
    dfy_market.subprocess.Popen = _SpyPopen
    was_frozen = hasattr(sys, "frozen")
    old = getattr(sys, "frozen", None)
    sys.frozen = True
    try:
        r = dfy_market.open_window()
    finally:
        dfy_market.subprocess.Popen = orig_popen
        if was_frozen:
            sys.frozen = old
        else:
            del sys.frozen
    assert isinstance(r, dict), "返回必须是 dict"
    assert r.get("ok") is True, "frozen 分支必须 ok=True，实际 %r" % r
    assert r.get("embedded") is True, "frozen 分支必须带 embedded=True，实际 %r" % r
    assert not calls, "frozen 分支绝不能起子进程，实际调用了 %r" % calls
    print("[PASS] frozen 分支返回 embedded 且未起子进程：", r)


def test_source_keeps_subprocess():
    # 源码形态：sys.frozen 不存在，应保留原 subprocess 逻辑（用假 Popen 拦截，不弹窗）
    assert not getattr(sys, "frozen", False)
    calls = []

    class _SpyPopen(_FakePopen):
        def __init__(self, *a, **k):
            calls.append((a, k))
            super().__init__(*a, **k)

    orig_popen = dfy_market.subprocess.Popen
    dfy_market.subprocess.Popen = _SpyPopen
    try:
        r = dfy_market.open_window()
    finally:
        dfy_market.subprocess.Popen = orig_popen
    assert "embedded" not in r, "源码形态不应该走 embedded 分支，实际 %r" % r
    assert calls, "源码形态应继续走 subprocess 链路"
    print("[PASS] 源码形态保留 subprocess 逻辑，未带 embedded：", r)


if __name__ == "__main__":
    test_frozen_embedded_no_subprocess()
    test_source_keeps_subprocess()
    print("ALL PASS")
