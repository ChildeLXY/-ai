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

NUM_CTX = 8192          # 默认值；运行时可被 apply_ctx() 改写（前端上下文滑块）。前端 CTX_LIMIT 必须与之同步。

# 3072 是 qwen3.6:35b-a3b（MoE 22.6G）实测可用窗口：再大内存放不下权重，开始换页掉速
_ALLOWED_CTX = (2048, 3072, 4096, 8192, 16384, 32768, 65536)


def apply_ctx(n):
    """运行时调整上下文窗口大小（前端"上下文用量"面板的拨杆调用）。

    所有请求都走 options() → 读这里的 NUM_CTX，所以改一次全链路（主对话+插件）一起变，
    不会出现主对话和插件 num_ctx 不一致导致 Ollama 反复卸载/重载模型的问题。
    """
    global NUM_CTX
    try:
        n = int(n)
    except (TypeError, ValueError):
        return NUM_CTX
    # Slider 只给固定档位；不在档位里的值取最接近的一个，防止乱值触发模型重载
    NUM_CTX = min(_ALLOWED_CTX, key=lambda x: abs(x - n))
    return NUM_CTX


def current_ctx():
    return NUM_CTX


def sync_from_settings(settings_path=None):
    """从设置文件读取 ctx_size 并应用（每个进程启动时调用一次）。

    默认路径覆盖两种运行模式：data/settings.json（exe/正式）和 ds图片/settings.json（源码）。
    这样前端滑块、主程序、web 客户端、桌宠 watcher 这些独立进程拿到的 num_ctx 完全一致，
    避免不一致触发 Ollama 反复卸载/重载模型。
    """
    import os, json
    paths = []
    if settings_path:
        paths.append(settings_path)
    here = os.path.dirname(os.path.abspath(__file__))
    paths += [os.path.join(here, "ds图片", "settings.json"),
              r"D:\本地部署大肥鱼环境\data\settings.json"]
    for p in paths:
        try:
            if os.path.exists(p):
                with open(p, encoding="utf-8-sig") as f:
                    v = (json.load(f) or {}).get("ctx_size")
                if v:
                    return apply_ctx(v)
        except Exception:
            continue
    return NUM_CTX


# 模块被 import 时自动同步一次（各独立进程无需改动即可生效）
sync_from_settings()
# 闲置多久才卸载模型。35B 权重 22.6G，卸载后重新加载要 20~30 秒；
# 各调用方（QQ/DSH 宿主/桌面端）必须用同一个值，配合 OLLAMA_MAX_LOADED_MODELS=1
# 保证单实例常驻、不互相挤掉。
KEEP_ALIVE = "30m"

# ---- MoE 大模型 runner 参数（35b-a3b 专用） ----
# 生成速度瓶颈是内存带宽（激活 3B 但全部 22.6G 权重都要能被摸到）：
#   num_batch 512 → prefill 吞吐从 9 t/s 提到 60 t/s
#   num_thread 8  → 只用 8 个 P 核，E 核参战反而拖慢同步
#   num_gpu 99    → 能塞进 4060 的层全上 GPU（prefill 快 6 倍）
#   num_keep 48   → 系统提示 KV 常驻，重复前缀不重算
# 这些参数改变 runner 分配，必须全链路一致，否则 Ollama 每次都重载 runner（20s+）。
MOE_TUNE = {"num_batch": 512, "num_thread": 8, "num_gpu": 99, "num_keep": 48}
_moe_cache = {}


def is_moe_big(model):
    """按模型名/体积判断是不是 22G 级 MoE（35b / 30b-a3b 这类）。"""
    if model in _moe_cache:
        return _moe_cache[model]
    big = False
    low = str(model or "").lower()
    if "35b" in low or "30b" in low or "a3b" in low:
        big = True
    else:
        try:
            import urllib.request, json as _json
            _op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with _op.open("http://127.0.0.1:11434/api/tags", timeout=6) as r:
                for m in _json.loads(r.read().decode("utf-8")).get("models", []):
                    if m.get("name") == model and (m.get("size") or 0) >= 15 * 2**30:
                        big = True
                        break
        except Exception:
            pass
    _moe_cache[model] = big
    return big


def options(num_predict=400, temperature=None, model=None, **extra):
    """拼 options 字典，保证 num_ctx 永远带上；MoE 大模型自动附加 runner 参数。"""
    o = {"num_ctx": NUM_CTX, "num_predict": num_predict}
    if temperature is not None:
        o["temperature"] = temperature
    o.update(extra)
    if model and is_moe_big(model):
        o.update(MOE_TUNE)
    return o


def payload(model, messages, stream=False, num_predict=400,
            temperature=None, think=None, extra_options=None):
    """拼完整的 /api/chat 请求体，num_ctx / keep_alive 不用各处再操心。"""
    body = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "keep_alive": KEEP_ALIVE,
        "options": options(num_predict, temperature, model=model, **(extra_options or {})),
    }
    if think is not None:
        body["think"] = think
    return body
