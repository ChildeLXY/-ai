# -*- coding: utf-8 -*-
"""
大肥鱼 · 碎碎念守望者（独立进程）
每隔 mutterMin 分钟，用本地模型生成一句"女儿式"念叨，写进命令文件，
由桌宠(dsh-pet)的原生气泡显示。本脚本不弹窗、不碰桌宠本体。
"""
import os, json, time, datetime, urllib.request, re

_DATA_DIR = os.environ.get("DFY_DATA_DIR") or r"D:\本地部署大肥鱼环境\data"
_CACHE_DIR = os.environ.get("DFY_CACHE_DIR") or r"D:\大肥鱼缓存"
ROOT = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(ROOT, "ds图片", "settings.json")
MEMORY = os.path.join(_DATA_DIR, "memory.json")
SAY_FILE = os.path.join(_CACHE_DIR, "pet_say.txt")
OLLAMA = "http://localhost:11434"
MODEL = "qwen2.5vl:7b"  # 非推理模型，直接出话

def read_settings():
    try:
        return json.load(open(SETTINGS, encoding="utf-8"))
    except Exception:
        return {}

def mutter_minutes():
    try:
        v = int(read_settings().get("mutterMin", "20"))
        return max(5, min(180, v))
    except Exception:
        return 20

def load_facts():
    try:
        m = json.load(open(MEMORY, encoding="utf-8"))
        return "；".join((m.get("facts") or [])[-10:])
    except Exception:
        return ""

def ask_ollama():
    now = datetime.datetime.now(); h = now.hour
    tz = "凌晨" if h < 6 else "早上" if h < 11 else "中午" if h < 14 else "下午" if h < 18 else "晚上" if h < 23 else "深夜"
    facts = load_facts()
    prompt = (
        "你是大肥鱼，一个像女儿一样住在用户电脑里的本地AI，开发者是Childe。"
        "现在是" + tz + str(h) + "点。请用一句不超过30字、自然亲昵、像女儿在旁边自言自语的话："
        "可以提醒休息/喝水/早睡，或结合你知道的用户近况给点小建议；深夜要催他睡觉。"
        "只输出那句话本身，不要引号、不要解释、不要思考过程。\n"
        "你对用户的了解：" + (facts or "你还不太了解他")
    )
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                        "stream": False, "options": {"num_predict": 80}}).encode("utf-8")
    req = urllib.request.Request(OLLAMA + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        out = json.loads(r.read().decode("utf-8"))
    txt = out.get("message", {}).get("content", "").strip()
    txt = re.sub(r"<think>[\s\S]*?</think>", "", txt).strip().strip('"').strip()
    return txt[:80] if txt else ""

def deliver(line):
    try:
        os.makedirs(os.path.dirname(SAY_FILE), exist_ok=True)
        with open(SAY_FILE, "w", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

def main():
    time.sleep(90)  # 开机后先等 90 秒
    while True:
        try:
            line = ask_ollama()
            if line:
                deliver(line)
        except Exception:
            pass  # ollama 没就绪就静默跳过
        time.sleep(mutter_minutes() * 60)

if __name__ == "__main__":
    main()
