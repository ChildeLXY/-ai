# -*- coding: utf-8 -*-
"""设置面板「朗读音色」的界面验收（Edge headless + 桩 pywebview.api）。

验：默认读设置、两个音色按钮能切、切换会写回 save_setting、试听按钮会打 tts_speak、
以及"释放显存"按钮会打 voice_stop。不真的合成音频（桩返回假路径），所以几秒跑完。
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HTML = r"D:\大肥鱼AI\大肥鱼AI.html"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if not os.path.exists(EDGE):
    for p in (r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",):
        if os.path.exists(p):
            EDGE = p

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (("  " + str(extra)) if extra else ""))


STUB = r"""
<script>
window.__calls = {};
window.pywebview = { api: new Proxy({}, { get(_, name) {
  return async function(...args){
    window.__calls[name] = window.__calls[name] || [];
    window.__calls[name].push(args);
    if (name === 'load_setting') return args[0] === 'voice_engine' ? 'edge' : '0';
    if (name === 'save_setting') return 'ok';
    if (name === 'voice_status') return {available: true, ready: false, voice: 'Kiriziki Kurena 2.0'};
    if (name === 'tts_speak') return 'D:\\\\大肥鱼缓存\\\\voice\\\\preview.wav';
    if (name === 'voice_stop') return {ok: true};
    return null;
  };
} }) };
window.addEventListener('load', async () => {
  try {
    openSettings();
    await new Promise(r => setTimeout(r, 400));
    const row = document.getElementById('voiceEngineGroup');
    const res = {};
    res.rowExists = !!row;
    res.buttons = row ? [...row.querySelectorAll('.opt')].map(b => b.dataset.voice) : [];
    res.defaultMarked = row ? [...row.querySelectorAll('.opt.on')].map(b => b.dataset.voice) : [];
    setVoiceEngine('kurena');
    await new Promise(r => setTimeout(r, 250));
    res.afterClick = [...row.querySelectorAll('.opt.on')].map(b => b.dataset.voice);
    res.note = (document.getElementById('voiceEngineNote') || {}).textContent || '';
    res.saveCalls = (window.__calls.save_setting || []).slice(-1);
    await voicePreview();
    await new Promise(r => setTimeout(r, 250));
    res.ttsCalls = (window.__calls.tts_speak || []).length;
    await voiceUnload();
    res.stopCalls = (window.__calls.voice_stop || []).length;
    document.title = 'VOICE_RESULT:' + JSON.stringify(res);
  } catch (e) { document.title = 'VOICE_RESULT:' + JSON.stringify({error: String(e)}); }
});
</script>
"""


def run_ui():
    with tempfile.TemporaryDirectory() as td:
        html = open(HTML, encoding="utf-8").read()
        # 桩脚本必须在页面自己的脚本之后注入，所以插到 </body> 前
        html = html.replace("</body>", STUB + "\n</body>")
        p = os.path.join(td, "probe.html")
        open(p, "w", encoding="utf-8").write(html)
        out = subprocess.run(
            [EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
             "--user-data-dir=" + os.path.join(td, "edge_profile_%d" % int(time.time())),
             "--virtual-time-budget=8000", "--dump-dom",
             "file:///" + p.replace("\\", "/")],
            capture_output=True, text=True, timeout=120)
        dom = out.stdout or ""
        for line in dom.splitlines():
            if "VOICE_RESULT:" in line:
                raw = line.split("VOICE_RESULT:", 1)[1]
                raw = raw.split("</title>")[0].split("<")[0]
                raw = raw.replace("&quot;", '"').replace("&amp;", "&").strip()
                try:
                    return json.loads(raw)
                except Exception as e:
                    print("  结果解析失败:", e, raw[:200])
        return None


print("== 设置面板「朗读音色」 ==")
res = run_ui()
if not res:
    check("页面能跑出结果", False, "没拿到 VOICE_RESULT")
else:
    check("页面无异常", "error" not in res, res.get("error", ""))
    check("音色行存在", res.get("rowExists") is True)
    check("有两个音色按钮", res.get("buttons") == ["edge", "kurena"], res.get("buttons"))
    check("打开时按设置高亮（默认 edge）", res.get("defaultMarked") == ["edge"], res.get("defaultMarked"))
    check("点 Kurena 后高亮切换", res.get("afterClick") == ["kurena"], res.get("afterClick"))
    check("写回 voice_engine=kurena", (res.get("saveCalls") or [[]])[-1][:2] == ["voice_engine", "kurena"], res.get("saveCalls"))
    check("说明文字提到 RVC 与耗时", "RVC" in (res.get("note") or "") and "显存" in (res.get("note") or ""))
    check("试听会调用 tts_speak", res.get("ttsCalls") == 1, res.get("ttsCalls"))
    check("释放显存会调用 voice_stop", res.get("stopCalls") == 1, res.get("stopCalls"))

print("\n===== 结果：%d 通过 / %d 失败 =====" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + "；".join(FAIL))
sys.exit(1 if FAIL else 0)
