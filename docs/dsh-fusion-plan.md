# DSH → 大肥鱼AI 融合方案（80% 主要功能）

> 性质：长线工程规划。本文是唯一权威路线图，每阶段完成后更新状态与验收记录。
> 取证日期：2026-11-18，基于本机 `E:\DSH\resources\app.asar`。

## 零、取证结论（先看这个，它决定方案性质）

| 项 | 实测值 |
|---|---|
| asar 内文件总数 | 21,682 |
| `package.json` 数 | 792 |
| `@deepseek-ai/*` 包数 | **337** |
| 带中文 README 的包 | **331** |
| JS/TS 文件数 | **13,525** |
| 代码形态 | **未压缩、未混淆、带完整 JSDoc 注释的 ESM** |
| 许可证 | **MIT**（`@deepseek-ai/dsh-desktop` 0.2.2-rc.2，杭州深度求索） |

结论：这不是"黑盒应用"，是一份可读的 MIT 源码。**代码可以直接跑，不需要翻译成 Python。**

## 一、核心路线：跑代码，不抄代码

**为什么不能抄**：337 个包，连数据格式演进都拆成了 `dsh-session-format-v0-to-v1 … v3-to-v4`。手工移植等于重新实现一套运行时，且永久落后于上游。

**为什么能跑**：DSH 的一切挂在 Cordis 树上，靠 `inject` 声明依赖。大肥鱼已有的 `dsh_host/host.mjs` 就是一个 Cordis 宿主，已经实现了 6 个服务：

```
createSystemPrompt()  createTools()      createWebServer()
createLlm()           createTimer()      createSettings()
```

**所以工程量集中在"补宿主服务"，而不是"抄业务逻辑"。** DSH 的包不需要改，宿主提供的服务够了它自己就跑起来。

## 二、"80%"的精确定义

按**能力域**算，不按包数算（包数会被前端 68 个 UI 包稀释，没有意义）。

| 能力域 | 包数 | 实现方式 | 目标覆盖 |
|---|---|---|---|
| 会话持久化与恢复 | 33 | 直接跑 | 95% |
| 多模型接入与重试 | 11 | 直接跑 | 90% |
| 工具执行 | 35 | 直接跑 | 85% |
| Agent 主循环 | 19 | 直接跑 | 90% |
| 插件体系 | 27 | 直接跑（已有基础） | 95% |
| 写插件 forge | 5 | 直接跑 | 90% |
| 存储与配置 | 4 | 直接跑 | 95% |
| 前端界面 | 68 | 嵌它自己的 web 前端，不重写 | 80% |

**明确不做**（这四块收益低、依赖重）：

- `dsh-computer-use` / `dsh-cua` —— 大肥鱼已有豆包侧 GUI 能力，重复
- `dsh-experimental-liveshare` —— 协作场景，单机无意义
- `dsh-otel` 系遥测 —— 隐私与体积都不划算
- `dsh-acp` 系 —— 外部编辑器协议，与本地养鱼无关

## 三、关键词总表（检索源码用）

按域分组，全部为实测存在的包名。检索时直接 `grep` asar 解包目录即可定位。

### 会话（33）
```
dsh-session  dsh-session-format  dsh-session-format-catalog
dsh-session-format-v0-to-v1  dsh-session-format-v1-to-v2
dsh-session-format-v2-to-v3  dsh-session-format-v3-to-v4
dsh-session-persistence  dsh-session-persistence-jsonl
dsh-session-projection  dsh-session-projection-cache
dsh-session-query  dsh-session-query-sqlite  dsh-session-reference
dsh-session-stats  dsh-session-title  dsh-session-title-llm
dsh-session-title-first-prompt-llm  dsh-session-title-all-prompts-llm
dsh-session-turn-outline  dsh-session-checkpoint-policy
dsh-session-log-deepseek  dsh-session-log-export  dsh-session-telemetry
dsh-session-search  dsh-session-inspector
```

### 模型 / LLM（11）
```
dsh-llm  dsh-llm-deepseek  dsh-llm-deepseek-account  dsh-llm-deepseek-api-key
dsh-llm-retry  dsh-llm-pi-ai  dsh-token-meter
dsh-deepseek-llm-api-extensions  dsh-web-deepseek-api
```

### 工具（35）
```
dsh-tools  dsh-tool-executor-native  dsh-tool-executor-ptc
dsh-tool-bash  dsh-tool-bash-persistent  dsh-tool-pwsh  dsh-tool-pwsh-persistent
dsh-tool-fs  dsh-tool-fs-search  dsh-tool-str-replace-editor
dsh-tool-web  dsh-tool-subagent  dsh-tool-subagent-control  dsh-tool-agent-team
dsh-tool-ask-user  dsh-tool-todo  dsh-tool-goal  dsh-tool-jobs
dsh-tool-schedule  dsh-tool-skill  dsh-tool-workflow  dsh-tool-office
dsh-tool-worktree  dsh-tool-cordis  dsh-tool-session-query
dsh-tool-terminal  dsh-tool-working-directory  dsh-tool-workspace-dependencies
dsh-tool-call-timeout-policy  dsh-repeat-tool-reminder  dsh-tool-present
```

### Agent / 代理（19）
```
dsh-agent  dsh-agent-loop  dsh-agent-team  dsh-agent-preset
dsh-agent-preset-registry  dsh-agent-instructions  dsh-agent-default-model
dsh-subagent  dsh-subagent-fork-in-process  dsh-subagent-spawn-in-process
dsh-agent-tool-presentation
```

### 插件体系（27）
```
dsh-plugin-manager  dsh-plugin-market  dsh-plugin-market-index
dsh-plugin-detail  dsh-plugin-recommendations  dsh-package-manifest
dsh-host-plugin-inventory  dsh-plugin-package-inventory-deepseek
dsh-official-bundles
cordis-plugin-group  cordis-plugin-include  cordis-plugin-loader  cordis-plugin-timer
```

### 写插件 / Forge（5）
```
dsh-experimental-forge  dsh-experimental-forge-bundle
dsh-experimental-forge-github  dsh-experimental-forge-session
```

### 沙箱（7）
```
dsh-sandbox  dsh-sandbox-local  dsh-sandbox-policy  dsh-sandbox-windows-acl
dsh-bash-sandbox  dsh-pwsh-sandbox  dsh-fs-sandbox
```

### 存储（4）
```
dsh-storage  dsh-storage-json  dsh-storage-domain  dsh-atomic-write
```

### 核心运行时（19）
```
cordis  schemastery  cosmokit
dsh-brand  dsh-scope  dsh-base  dsh-error  dsh-lazy-require
dsh-util-values  dsh-util-crypto  dsh-util-time  dsh-util-ripgrep
dsh-util-workspace-path  dsh-util-code-language
dsh-cordis-host-runner  dsh-cordis-client-runner  dsh-hmr  dsh-typert-loader
```

### 平台 / 宿主（16）
```
dsh-app-boot  dsh-desktop-host  dsh-host-webserver  dsh-host-frontend-static
dsh-api-gateway  dsh-api-session-controller  dsh-api-settings-controller
dsh-api-workspace-controller  dsh-api-workspace-files  dsh-api-terminal-controller
dsh-api-job-controller  dsh-api-remote-access  dsh-api-remotes
dsh-sdk-app  dsh-sdk-protocol  dsh-sdk-jsonrpc-server
```

### 其余关键单体（按需取用）
```
dsh-system-prompt  dsh-persona  dsh-skill  dsh-skill-filesystem
dsh-compaction  dsh-compaction-basic  dsh-compaction-image-offload
dsh-permission-presets  dsh-user-approval  dsh-user-questions  dsh-plan-mode
dsh-workspace  dsh-working-directory  dsh-fs  dsh-fs-local  dsh-fs-patch
dsh-subprocess  dsh-subprocess-local  dsh-terminal  dsh-terminal-bash
dsh-mcp-client  dsh-mcp-manager  dsh-credentials  dsh-credentials-local
dsh-settings  dsh-shell  dsh-shell-env  dsh-home-paths
dsh-time-context  dsh-spill  dsh-spill-local  dsh-output-retention
```

### 前端（68，整体嵌入不重写）
```
dsh-web-frontend  dsh-client-ui-chat  dsh-client-ui-conversation
dsh-client-ui-sidebar  dsh-client-ui-sidebar-files  dsh-client-ui-sidebar-terminal
dsh-client-ui-model-selection  dsh-client-ui-settings-models
dsh-client-ui-plugin-market  dsh-client-ui-plugin-manager
dsh-client-ui-tool  dsh-client-ui-trajectory  dsh-client-ui-plan
dsh-client-ui-renderer  dsh-client-ui-layout  dsh-client-ui-theme
```

## 四、任务流程（九个阶段）

每个阶段独立可验收。**未通过验收不进下一阶段**（对齐用户的"每功能必须自测"规则）。

### P0 · 解包与资产化
- 写 `tools/asar_extract.mjs`，把 `app.asar` 全量解到 `D:\大肥鱼AI\dsh_pkgs\`
- 生成包索引 `index.json`：包名、版本、dependencies、injects、入口文件
- 抽出 331 份 `README.zh.md` 建检索库
- **验收**：解出文件数 ≥21,000；337 个包目录齐全；索引能按包名查询到入口

### P1 · 宿主服务层（最关键，决定后面全部）
现有 6 个服务补齐到 DSH 插件所需的最小集。按 `inject` 反查缺什么：

```
session  storage  fs  workspace  subprocess  terminal
credentials  settings  permission  approval  skill  persona
jobs  schedule  attachment  time  homePaths
```

做法：对每个 DSH 包读它的 `inject` 声明，列出缺失服务名，逐个在 `host.mjs` 里实现。
- **验收**：单包里 `inject` 声明的服务 100% 可解析；空实现也不算通过，必须有真实行为

### P2 · 会话层
接 `dsh-session` + `dsh-session-persistence-jsonl` + `dsh-session-projection`。
大肥鱼现有 `data/chats` 做迁移，兼容读取旧记录。
- **验收**：新会话落盘可回读；重启后历史完整；旧 chats 能被识别

### P3 · 模型层
接 `dsh-llm` + `dsh-llm-deepseek` + `dsh-llm-retry` + `dsh-token-meter`。
现有本地 Ollama 与云端模型包成 `dsh-llm` 的一个 provider。
- **验收**：本地与云端各跑通一轮；断网重试生效；token 计量与实际相符

### P4 · 工具层
接 `dsh-tools` + `dsh-tool-fs` + `dsh-tool-bash` + `dsh-tool-web` + `dsh-tool-subagent`。
- **验收**：每个工具能被 agent 真实调用一次并返回结果；失败时错误可读

### P5 · Agent 主循环
接 `dsh-agent` + `dsh-agent-loop` + `dsh-subagent-fork-in-process`。
- **验收**：多步任务能自主拆解执行；子代理可派生并回收；中断可停止

### P6 · 插件体系
现有 `dfy_market` 与 `dsh-plugin-manager` 对齐；保留已实现的安装前 probe 自测。
- **验收**：市场安装、启停、卸载全链路通；不兼容包在安装阶段被拦

### P7 · Forge 写插件
接 `dsh-experimental-forge`，与现有 Python 版 Forge 分工：DSH 插件走 forge，Python 插件走现流程。
- **验收**：生成→校验→落盘全链路通；坏插件不进目录

### P8 · 前端嵌入
`dsh-web-frontend` 起独立 HTTP 服务，大肥鱼用独立窗口加载，与 DSH 模式按钮联动。
- **验收**：界面能显示真实会话与工具调用；不遮挡主界面；可一键退回大肥鱼模式

### P9 · 总体验收
- 19 项端到端用例（每阶段至少 2 项）
- 冷启动时间、常驻内存、连续 24h 稳定性
- **验收**：用例全绿 + 三项指标达标才判定完成

## 五、关键风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| 原生依赖缺失 | 沙箱、terminal、语音不可用 | 用 asar 未打包目录里现成的 dll（`node-pty`、`koffi`、`sherpa-onnx` 都在） |
| Cordis 服务语义不匹配 | 插件加载但行为错 | 以 README.zh.md 为准逐个对齐，不靠猜 |
| 上游版本演进 | 代码过期 | 保留解包脚本，升级只需重跑 P0 |
| 体积膨胀 | 解包后约 1GB | 只解需要的包，按阶段增量解 |
| 许可证合规 | 法律风险 | 保留全部 LICENSE 与版权声明；对外不宣称官方出品 |

## 六、里程碑建议

| 阶段 | 依赖 | 预估 |
|---|---|---|
| P0 | 无 | 半天 |
| P1 | P0 | 最重，建议拆成小步多次提交 |
| P2–P4 | P1 | 每层独立可测 |
| P5 | P2–P4 | 依赖前面全部 |
| P6–P7 | P1 | 可与 P2 并行 |
| P8 | P2 | 需要真实会话数据 |
| P9 | 全部 | 一次总跑 |

## 七、当前状态

- [ ] P0 解包与资产化
- [ ] P1 宿主服务层
- [ ] P2 会话层
- [ ] P3 模型层
- [ ] P4 工具层
- [ ] P5 Agent 主循环
- [ ] P6 插件体系
- [ ] P7 Forge
- [ ] P8 前端嵌入
- [ ] P9 总体验收
