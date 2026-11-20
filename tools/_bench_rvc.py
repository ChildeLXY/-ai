# -*- coding: utf-8 -*-
"""基准：同一进程内连续两次转换，看模型是否复用（决定要不要做常驻进程）。

用 E:\Applio\env\python.exe 跑，cwd 必须是 E:\Applio。
"""
import sys
import time

sys.path.insert(0, r"E:\Applio")
import os

os.chdir(r"E:\Applio")

t0 = time.time()
import core  # noqa: E402

print("import core 用时 %.1f 秒" % (time.time() - t0))

PTH = r"E:\Applio\logs\Kiriziki Kurena 2.0\Kiriziki Kurena 2.0.pth"
IDX = r"E:\Applio\logs\Kiriziki Kurena 2.0\Kiriziki Kurena 2.0.index"
IN = r"D:\大肥鱼缓存\rvc_test\base.wav"


def once(out, index_rate=0.6, f0="fcpe"):
    return core.run_infer_script(
        pitch=0, index_rate=index_rate, volume_envelope=1.0, protect=0.33,
        f0_method=f0, input_path=IN, output_path=out, pth_path=PTH, index_path=IDX,
        f0_autotune=False, f0_autotune_strength=0.0, proposed_pitch=False,
        proposed_pitch_threshold=255, export_format="WAV", embedder_model="contentvec",
        embedder_model_custom=None,
    )


for i in (1, 2, 3):
    t = time.time()
    once(r"D:\大肥鱼缓存\rvc_test\bench%d.wav" % i)
    print("第 %d 次转换：%.1f 秒（累计 %.1f）" % (i, time.time() - t, time.time() - t0))
    print("  输出存在:", os.path.exists(r"D:\大肥鱼缓存\rvc_test\bench%d.wav" % i))

t = time.time()
once(r"D:\大肥鱼缓存\rvc_test\bench_idx0.wav", index_rate=0.0)
print("index_rate=0 那次：%.1f 秒" % (time.time() - t))
