# -*- coding: utf-8 -*-
"""验证 qqbot 发空间护栏：只有用户明确要求发空间/说说/动态时才放行。"""
import sys
sys.path.insert(0, r"D:\大肥鱼AI")

from qqbot_core.qqbot import OneBot


class _Dummy(OneBot):
    def __init__(self):
        pass  # 不跑 OneBot 的真实初始化，只借用方法与类级正则


d = _Dummy()

# 必须判为「真的在要求发空间」
positive = [
    "帮我发个空间，说今天好累",
    "发一条说说：周末去爬山了",
    "更新一下朋友圈",
    "空间里发张图",
    "发条动态",
    "写个qq空间",
]
for s in positive:
    assert d._qzone_asked(s) is True, "应识别为发空间请求：%r" % s

# 必须判为「没要求发空间」（模型不该把回答发出去）
negative = [
    "帮我查一下这篇文章讲了什么",
    "今天天气怎么样",
    "你看我发的表情包",
    "空间里那个游戏好不好玩",   # 只是提到空间，不是要发
    "帮我写个工作总结",
    "",
]
for s in negative:
    assert d._qzone_asked(s) is False, "不应识别为发空间请求：%r" % s

print("[PASS] 发空间护栏：%d 正例 + %d 反例全部正确" % (len(positive), len(negative)))
