# -*- coding: utf-8 -*-
"""Kurena 音色链路实测：主环境 → dfy_voice → 常驻 RVC worker → wav。

验四件事：能合成、缓存秒回、模型驻留后真的快、stop 能释放。
跑法：D:\\本地部署大肥鱼环境\\venv312\\Scripts\\python.exe tools\\test_voice_kurena.py
"""
import os
import sys
import time
import wave

sys.path.insert(0, r"D:\大肥鱼AI")
import dfy_voice as v  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (("  " + str(extra)) if extra else ""))


def dur(path):
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate()), w.getframerate(), w.getnchannels()


print("== 0) 依赖到位 ==")
check("Applio 环境/模型/索引都在", v.available())
print("  worker:", v.WORKER)
print("  模型:", os.path.basename(v.PTH), os.path.getsize(v.PTH) // 1024 // 1024, "MB")

print("\n== 1) 第一句（含冷加载）==")
v.stop()
NONCE = str(int(time.time()))            # 每次跑换一句话，避免撞上上一轮的缓存
TEXT1 = "你好呀，我是绀。第 %s 次测试，以后写完东西我念给你听。" % NONCE
t0 = time.time()
p1 = v.say(TEXT1)
t1 = time.time() - t0
check("第一句拿到 wav", p1.endswith(".wav") and os.path.exists(p1), p1)
if p1.endswith(".wav") and os.path.exists(p1):
    d, sr, ch = dur(p1)
    check("音频有效（时长>1秒 / >=44.1k 单声道，模型是 48k v2）", d > 1 and sr >= 44100 and ch == 1,
          "%.1f 秒 / %d Hz / %d ch" % (d, sr, ch))
print("  第一次端到端 %.1f 秒（含 20 秒左右的模型冷加载）" % t1)

print("\n== 2) 同样一句：走缓存，必须秒回 ==")
t0 = time.time()
p2 = v.say(TEXT1)
check("缓存命中返回同一文件", p2 == p1)
check("缓存耗时 < 0.2 秒", time.time() - t0 < 0.2, "%.2f 秒" % (time.time() - t0))

print("\n== 3) 换一句：模型已驻留，应明显变快 ==")
t0 = time.time()
p3 = v.say("这一句是第一次说，需要真的跑一遍变声，编号 %s。" % NONCE)
t3 = time.time() - t0
check("新句子也产出 wav", os.path.exists(p3), p3)
if os.path.exists(p3):
    d3, _, _ = dur(p3)
    check("新句子音频有效", d3 > 1, "%.1f 秒" % d3)
# 原来写死「< 8 秒」：机器一旦被别的重活占着 CPU（比如同时跑 pnpm 装包），
# 冷启动和热跑都会一起变慢，这条就会假红。真正的不变量是「热跑显著快于冷启动」，
# 所以按相对比值判，并保留一个宽松的绝对上限防止「根本没跑完」。
check("驻留后比冷启动快得多（热跑 < 冷启动 60% 且 < 30 秒）",
      t3 < max(30.0, 0.0) and t3 < 30.0 and t3 < (t1 * 0.6 if t1 > 0 else 30.0),
      "%.1f 秒（第一句 %.1f 秒）" % (t3, t1))

print("\n== 4) 状态与释放 ==")
st = v.status()
check("status 报 ready", st["ready"] is True, "calls=%d last=%dms" % (st["calls"], st["last_ms"]))
s = v.stop()
check("stop 返回并结束进程", s["ok"] is True)
time.sleep(1)
check("stop 后不再 ready", v.status()["ready"] is False)

print("\n== 5) 失败要给出人能看懂的话 ==")
r = v.say("   ")
check("空文本返回人话", r == "[空文本]")

print("\n===== 结果：%d 通过 / %d 失败 =====" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + "；".join(FAIL))
sys.exit(1 if FAIL else 0)
