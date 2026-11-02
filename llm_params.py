# -*- coding: utf-8 -*-
""" Ollama 请求参数的唯一来源。

为什么单独抽一个文件：以前主对话发 num_ctx=8192，而插件调用（表情包选图、
心情判定、记忆提炼、桌宠自言自语）都没传这个参数，会落到模型默认的 4096。
Ollama 发现"这次要求的上下文长度和已加载的不一样"，就会把整个 5GB 模型
卸载再重新加载一遍——实测每次 load_duration 约 4 秒。

于是每发一条消息的实际流程变成：
    主对话(8192) → 重载 4s → 生成 → 插件选图(不传→4096) → 又重载 4s
下一轮再来一遍。用户感觉到的"回复慢"，一半时间花在这上面。

所以这里统一成一个常量，所有调用点都必须用它。
"""

NUM_CTX = 8192          # 与前端 大肥鱼AI.html 里的 options.num_ctx 必须一致
# 闲置多久才卸载模型。模型 5.5GB 在 D 盘，卸载后重新加载要读盘 20~30 秒，
# 这是"聊完隔一会儿再聊，第一句特别慢"的真正原因。拉到 60 分钟，
# 代价是模型一直占着 5.5GB 显存（要打游戏可以关掉大肥鱼，或改成 '10m'）。
KEEP_ALIVE = "60m"


def options(num_predict=400, temperature=None, **extra):
    """拼 options 字典，保证 num_ctx 永远带上。"""
    o = {"num_ctx": NUM_CTX, "num_predict": num_predict}
    if temperature is not None:
        o["temperature"] = temperature
    o.update(extra)
    return o


def payload(model, messages, stream=False, num_predict=400,
            temperature=None, think=None, extra_options=None):
    """拼完整的 /api/chat 请求体，num_ctx / keep_alive 不用各处再操心。"""
    body = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "keep_alive": KEEP_ALIVE,
        "options": options(num_predict, temperature, **(extra_options or {})),
    }
    if think is not None:
        body["think"] = think
    return body
