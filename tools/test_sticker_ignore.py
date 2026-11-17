# -*- coding: utf-8 -*-
"""表情包前缀解析 + 忽略名单 marker 的单元测试（不依赖真实 QQ/Ollama）。"""
import os, sys, importlib.util

sys.path.insert(0, r"D:\大肥鱼AI\qqbot_core")
QQBOT = r"D:\大肥鱼AI\qqbot_core\qqbot.py"
spec = importlib.util.spec_from_file_location("qqbot_under_test", QQBOT)
qb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qb)

Brain, OneBot = qb.Brain, qb.OneBot
PASS = 0


def check(cond, msg):
    global PASS
    assert cond, "FAIL: " + msg
    PASS += 1
    print("ok -", msg)


print("== 一、表情包 _extract_sticker ==")

# 1 带前缀（截图里的真实案例）
clean, name = Brain._extract_sticker("专注陪你～[表情包:怎么个事]")
check(name == "怎么个事", "带前缀 [表情包:xxx] 提取真实名字")
check("表情包" not in clean and "怎么个事" not in clean, "带前缀标记从正文移除")
check(clean == "专注陪你～", "前缀标记移除后正文保留")

# 2 其他前缀
clean, name = Brain._extract_sticker("哈哈[表情:开心]")
check(name == "开心", "[表情:xxx] 前缀识别")
clean, name = Brain._extract_sticker("晚安[贴纸:晚安]")
check(name == "晚安", "[贴纸:xxx] 前缀识别")

# 3 旧格式向后兼容
clean, name = Brain._extract_sticker("我超听话的~ [猫猫搓手]")
check(name == "猫猫搓手", "旧格式 [名字] 仍识别")

# 4 语音开关不被当成表情
clean, name = Brain._extract_sticker("念给你听[语音]")
check(name == "" and "[语音]" in clean, "[语音] 原样保留，不当表情")

# 5 前缀标记写在正文中间（兜底）
clean, name = Brain._extract_sticker("先[表情包:无语]一下再说")
check(name == "无语", "正文中间的前缀标记兜底识别")
check("表情包" not in clean, "中间标记被移除")

# 6 含句读的普通方括号不认
clean, name = Brain._extract_sticker("备注[今天要开会，记得带电脑]")
check(name == "", "含句读的方括号判定为正文，不误删")

# 7 无标记纯文本
clean, name = Brain._extract_sticker("就是普通的一句话")
check(name == "" and clean == "就是普通的一句话", "无标记文本不动")

print("== 二、忽略名单 _do_ignore_marker ==")

def make_onebot(friends):
    ob = object.__new__(OneBot)
    ob.blacklist = []
    saved = {}
    ob.blacklist_save = lambda uids: (True, saved.setdefault("v", list(uids))) or (True, list(uids))
    sent = []
    ob.send = lambda kind, t, text, **kw: sent.append((kind, str(t), text))
    ob._api = lambda action, params: {"data": friends} if action == "get_friend_list" else {}
    ob.sent = sent
    return ob

FRIENDS = [
    {"user_id": 123456789, "nickname": "流萤酱", "remark": ""},
    {"user_id": 987654321, "nickname": "路人甲", "remark": "备注名"},
]

# 8 找到好友：通知发给本人，汇报发给主人，黑名单持久化
ob = make_onebot(FRIENDS)
out = ob._do_ignore_marker("private", "111", "好的[忽略:流萤酱]")
check("忽略" not in out, "ignore 标记从正文移除")
targets = [(k, t) for k, t, _ in ob.sent]
check(("private", "123456789") in targets, "通知发给联系人本人（正确对象）")
check(("private", "111") in targets, "结果汇报发给主人")
check("123456789" in ob.blacklist, "联系人加入内存黑名单")
# 通知内容与汇报内容分开，不把对她说的话发给主人
notify = [txt for k, t, txt in ob.sent if t == "123456789"][0]
report = [txt for k, t, txt in ob.sent if t == "111"][0]
check("恕不奉陪" in notify and "尾号" in report, "通知与汇报内容各自正确，不发错对象")
check("123456789" not in report, "汇报里不暴露完整 QQ 号（只显示尾号）")

# 9 按备注名匹配
ob = make_onebot(FRIENDS)
ob._do_ignore_marker("private", "111", "[忽略:备注名]")
check("987654321" in ob.blacklist, "支持按备注名解析联系人")

# 10 找不到好友：不发通知、不加黑名单，提示主人
ob = make_onebot(FRIENDS)
ob._do_ignore_marker("private", "111", "[忽略:不存在的人]")
check(ob.blacklist == [], "找不到人时不写黑名单")
check(any("没找到" in txt for _, _, txt in ob.sent), "找不到人时明确告诉主人")
check(not any(t == "123456789" for _, t, _ in ob.sent), "找不到人时不乱发通知")

# 11 自定义通知内容
ob = make_onebot(FRIENDS)
ob._do_ignore_marker("private", "111", "[忽略:流萤酱|我们以后别联系了]")
notify = [txt for k, t, txt in ob.sent if t == "123456789"][0]
check("别联系了" in notify, "支持「名字|自定义通知」写法")

print("\n全部通过：%d 项断言" % PASS)
