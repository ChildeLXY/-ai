# -*- coding: utf-8 -*-
"""朗读��由测试：tts_speak 在「晓伊 / Kurena」之间切，出事必须退回晓伊。

做法：不去 import 大肥鱼AI.py（模块级就会拉 pywebview 起窗），而是用 ast 把
tts_speak / _voice_log 两个函数源码抠出来，喂一份假依赖执行 —— 判的就是真实那份代码。
"""
import ast
import os
import sys
import tempfile
import types

SRC = r"D:\大肥鱼AI\大肥鱼AI.py"
PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (("  " + str(extra)) if extra else ""))


def load_funcs(namespace):
    tree = ast.parse(open(SRC, encoding="utf-8").read())
    wanted = {"tts_speak", "_voice_log"}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            exec(compile(ast.Module(body=[node], type_ignores=[]), SRC, "exec"), namespace)
    return namespace


def make_edge_stub(tmpdir):
    """极简 edge_tts 假实现：Communicate(text, voice).save(path) 写个文件。"""
    mod = types.ModuleType("edge_tts")

    class Communicate:
        def __init__(self, text, voice):
            self.text = text
            self.voice = voice

        async def save(self, path):
            with open(path, "w", encoding="utf-8") as f:
                f.write("FAKE_EDGE:" + self.voice)

    mod.Communicate = Communicate
    return mod


def make_kurena_stub(result):
    mod = types.ModuleType("dfy_voice")

    def say(text, **kw):
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result(text)
        return result

    mod.say = say
    return mod


def run_case(tmpdir, engine, kurena_result):
    ns = {"os": os, "time": time_mod(), "cache_dir": tmpdir}
    settings = {"voice_engine": engine}
    ns["load_setting"] = lambda k: settings.get(k)
    injected = {"edge_tts": make_edge_stub(tmpdir), "dfy_voice": make_kurena_stub(kurena_result)}
    old = {k: sys.modules.get(k) for k in injected}
    sys.modules.update(injected)
    try:
        load_funcs(ns)
        return ns["tts_speak"]("这是测试句子。")
    finally:
        for k, v in old.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def time_mod():
    import time
    return time


with tempfile.TemporaryDirectory() as td:
    print("== 路由与回退 ==")
    p = run_case(td, "kurena", "D:\\fake\\kurena.wav")
    check("设成 Kurena 时走 RVC", p == "D:\\fake\\kurena.wav", p)

    p = run_case(td, "kurena", "[Kurena 语音失败: 模型没加载]")
    check("Kurena 返回错误串 → 退回晓伊", p.endswith(".mp3"), os.path.basename(p))

    p = run_case(td, "kurena", RuntimeError("worker 挂了"))
    check("Kurena 抛异常 → 退回晓伊", p.endswith(".mp3"), os.path.basename(p))

    p = run_case(td, "edge", "D:\\fake\\kurena.wav")
    check("设成晓伊时不碰 RVC", p.endswith(".mp3"), os.path.basename(p))

    p = run_case(td, None, "D:\\fake\\kurena.wav")
    check("没设置过默认晓伊", p.endswith(".mp3"), os.path.basename(p))

    print("\n== 接线检查（源码级） ==")
    src = open(SRC, encoding="utf-8").read()
    check("暴露了 voice_status", "voice_status, voice_stop, voice_warm" in src)
    check("失败会记 _voice.log", "_voice.log" in src and "退回 edge" in src)

print("\n===== 结果：%d 通过 / %d 失败 =====" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + "；".join(FAIL))
sys.exit(1 if FAIL else 0)
