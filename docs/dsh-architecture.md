# DSH（DeepSeek Harness）后端架构取证与迁移对照

> 取证来源：本机 `E:\DSH\resources\app.asar`（Electron 安装，208MB）。
> 用 `extract_asar.js` 解析 asar 头，抽出全部 665 个 `package.json`，得到 `@deepseek-ai/*` 包目录 335 个。
> 不是猜的，是从安装包里读出来的包结构。

## 一、DSH 后端的真实结构

**底座：Cordis 元框架**

- `@deepseek-ai/cordis` 4.1.0 —— "Meta-Framework for Modern JavaScript Applications"
- 配套：`cordis-plugin-group`、`cordis-plugin-include`、`cordis-plugin-loader`、`cordis-plugin-timer`
- 配置 schema 用 `schemastery`

结论：DSH 是**「一切皆插件」+ 服务上下文注入（ctx.services）**。整个应用不是一个大服务，而是几百个插件挂在同一棵 Cordis 树上，靠 `inject` 声明依赖、按声明顺序启动。

**能力域（按包名前缀归类，节选）**

| 域 | 代表包 | 作用 |
|---|---|---|
| 会话 | `dsh-session`、`dsh-session-format-v1..v4`、`dsh-session-persistence-jsonl`、`dsh-session-projection` | 会话模型 + 多版本 format 迁移 + JSONL 落盘 + 投影缓存 |
| 模型 | `dsh-llm`、`dsh-llm-deepseek`、`dsh-llm-retry`、`dsh-token-meter` | LLM 抽象层、按厂商拆分、重试、token 计量 |
| 工具 | `dsh-tool-bash`、`dsh-tool-fs`、`dsh-tool-web`、`dsh-tool-subagent`、`dsh-tools` | 每个工具一个包，工具执行器可换（native / ptc） |
| 代理 | `dsh-agent`、`dsh-agent-loop`、`dsh-agent-team`、`dsh-subagent-fork-in-process` | 主循环、多代理、子代理 |
| 插件体系 | `dsh-plugin-manager`、`dsh-plugin-market`、`dsh-plugin-market-index`、`dsh-package-manifest`、`dsh-plugin-detail` | 插件安装/市场/清单/详情 |
| 写插件 | `dsh-experimental-forge`、`dsh-experimental-forge-bundle`、`dsh-experimental-forge-github`、`dsh-experimental-client-ui-forge` | **DSH 自己就有「forge」——一个会写插件的 agent** |
| 前端 | `dsh-web-frontend`、`dsh-client-ui-*`（60+ 个）、`dsh-client-hmr` | UI 也拆成插件，一个页面/面板一个包 |
| 沙箱 | `dsh-sandbox-policy`、`dsh-sandbox-windows-acl`、`dsh-fs-sandbox` | 执行策略与 Windows ACL |
| 平台 | `dsh-app-boot`、`dsh-desktop-host`、`dsh-host-webserver`、`dsh-api-*` | 启动、宿主、API 网关 |
| 存储 | `dsh-storage`、`dsh-storage-json`、`dsh-atomic-write` | 存储抽象 + 原子写 |

**从包结构能直接读出的三条设计原则**

1. **一个能力一个包**，不搞大文件；`dsh-client-ui-sidebar`、`dsh-client-ui-settings-models` 这种细到单个面板。
2. **可替换实现**：`dsh-llm` 与 `dsh-llm-deepseek`、`dsh-session-persistence` 与 `-jsonl`、`dsh-tool-executor-native` 与 `-ptc` —— 接口与实现分离，实现层可换。
3. **迁移与兼容是独立包**：`dsh-session-format-v0-to-v1 … v3-to-v4`，数据格式演进不写在业务里。

## 二、大肥鱼 AI 现状

- Python + pywebview 单进程；`plugins/__init__.py` 提供 `PluginContext` / `load_plugins` / `call_event`。
- 插件契约：`PLUGIN_ID / NAME / ICON / VERSION` + `setup(ctx)` + `apis()` + `shutdown()`。
- 已有 12 个插件（bili_whale、feiyu_mood、qq_chat、qq_zone、soul_md、speak_up、live_cast、nekowhale_stickers、deep_think 等）。
- 强项：本地模型、人格与记忆、QQ/B 站真实账号能力、桌宠。
- 弱项：插件加载无隔离、无依赖声明顺序、无清单格式、无清单元数据校验。

## 三、这一轮真正搬进来的东西

1. **隔离验证（照 `dsh-experimental-forge` 的做法）**
   模型写出的插件不再只做 `py_compile`，而是丢进**独立子进程** `exec_module` 一遍，校验 `PLUGIN_ID / NAME / setup` 齐全。坏代码不会进主进程，也不会写进 `plugins/`。
   落点：`大肥鱼AI.py` → `_forge_check()`

2. **清单驱动（照 `dsh-package-manifest`）**
   插件说明统一从模块常量读，前端按 `已开启 / 已关闭 / 异常` 分组展示，不再各自维护一份 JSON。
   落点：`plugin_list()` + DSH 框架的已安装页

3. **接口与实现分离的思路（照 `dsh-llm` / `dsh-session-persistence`）**
   市场、插件管理走 `dfy_market` 一层封装，前端只认 `market_*` 契约，后面换数据源不用动 UI。

## 四、没搬、以及为什么不搬

- **Cordis 本体搬不动**：它是 Node/TypeScript 运行时 + 依赖注入容器，大肥鱼是 Python + pywebview，硬塞进来等于重写整个 runtime。
- **DSH 的 335 个包不逐个移植**：那是拆到面板级别的工程，收益远小于维护成本。取它的**分层思想**比取代码更值。
- **前端 bundle 不能直接跑**：类名哈希 + 依赖宿主 API，只能按 CSS Module 源码和截图像素重建。

## 五、下一步建议（按性价比排序）

1. 给 `plugins/__init__.py` 加**依赖声明**（`DEPENDS = ["xxx"]`），按拓扑序加载，对应 Cordis 的 `inject`。
2. 插件配置改成 `schemastery` 式的 **schema 声明**，前端自动生成表单，省掉每个插件手写 UI。
3. 会话格式加版本号，为将来迁移留口子（对应 `dsh-session-format` 系列）。
4. DSH 框架里补独立聊天页，现在只有插件页。
