# 大肥鱼 AI · 本地插件

把 DSH（DeepSeek Harness）那套 "everything is a plugin" 的思路，用一个轻量版本搬到本地大肥鱼上。
**每个插件就是一个独立的 .py 文件**，丢进这个目录就生效，删掉就没了，不用动主程序。

主程序只加了不到 30 行：建上下文 → 加载插件 → 把插件的接口并进 `window.expose`。
插件自己崩了只会跳过自己，绝不会拖垮大肥鱼。

## 写一个插件

在 `plugins/` 下新建 `my_plugin.py`：

```python
PLUGIN_ID = "my_plugin"        # 必填，唯一，也是数据落盘的目录名
PLUGIN_NAME = "我的插件"
PLUGIN_VERSION = "0.1.0"

def setup(ctx):                # 可选：初始化
    ctx.log(PLUGIN_ID, "起来了")

def apis():                    # 必填：返回要暴露给前端 JS 的函数
    return [hello]

def hello(name):               # 前端 window.pywebview.api.hello('xx')
    return "hi " + name
```

`ctx` 能用的东西：

| 方法 | 说明 |
|---|---|
| `ctx.plugin_data(id, filename)` | 插件自己的数据文件，落在 `data/plugins/<id>/` |
| `ctx.read_json(path, default)` / `ctx.write_json(path, obj)` | 读写 JSON（原子写，不会写坏） |
| `ctx.setting(key, default)` / `ctx.save_setting(key, value)` | 读写设置（没设过返回 default，空串也算没设过） |
| `ctx.chat(messages, model, ...)` | 调**本地** Ollama，不碰云端 |
| `ctx.on(evt, cb)` / `ctx.emit(evt, data)` | 事件总线，`emit` 会推给前端 `window.__dfyEvent` |
| `ctx.log(id, msg)` | 写日志到 `D:\大肥鱼缓存\_plugins.log` |

注意：暴露给前端的函数**别用 `*args/**kwargs`**，主程序的 `_safe_api` 靠
`inspect.getfullargspec` 生成 JS 端参数名，用了就会丢参数（踩过这个坑）。

## 开关

编辑 `D:\本地部署大肥鱼环境\data\plugins.json`：

```json
{ "feiyu_mood": true, "speak_up": false }
```

没写过的插件默认开启。改完重启生效。

## 四个核心插件（从 DSH 插件 1:1 移植，判断全走本地模型）

### feiyu_mood · 鱼的心情
移植自 `dsh-feiyu-mood`。每轮聊完用本地模型判一次心情涨跌 + 七维情绪
（开心/踏实/甜蜜/心疼/担心/难过/吃醋），会随时间自己回稳。

- 数值照搬原版：基线 70，步进 = moodDelta/3，心情半衰期 12 小时、单根情绪 90 分钟
- 心情低于门槛（confidence < 60）的那句话**不会说出口**，只记进时间线
- 一次变化只注入一次，不会重复念叨
- 右下角挂饰显示当前心情，点开看七维情绪和时间线

接口：`mood_get()` `mood_judge(user, reply, mode)` `mood_context()`
`mood_timeline(n)` `mood_set_enabled(on)` `mood_reset()`

### nekowhale_stickers · 大肥鱼表情包
移植自 `dsh-nekowhale-stickers`。322 张表情包，每张都有使用场景/画面/梗出处标注。

- **索引全本地**：检索纯本地算分，一个 token 不花
- **图片按需下载**：第一次用到才从 kiriyuki.com 拉，之后永久存在
  `data\plugins\nekowhale_stickers\gif\`，断网也能显示
- 大肥鱼在回复末尾写 `[摸鱼]` 这样的代号，前端自动换成真图

接口：`sticker_search(q, emotion, n)` `sticker_local(id)` `sticker_hint(text, n)`
`sticker_sync(n)` `sticker_stats()` `sticker_emotions()`

### speak_up · 主动搭话
移植自 `dsh-speak-up`。没人在跟它说话时，它也有可能先开口。

- **掷骰子是纯本地随机，一个 token 都不花**，只有掷中之后才走模型
- 那两个数不是概率是**命中线**：掷 0~100，掷到命中线**以下**才开口
- 峰时（工作日 9-12、14-18）命中线 20，谷时 60；刚说过话 ×0.15，太久没说话 ×1.5
- 每没掷中一次命中线攒 +10（最多 +40），掷中归零
- 安静时段（默认 1~8 点）不掷；**默认开着「试运行」**，掷中只记账不真发

关掉试运行：`window.pywebview.api.speak_up_config('试运行', 0)`

接口：`speak_up_get()` `speak_up_roll(dry)` `speak_up_config(key, value)`
`speak_up_note_spoke()` `speak_up_reset()`

### soul_md · 灵魂记忆
移植自 `dsh-soul-md`。让大肥鱼跨会话长大，而不是每次重置。

- **人格卡片**：一张写着"它是谁"的 markdown，可写回更新
- **分层长期记忆**：按话题分格，总量上限 1MB
- **按话题召回**：单次最多 5 个话题 / 1600 字塞进 prompt
- 首次启动自动把旧的 `data\memory.json` 迁过来，并**洗掉混进去的模型推理痕迹**
  （"首先，""关键点：""用户说："这类不是事实的条目，第一次就清掉了 9 条）

接口：`soul_get()` `soul_update(name, card)` `memory_append(text, topic)`
`memory_recall(q)` `soul_context(q)` `memory_list()` `memory_show(topic)` `memory_forget(topic, text)`

## 界面：插件中心（对齐 DSH）

主界面**左侧栏底部一排图标**，或顶栏的 🧩，打开「插件中心」：左边是插件列表，右边是每个插件
自己的页面 —— 和 DSH 的设置弹窗一个路子。每个插件一个独立页面，互不干扰。

给插件加页面，在 `大肥鱼AI.html` 的 `_hubViews` 里注册就行：

```js
_hubViews.my_plugin = {
  icon: '🧩', name: '我的插件', sub: '一句话说明',
  mount: function(box){
    box.innerHTML = '<div class="pg-card"><div class="pg-h"><span>标题</span></div>'
                  + '<div class="pg-note" id="mpStat">…</div></div>';
  },
  refresh: async function(){
    const r = await pgCall('hello', 'world');   // 参数会原样传给 Python 那个函数
    pgTxt('mpStat', r);
  }
};
```

`pgCall(接口名, ...参数)` 就是调插件 `apis()` 里的函数；`pgToast / pgTxt / pgShow / pgEsc`
是现成的小工具。页面里的样式类（`pg-card / pg-row / pg-note / pg-view / pg-tabs / pg-tab /
pg-badge / pg-input / pg-num / pg-chk / pg-thumbs / pg-bar / pg-item / pg-list`）都已经有了。

## 现在装了哪些

| 插件 | 文件 | 干什么 |
|---|---|---|
| 鱼的心情 | `feiyu_mood.py` | 每轮用本地模型判七维情绪，心情基线 70、12 小时半衰期回稳 |
| B站鱼塘 | `bili_pond.py` | 检索→判重→看字幕→本地筛选→写日报/日记/记忆（复用 `~/.dsh/bili-duty` 的数据） |
| B站养鱼 | `bili_whale.py` | 听私信、守评论、按风控预算回一句，默认全关 |
| 主动搭话 | `speak_up.py` | 定时本地掷骰子，命中才让大肥鱼开口，不花 token |
| 灵魂记忆 | `soul_md.py` | 人格卡 + 分层记忆 + 按话题召回 |
| 表情包 | `nekowhale_stickers.py` | 322 张标注好的表情，本地检索，图片按需下载后永久本机 |
| 大肥鱼直播 | `live_cast.py` | 让 AI 自己开播说话，画面推给抖音直播伴侣捕获 |

## 大肥鱼直播（live_cast）

让大肥鱼自己对着镜头说话。选这个话题是因为**它不需要抓任何平台的数据**——数据源是它自己，
所以没有抖音那套签名、风控、协议变更的麻烦。

### 三个关键设计

- **生成与播放并行**：本地模型出一句话要 2~3 秒。如果说完才去写下句，直播间会成片冷场，
  所以单独开一个生成线程，不停往队列里囤句子，播放线程只管取和念。
- **零依赖出声**：`edge-tts` 合成 mp3，用 Windows 的 MCI（`winmm.dll`）直接播，
  不装 ffmpeg、不装 pygame。播完才返回，所以字幕和语音天然同步。
- **画面独立成网页**：`live/live_screen.html`，不起第二个 pywebview 窗口（本机会崩），
  改成浏览器全屏 + 直播伴侣窗口捕获。

### 模型选择（重要）

**只能用 `qwen2.5vl:7b`。** 实测本机这几个模型的表现：

| 模型 | 直播表现 |
|---|---|
| `qwen2.5vl:7b` | ✅ 自然、不重复，和你桌宠碎碎念用的是同一个 |
| `qwen3:8b` | ⚠️ 会把 prompt 里的示例原样抄一遍，两句一模一样 |
| `qwen3:4b` / `qwen3:30b` | ❌ 直接复述任务（"首先，用户要求我扮演大肥鱼…"），直播当场翻车 |

代码里有 `_is_garbage()` 兜底：出现"用户要求/首先/关键点"就丢弃重生成，
连续失败三次就上预设的兜底句，保证直播间不哑火。

### 开播流程

1. 插件中心 → 🎙 大肥鱼直播 → **开播**
2. 点 **打开直播画面**，浏览器里按 **F11** 全屏
3. 装抖音「直播伴侣」→ 添加来源 → **窗口捕获** → 选这个浏览器窗口
4. 声音：装 **VB-CABLE** 虚拟声卡，让 TTS 走 CABLE，直播伴侣麦克风选 **CABLE Output**
5. 画面已固定标注「AI 生成内容」——这是抖音的硬性要求，别删

> 抖音禁止纯 AI 无人直播和循环录播，会被限流封号。做成 AI 主导、人能随时接管（用「让它说一句」
> 插话）才安全。你自己盯着场，风险自己评估。

接口：`live_start()` `live_stop()` `live_state()` `live_config(k,v)`
`live_open_screen()` `live_say(text)` `live_voices()` `live_selftest()`

## 开关插件

设置 → 插件中心底部，或者直接写 `data\plugins.json`：
`{"bili_whale": false}` 就是关掉养鱼。**重启后生效**（中途卸载容易留下孤儿线程）。

## 还没移植的

`dsh-onebot-qq` / `dsh-qq-bridge`（QQ 收发，你本地已有 `D:\大肥鱼QQ\qqbot.py`）、
`dshmarket`（插件市场）、`dsh-pet`（已有 `pet.pyw`）、`dsh-local-ai`（本来就直连 Ollama）。
