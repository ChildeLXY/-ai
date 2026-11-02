# -*- coding: utf-8 -*-
"""
深度思考开关
============

一个"模式"型插件：不做具体功能，只决定大肥鱼这一轮**怎么想**。

- 开着：先拆问题、逐条核对、给能跑的结论，准确性排在人设前面；
  模型本身支持思考链（qwen3 / deepseek-r1 / qwq 等）时，还会把 think 打开，
  思考内容会显示在气泡里的「💭 思考过程」。
- 关着：不分析、不列步骤、不自我检查，人格和情绪优先，回短句。

状态存在设置里（键名 deep_think），前端每轮对话前调 deep_think_context()
拿提示词和采样参数，所以切换是**即时生效**的，不用重启。
"""

PLUGIN_ID = "deep_think"
PLUGIN_NAME = "深度思考"
PLUGIN_ICON = "🔬"
PLUGIN_VERSION = "1.0.0"

SETTING_KEY = "deep_think"

_ctx = None


# ---------------------------------------------------------------- 提示词
_ON_PROMPT = (
    "【本轮：深度思考 已开启】\n"
    "1. 先在脑子里把问题拆开：用户到底在问什么、需要哪些依据、有没有反例、结论是什么。想清楚再开口，不要边说边想。\n"
    "2. 涉及代码、命令、数字、事实时逐条核对：能跑通的才给；拿不准就直说「我不确定」，宁可少说也不编。\n"
    "3. 先给结论或可直接用的东西，再给理由；该长就长，不要为了显得可爱而牺牲正确性。\n"
    "4. 语气仍然是大肥鱼，但准确性排在可爱前面；拿不准的地方标出来，别硬撑。\n"
    "5. 分析过程只留在心里，正文里不要出现「第一步/让我想想/综上所述」这类旁白。\n"
    "6. 想清楚就停：想到能回答了的那个点就直接开口，不要复述上面的规则、不要一遍遍自查、不要在心里把回复草稿改三遍。"
    "思考只需要几步，不能变成十几段的独白。"
)

_OFF_PROMPT = (
    "【本轮：深度思考 已关闭】\n"
    "1. 不要长篇分析、不要列步骤、不要写提纲、不要反复自我检查。脑子里第一反应是什么就说什么。\n"
    "2. 你的人格、情绪、口癖、和你家小鱼的关系，优先于「答得全面」。宁可回一句有性格的短话，也不要写一段正确的废话。\n"
    "3. 就算被问技术问题，也先用最直白的一两句话说结论；用户追问细节再展开。\n"
    "4. 默认简短。只有在用户明确说「详细讲讲」时才铺开。"
)


def _on():
    """当前开关状态。没设过就是关（默认像个正常人聊天）。"""
    try:
        v = _ctx.setting(SETTING_KEY, "0")
    except Exception:
        return False
    return str(v).strip().lower() in ("1", "true", "on", "yes")


# ---------------------------------------------------------------- 对外接口
def setup(ctx):
    global _ctx
    _ctx = ctx
    ctx.log(PLUGIN_ID, "深度思考开关就绪，当前：%s" % ("开" if _on() else "关"))


def deep_think_state():
    """给前端和插件中心看的状态。"""
    on = _on()
    return {
        "ok": True,
        "on": on,
        "label": "开" if on else "关",
        "note": ("认真拆问题、逐条核对，准确性优先" if on
                 else "不做分析，人格和情绪优先，回短句"),
    }


def deep_think_set(on):
    """切换开关。前端点一下按钮就调这里，立即生效。"""
    if not _ctx:
        return {"ok": False, "error": "没有上下文"}
    want = bool(on)
    if isinstance(on, str):
        want = on.strip().lower() in ("1", "true", "on", "yes")
    _ctx.save_setting(SETTING_KEY, "1" if want else "0")
    try:
        _ctx.log(PLUGIN_ID, "切到：%s" % ("开" if want else "关"))
        _ctx.emit("deep_think.changed", {"on": want})
    except Exception:
        pass
    return deep_think_state()


def deep_think_context():
    """每轮对话前调用：返回这一轮该注入的提示词和采样参数。

    返回 {ok, on, prompt, temperature, num_predict, think}
    think=True 只是"可以打开思考链"，到底开不开由前端看模型支不支持。
    """
    if not _ctx:
        return {"ok": False, "error": "没有上下文"}
    on = _on()
    if on:
        return {"ok": True, "on": True, "prompt": _ON_PROMPT,
                "temperature": 0.3, "num_predict": 2048, "think": True}
    # 关着的时候不限制长度（num_predict=0 表示不传），只把温度调活泼一点：
    # 硬截断会切在半句话上，长度交给提示词管更自然
    return {"ok": True, "on": False, "prompt": _OFF_PROMPT,
            "temperature": 0.85, "num_predict": 0, "think": False}


def apis():
    return [deep_think_state, deep_think_set, deep_think_context]
