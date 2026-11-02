# 大肥鱼AI

一个住在你电脑里的本地 AI 桌面伴侣。聊天、记忆、心情、判断全部跑在本机 Ollama 上，
**不经过任何云端 API**，聊天记录只存在你自己的硬盘里。

界面用 WebView2 承载 HTML（也可以换成 Qt 原生版），插件系统照着
DSH（DeepSeek Harness）那套 "everything is a plugin" 的思路做了一版轻量实现。

---

## 它能做什么

| 能力 | 说明 |
|---|---|
| 本地推理 | 走 `127.0.0.1:11434` 的 Ollama，断网可用（联网搜索除外） |
| 长期记忆 | 分层记忆 + 按话题召回，跨会话不失忆 |
| 对话时间戳 | 每条消息记下真实发生时间，历史送到模型时带 `[昨天 深夜]` 这样的标记，避免"把昨晚的对话当成刚刚发生"；标记只到"哪一天"，不给钟点，防止模型在回复里机械报时 |
| 人格卡片 | 一张 markdown 描述"它是谁"，可随时改写 |
| 六插件系统 | 心情、表情包、主动搭话、灵魂记忆、B 站鱼塘、B 站养鱼 |
| 插件中心 | 每个插件一个独立页面，界面对齐 DSH |
| 桌面宠物 | 可选的 tkinter 桌宠，会自己走动、念叨 |
| 语音朗读 | 走 edge-tts（可选） |
| 联网检索 | DuckDuckGo / Bing / 百度，可开关 |

---

## 快速开始

### 1. 环境要求

- **Windows 10/11**（依赖 WebView2 与部分 Win32 API，没有做跨平台适配）
- **Python 3.10+**，建议 **3.12**
  > Qt 原生版 + pythonnet 在 3.14 上会因后台子进程把窗口搞崩，3.12 验证最稳。
- **[Ollama](https://ollama.com/)**，默认监听 `127.0.0.1:11434`

### 2. 拉一个本地模型

```bash
ollama pull qwen3:4b      # 推荐，快、够用
```

界面里可选的模型（装了哪个就能选哪个）：

| 模型 | 定位 | 大致需求 |
|---|---|---|
| `qwen2.5vl:7b` | **默认**。指令跟随稳，会挑表情包 | 8 GB |
| `qwen3:4b` | 最快，但常复述 prompt、不爱挑表情包 | 4 GB 显存以内 / 纯 CPU 也行 |
| `qwen3:8b` | 更强，稍慢 | 8 GB |
| `deepseek-r1:8b` | 爱推理，最慢 | 8 GB |
| `qwen3:30b` | 最强，需大内存 | 18 GB+ |

### 3. 安装 Python 依赖

```bash
pip install pywebview PySide6 Pillow edge-tts
```

- `pywebview` —— WebView2 版界面（`大肥鱼AI.py`）
- `PySide6` —— Qt 原生版界面（`大肥鱼AI_Qt.py`），两个前端二选一，也可都装
- `Pillow` —— 截图、桌宠图像处理
- `edge-tts` —— 语音朗读，可选；缺失时该功能静默跳过

### 4. 启动

```bash
python 大肥鱼AI.py        # WebView2 版（HTML 前端，功能最全）
python 大肥鱼AI_Qt.py     # Qt 原生版
```

或者双击 `大肥鱼AI.bat` / `start.ps1`（会顺带拉起 Ollama、桌宠和碎碎念守望者）。

> **启动脚本里的路径要按自己的环境改。** `start.ps1` 默认按作者的磁盘布局写了
> `D:\本地部署大肥鱼环境\...`（Ollama 程序、模型目录、venv）。你只需要改开头那几行：

```powershell
$ollamaPath = "D:\本地部署大肥鱼环境\program\ollama.exe"   # ← 你的 ollama.exe
$env:OLLAMA_MODELS = "D:\本地部署大肥鱼环境\models"          # ← 你的模型目录
```

`start_ollama.bat` 里也有同样的路径，一并改。

---

## 数据放在哪

默认在 `D:\本地部署大肥鱼环境\data`，**不在仓库里**，也不在程序目录下。

| 文件 | 内容 |
|---|---|
| `chats.json` | 全部聊天记录 |
| `memory.json` | 长期记忆的事实条目 |
| `settings.json` | 设置（模型、主题、搜索开关等） |
| `user_avatar.jpg` | 你自己设的头像 |
| `db.sqlite` | 知识库 |
| `plugins.json` | 插件开关 |
| `plugins/<id>/` | 各插件自己的数据（心情时间线、表情缓存、人格卡…） |

缓存（截图、日志、WebView2 profile）默认在 `D:\大肥鱼缓存`。

两个目录都能用环境变量搬走，不用改代码：

```bash
set DFY_DATA_DIR=E:\my-ai-data
set DFY_CACHE_DIR=E:\my-ai-cache
```

---

## 目录结构

```
大肥鱼AI.py            WebView2 版主程序：窗口、单实例锁、把 Python 函数暴露给 JS
大肥鱼AI.html          前端界面（聊天、设置、插件中心）
dfy_core.py            后端核心，无 UI 依赖：模型调用、检索、记忆、人格卡、语音
大肥鱼AI_Qt.py         Qt 原生版主程序（复用 dfy_core）
plugins/               插件目录，一个 .py 就是一个插件
  __init__.py          插件加载器 + PluginContext
  _bili_common.py      两个 B 站插件共用的底座（下划线开头，不算插件）
  _bili_bridge.py      本地 MCP 子进程桥
pet.pyw                tkinter 桌宠
pet_mutter_watcher.py  碎碎念守望者（独立进程，定时让桌宠冒一句话）
ds图片/                图标、头像、桌宠图素材
start.ps1 / *.bat      启动脚本
*.spec                 PyInstaller 打包配置
```

---

## 插件系统

**一个 `.py` 文件丢进 `plugins/` 就生效，删掉就没了，不用动主程序。**
单个插件崩溃只会跳过自己，不会拖垮大肥鱼。

```python
PLUGIN_ID = "my_plugin"        # 必填，唯一，也是数据落盘目录名
PLUGIN_NAME = "我的插件"
PLUGIN_VERSION = "0.1.0"

def setup(ctx):                # 可选：初始化
    ctx.log(PLUGIN_ID, "起来了")

def apis():                    # 必填：返回要暴露给前端 JS 的函数
    return [hello]

def hello(name):               # 前端 window.pywebview.api.hello('xx')
    return "hi " + name
```

`ctx` 提供宿主能力：`ctx.plugin_data()` 落盘、`ctx.setting()` 读写设置、
`ctx.chat()` 调本地模型、`ctx.on/emit` 事件总线、`ctx.log()` 写日志。

已内置的六个：

| 插件 | 干什么 |
|---|---|
| 🐟 **鱼的心情** | 每轮用本地模型判七维情绪（开心/踏实/甜蜜/心疼/担心/难过/吃醋），基线 70，12 小时半衰期自己回稳 |
| 🐱 **表情包** | 322 张标注好的表情，情绪识别 + 本地检索不花 token，按「最近发过的」扣分轮换，图片按需下载后永久缓存。**用户也能发**：输入框左边 😺 打开图库，可按 25 类情绪筛、按关键词搜、滚到哪下到哪 |
| 💬 **主动搭话** | 没人理它时也可能先开口。掷骰子纯本地随机，命中才走模型 |
| 🧠 **灵魂记忆** | 人格卡片 + 分层记忆 + 按话题召回 |
| 📺 **B站鱼塘** | 检索 → 判重 → 看字幕 → 本地模型筛选 → 写日报/日记/记忆 |
| 🐋 **B站养鱼** | 听私信、守评论、按风控预算回一句，**默认全关** |

详细的插件开发文档（`_hubViews` 页面注册、样式类、接口清单）见
**[`plugins/README.md`](plugins/README.md)**。

插件开关写 `data/plugins.json`，改完**重启生效**：

```json
{ "bili_whale": false, "speak_up": false }
```

---

## ⚠️ B 站插件的额外前提

`bili_pond` / `bili_whale` 不是独立实现，它们**复用 DSH 已有的 `bili-duty` 工作区**，
默认路径 `~/.dsh/bili-duty/`（Windows 上是 `C:\Users\<你>\.dsh\bili-duty\`）。
那里存放着登录态、判重表 `seen.md`、记忆、历史日报和日记。

**如果你没有这个工作区，这两个插件跑不起来**——它们会找不到登录态和判重表。
可以用环境变量指到别处：

```bash
set BILI_DUTY_HOME=E:\my-bili-workspace
```

没有 DSH 环境的话，建议直接在设置里把这两个插件关掉，其余四个插件不受影响。

风控方面，`bili_whale` 默认**全部关闭**，开之前请自己想清楚——它带读/写预算、
冷却时间和撞 412/429 自动熔断，但这些只是降低风险，不是免死金牌。

---

## 环境变量

| 变量 | 作用 |
|---|---|
| `DFY_DATA_DIR` | 数据目录，默认 `D:\本地部署大肥鱼环境\data` |
| `DFY_CACHE_DIR` | 缓存目录，默认 `D:\大肥鱼缓存` |
| `BILI_DUTY_HOME` | B 站工作区，默认 `~/.dsh/bili-duty` |
| `DFY_ALLOW_MULTI=1` | 允许开多个实例（默认单实例，防聊天记录互相覆盖） |
| `DFY_SELFTEST=1` | 启动后把界面真实状态写成 JSON 再退出（调试用） |
| `DFY_HUBTEST=1` | 逐个渲染插件页面并记录结果（调试用） |
| `DFY_SHOT=<插件id>` | 打开插件中心并抓窗口图（调试用） |
| `DFY_REMOTE_DEBUG_PORT=9334` | 打开 WebView2 远程调试端口 |

---

## 打包成 exe

```bash
pyinstaller 大肥鱼AI_Qt.spec      # Qt 版（入口 大肥鱼AI_Qt.py）
pyinstaller 大肥鱼AI_原版.spec    # WebView2 版（入口 大肥鱼AI.py）
```

> 两个 spec 的 `name` 都叫 `大肥鱼AI`，产出都会写成 `dist/大肥鱼AI.exe`。
> 想要两个版本共存，请先改其中一个 spec 里的 `name`。

打包后 `ds图片/` 会被解进临时目录（退出即删），所以可写文件（头像、设置）
会自动改存到 `DFY_DATA_DIR`，首次运行会把旧设置和头像迁过去。

---

## 已知限制

- **只支持 Windows**，大量地方直接用了 `ctypes.windll` 和 WebView2。
- **素材扩展名和实际格式不符**：`ds图片/*.jpg` 里其实都是 PNG（魔数 `89 50 4E 47`）。
  浏览器和 PIL 都按内容识别，所以能正常显示，看着别扭而已。
- **别和 DSH 桌面端同时开鱼塘**：两边会抢着写同一个 `~/.dsh/bili-duty` 工作区，
  建议在 DSH 里关掉鱼塘的定时任务。
- 单实例锁用的是全局 Mutex，多用户同时登录同一台机器会互相顶掉（用 `DFY_ALLOW_MULTI=1` 绕开）。
- **旧聊天记录没有时间戳**：时间戳是后加的，此前存下的消息没有 `ts` 字段，界面上不显示时间，
  送给模型时也不带标记——但它们会被明确告知"更早期的记录、时间不可考"，不会当成刚刚发生。

---

## 第三方组件与致谢

- **桌宠**参考 [dsh-pet-indesktop](https://github.com/MerZlin/dsh-pet-indesktop)（MIT © 2026 Merzlin）。
  它的完整源码**没有**包含在本仓库里——`pet_repo/` 已被 `.gitignore` 排除（1.6 GB）。
  想用完整桌宠请自行 clone 到 `pet_repo/dsh-pet-indesktop-main/`，本仓库的 `pet.pyw`
  是不依赖它的独立简化版。
- **插件设计**借鉴 DSH（DeepSeek Harness）的插件契约与插件中心界面。
- **表情包**图源 kiriyuki.com，仓库内只保留了索引，图片按需下载缓存到本地。
- 判断类调用一律走本地 Ollama，不调用任何云端 API。

## 许可

本项目以 **MIT** 协议开源，完整条款见 [LICENSE](LICENSE)。

第三方组件仍遵循各自原始协议：`dsh-pet-indesktop` 为 MIT © 2026 Merzlin，
表情包素材版权归原作者所有，本仓库仅保留检索索引。
