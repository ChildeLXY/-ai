# 大肥鱼AI · 第三方云端模型接入说明

> 落地日期：2026-11-12　方案：**本地兼容代理**（不重构现有代码）
> 默认状态：**关闭**，行为和以前完全一样，直连本机 Ollama，不依赖任何新进程。

---

## 一、怎么用

1. 打开大肥鱼 → 右上角 ⚙ **设置** → **「云端模型 ☁」** 标签
2. 填接口地址、API Key、模型名
3. 按厂商能力勾「支持看图」「支持思考链」
4. 点 **「保存并应用」**（会自动拉起兼容代理），再点 **「测试连接」**
5. 侧边栏会变成「☁ 云端模型已开启 · 对话会离开本机」，模型下拉框变成云端模型

关掉同一个开关再保存，立刻回到纯本地，无需重启。

### 接口地址：填到哪一层都行

不要求你自己补 `/v1` 或 `/chat/completions`，直接粘贴文档上给的完整地址即可。
`upstream_url()` 会按下面规则归一化：

| 你填的 | 实际请求 |
|---|---|
| `https://api.deepseek.com/v1/chat/completions` | 原样使用 |
| `https://api.deepseek.com/v1` | 补 `/chat/completions` |
| `https://api.deepseek.com` | 补 `/v1/chat/completions` |
| `https://open.bigmodel.cn/api/paas/v4` | 补 `/chat/completions`（智谱不是 /v1）|

保存后会把这行「实际请求：…」显示出来，方便核对。

### Key 必须和地址是同一家

换渠道时旧 Key 会被自动清空——各家的 Key 不通用，这是最常见的 401 来源。
如果只是改了地址没换 Key，401 时会直接点名：

> 这个 Key 是 **智谱 BigModel（32 位 ID.16 位密钥）** 的格式，但接口地址是 **硅基流动**，两家的 Key 不通用。

各家 Key 长相：DeepSeek / 硅基流动 / 阿里百炼 `sk-` 开头；智谱 `32位ID.16位密钥`；
OpenRouter `sk-or-v1-`；Groq `gsk_`；Google `AIza`。

## 二、支持哪些服务

任何 **OpenAI-compatible** 的 `/v1/chat/completions` 都能用，一次适配全覆盖：

| 服务 | `base_url` 示例 | 模型名示例 |
|---|---|---|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` / `deepseek-reasoner` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` / `qwen-vl-max` |
| Moonshot | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| 本地 vLLM / LM Studio | `http://127.0.0.1:8000/v1` | 按实际填 |

访问 OpenAI 这类需要梯子时，把「出网代理」填 `http://127.0.0.1:7897`；国内 API 留空直连。

### 免费渠道（设置页里一键预设，不用手填）

「云端模型 ☁」里有个 **「快速选一个免费渠道」** 下拉，选中会自动填好接口地址和模型名，
你只需要填 API Key。这些都是**正规的 OpenAI-compatible 端点**，走厂商公开的免费额度：

| 预设 | 免费额度 | 出网代理 |
|---|---|---|
| DeepSeek 官方 | 新用户约 500 万 Token（30 天），之后按量极便宜 | 不用 |
| 智谱 GLM-4-Flash | **永久免费** | 不用 |
| 硅基流动 Qwen3-8B / R1 蒸馏 7B | 9B 以下**永久免费** + 注册送 2000 万 Token | 不用 |
| 阿里云百炼 qwen-plus | 每模型 100 万 Token | 不用 |
| OpenRouter `:free` | 50 次/天 | 自动填 7897 |
| Groq Llama 3.3 70B | 30 RPM | 自动填 7897 |
| Google Gemini Flash | 免费层，支持看图 | 自动填 7897 |

> 免费政策变动频繁，额度以各平台官网为准。免费层普遍限速（20~30 RPM），
> 撞 429 很正常；真要稳定还得靠本地 Ollama 兜底。
>
> 模型名可能随平台改名（尤其智谱 Flash 系列），如果「测试连接」报模型不存在，
> 去平台控制台抄当前模型名填进「模型名」即可，其它不用动。

## 三、架构

```
大肥鱼AI.html / 大肥鱼AI.py / dfy_core.py / 插件 / web_client.py
        │  统一经 api_base.url() 取地址
        ├─ 云端关 → http://127.0.0.1:11434  （真 Ollama，零额外依赖）
        └─ 云端开 → http://127.0.0.1:11435  （cloud_proxy.py）
                          │
                          ├─ Ollama 协议 ⇄ OpenAI 协议翻译
                          └─ 出公网 → /v1/chat/completions
```

**关键点：现有前端、插件、主程序都没改协议**，只把原来写死在 12 处的 `11434` 换成 `api_base.url()`。
关掉云端时直连 11434，代理挂了也不影响使用。

## 四、新增/改动文件

| 文件 | 说明 |
|---|---|
| `api_base.py` | **新增**：地址与配置的唯一来源 |
| `cloud_proxy.py` | **新增**：aiohttp 兼容代理，11435 |
| `cloud_config.json` | **新增**：配置，默认 `enabled:false` |
| `启动云端代理.bat` | **新增**：手动启动入口（一般用不到） |
| `大肥鱼AI.py` | 加 `get_api_base / cloud_config_get / cloud_config_save / cloud_test` 并注册到 JS；12 处 URL 收敛 |
| `大肥鱼AI.html` | `const API`→`let API`；设置页新增云端标签；侧栏提示随模式切换 |
| `dfy_core.py` / `pet_mutter_watcher.py` / `web_client.py` | URL 收敛到 `api_base`（web_client 改为每次请求动态取，运行中切换即时生效） |
| `_backup_cloud_api/` | **改动前的完整备份**，可随时还原 |

## 五、六个协议坑是怎么处理的

| 坑 | 处理 |
|---|---|
| Ollama `num_ctx` / `keep_alive` / `think` / `options` 云端不认 | `to_openai_payload()` 消化：`num_predict→max_tokens`、`temperature→temperature`，其余丢弃 |
| 流式格式不同 | 上游 SSE `data: {...}` + `[DONE]` → 下游逐行 NDJSON `{"message":{"content":...}}`，末尾 `{"done":true}`；实测 7 个分片 + done 正确 |
| 图片格式不同 | Ollama `images:[base64]` → OpenAI `content:[{type:"image_url",...}]`；实测 `shape=parts` |
| 能力探测 `/api/show` 云端没有 | 按配置里的「看图 / 思考链」勾选伪造 `capabilities`，否则深度思考按钮会判错 |
| 预热 / 卸载 / `/api/ps` 无意义 | 云端模式返回空模型列表，前端跳过预热；`/api/tags` 返回单个云端模型 |
| **代理方向反了** | 本机请求一律绕系统代理（`api_base.opener()`）；只有代理出公网那一跳按 `proxy` 配置走梯子 |

另外：`reasoning_content` / `reasoning` / `thinking` 三种推理字段都会映射成 Ollama 的 `message.thinking`，深度思考能显示。
上游报错时不会把 Key 或完整请求体回显，只给截断后的错误信息。

## 六、限流（429）与自动回落

免费渠道最常见的故障不是连不上，是 **429 频率限制**——智谱 GLM-4-Flash 免费版并发很低，连着问两句就会撞上，报错原文是
「您的使用量已超出频率限制，将在 2026-11-12 20:06:52 重置」。这类错误直接甩给用户既看不懂也用不了。

现在的处理顺序：

1. **先自己重试**：429 / 5xx 自动重试最多两次（等 1.5s、3s；有 `Retry-After` 头就按它等）。
   但如果报错里带「将在 xx 重置」这种**额度耗尽**字样，等待几秒没用，直接跳到下一步
2. **回落本地**（默认开，设置页可关）：把这次请求转给本机 Ollama，对话不断。
   回落的回复**开头会写明**「⚠️ 云端限流了，这句由**本地模型 qwen2.5vl:7b** 接着答」，
   不会让你误以为还是云端答的
3. **回落也失败**才报错，且是中文人话

回落时有个必须做的细节：云端模式发来的 `model` 是 `glm-4-flash`，Ollama 上没有这个模型，
直接转过去会 `model not found`。所以回落前会用 `/api/tags` 查本机实际模型，
按 `qwen2.5vl:7b → qwen3-vl:4b-instruct → deepseek-r1:8b → qwen3.6:35b` 的顺序挑一个换上。

**401 / 402 / 403 / 400 也回落**（2026-11-12 改）。原先这几类不回退，理由是"Key 错、欠费、
参数不支持，回退会把真正原因藏起来"。实际用下来这是个错误决定：Key 填错一次，整个程序就
**一句话都发不出去**，用户看到的是"开了云端就退不回本地"。现在照样回落，但把云端原话写在
回答最前面，错误一样看得见，只是不再把人卡死：

```
⚠️ 云端鉴权失败（401），下面这句由本地模型 qwen3-vl:4b-instruct 代答。
🔑 云端鉴权失败（401）：这个 Key 是 **智谱 BigModel** 的格式，但接口地址是 **DeepSeek**，两家的 Key 不通用。
```

常见报错的翻译：

| 状态 | 提示 |
|---|---|
| 401（Key 是智谱 49 位格式，地址却是 DeepSeek）| 「这个 Key 是智谱格式，但接口地址是 DeepSeek，两家的 Key 不通用」|
| 429 | 「云端限流了（429），2026-11-12 20:06:52 后恢复……也可以换个免费渠道」|
| 402 / Insufficient Balance | 「云端余额不足，去充值或换永久免费的渠道」|
| 404 | 「接口地址或模型名不对，地址要填到 /v1」|
| 400 + image | 「当前模型看不了图，到设置页取消「支持看图」」|

最近一次失败和回落次数可从 `GET /_health` 看到（`last_error` / `fallbacks`），排查时不用翻日志。

## 七、隐私与默认行为

- **默认关闭**。开启后人格卡、长期记忆、聊天内容会发给第三方，与 README「数据不出本机」的定位相反，设置页里有红字提示
- API Key 只写本机 `cloud_config.json`，页面只显示 `********`，不写日志、不进聊天记录
- 切换无需重启；关掉即刻恢复纯本地
- 手机端（llama.cpp 直连）**不走这套**，仍为纯本地

## 八、验证记录（2026-11-12）

| 项 | 结果 |
|---|---|
| Python 6 个文件 `py_compile` | 全通过 |
| 前端 2 个 script 块 `node --check` | 通过 |
| 本地模式 `/api/tags` 透传 | 7 个模型原样返回 |
| 本地模式 `/api/show` 透传 | 通过 |
| 云端模式 `/_health` `/api/tags` `/api/show` | 模式、模型名、伪造 caps 正确 |
| 云端非流式 | Ollama 专有字段全部消化（`leaked=none`），返回标准 Ollama 响应 |
| 云端流式 | SSE→NDJSON 逐块 + `done:true` |
| 图片 | `shape=parts` |
| 恢复默认 | `enabled=false`，健康接口回到 `mode:local` |
| **真实 DeepSeek API 复测（11-12 10:45）** | 用户填入真实 Key 后，经 11435 代理实测：非流式返回标准 Ollama 响应、流式 SSE→NDJSON 逐块 + `done:true`，**均通过** |
| 429 回落（11-12 11:05，假上游） | 上游固定返回「将在 20:06:52 重置」→ 判定额度耗尽、跳过重试 → 直接回落本地，首块即提示，后续由 `qwen2.5vl:7b` 正常输出；`/_health` 的 `fallbacks` 由 0 变 1 |
| 401 策略（11-12 11:25 改） | `should_fallback(401)=True`：先回落本地，回复开头带上云端原话和「Key 与地址不匹配」提示 |

> 云端链路最初用本地假上游（`_mock_openai.py`）验证，后来已用真实 API Key 复测通过。
> 兼容性调整：去掉 `stream_options`（Groq 等端点不认会 400）；
> `deepseek-reasoner` / `o1` / `o3` 自动不传 `temperature`（推理模型传了会 400）。

## 九、还原

把 `_backup_cloud_api/` 里对应的 `.bak` 拷回去覆盖，删掉 `api_base.py` /
`cloud_proxy.py` / `cloud_config.json` / `启动云端代理.bat` 即可，项目回到纯本地状态。

## 十、「开了云端就退不回本地」是怎么来的（11-12 修复）

用户实测反馈：点关闭、切回本地，顶部模型下拉还是「云端」，发消息还是报 401。
查下来是四个独立的坑叠在一起：

| # | 坑 | 为什么会这样 | 现在怎么处理 |
|---|---|---|---|
| 1 | 那个「当前：云端已开启（点此关闭）」**不是开关** | 它只改按钮文字，真正写盘要再点一次「保存并应用」。点了关闭却没保存，配置还是 `enabled:true` | 开关**即点即生效**，直接写盘，不再依赖第二个按钮 |
| 2 | 保存时无条件校验接口地址 | 想退出时顺手把地址清空/填歪 → 校验不过直接 return，连「关闭」都存不进去，等于被锁死 | 关闭状态下**跳过所有校验**，地址、Key 都可以是空的 |
| 3 | 401 不回落 | `should_fallback` 只放行 429/5xx/断网，"Key 错了要让人看见"——结果一句都发不出去 | 一律回落，云端原因写在回复开头 |
| 4 | 下拉仍挂云端模型名 | `MODEL` 变量和 `LOCAL_MODELS` 快照都可能被云端项污染，`base` 回到 11434 了，却还拿 `deepseek-chat` 去问 Ollama → 404 | 本地清单改成唯一来源 `LOCAL_PRESET`；发消息前再校验一次模型名，不在列表就自动纠正 |

另外补了两个会让人误以为「卡死在云端」的场景：

- **开机自启时代理没起来**：以前只在"保存配置"那一刻拉代理，重启电脑后 `enabled:true` 但 11435 空着，
  所有请求 502。现在主程序启动时补拉一次，并且 `api_base.base()` 在 11435 不通时**自动退回 11434**——
  代理挂了也还能说话，设置页会显示「代理未启动，已自动改用本地」
- **设置页新增「↩ 立即回到纯本地」**：不校验、不测试、不保存，点一下当场生效，云端地址和 Key 都留着

验证：`GET http://127.0.0.1:11435/_health` 的 `mode` 应为 `local`；关闭云端后前端顶部下拉应恢复
`灵动 / 轻快 / 思考 / 深度ProMax` 四个本地模型。

---

## 十一、「云端能聊天，但不发表情包了」是怎么来的（11-12 二次修复）

症状：开了云端以后正文一切正常，就是**一句都不带表情包**，判心情/记记忆这类后台小调用也一起哑掉。
查下来是两条独立的链路问题，都出在"云端模式"这个开关之外：

| # | 坑 | 为什么会这样 | 现在怎么处理 |
|---|---|---|---|
| 1 | **空 content 被智谱 400** | 智谱的 `/chat/completions` 只要收到 `content:""` 就直接回 400「未正常接收到prompt参数」。用户只发表情包不写字、或某条消息只剩图片占位时，content 就是空串 → 整轮被拒 | `to_openai_messages()` 给空文本补一句中性占位「（这一条没有文字）」，不再让请求因为"没字"被拒 |
| 2 | **代理中途挂了没人管** | `_ensure_proxy()` 只在"保存配置"和程序启动那一刻调用。代理之后再崩（被杀、开机抢跑、内存不足），Python 侧所有调用（选表情包 / 判心情 / 提炼记忆）都悄悄落到 11434，而那里根本没有 `glm-4-flash` 这个模型 → 404 → 静默失败。前端 `API` 变量还指着 11435，所以**正文照常、只有插件全哑** | `api_base.base()` 增加自愈：云端开着但 11435 不在听 → 自己 `Popen` 一个 `cloud_proxy.py` 再重试（20 秒节流一次，最多等 1.2 秒）；起不来才退回 11434 |
| 3 | 选图失败完全无痕 | `sticker_pick` 失败只返回 `{ok:false}`，前端 `catch` 吞掉，用户只看到"不发表情包" | 失败原因写进 `_plugins.log`（带模型名和模型原话）；代理侧又对所有非 200 记 `D:\大肥鱼缓存\_proxy_dbg.log`（状态码 + 每条消息的 role/长度/开头），下次能直接看现场 |

验证方式：

```bash
# 1. 后端能自愈：杀掉 11435 后再调一次 base()，应自己把代理拉起来
python -c "import api_base,socket;s=socket.socket();print(s.connect_ex(('127.0.0.1',11435)));print(api_base.base())"

# 2. 空 content 不再 400：应该看到云端正常回答，而不是 400 未正常接收到prompt参数
curl -s --noproxy '*' http://127.0.0.1:11435/api/chat -H "Content-Type: application/json" \
  -d '{"model":"glm-4-flash","messages":[{"role":"user","content":""}],"stream":false}'
```

> 顺带一句：`_proxy_dbg.log` 只记消息形状（角色 / 字数 / 前 40 字），**不记 Key、不记全文**。

---

## 附：为什么没有做「接入 DeepSeek 网页版免费对话」

用户提过这个需求，**评估后没有实现**，理由是它和接官方 API 是两回事：

网页版（`chat.deepseek.com`）没有公开接口，要靠逆向私有 API：

1. 从浏览器 `localStorage` 抠出 **`userToken`**（Cookie 无效）当认证 —— 等于把账号登录凭证交给脚本
2. 每次提问前解一道 `DeepSeekHashV1` 工作量证明（WASM / PoW），领题 → 求解 → 塞 `x-ds-pow-response` 头
3. 先建会话、串 `parent_message_id` 才能维持多轮
4. 返回的是 `{"p":...,"v":...}` 私有 SSE 结构，不是 OpenAI 格式，要单独写解析器
5. 为了「看起来像真人」，还得在真实浏览器上下文求解、随机请求间隔、多账号轮转躲 429

三个硬代价：

- **可能违反服务条款，账号被限流或封禁**
- **接口随时变**，这类项目历史上反复失效，无 SLA、无公告
- 需要长期维护 PoW 与签名逆向，成本远超省下的调用费

做这类逆向的项目自己的 README 也写「稳定性优先请用官方 API」「不适合生产环境」。
而上面那些**正规免费额度**同样是零成本，且是公开契约、有配额语义、不会封号——
既然本项目已经做了 OpenAI-compatible 兼容层，走正路没有额外成本，就没必要搭进去一个主力账号。

如果用户明确表示接受风险、只是想在自己机器上做实验，可以单独加一个「实验性 Provider」，
但必须默认关闭、显式免责，且不与正式云端配置混用。
