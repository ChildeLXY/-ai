# -*- coding: utf-8 -*-
"""大肥鱼AI · 云端模型兼容代理

把 OpenAI-compatible 的第三方 API 伪装成本机 Ollama，这样前端、插件、
主程序全都一个字不用改，只由 api_base.py 决定走哪条路。

    Ollama 协议 (11435)  ──►  本代理  ──►  /v1/chat/completions (云端)
    Ollama 协议 (11435)  ──►  本代理  ──►  11434 (本地模式，纯透传)

启动：
    python cloud_proxy.py            # 默认 11435
    python cloud_proxy.py 11435      # 指定端口

配置：同目录 cloud_config.json，可在设置页里改，改完立即生效（每次请求重读）。
"""
import asyncio
import json
import os
import re
import sys
import time

from aiohttp import web, ClientSession, ClientTimeout

import api_base

LOCAL = api_base.LOCAL_BASE
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 11435

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
}


def cfg():
    return api_base.config()


async def options(request):
    return web.Response(status=204, headers=CORS)


# ---------------------------------------------------------------- 透传
async def passthrough(request, path, body: bytes, note=None):
    """本地模式：原封不动转发给真正的 Ollama，包括流式分块。

    note: 云端失败后回落本地时，往流式回答最前面塞一句提示，
          让用户知道这句话不是云端模型说的（非流式不加，避免破坏 JSON）。
    """
    timeout = ClientTimeout(total=None, sock_read=600)
    try:
        async with ClientSession(timeout=timeout, trust_env=False) as s:
            async with s.post(LOCAL + path, data=body,
                              headers={"Content-Type": "application/json"}) as up:
                if up.status != 200:
                    text = await up.text()
                    return web.Response(status=up.status, text=text, headers=CORS)
                ctype = up.headers.get("Content-Type", "application/json")
                resp = web.StreamResponse(headers=dict(
                    CORS, **{"Content-Type": ctype, "Cache-Control": "no-cache"}))
                await resp.prepare(request)
                if note and "ndjson" in ctype:
                    await resp.write(json.dumps({
                        "model": "local", "message": {"role": "assistant", "content": note},
                    }, ensure_ascii=False).encode("utf-8") + b"\n")
                async for chunk in up.content.iter_any():
                    await resp.write(chunk)
                await resp.write_eof()
                return resp
    except Exception as e:
        return web.json_response({"error": "本地 Ollama 连不上：%s" % e},
                                 status=502, headers=CORS)


async def passthrough_get(request, path):
    try:
        async with ClientSession(trust_env=False) as s:
            async with s.get(LOCAL + path) as up:
                return web.Response(body=await up.read(), status=up.status,
                                    content_type="application/json", headers=CORS)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=502, headers=CORS)


# ---------------------------------------------------------------- 协议翻译
def to_openai_messages(messages):
    """Ollama 的 images:[base64] → OpenAI 的 content 数组。"""
    out = []
    for m in messages or []:
        role = m.get("role", "user")
        text = m.get("content", "") or ""
        images = m.get("images") or []
        if images:
            parts = []
            if text:
                parts.append({"type": "text", "text": text})
            for b64 in images:
                b = str(b64)
                if b.startswith("data:"):
                    url = b
                else:
                    url = "data:image/jpeg;base64," + b
                parts.append({"type": "image_url", "image_url": {"url": url}})
            out.append({"role": role, "content": parts})
        else:
            # 智谱等国内端点对「空 content」是直接 400（未正常接收到prompt参数）；
            # 用户只发一张表情包、或这一段只有图片占位时，content 就可能是空串。
            # 这里补一个中性占位，别让整轮请求因为"没字"被拒。
            if not str(text or "").strip():
                text = "（这一条没有文字）"
            out.append({"role": role, "content": text})
    return out


def to_openai_payload(body, c):
    """Ollama 专有字段（num_ctx / keep_alive / think / options）在这里消化掉。"""
    opts = body.get("options") or {}
    p = {
        "model": resolve_model(body, c),
        "messages": to_openai_messages(body.get("messages")),
        "stream": bool(body.get("stream")),
        "temperature": opts.get("temperature", 0.7),
    }
    n = opts.get("num_predict")
    if isinstance(n, int) and n > 0:
        p["max_tokens"] = min(n, int(c.get("max_tokens") or 4096))
    elif c.get("max_tokens"):
        p["max_tokens"] = int(c["max_tokens"])
    # 思考型模型（glm-4.7/4.5-flash 等）思维链和正文共用 max_tokens 一个预算：
    # 预算太小会被思考吃光，正文只剩空字符串（实测 num_predict=120 时正文 0 字）。
    # 和大肥鱼AI 主站同一套策略：思考型至少给到 3000。
    if model_caps_for(p["model"], c)[1]:
        p["max_tokens"] = max(int(p.get("max_tokens") or 0), 3000)
    # 推理型模型（deepseek-reasoner / o1 / o3）不收 temperature，传了会 400
    m = str(p["model"]).lower()
    reasoner = "reasoner" in m or m.startswith("o1") or m.startswith("o3")
    if reasoner:
        p.pop("temperature", None)
    # Ollama 的 format:"json" → OpenAI 的 response_format。agent 循环要 JSON，
    # 带上这个后厂商端会做结构化约束，模型输出裸 JSON 的成功率大幅提高。
    # 例外：deepseek-reasoner / o1 / o3 不支持 response_format，传了会 400；
    # 另外 qwen 的商业端点对 json_object 要求 prompt 里带 "json" 字样，
    # DeepBuddy 的 system 提示词里本来就有 JSON 字样，满足。
    if str(body.get("format") or "").lower() == "json" and not reasoner:
        p["response_format"] = {"type": "json_object"}
    # 不传 stream_options：Groq / 部分国产兼容端点不认这个字段，会直接 400
    return p


def cloud_headers(c):
    key = (c.get("api_key") or "").strip()
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = "Bearer " + key
    return h


def resolve_model(body, c):
    """决定这次请求实际打到哪个上游模型。

    以前只有配置里的 model 一个，所有请求都发给它。现在同一个 Key 下挂了
    一份模型清单（api_base.cloud_models），前端下拉选哪个就发哪个：

      · 请求里的 model 在清单里 → 用它（这是正常路径）
      · 请求里的 model 不在清单里 → 退回配置的主模型（老行为，别人的调用不受影响）
      · 清单为空（单模型模式）→ 直接用配置的 model

    这么分流而不是"认模型名里的关键词"，是因为同一家的模型 id 长得都差不多
    （glm-4.7-flash / glm-4.6v-flash / glm-5.3-flash），按名字猜必然出错。
    """
    want = str((body or {}).get("model") or "").strip()
    models = api_base.cloud_models(c)
    ids = [m["id"] for m in models]
    if want and want in ids:
        return want
    if not models:
        # 没有清单的极端情况：让请求里的模型名原样透传，
        # 至少比硬塞配置里的模型更贴近调用方的意图
        return want or str(c.get("model") or "")
    return models[0]["id"] if models else str(c.get("model") or "")


def model_caps_for(model, c):
    """这个模型支不支持看图 / 思考链。

    清单里写了就照清单的；没写就退回顶层开关（老配置只有 model 一项时）。
    """
    for m in api_base.cloud_models(c):
        if m["id"] == model:
            return m["vision"], m["thinking"]
    return bool(c.get("vision")), bool(c.get("thinking"))


def upstream_url(c):
    """接口地址：填到哪一层都行，不做前缀假设。

    以下写法都能用：
        https://api.deepseek.com/v1/chat/completions   完整地址 → 直接用
        https://api.deepseek.com/v1                    到版本号 → 补 /chat/completions
        https://api.deepseek.com/                      只到域名 → 补 /v1/chat/completions
        https://open.bigmodel.cn/api/paas/v4           智谱这种非 /v1 路径 → 补 /chat/completions
    """
    base = (c.get("base_url") or "").strip().rstrip("/")
    if not base:
        base = "https://api.deepseek.com/v1"
    if base.endswith("/chat/completions"):
        return base
    # 只填了域名（没有路径）→ 绝大多数服务商的版本前缀是 /v1
    from urllib.parse import urlparse
    if not urlparse(base).path.strip("/"):
        base += "/v1"
    return base + "/chat/completions"


def session_kwargs(c):
    """出公网这一跳：按配置走代理或直连，不继承系统代理。"""
    kw = {"trust_env": False, "timeout": ClientTimeout(total=None, sock_read=600)}
    px = (c.get("proxy") or "").strip()
    if px:
        kw["proxy"] = px
    return kw


class CloudError(Exception):
    """上游返回非 200，或者在准备响应前就断了。"""

    def __init__(self, status, msg, retry_after=None):
        super().__init__(msg)
        self.status = status          # 0 表示网络层异常（连不上/超时）
        self.msg = msg
        self.retry_after = retry_after


# 最近一次失败，供 /_health 与设置页展示
_STATE = {"last_error": None, "last_error_at": None, "last_status": None,
          "fallbacks": 0}


def _mark_error(status, text):
    _STATE["last_error"] = str(text)[:300]
    _STATE["last_error_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _STATE["last_status"] = status


def parse_retry_after(up, msg):
    """优先用 Retry-After 头；没有就从中文报错里抠"将在 xxx 重置"。"""
    try:
        v = up.headers.get("Retry-After")
        if v:
            return max(0.0, float(str(v).strip()))
    except Exception:
        pass
    m = re.search(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?)", str(msg))
    return None if m is None else -1        # -1 表示"知道时间但算不出秒数"


def extract_msg(text):
    try:
        j = json.loads(text)
        if isinstance(j, dict):
            err = j.get("error")
            if isinstance(err, dict):
                return err.get("message") or text
            if isinstance(err, str):
                return err
            if j.get("message"):
                return str(j["message"])
    except Exception:
        pass
    return text


# 各家 Key 的格式特征。401 时先判断"这 Key 到底是谁家的"，能直接点出填错平台
_HOST_STYLE = [
    ("bigmodel.cn", "zhipu"),
    ("deepseek.com", "sk"),
    ("siliconflow.cn", "sk"),
    ("dashscope.aliyuncs.com", "sk"),
    ("openrouter.ai", "openrouter"),
    ("groq.com", "groq"),
    ("googleapis.com", "google"),
]
_STYLE_NAME = {
    "zhipu": "智谱 BigModel（32 位 ID.16 位密钥）",
    "sk": "DeepSeek / 硅基流动 / 阿里百炼这类 sk- Key",
    "openrouter": "OpenRouter（sk-or-v1-）",
    "groq": "Groq（gsk_）",
    "google": "Google AI Studio（AIza）",
}
_HOST_TITLE = {
    "bigmodel.cn": "智谱 BigModel", "deepseek.com": "DeepSeek",
    "siliconflow.cn": "硅基流动", "dashscope.aliyuncs.com": "阿里百炼",
    "openrouter.ai": "OpenRouter", "groq.com": "Groq", "googleapis.com": "Google",
}


def key_style(key):
    k = (key or "").strip()
    if not k:
        return "empty"
    if k.startswith("sk-or-v1-"):
        return "openrouter"
    if k.startswith("gsk_"):
        return "groq"
    if k.startswith("AIza"):
        return "google"
    if k.startswith("sk-"):
        return "sk"
    if re.fullmatch(r"[0-9a-fA-F]{32}\.[A-Za-z0-9_-]{16}", k):
        return "zhipu"
    return "other"


def key_mismatch_hint(key, base):
    """Key 的格式和接口地址对不上时，直接说清是谁家的 Key 填错了地方。"""
    style = key_style(key)
    host = host_title = None
    for frag, want in _HOST_STYLE:
        if frag in (base or ""):
            host, host_title = want, _HOST_TITLE[frag]
            break
    if not host or style in ("empty", "other"):
        return None
    if style == host or (host == "sk" and style == "sk"):
        return None
    return ("这个 Key 是 **%s** 的格式，但接口地址是 **%s**，两家的 Key 不通用。"
            "请到%s控制台重新生成一个 Key，或把地址改回%s。"
            % (_STYLE_NAME.get(style, style), host_title, host_title,
               _STYLE_NAME.get(style, style).split("（")[0]))


def friendly_error(status, msg, c):
    """把厂商的原始报错翻成人话。这里是用户唯一会看到的地方，值得细写。"""
    raw = str(msg or "").strip()
    low = raw.lower()
    base = (c.get("base_url") or "").lower()
    key = (c.get("api_key") or "").strip()

    if status in (401, 403):
        hint = key_mismatch_hint(key, base)
        if not hint:
            hint = ("API Key 无效、过期，或者没有这个模型的权限。"
                    "确认 Key 是从当前接口地址对应的平台后台复制的。")
        return "🔑 云端鉴权失败（%d）：%s" % (status, hint)
    if status == 429:
        m = re.search(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?)", raw)
        when = ("，%s 后恢复" % m.group(1)) if m else ""
        return ("⏳ 云端限流了（429）%s。免费额度按分钟算，等一会儿就回来。"
                "不同模型的额度是分开算的——换下拉里的另一个云端模型多半能立刻接上；"
                "或者到设置页换个免费渠道。" % when)
    if status == 402 or "insufficient" in low or "balance" in low or "余额" in raw or "欠费" in raw:
        return "💳 云端账户余额不足（%d）：去厂商控制台充值，或换成永久免费的渠道（智谱 GLM-4-Flash / 硅基流动 9B 以下）。" % status
    if status == 400 and ("image" in low or "vision" in low or "multimodal" in low):
        return "🖼 云端拒绝了图片（400）：当前模型看不了图。到设置页取消「支持看图」，或换多模态模型。"
    if status == 404:
        return "🧭 云端 404：接口地址或模型名不对（%s）。地址要填到 /v1 结尾，模型名要和服务商列表一致。" % c.get("model")
    if status == 0:
        return "🌐 云端连不上：%s。检查网络；要出网的（OpenAI / Gemini / Groq）需在设置页填代理。" % raw[:200]
    return "云端 API %s：%s" % (status, raw[:300])


def should_fallback(status):
    """云端出错时要不要让本地模型顶上。

    以前这里只放行 429 / 5xx / 网络异常，理由是"key 错了要让人看见"。
    实际用下来不行：只要 key 填错一次（401），整个程序就一句话都发不出去，
    看起来就像"开了云端就退不回本地"。所以现在一律回落——但回落的第一句
    就把云端真实原因写出来，错误照样看得见，只是不再把人卡死。
    """
    return True


def ollama_chunk(model, content="", thinking=""):
    msg = {"role": "assistant", "content": content}
    if thinking:
        msg["thinking"] = thinking
    return json.dumps({"model": model, "message": msg}, ensure_ascii=False) + "\n"


async def _cloud_attempt(request, c, payload, streaming, state):
    """一次尝试。成功就返回已写好的响应，失败抛 CloudError。"""
    model = payload["model"]
    async with ClientSession(**session_kwargs(c)) as s:
        async with s.post(upstream_url(c), json=payload,
                          headers=cloud_headers(c)) as up:
            if up.status != 200:
                text = await up.text()
                raise CloudError(up.status, extract_msg(text),
                                 parse_retry_after(up, text))

            if not streaming:
                data = await up.json()
                msg = {}
                try:
                    msg = data["choices"][0]["message"] or {}
                except Exception:
                    msg = {}
                text = msg.get("content") or ""
                # 思考型模型（glm-4.7-flash 等）的思维链在 reasoning_content 里，
                # 不映射出来的话，插件判心情/选表情包拿到的是空字符串，
                # 而且非流式响应里用户也看不到思考过程。流式分支本来就有映射。
                thinking = (msg.get("reasoning_content")
                            or msg.get("reasoning")
                            or msg.get("thinking") or "")
                out = {"role": "assistant", "content": text}
                if thinking:
                    out["thinking"] = thinking
                # 云端 usage → Ollama 风格 token 计数字段，供前端"上下文用量"面板显示真实占用。
                # OpenAI 兼容端点在非流式响应最后带 usage.prompt_tokens / completion_tokens。
                resp = {"model": model, "message": out,
                        "done": True, "done_reason": "stop"}
                try:
                    u = data.get("usage") or {}
                    if isinstance(u.get("prompt_tokens"), int):
                        resp["prompt_eval_count"] = u["prompt_tokens"]
                    if isinstance(u.get("completion_tokens"), int):
                        resp["eval_count"] = u["completion_tokens"]
                except Exception:
                    pass
                return web.json_response(resp, headers=CORS)

            # ---- 流式：上游 SSE → 下游 Ollama NDJSON ----
            resp = web.StreamResponse(headers=dict(
                CORS, **{"Content-Type": "application/x-ndjson",
                         "Cache-Control": "no-cache"}))
            await resp.prepare(request)
            state["prepared"] = True

            buf = ""
            usage = {}          # 收集流里出现的 usage（部分厂商最后一块带）
            async for raw in up.content.iter_any():
                buf += raw.decode("utf-8", errors="replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        buf = ""
                        break
                    try:
                        j = json.loads(data)
                    except Exception:
                        continue
                    # 有些厂商把 usage 放在没有 choices 的帧里，单独收下
                    try:
                        u = j.get("usage")
                        if isinstance(u, dict) and u:
                            usage = u
                    except Exception:
                        pass
                    try:
                        delta = j["choices"][0]["delta"]
                    except Exception:
                        continue
                    content = delta.get("content") or ""
                    # 各家推理字段不统一，能拿到就塞进 thinking
                    thinking = (delta.get("reasoning_content")
                                or delta.get("reasoning")
                                or delta.get("thinking") or "")
                    if content or thinking:
                        await resp.write(ollama_chunk(
                            model, content, thinking).encode("utf-8"))

            final = {"model": model, "done": True, "done_reason": "stop"}
            try:
                if isinstance(usage.get("prompt_tokens"), int):
                    final["prompt_eval_count"] = usage["prompt_tokens"]
                if isinstance(usage.get("completion_tokens"), int):
                    final["eval_count"] = usage["completion_tokens"]
            except Exception:
                pass
            await resp.write(json.dumps(final, ensure_ascii=False).encode("utf-8"))
            await resp.write_eof()
            return resp


async def _local_models():
    try:
        async with ClientSession(trust_env=False,
                                 timeout=ClientTimeout(total=3)) as s:
            async with s.get(LOCAL + "/api/tags") as r:
                if r.status != 200:
                    return []
                d = await r.json()
        return [m.get("name") or m.get("model") for m in (d.get("models") or [])]
    except Exception:
        return []


LOCAL_PREFER = ["qwen2.5vl:7b", "qwen3-vl:4b-instruct", "deepseek-r1:8b", "qwen3.6:35b"]


async def _pick_local_model():
    """云端模型名在 Ollama 上不存在，回落时必须换成真的有的那个。"""
    have = await _local_models()
    if not have:
        return None
    for p in LOCAL_PREFER:
        if p in have:
            return p
    return have[0]


def _dbg_fail(raw_body, streaming, err):
    """排查用：把失败请求的形状记下来（不落 key、不落正文全文）。"""
    try:
        b = json.loads(raw_body or b"{}")
    except Exception:
        b = {}
    msgs = b.get("messages") or []
    brief = []
    for m in msgs[:9]:
        c = m.get("content")
        if isinstance(c, list):
            c = " ".join(str(p.get("text", "")) for p in c if isinstance(p, dict))
        s = str(c or "")
        brief.append("%s:%d:%r" % (m.get("role"), len(s), s[:40]))
    with open(r"D:\大肥鱼缓存\_proxy_dbg.log", "a", encoding="utf-8") as f:
        f.write("%s status=%s stream=%s msgs=%d model=%r\n  %s\n  err=%s\n"
                % (time.strftime("%H:%M:%S"), err.status, streaming, len(msgs),
                   b.get("model"), " | ".join(brief), str(err.msg)[:200]))


async def _fail(request, raw_body, c, streaming, err):
    """云端失败后的收尾：能回落本地就回落，否则给出人话报错。"""
    _dbg_fail(raw_body, streaming, err)
    _mark_error(err.status, err.msg)

    fb = c.get("fallback_local", True)
    if fb and should_fallback(err.status):
        local_model = await _pick_local_model()
        if local_model:
            # 把请求里的模型名换成本机真有的，否则 Ollama 直接 404
            try:
                b = json.loads(raw_body or b"{}")
                b["model"] = local_model
                raw_body = json.dumps(b, ensure_ascii=False).encode("utf-8")
            except Exception:
                pass
            note = None
            if streaming:
                if err.status == 429:
                    head = "⚠️ 云端限流了"
                elif err.status == 0:
                    head = "⚠️ 云端连不上"
                elif err.status in (401, 403):
                    head = "⚠️ 云端鉴权失败（%d）" % err.status
                elif err.status == 402:
                    head = "⚠️ 云端余额不足（%d）" % err.status
                elif err.status == 400:
                    head = "⚠️ 云端拒绝了这次请求（%d）" % err.status
                else:
                    head = "⚠️ 云端服务异常（%d）" % err.status
                # 顺手把云端原话带出来：用户照着改一次就通了，不用再猜
                why = friendly_error(err.status, err.msg, c)
                note = "%s，下面这句由本地模型 **%s** 代答。\n%s\n\n" % (
                    head, local_model, why)
            _STATE["fallbacks"] += 1
            return await passthrough(request, "/api/chat", raw_body, note=note)

    text = friendly_error(err.status, err.msg, c)
    name = c.get("model") or "cloud"
    if streaming:
        resp = web.StreamResponse(headers=dict(
            CORS, **{"Content-Type": "application/x-ndjson", "Cache-Control": "no-cache"}))
        await resp.prepare(request)
        await resp.write(ollama_chunk(name, text).encode("utf-8"))
        await resp.write(json.dumps({"model": name, "done": True,
                                     "done_reason": "error"}).encode("utf-8"))
        await resp.write_eof()
        return resp
    return web.json_response({"error": text}, status=502, headers=CORS)


async def cloud_chat(request, body, c, raw_body):
    payload = to_openai_payload(body, c)
    streaming = bool(body.get("stream"))
    state = {"prepared": False}

    # 429 先自己重试两次（尊重 Retry-After），很多限流是瞬时的，等几秒就好
    for attempt in range(3):
        try:
            return await _cloud_attempt(request, c, payload, streaming, state)
        except asyncio.CancelledError:
            raise
        except CloudError as e:
            # "20:06 才重置"这种额度耗尽，等几秒没用，直接交给回落逻辑
            quota_over = (e.status == 429 and e.retry_after == -1)
            retriable = (e.status == 429 and not quota_over) or e.status >= 500
            if retriable and attempt < 2:
                wait = 1.5 if attempt == 0 else 3.0
                if isinstance(e.retry_after, float) and 0 < e.retry_after <= 8:
                    wait = e.retry_after
                await asyncio.sleep(wait)
                continue
            return await _fail(request, raw_body, c, streaming, e)
        except Exception as e:
            # 已经开始往客户端写了（流到一半断线），没法再改口
            if state["prepared"]:
                raise
            # 网络层／客户端异常：aiohttp 的各种 ClientError、超时等
            return await _fail(request, raw_body, c, streaming, CloudError(0, str(e)))


# ---------------------------------------------------------------- 路由
async def handle_chat(request):
    body_bytes = await request.read()
    c = cfg()
    if not c.get("enabled"):
        return await passthrough(request, "/api/chat", body_bytes)
    try:
        body = json.loads(body_bytes or b"{}")
    except Exception:
        return web.json_response({"error": "请求体不是 JSON"}, status=400, headers=CORS)
    return await cloud_chat(request, body, c, body_bytes)


async def handle_tags(request):
    c = cfg()
    if not c.get("enabled"):
        return await passthrough_get(request, "/api/tags")
    # 云端模式下把清单里的模型全列出来，前端下拉才能切换。
    # 以前只返回一个 config.model，用户想换个免费模型就只能改配置再重启。
    models = api_base.cloud_models(c)
    out = []
    for m in models:
        out.append({
            "name": m["id"], "model": m["id"], "modified_at": "", "size": 0,
            "digest": "cloud-model",
            "details": {"family": "cloud", "parameter_size": "cloud",
                        "quantization_level": "N/A"},
            # 自定义字段，前端据此知道这个模型看得了图 / 有思维链 / 上下文上限
            "cloud_vision": m["vision"], "cloud_thinking": m["thinking"],
            "context": m.get("context") or 32768,
        })
    return web.json_response({"models": out}, headers=CORS)


async def handle_show(request):
    c = cfg()
    body_bytes = await request.read()
    if not c.get("enabled"):
        return await passthrough(request, "/api/show", body_bytes)
    try:
        want = str((json.loads(body_bytes or b"{}") or {}).get("model") or "").strip()
    except Exception:
        want = ""
    # 按被问的那个模型给能力，不能全用顶层开关——
    # 清单里 4.7-Flash 看不了图、4.6V-Flash 能看图，答错了前端会传错参数。
    vision, thinking = model_caps_for(want, c)
    caps = ["completion"]
    if vision:
        caps.append("vision")
    if thinking:
        caps.append("thinking")
    return web.json_response({
        "capabilities": caps,
        # 主站后端的 model_caps() 读的是顶层 thinking / vision 字段，
        # 不是 capabilities 数组。本地 Ollama 两个都给，代理以前只给数组，
        # 结果云端模型一律被算成"不支持思考、不支持看图"——
        # 看图被拦、深度思考开关点不动。两个都给上，老代码也能读。
        "thinking": bool(thinking),
        "vision": bool(vision),
        "details": {"family": "cloud", "parameter_size": "cloud"},
        "modelinfo": {},
    }, headers=CORS)


async def handle_ps(request):
    c = cfg()
    if not c.get("enabled"):
        return await passthrough_get(request, "/api/ps")
    # 云端没有"已加载模型"的概念。以前返回空列表，前端 preloadModel()
    # 的 /api/ps 轮询就永远查不到目标模型，进度条卡满 30 秒才自己消失，
    # 看着就像"切模型没反应"。把云端清单报上去，轮询立刻就通过。
    return web.json_response({
        "models": [{"name": m, "model": m, "size": 0, "digest": "cloud-model"}
                   for m in api_base.cloud_model_ids(c)],
    }, headers=CORS)


async def handle_any_post(request):
    """其它 Ollama 接口（generate/embed 等）一律透传，避免插件用到时 404。"""
    return await passthrough(request, request.path, await request.read())


async def handle_health(request):
    c = cfg()
    return web.json_response({
        "ok": True,
        "mode": "cloud" if c.get("enabled") else "local",
        "upstream": upstream_url(c) if c.get("enabled") else LOCAL,
        "model": c.get("model") if c.get("enabled") else "(local ollama)",
        # 本地模式下不列云端模型：配置里的清单还在，但此刻根本没走云端，
        # 报出来会让人以为"云端还开着"。
        "models": api_base.cloud_model_ids(c) if c.get("enabled") else [],
        "api_key_set": bool((c.get("api_key") or "").strip()),
        "fallback_local": bool(c.get("fallback_local", True)),
        "last_error": _STATE["last_error"],
        "last_error_at": _STATE["last_error_at"],
        "fallbacks": _STATE["fallbacks"],
    }, headers=CORS)


async def handle_models_list(request):
    """拉取上游厂商的模型清单（智谱 /v4/models、OpenAI /v1/models 都是这个形状）。

    设置页点一下就能看到"这个 Key 到底能用哪些模型"，不用去官网翻文档。
    请求带自己的 Authorization，不依赖配置里的 Key，所以
    "填了 Key 但还没保存"也能试。
    """
    key = (request.query.get("key") or "").strip()
    base = (request.query.get("base") or "").strip()
    if not base:
        base = (cfg().get("base_url") or "").strip()
    if not key:
        key = (cfg().get("api_key") or "").strip()

    # 模型清单端点 = 补齐后的 chat/completions 地址去掉后半截
    root = base.rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if root.endswith(suffix):
            root = root[: -len(suffix)]
            break
    if not root:
        return web.json_response({"ok": False, "msg": "没填接口地址"}, headers=CORS)
    if not root.endswith("/v1") and not root.endswith("/v4") and "/v" not in root.rsplit("/", 1)[-1]:
        root += "/v1"
    url = root + "/models"

    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    try:
        async with ClientSession(**session_kwargs(cfg())) as s:
            async with s.get(url, headers=headers) as up:
                raw = await up.text()
                if up.status != 200:
                    return web.json_response(
                        {"ok": False, "msg": "厂商返回 %d：%s"
                         % (up.status, extract_msg(raw)[:200]), "url": url},
                        headers=CORS)
                data = json.loads(raw)
    except Exception as e:
        return web.json_response({"ok": False, "msg": str(e)[:200], "url": url},
                                 headers=CORS)

    items = []
    for it in (data.get("data") or []):
        if isinstance(it, dict):
            mid = str(it.get("id") or "").strip()
            if mid:
                items.append(mid)
    # 厂商清单常常不含免费模型（智谱实测一个都没有），并上内置免费目录
    free = api_base.free_models_for(base)
    have = set(items)
    for f in free:
        if f["id"] not in have:
            items.append(f["id"])
    items = sorted(set(items))
    return web.json_response({"ok": True, "url": url, "models": items,
                              "count": len(items), "free": free}, headers=CORS)


async def handle_config_get(request):
    c = cfg()
    # 不把完整 key 回显给页面，只给长度提示
    return web.json_response(dict(c, api_key=("*" * 8) if c.get("api_key") else ""),
                             headers=CORS)


async def handle_config_post(request):
    try:
        incoming = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "JSON 解析失败"}, headers=CORS)
    # 前端回传 "********" 表示不修改已有 key
    if incoming.get("api_key") == "*" * 8:
        incoming.pop("api_key")
    c = api_base.save_config(incoming)
    return web.json_response({"ok": True, "enabled": c["enabled"]}, headers=CORS)


def make_app():
    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_route("OPTIONS", "/{tail:.*}", options)
    app.router.add_post("/api/chat", handle_chat)
    app.router.add_post("/api/show", handle_show)
    app.router.add_get("/api/tags", handle_tags)
    app.router.add_get("/api/ps", handle_ps)
    app.router.add_get("/_health", handle_health)
    app.router.add_get("/_models", handle_models_list)
    app.router.add_get("/_config", handle_config_get)
    app.router.add_post("/_config", handle_config_post)
    app.router.add_route("POST", "/{tail:.*}", handle_any_post)
    app.router.add_route("GET", "/{tail:.*}", passthrough_get_any)
    return app


async def passthrough_get_any(request):
    return await passthrough_get(request, request.path)


if __name__ == "__main__":
    print("大肥鱼云端兼容代理  http://127.0.0.1:%d" % PORT)
    print("当前模式：%s" % ("云端 " + cfg().get("model", "") if cfg().get("enabled") else "本地透传 → " + LOCAL))
    web.run_app(make_app(), host="127.0.0.1", port=PORT, access_log=None)
