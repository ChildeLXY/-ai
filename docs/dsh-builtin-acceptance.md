# 大肥鱼内置修改版 DSH — 架构与验收

> 本文取代旧的 `dsh-1to1-acceptance.md`（那是「iframe 套官方独立进程」的旧形态，已废弃）。
> 编写：Childe　日期：2026-11-20

## 1. 需求与边界（用户拍板）

大肥鱼里要的是**大肥鱼内置、经过修改的 DSH**，而不是「打开大肥鱼时拉起一个官方原版独立进程再套壳」。三条硬边界：

1. **保留大肥鱼主界面**：打开程序默认停在主界面，点「DSH 模式」按钮才进入，不做开机直进；
2. **外部独立 DSH 进程停用**：不再有常驻/自启的官方 `dsh web`、浮控按钮、「◈ DSH 原生」入口；
3. **直接改官方 DSH 前端**：以官方 `@deepseek-ai/dsh-web-frontend` 的构建产物为底，做品牌/功能修改，由大肥鱼内置托管，保留全部官方 API 兼容。

## 2. 架构形态

```
大肥鱼主界面（pywebview，http_server，随机端口）
   │  点「DSH 模式」
   ▼
#dshUiOverlay 全屏层（iframe，无浮控，仅一个「← 回到大肥鱼」）
   │  src = http://127.0.0.1:6811/?token=...
   ▼
内置 node（dfyweb profile，按需启动 / 关闭即释放）
   ├─ dsh-web-app（补丁：resolveDistIndex 读 DFY_DSH_DIST）
   ├─ 托管「修改版 dist」（dsh_host/dfy-dsh/dist）
   └─ 官方 API 全保留：插件管理 / 插件市场 / 会话 / 终端 …
```

要点：
- node 进程**只在用户点按钮时启动**，返回主界面即结束进程、释放端口，后台不留 node；
- 它与大肥鱼宿主共用同一个 `DSH_HOME` 与同一份 `node_modules`，在内置 DSH 里装/卸的插件，宿主重启后看到同一批；
- 鉴权沿用官方 loopback token：首请求 `?token=` 302 并 Set-Cookie，之后凭 cookie 访问，全程在本机回环，不暴露网络。

## 3. 「修改版前端」怎么改的

官方包只发布 Vite 构建产物（无 TS 源码、无 sourcemap），品牌名是内联 SVG 矢量字标，无法靠字符串替换。因此采用「整份产物 + 注入覆盖层」的可复现方案，由构建脚本 `dfy_dsh_build.py` 一键生成：

1. 把官方 `dsh-web-frontend/dist` 整份复制到 `dsh_host/dfy-dsh/dist`（产物目录，每次重建，不污染 `node_modules`）；
2. `index.html`：标题改为「大肥鱼 · 内置 DSH」，注入 `dfy-override.js / dfy-override.css`；
3. `manifest.webmanifest`：name / short_name 改为大肥鱼；
4. `dfy-override.js`（运行时覆盖层）：
   - 替换侧栏品牌槽位 `[data-slot="sidebar.brand.mark"]`（🐟）与 `[data-slot="sidebar.brand.name"]`（大肥鱼 + 蓝色 DSH 徽标），MutationObserver 防 React 重渲染还原；
   - 标题守护：轮询纠正被 React 覆盖的 `document.title`；
   - 文案兜底：对残留可见的 DeepSeek 文本做替换（避开 script/style/input）；
   - **首屏 hero 去品牌化**：隐藏官方鲸鱼矢量图 `.pXSMma_fishHitbox`（`[data-slot="conversation.hero.brand.mark"]`）、隐藏 `.pXSMma_previewBadge`（「预览版」徽标），把 `.pXSMma_titleGroup` 里的「探索未至之境」改写为「大肥鱼AI」，定时复查以对抗 React 重渲染；
   - **账户区剔除登录类条目**：文本命中 `退出登录 / 登出 / 注销 / 切换账号 / 登录 / Log out / Sign in` 的菜单项一律 `display:none` 并打标，MutationObserver 持续守护；「设置」「意见反馈」保留。
5. `dsh-web-app` 补丁（带 `.bak-dfy` 备份，幂等）：`resolveDistIndex()` 优先读环境变量 `DFY_DSH_DIST`，使 node 托管修改版而非官方 dist；
6. `dfy_dshui.py` 启动子进程时注入 `DFY_DSH_DIST` 与 pnpm 垫片，保证 pnpm 锁死 11.28.5。

重建命令：`python dfy_dsh_build.py`（还原官方 web-app：`python dfy_dsh_build.py --restore`）。

## 4. 自动化验收

### 4.1 端到端（真实 html + 真实内置 node，独立 pywebview 窗口）

脚本 `D:\大肥鱼缓存\dsh_builtin_e2e.py`，结论 `builtin_e2e.json`，**10/10 全过**：

| 检查项 | 结果 |
|---|---|
| 加载后不自动进入（overlay 不可见） | 通过 |
| origin 为 http://127.0.0.1 | 通过 |
| 点 DSH 模式后 overlay 出现 | 通过 |
| iframe 拿到 6811 token 地址 | 通过 |
| 骨架屏已撤 | 通过 |
| 浮控只剩 1 个返回按钮 | 通过 |
| 浮控在右侧、不压侧栏品牌（新增） | 通过 |
| iframe 指向修改版（标题含大肥鱼、含 dfy-override） | 通过 |
| 返回后 overlay 隐藏 | 通过 |
| 6811 node 已释放 | 通过 |

### 4.2 真实浏览器渲染（headless Edge / Chromium）

`inspect_ui3.mjs` 加载真实 node 地址并逐项读 computed style：

| 项 | 结果 |
|---|---|
| 侧栏品牌 | 「🐟 大肥鱼」+ 蓝色「DSH」徽标 |
| 文档标题 | 大肥鱼 · 内置 DSH |
| hero 品牌位 | `.pXSMma_fishHitbox` display:none；hero 内可见大尺寸 svg 数 = 0 |
| hero 文案 | 「大肥鱼AI」，正文已无「探索未至之境」 |
| 「预览版」徽标 | `.pXSMma_previewBadge` display:none，正文无该字样（除一次性说明弹窗） |
| 账户菜单 | 展开后「退出登录」display:none 且已打标；「设置」「意见反馈」正常 |
| 侧栏入口 | 出现「B站鱼塘」，证明统一后的插件已在界面生效 |

`inspect_plugins.mjs` 点进「插件 → 已安装」，列出 15 个插件（含 dsh-fish-memory / soul-md / 表情包等），与宿主 15/15 一致。

### 4.3 环境自愈体检

`dfy_dsh_doctor.py check --safe`：proxy / pnpm(11.28.5) / store / allowbuilds / modules(15 插件) / ports 六项**全部 OK**。

### 4.4 值守与轮巡调整

- 旧的「DSH 回归轮巡」cron（每 2h，基于常驻 6811 旧口径）已**删除**，与新的按需模型一致；
- `dfy_dsh_watchdog.py` 未在运行、未被启动脚本引用，按需模型下不再使用，仅保留文件作健康检查参考。

## 5. 插件「同一套」怎么实现的

原来的分叉：宿主 `host.mjs` 的清单 = `host-plugins.json` ∪ desktop bundles ∪ dfyweb bundles（16 个），
而内置 DSH 只读 `profiles/dfyweb` 的 bundles（5 个），配置也分叉（desktop 有真实 provider/人设/onebot，dfyweb 只有 1KB 空壳）。

统一由 `dfy_dsh_unify.py`（幂等，带 `.bak-unify` 备份）落地：

| 动作 | 说明 |
|---|---|
| 唯一真源 | `profiles/dfyweb/package.json` 的 `dsh.profile.bundles` 是唯一启用清单；`host.mjs` 的 `readProfileBundles()` 只读 `['dfyweb']`（`UNIFY_SINGLE_SOURCE`） |
| 清单补齐 | bundles = 3 个框架包 + 15 个业务插件；`host-plugins.json` 也收敛到同样 15 条 |
| 依赖联接 | `dsh-fish-memory` 用 `mklink /J` 联进共享 `node_modules`（`dfyweb/node_modules` 本身就是 `desktop/node_modules` 的联接，realpath 相同） |
| 配置镜像 | `desktop/cordis.patch.yml`（8787B，智谱 provider / 人设 / onebot / local-ai / nekowhale）整体镜像到 `dfyweb/cordis.patch.yml`；脚本每次运行自动比对，不一致才覆盖 |
| doctor 对齐 | `dfy_dsh_doctor.py` 的期望插件集改为从 bundles 收集（`UNIFY_BUNDLES_AWARE`），避免 fish-memory 被误判缺失 |

**结果**：宿主 15/15 全 ok、失败 0、tools 35；内置 DSH 15 个全部激活、0 个 `did not activate`；插件页列表与宿主完全一致。

**为什么少了 `dsh-bili-scroll`**：`dsh-bili-pond` 与 `dsh-bili-scroll` 都向全局命名空间注册 `bili_whoami` 工具，DSH 对同名全局工具直接拒载（提示要用 per-agent ctx 才能变体），不改第三方包无法双赢。取舍：保留 pond（鱼塘互动是用户主动要的功能），弃 scroll（官方 desktop bundles 本来也没启用它）。scroll 仍然「已安装未启用」，两边一致。

**以后怎么加插件**：在 `profiles/dfyweb/package.json` 的 `dsh.profile.bundles` 里加一条 → 宿主与内置 DSH 同时生效；只想装不启用就只写 `dependencies`。

## 6. 文件清单

| 路径 | 作用 |
|---|---|
| `dfy_dsh_build.py` | 修改版前端构建/还原（含 web-app 补丁） |
| `dsh_host/dfy-dsh/dist/` | 修改版前端产物（92 文件，大肥鱼品牌） |
| `dsh_host/dfy-dsh/dist/dfy-override.js/.css` | 运行时品牌与文案覆盖层 |
| `dsh_web/.../dsh-web-app/lib/index.js` | 已打 DFY_DSH_DIST 补丁（备份 `.bak-dfy`） |
| `dfy_dshui.py` | 内置 node 按需启停、token 地址、关闭同步 |
| `dfy_dsh_doctor.py` | 六项体检 + 自愈 + pnpm 探针 |
| `dfy_dsh_unify.py` | 插件/配置统一（唯一真源、fish-memory 联接、配置镜像、host/doctor 打补丁） |
| `dsh_host/host.mjs`（6800） | 兼容宿主，bundles 驱动清单，只读 dfyweb |
| `dsh_host/host-plugins.json` | 宿主插件名单（15 条，与 bundles 一致） |
| `profiles/dfyweb/package.json` | 唯一启用清单（bundles 18 条：3 框架 + 15 业务） |
| `profiles/dfyweb/cordis.patch.yml` | 与 desktop 同内容的用户层配置（镜像） |

## 7. 「插件全用不了」的排查结论（2026-11-20）

用户反馈 DSH 模式里插件全用不了。逐层实测结论：**插件层没坏，是模型层被 429 卡死**。

| 层 | 实测结果 |
|---|---|
| 插件装载 | 15 个全激活，`did not activate` 零条 |
| 插件页 | 15 张卡片，开关除 `dsh-bili-scroll` 外全 true；点进去详情显示「运行中 · 1 个组件」 |
| 插件服务端 API | `/dsh-bili/api/settings`、`/status`、`/deps` 全 200；deps 报「B站 cookie 已登录：大肥鱼本地版」 |
| 设置面板 | 「养鱼 / B站鱼塘 / 桌宠配置 / 鱼的心情 / 鱼的记忆 / 主动搭话 / 通到 QQ / 插件市场」section 全在 |
| 模型 | 智谱免费池 `glm-4.6v-flash` 返回 429（code 1305），对话卡在「正在重试模型请求」→ 任何要用模型的插件功能都不响应 |

**为什么看起来像"插件全废"**：插件功能（B站看视频、桌宠对话、QQ 回复、主动搭话）全部由 agent 驱动，模型一被限流，这些就全都不动，界面只剩壳。

**并发会放大限流**：DSH 的会话是并行跑的，开着多个会话同时发消息会立刻把免费额度打满，而且卡住的会话会持续重试、继续占额度，后面每一条新消息都 429。

**已做的处理**：
- 清掉排查期间产生的 11+1 个卡死测试会话（移动到 `D:\大肥鱼缓存\_session_trash_20261120`，可随时恢复）；
- 重启 6811 清空所有 in-flight 请求；
- 清理后单条对话实测：24 秒完成，人设正常。

**模型取舍（配置已留注释）**：
- 默认保持 `zhipu / glm-4.6v-flash`（免费、用户原有配置）；
- `deepseek-account` 这个 provider 看似「已登录」，但实际缺 `DEEPSEEK_API_KEY`，切过去会变成「请选择模型」，别用；
- 完全避开限流的办法是在 设置→模型 切到本机 Ollama（`ollama / qwen3:8b`，已配 `think:false`）；缺点是吃 GPU，玩 CS 时不要用。

## 8. 已知边界（官方语义，非缺陷）

- 官方插件详情页无「更新」按钮，更新只能 `POST /dsh-market/update {name}`；
- `dsh web` 无关闭鉴权参数；`file://`（Origin=null）被入口栅栏拒，只能 http 同源；
- 安装确认后 5s 再取消会装完（已进 linking），1.5s 内取消可干净回滚；
- 后台 GDI/PrintWindow 对 GPU 合成的 WebView2 可能取到白帧，属抓图限制，不代表内容异常；真实渲染以 headless Chromium 与 HTTP 校验为准。

## 9. 插件市场实装验证（2026-11-20）

在完整运行时（6811）经「插件 → 插件市场」实装热门插件，验证安装、启用、激活与真实使用。

**安装链路要点**：市场卡片点「安装」后弹确认框，确认按钮文案是「**确认安装**」（非「安装」）；确认后后端 `pnpm` 写入 `dependencies` + `node_modules`，并自动把包名追加进 `dsh.profile.bundles` 启用，无需手动开开关。

| 插件 | 版本 | 验证方式 | 结果 |
| --- | --- | --- | --- |
| dsh-context | 0.86.0 | 侧栏「上下文洞察」面板真实加载 | 35 会话/4.1M token/缓存命中 90.33% 等数据完整，53 个图表元素，`/api/dsh-context/{facts,skills,overview}` 全 200 |
| cc-safety-net | 2.8.0 | apply 后驱动真实 `tools.guard` 回调 | 6/6：安全命令放行（返回 undefined），危险命令返回 `BLOCKED by CC Safety Net`（git.reset-hard / git.push-force / secret.basename.env / analysis.dynamic-shell-source） |
| dsh-free-search | 0.9.73 | 直接驱动免费引擎真实联网搜索 | Bing 引擎返回 8 条真实结果（标题/URL/跳转解析正确）；DDG 国内超时，符合「中国出口自动用 bing、失败自动回退」设计 |

**架构差异（非缺陷）**：`cc-safety-net` 是纯守卫插件，依赖官方 cordis 的 `tools.guard`，不注册工具/片段。宿主 `host.mjs`（6800）是兼容层，只桥接工具与片段、不实现 guard，故不加载它（`host-run.log` 无记录、health 不计入）；它在完整运行时 6811 内完整生效。`dsh-context`、`dsh-free-search` 主要走 HTTP 路由/接管既有 search provider，不新增全局工具名，故 host 工具数仍为 35。

**测试脚本**（`D:\大肥鱼缓存`）：`test_safety.mjs`（checkCommand 8/8）、`test_guard_integration.mjs`（guard 6/6）、`test_freesearch.mjs`（Bing 真实搜索）、`use_context.mjs`（面板端到端）。doctor 复检六项全绿，modules 17。
