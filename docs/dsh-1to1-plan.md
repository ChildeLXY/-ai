# 大肥鱼 AI —— 1:1 原生 DSH 前端：通宵冲刺方案（2026-11-20 00:22 → 2026-11-21 12:00）

## 前提与已完成

插件安装链路的三层坑（pnpm store 跨盘冲突 / allowBuilds 白名单 / 死代理 7897）已在真实 UI 上打通并验收：
装 dsh-qrcode（两步确认 → resolving/downloading/linking → 成功）、卸载、启用禁用与持久化全部通过。
本方案从"链路能跑通"推进到"链路不靠人手、宿主能 100% 接管 DSH 市场与插件管理"。

## 阶段划分（每阶段必须有自动化验收，失败修到通过才进下一阶段）

| 阶段 | 时间窗 | 目标 | 验收方式 |
|---|---|---|---|
| S1 自愈层 | 00:30–04:00 | 把这次踩的坑做成永久自动修复，任何人手动改坏都能自愈 | 故障注入 5 项 → doctor fix → 复查全绿 |
| S2 抗漂移 | 04:00–07:00 | 锁定 pnpm 主版本、覆盖安装/更新/卸载/取消四条链路 | 四条链路脚本全绿，pnpm 版本恒定 |
| S3 统一清单 | 07:00–11:00 | host.mjs 与官方前端共用同一份插件清单，单一真源 | UI 装插件 → 宿主重启可见；UI 禁用 → 宿主不加载 |
| S4 宿主入口 1:1 | 11:00–16:00 | 大肥鱼主界面默认进入官方彩色 DSH 前端，含安装动画与任务面板 | 截图比对 + 交互脚本 + 零 console error |
| S5 全功能回归 | 16:00–22:00 | 12 项回归矩阵全部自动化 | 回归报告全绿，输出 report.md |
| S6 打包与文档 | 22:00–01:00 | 一键启动、README、变更记录 | 干净环境冷启动验证 |
| S7 值守加固 | 01:00–12:00 | 每 2 小时自跑回归，失败即修 | 连续 N 轮全绿 + 中午总验收报告 |

---

## 进度（滚动更新）

| 阶段 | 状态 | 结束时间 | 关键结论 |
|---|---|---|---|
| S1 自愈层 | ✅ 完成 | 2026-11-20 00:40 | 6 项体检 + 5 项故障注入验收全绿；已接入桥接启动 |
| S2 抗漂移 | ✅ 完成 | 2026-11-20 01:24 | pnpm 锁 11.28.5 已取证；安装/更新/卸载/取消四条链路真实 UI 全绿 |
| S3 统一清单 | ✅ 完成 | 2026-11-20 02:05 | 宿主名单改为从 profile 的 `dsh.profile.bundles` 派生；装/禁/启/卸四条往返全绿 |
| S4 宿主入口 1:1 | ✅ 完成 | 2026-11-20 02:50 | 大肥鱼窗口开机直进官方彩色前端（真机截图已验）；file:// 起源鉴权坑已修 |
| S5–S7 | ⏳ 待开始 | — | — |

### S3 阶段实测记录

`host.mjs` 的 `loadManifest()` 不再只读 `host-plugins.json`，改为：

```
plugins = 手工基础名单(host-plugins.json.plugins)
        ∪ profiles/desktop 的 dsh.profile.bundles
        ∪ profiles/dfyweb  的 dsh.profile.bundles
        − host-plugins.json.disabled
```

**为什么只跟 bundles、不跟 dependencies**：官方前端的启用/禁用就是把名字从
`dsh.profile.bundles` 里加进去/删掉（实测 `pluginManager/setBundleEnabled` 只动这个数组）。
如果连 dependencies 一起并，被禁用但没卸载的插件会被宿主照旧加载，和界面显示相反。

往返验收（全部零失败）：

| 动作 | 官方接口/路径 | 宿主插件数 | dsh-tool-qrcode |
|---|---|---|---|
| 初始 | — | 16 | 无 |
| 界面安装 | 市场卡片两步确认 | 17 | ✅ 已加载 |
| 界面禁用 | `setBundleEnabled {name,enabled:false}` | 16 | ❌ 不加载 |
| 界面启用 | `setBundleEnabled {name,enabled:true}` | 17 | ✅ 恢复 |
| 界面卸载 | 详情页卸载 + 模态框 | 16 | ❌ 消失 |

顺带修掉一个真问题：`dsh-qrcode` 早就在 desktop 的 dependencies 里，但从来没进过
`host-plugins.json`，所以历史上装完在宿主里查无此插件 —— 现在由 bundles 自动带出。

### S4 阶段实测记录

**根因 C：file:// 起源会被 DSH 的入口栅栏直接拒。**
现象：把 6811 的 token 地址塞进 iframe，页面显示
`dsh web: authentication required; reopen the URL printed by dsh web.`
对照实验（同一 token，三种加载方式）：

| 加载方式 | 结果 |
|---|---|
| 顶层直接打开 | ✅ 正常渲染 |
| `http://127.0.0.1:7900` 起源里的 iframe | ✅ 正常渲染 |
| `file://` 起源里的 iframe | ❌ authentication required |

原因是 DSH web 的栅栏认 Origin/站点，`file://` 的 Origin 是 `null`，一律拒。
而大肥鱼原来用 `f"file:///{html_path}"` 起窗口 —— 所以「DSH 原生」按钮在真机里
注定只看到鉴权提示（历史截图里的 iframe 白屏就是这个）。

修法（两处）：
1. `大肥鱼AI.py` 的 `create_window` 改传文件路径（不再拼 `file://`），
   `webview.start(http_server=True)`，让 pywebview 用内置服务把页面发到
   `http://127.0.0.1:<随机端口>`；失败自动回退 `file://`。
2. `大肥鱼AI.html` 的 DSH 入口重写成「开机直进」：
   - `dshUiBoot()` 挂 `pywebviewready` + `DOMContentLoaded`，默认自动打开官方前端；
   - 全屏 iframe + 骨架屏（转圈 + 文案），首帧到达即撤骨架；
   - 右下角只有 ⟳ / ⤢ / ✕ 三个浮控，去掉原来那条抢占视觉的深色工具条；
   - 用户按 ✕ = 以后不再自动进（写 localStorage `dfy_dsh_entry=0`）；
     启动失败自动关的**不**记偏好，避免一次故障把功能永久关掉。

真机验收（独立 pywebview 窗口 + stub js_api，不动正在运行的 QQ/桌宠）：
窗口标题「大肥鱼AI · 入口验收」，`location.origin = http://127.0.0.1:42038`，
截图里直接是官方界面：左侧 `deepseek HARNESS`、新会话、插件、自动化任务、更多、
工作区 desktop / 默认工作区 / 新会话，主区「探索未至之境 预览版」。
静态资源（`ds图片/avatar.jpg`）经 http 取回正常（naturalWidth=960）。

另修一个既有隐患：`updateMoodWidget()` 里裸读 `window.pywebview.api`，
主程序还没连上时会抛 TypeError（file 起源下必现）。已加存在性判断。

#### S4 真机复验（2026-11-20 03:16 · 用户退出主界面后重启）

前面 02:50 那次是「独立 pywebview 窗口 + stub js_api」，属于旁证。这次是真的主程序，
一次把三件事全部实锤：

1. **http_server 生效**：主进程（PID 25820）在 `127.0.0.1:45111` 提供页面，
   `GET /大肥鱼AI.html` → 200 / 302047 字节，页面里 `dshUiFrame` 在 —— 说明页面起源是
   `http://127.0.0.1:45111` 而不是 `file://`（旧版本只会是 file://）。
2. **开机直进成立**：`PrintWindow` 抓主窗口（没抢前台，抓完即还原 topmost），
   窗口标题「大肥鱼AI · By Childe」，内容直接是官方前端：左侧 deepseek HARNESS / 新会话 /
   插件 / 自动化任务 / 更多 / 工作区 desktop·默认工作区·新会话·Greeting·安装 dsh-bili-whale 插件，
   主区「探索未至之境 预览版」，右下 ⟳ ⤢ ✕ 三个浮控，左下署名 Childe。
   截图：`D:\大肥鱼缓存\shot_main_pw.png`。
3. **iframe 真连上了市场**：`Get-NetTCPConnection -LocalPort 6811` 有两条 Established，
   客户端是 `msedgewebview2` PID 27104（主程序的渲染进程）；同时 `doctor probe` → 可用 / 11.28.5。

> 顺带记一个抓图教训：`CopyFromScreen` + `SetForegroundWindow` 在用户开着 UE 的情况下一律
> 抓到 UE（Windows 不允许后台进程抢前台）。改用 `PrintWindow(PW_RENDERFULLCONTENT=2)` 才稳，
> 而且完全不打扰用户正在用的窗口。

### S2 阶段实测记录（含两个此前没人知道的根因）

**根因 A：`.cmd` 垫片正文含中文路径 → cmd 解码失败 → 市场探针永远 false。**
现象：重启后 `/dsh-market/status` 的 `pnpm` 变 false，市场页重新弹出"安装软件前需要先配置 pnpm 环境"横幅；
但在 PowerShell 里跑同一个 `pnpm.cmd` 稳稳返回 11.28.5。
定位：直接调用市场的 `probePnpm()`，拿到 `failure: {kind:"failed", output:"系统找不到指定的路径。"}`。
原因：cmd.exe 读批处理正文按 OEM 代码页解码，正文里写 `D:\本地部署大肥鱼环境\...` 的 UTF-8 中文会变乱码，
cmd 找不到 node.exe；而 PowerShell 调用时控制台代码页恰好是 UTF-8，所以手工测试永远"看起来正常"。
修法：建目录联接 `D:\dfy-rt` → 运行时 dependencies 目录，垫片正文改成纯 ASCII
（`"D:\dfy-rt\node\bin\node.exe" "D:\dfy-rt\pnpm\bin\pnpm.cjs" %*`）。
doctor 新增两项断言：垫片正文必须全 ASCII、`cmd /d /s /c "pnpm --version"` 裸名解析必须通。
复测：`probePnpm()` → true；宿主 status → `pnpm:true`；横幅消失。

**根因 B：`virtualStoreDir` 必须字面对齐，写等价路径也报错。**
`dfyweb\node_modules` 是指向 `desktop\node_modules` 的目录联接，所以 `...\dfyweb\node_modules\.pnpm`
与 `...\desktop\node_modules\.pnpm` 是同一处。但 pnpm 按 **字面串** 比对 `.modules.yaml` 里记录的值，
写成 dfyweb 版本会抛 `ERR_PNPM_UNEXPECTED_VIRTUAL_STORE`（安装能过、卸载必挂）。
修法：`pnpm-workspace.yaml` 的 `virtualStoreDir`、`.modules.yaml`、doctor 期望三处统一写 desktop 物理路径。
doctor 的该项检查也从"比 realpath"改回"比字面串"，否则会把等价路径误判为正常。

**pnpm 11 对 12 建成的 node_modules 无破坏**：`pnpm install --frozen-lockfile` 返回
`Already up to date / Done in 366ms using pnpm v11.28.5`，14 个业务插件零丢失、零搬进 `.ignored`。

**"市场真的在用 11.28.5" 的直接证据**：UI 装 `dsh-tool-qrcode@1.0.0` 后，
`.modules.yaml` 的 `packageManager` 从 `pnpm@12.21.0` 被改写成 `pnpm@11.28.5`；
市场日志 `dsh-tool-qrcode@1.0.0 exit=0 hot=true`。

**四条链路实测**：
1. 安装（npm 源）：卡片「安装」→「确认安装」→ 24s 完成，16 个插件，零 console error。
2. 更新（npm 源）：`dsh-bili-pond 1.3.13 → 1.3.14`，日志 `update ... exit=0`，`updateAvailable` 变 false。
   注：官方插件详情页没有"更新"按钮，更新由市场 `POST /dsh-market/update {name}` 驱动。
3. 卸载：详情页「卸载 X」→ 模态框「卸载」→ 16→15，磁盘目录删除，`.ignored` 无新增。
4. 安装中取消：确认后 1.5s 点「取消」→ 日志 `exit=1 CANCELLED` / `cancelled`，
   package.json 与 node_modules 均无残留，doctor 全绿。
   （5s 才取消时已进 linking 阶段，会装完 —— 这是官方语义，不是缺陷。）

---

## S1 自愈层（守护 6 项）

新增 `D:\大肥鱼AI\dfy_dsh_doctor.py`，`check` / `fix` / `--json` 三种用法。

1. **代理 7897**：TCP 探活；不活则拉起 `C:\Program Files\Clash Verge\clash-verge.exe` 并等监听（≤40s）。
   理由：git 全局把 github.com 指向 7897，代理一死所有 GitHub 插件装不上。
2. **pnpm 主版本**：PATH 上的 pnpm 必须是运行时 11.28.5，不能是 12.x。
   实测：当前 PATH 解析到 12.21.0（豆包沙箱自带），而 profile 的 node_modules 由 11.28.5 建成；pnpm 主版本漂移会导致 modules 语义变化与 lockfile 重写。
   修法：生成 `D:\大肥鱼AI\dsh_host\bin\pnpm.cmd` 指向运行时 node+pnpm.cjs，并在 `dfy_dshui.py` 启动子进程时把该目录前置到 PATH。
3. **store 一致性**：profile 配置里的 storeDir、`pnpm store path` 实回、`.modules.yaml` 记录三者一致。
4. **allowBuilds 对齐**：扫 lockfile 里全部 `gitHosted` 且带 40 位 commit 的条目，逐个确认在 `pnpm-workspace.yaml` 的 allowBuilds 里；缺则自动补。
   理由：这次就是 nekowhale-stickers 升到 1.1.2、commit 变了，旧白名单失效。
5. **插件目录完整性**：14 个业务插件目录齐全；缺失时优先从 `node_modules/.ignored` 移回。
6. **端口归属**：6800 必须是命令行含 `host.mjs` 的 node，6811 必须是含 `dfyweb` 的 node；不匹配只报告不杀进程。

验收：故意制造 5 种故障（关 Clash、改 allowBuilds、移走插件、篡改 storeDir、改 PATH pnpm），跑 `fix` 后 `check` 必须全绿。

## S2 抗漂移

- 在 DSH 子进程环境里把 pnpm 锁死到 11.28.5（PATH 前置 + `PNPM_HOME`），验证市场实际调用的是 11.28.5。
- 四条链路脚本：安装（npm 源 + GitHub 源各一）、更新、卸载、安装中取消。
- 每条都要检查副作用：业务插件是否被搬进 `.ignored`、`.modules.yaml` 是否被重写坏。

## S3 统一清单

- 单一真源：`profiles\desktop\package.json` 为插件清单唯一来源。
- `dsh_host\host-plugins.json` 改为由 desktop/package.json 自动派生（含 entryOverrides 保留）。
- dfyweb package.json 的 dependencies 与之对齐（`dfy_dshui.py` 的 `_sync_back` 已做单向合并，改为双向一致性校验）。
- 验收：UI 装 → 宿主重启加载 → UI 禁用 → 宿主不加载 → UI 卸载 → 宿主不加载。

## S4 宿主入口 1:1

- 大肥鱼主界面默认进官方前端（iframe 6811，token 复用、断线重连、加载骨架）。
- 自研插件中心降级为备用（离线/DSH 起不来时兜底）。
- 视觉 1:1 校验：首页、插件页三 tab、市场卡片、安装确认框、安装进度动画、任务面板。
- 验收：零 console error，交互脚本覆盖一轮完整安装。

## S5 全功能回归矩阵（12 项）

插件页渲染 / 内置插件配置四项 / 实验性开关四项 / 市场搜索 / 市场分类 / 兼容性检查 / 安装 / 卸载 / 启用禁用持久化 / 更新检查 / 日志导出 / 侧栏入口。
每项独立脚本 + 汇总 `report.md`。

## S6 打包与文档

- `启动大肥鱼.bat` 里加 doctor 预检（失败自动修再启动）。
- README 补：代理依赖、pnpm 锁定、自愈用法、排障。
- CHANGELOG 记录本轮所有改动与回滚点。

## S7 值守

- 每 2 小时自跑 S5 回归，失败自动 `doctor fix` 后重试，仍失败记入告警。
- 中午出总验收报告。

---

---

# 长跑方案：2026-11-20 02:00 → 2026-11-21 12:00（干到明天中午）

原则：**每一段都以「可复现的自动化闸门」结尾，闸门不全绿就不许进下一段**；
不许出现"改完看着像好了"的段落，所有结论必须有脚本输出或截图佐证。

## 已完成（截至 02:00）

| 段 | 状态 | 证据 |
|---|---|---|
| S1 自愈层 | ✅ | `doctor_inject_test.py` 5 项注入 → fix → 全绿，退出码 0 |
| S2 抗漂移 | ✅ | 四条链路真实 UI 全绿；`.modules.yaml` 记 `pnpm@11.28.5` |
| S3 统一清单 | ✅ | 装 17 / 禁 16 / 启 17 / 卸 16 往返全绿 |
| S4 宿主入口 1:1 | ✅ | 真机 pywebview 截图内即官方前端，origin=`http://127.0.0.1:42038` |
| **S5 回归矩阵** | ✅ 13/13 | `D:\大肥鱼缓存\regress_result.json`（2026-11-20 02:00 实跑） |
| S6 打包与文档 | ✅ 02:10 | 启动脚本接入 doctor 预检（OK/FAIL 两态实测，退出码 0）；runbook + 打包清单落盘，30 条路径全通检 |
| S7 探针与清单收口 | ✅ 02:19 | `doctor probe` 直接调市场 probePnpm；open_ui 拉起即验、失败自动重启复探（两态实测通过）；sync 改只读校验；qrcode 经查是 dsh-bili-whale 真依赖，**未删** |
| S8 入口 e2e | ✅ 02:22 | 真实窗口 7 项断言全过；截图里就是官方 deepseek HARNESS 界面（`shot_entry_e2e.png`） |
| S9 值守 + 冷启动 | ✅ 02:30 | 看门狗连跑 3 轮全绿；冷启动（脏 DSH_HOME + 坏垫片 + 停 6811）一次拉起成功，宿主 16 插件 |
| S10 长稳轮巡 | ⏳ 进行中 | cron 每 2 小时一轮，止于 11-21 10:00；首轮 341s 全绿，日志 `docs\dsh-regression-log.md` |

S5 修的四项时序问题（记录备查）：
1. 03/04 内置插件配置区内是**懒渲染**，必须先 `reload` → 进「已安装」tab → 滚 4×600px 再读 innerText；
2. 09 卸载前必须 `reload` + 4.5s 等列表回填，并先断言 `查看 X` 存在（否则 30s 空等）；
3. 12 导出日志按钮只在「插件市场」tab 内，先切 tab 再找；
4. 07 的 `body.includes('不兼容') || ...` 原写法括号不闭合，改为先算 `okCompat`。
另：13 号项加了非 2xx 响应采集（`badRes`），本轮 `[]`，此前一次的 400 是瞬时探测请求，已不复现。

## 剩余段落（按序执行，每段带闸门）

### S6 打包与文档 ✅（02:00–02:10）
- `start.ps1` 前置 doctor `check --safe`，不绿则 `fix --safe` 后复检，仍不绿只记日志继续启动（不阻断用户）。
  另加 `DFY_DOCTOR_ONLY=1` 开关：只体检不拉主程序，供自动化/排障用。
- 新增 `D:\大肥鱼AI\docs\dsh-runbook.md`：三进程分工、四条硬约束、故障速查表、自愈用法、回滚点、官方语义边界。
- 打包清单 `docs\dsh-package-manifest.md`：必须随包的文件 + `D:\dfy-rt` 联接重建命令 + 发版自检四步。
- **闸门实测**：把 `pnpm.cmd` 改名（模拟缺失）→ 冷跑 `start.ps1`（doctor-only）→ 日志出现
  `check=FAIL → [FIX] 垫片已重建 → 复检 rc=0`，退出码 0；垫片恢复为 11.28.5。文档 30 条路径全存在。

### S7 探针与清单收口 ✅（02:10–02:19）
- 新增 `dsh_host\probe_pnpm.mjs`：直接 import `dshmarket/lib/dsh-cli.js` 调 `probePnpm()`，
  带出 `lastPnpmProbeFailure()` 的 `kind/output`（"系统找不到指定的路径"这种话只有市场自己会给）。
- doctor 新增 `probe` 子命令（`--json`、`--fix`）。`--fix` 有个坑：`"fix" in args` 认不出 `--fix`，
  已改成 `("fix" in args) or ("--fix" in args)`，否则"自动修"其实没修。
- `dfy_dshui` 拆出 `_start_once / _stop_own / _probe_market`，open_ui 两条路径都验探针：
  复用到的实例探针已死 → 停掉重起 → 复探；新起后探针 false → 再重启一次再探。
- `dfy_market.sync_host_manifest` 退回**只读校验**（loaded / disabledInUi / missing），不再写
  `host-plugins.json` —— 否则界面禁用只动 bundles，JSON 里却还留着，宿主照旧加载，显示与行为相反。
- `desktop\node_modules\qrcode`：查证是 `dsh-bili-whale` 的 dependencies（`1.5.4`），
  且在 `.modules.yaml` / lockfile 里都有登记 —— **不是**手动 pnpm 残留，删了会坏事，保留。
- **闸门实测**：垫片改坏 → `probe` 返回 `ok=false, kind=failed, output=系统找不到指定的路径`；
  `probe --fix` → 重建后复探 true。复用分支：手起一个探针必挂的 6811 → `open_ui` 自动重启并复探 true。
  改完重跑 S5 → **13/13 不退化**（脚本还加了 token 自动刷新，避免旧 token 导致"全红假故障"）。

### S8 入口端到端自动化验收 ✅（02:19–02:22）
- `dsh_entry_e2e.py`：真实 html + `http_server=True` + 真 js_api（只暴露 `dsh_ui_open/close`，其余 stub），
  起独立 pywebview 窗口；`webview.start(worker, (win,))` 让断言跑在 GUI 线程，跑完 `window.destroy()`。
- 7 项断言：origin 是 http、iframe src 是 6811 token 地址、启动遮罩已撤、浮控 3 个、
  按 ⟳ 后 iframe 仍在、按 ✕ 后 localStorage 记 0、截图存证。
- 两处**顺序坑**（都是自己写错、不是产品问题）：
  1) 截图原本在按 ✕ 之后 → 拍到的是启动页；改到 ✕ 之前；
  2) 判「骨架已撤」用 `offsetWidth`，会把「正在启动 DSH 原生界面…」误判成已撤；
     改用 `getComputedStyle(#dshUiBoot).display === 'none'`，再补 2 秒稳定期。
- **闸门实测**：`verdict = OK`，截图 `shot_entry_e2e.png` 里窗口内就是官方界面
  （deepseek HARNESS / 新会话 / 插件 / 自动化任务 / 工作区 / Childe / 右下 ⟳ ⤢ ✕）。
- 另出 `dsh_after_restart_check.py`：doctor → open_ui+探针 → e2e 三步串跑，真机重启后一键自检（已实跑全过）。

### S9 值守守护 + 冷启动 ✅（02:22–02:30）
- `dfy_dsh_watchdog.py`：每轮查 6800 端口与 `/health` 插件数、6811、doctor 六项；
  异常自动 `doctor fix`；6811 挂了调 `open_ui` 拉起。**6800 不抢着重启**（两个 host 抢端口会把加载搞乱），只记录。
  日志 `logs\dsh-watchdog-YYYYMMDD.ndjson`，自动清 7 天前。
- 冷启动模拟 `_t_cold_start.py`：先 `DSH_HOME=E:\DSH-home`（本机历史脏值）+ 摘掉 `PNPM_HOME` + 停 6811
  + 垫片写成中文路径，然后 `open_ui()` 一次拉起。
- **闸门实测**：看门狗连跑 3 轮全绿（宿主 16 插件）；冷启动 `ok=true, marketPnpm=true`，
  宿主在载 16 个插件；随后一轮完整回归 **13/13（341s）**。

### S10 长稳轮巡（10:00 → 次日 10:00）
- 每 2 小时自动跑一次 S5 全量回归，结果追加 `docs\dsh-regression-log.md`；
  任何 FAIL 自动 `doctor fix` 后重跑一次，仍 FAIL 立即定位修复并在日志记「已修 / 待修」。
- **闸门**：连续 6 轮（12h）全绿且 `badRes` 持续为空。

### S11 中午总验收（次日 10:00–12:00）
- 出 `docs\dsh-1to1-acceptance.md`：13/13 回归 + doctor 注入复验 + 四条链路复验 +
  入口 e2e 截图 + watchdog 日志摘要 + 已知边界（官方语义 5s 取消会装完等）。
- 打包产物复核 + 交付清单（可回滚点全列）。
- **闸门**：报告内每个数字都能追到一次脚本实跑输出。

## 回滚点（全部可逆）

- dfyweb 三个配置文件：`pnpm-workspace.yaml` / `.npmrc` / `pnpm-lock.yaml`（新建，删掉即回原状）。
- desktop 的 `.modules.yaml` 有 `.bak-vsdir-*` 备份。
- 若 S3 改 host.mjs 出现回归：`host-plugins.json` 与 `host.mjs` 各留 `.bak-pre1to1`。
