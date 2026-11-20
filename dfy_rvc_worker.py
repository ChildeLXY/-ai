# -*- coding: utf-8 -*-
"""常驻 RVC 工作进程（跑在 E:\\Applio\\env\\python.exe 里，由 dfy_voice.py 拉起）。

为什么要有它：Applio 命令行每次调用都要重新 import torch + 载 hubert + 载模型，
冷启动 20 秒以上；而**模型一旦在显存里**，同一句 7 秒音频只要 0.7 秒。
所以这里一次装载、长期驻留，用 stdin/stdout 的 JSON 行协议接活。

协议（每行一个 JSON）：
  进：{"op":"ping"}                                 -> {"ok":true,"loaded":false,"device":"cuda:0"}
  进：{"op":"say","text":"…","voice":"zh-CN-XiaoxiaoNeural","out":"D:\\…wav",
        "pitch":0,"index_rate":0.6,"protect":0.33,"f0":"fcpe","rate":0}
  出：{"ok":true,"out":"D:\\…wav","ms":930,"audio_sec":7.2,"sample_rate":44100,"load_ms":0}
  进：{"op":"unload"}                               -> 释放显存 {"ok":true}
  进：{"op":"quit"}                                 -> 退出

要点：
- Applio 自己会往 stdout 打印（"Using Hi-Fi-GAN vocoder"、tqdm 进度条），
  所以先把真 stdout 抓在手里，再把 sys.stdout 指到 stderr —— 协议通道保持干净。
- 每句都打印 stdout 会污染 pipe，读端解析不了。
"""
import json
import os
import sys
import time
import traceback

APPLIO_ROOT = r"E:\Applio"
PTH_DEFAULT = os.path.join(APPLIO_ROOT, "logs", "Kiriziki Kurena 2.0", "Kiriziki Kurena 2.0.pth")
IDX_DEFAULT = os.path.join(APPLIO_ROOT, "logs", "Kiriziki Kurena 2.0", "Kiriziki Kurena 2.0.index")
FFMPEG = os.path.join(APPLIO_ROOT, "ffmpeg.exe")

_real_stdout = sys.stdout
sys.stdout = sys.stderr          # 挡住第三方库往 stdout 刷屏
sys.path.insert(0, APPLIO_ROOT)
os.chdir(APPLIO_ROOT)

_core = None
_loaded_ms = 0


def _out(obj):
    _real_stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _real_stdout.flush()


def _get_core():
    global _core
    if _core is None:
        import core  # noqa: F401  Applio 的 core.py：run_infer_script / run_tts_script 都在这里
        _core = core
    return _core


def _tts_to_wav(text, voice, rate, mp3_path, wav_path):
    """edge-tts 合成 mp3，再用 Applio 自带 ffmpeg 转成 44.1k 单声道 wav。"""
    import asyncio
    import edge_tts
    import subprocess

    kwargs = {}
    if rate:
        kwargs["rate"] = ("+%d%%" % rate) if rate > 0 else ("%d%%" % rate)

    async def _go():
        await edge_tts.Communicate(text, voice, **kwargs).save(mp3_path)

    asyncio.run(_go())
    if not os.path.exists(mp3_path) or os.path.getsize(mp3_path) == 0:
        raise RuntimeError("edge-tts 没产出音频（可能离线或音色名不对）")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", mp3_path,
                    "-ar", "44100", "-ac", "1", wav_path], check=True)
    return wav_path


def _audio_sec(path):
    try:
        import soundfile as sf
        info = sf.info(path)
        return round(info.frames / float(info.samplerate), 2), info.samplerate
    except Exception:
        return None, None


def op_say(req):
    global _loaded_ms
    core = _get_core()
    text = (req.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "text 为空"}
    out = req["out"]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    work = out + ".src"
    mp3 = work + ".mp3"
    wav_in = work + ".wav"

    t_start = time.time()
    _tts_to_wav(text, req.get("voice") or "zh-CN-XiaoxiaoNeural",
                int(req.get("rate") or 0), mp3, wav_in)

    pth = req.get("pth") or PTH_DEFAULT
    idx = req.get("index") or IDX_DEFAULT
    core.run_infer_script(
        pitch=int(req.get("pitch") or 0),
        index_rate=float(req.get("index_rate", 0.6)),
        volume_envelope=float(req.get("volume_envelope", 1.0)),
        protect=float(req.get("protect", 0.33)),
        f0_method=req.get("f0") or "fcpe",
        input_path=wav_in,
        output_path=out,
        pth_path=pth,
        index_path=idx,
        f0_autotune=False,
        f0_autotune_strength=0.0,
        proposed_pitch=False,
        proposed_pitch_threshold=255,
        export_format="WAV",
        embedder_model="contentvec",
        embedder_model_custom=None,
    )
    ms = int((time.time() - t_start) * 1000)
    sec, sr = _audio_sec(out)
    for f in (mp3, wav_in):
        try:
            os.remove(f)
        except Exception:
            pass
    if not os.path.exists(out):
        return {"ok": False, "error": "转换没产出文件"}
    return {"ok": True, "out": out, "ms": ms, "audio_sec": sec, "sample_rate": sr,
            "load_ms": _loaded_ms}


def op_unload():
    global _core, _loaded_ms
    _core = None
    _loaded_ms = 0
    try:
        import torch
        import gc
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception:
        pass
    return {"ok": True, "unloaded": True}


def main():
    _out({"ok": True, "hello": "dfy-rvc-worker", "pid": os.getpid()})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            _out({"ok": False, "error": "请求不是合法 JSON：%r" % (e,)})
            continue
        op = req.get("op")
        try:
            if op == "ping":
                _out({"ok": True, "loaded": _core is not None, "device": _device(),
                      "pth": PTH_DEFAULT, "index": IDX_DEFAULT})
            elif op == "say":
                _out(op_say(req))
            elif op == "unload":
                _out(op_unload())
            elif op == "quit":
                _out({"ok": True, "bye": True})
                return
            else:
                _out({"ok": False, "error": "未知 op: %r" % (op,)})
        except Exception as e:
            _out({"ok": False, "error": str(e), "trace": traceback.format_exc()[-800:]})


def _device():
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.get_device_name(0)
        return "cpu"
    except Exception:
        return "?"


if __name__ == "__main__":
    main()
