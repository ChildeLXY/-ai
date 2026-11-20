> **已废弃（2026-11-20）**：本文描述的「iframe 套官方独立进程 + 开机直进」形态已被用户否定，外部 6811 进程与轮巡 cron 已停用。当前有效架构与验收见 `dsh-builtin-acceptance.md`。以下内容仅作历史留档。

# 大肥鱼 AI · DSH 1:1 宿主总验收报告

> 状态：**进行中**（2026-11-20 03:30 起草；S10 长稳轮巡仍在跑，11-21 10:00 收口后补最后一节）
> 目标：拉起宿主即 **1:1 官方彩色 DSH 前端**，且「插件市场 + 插件管理」100% 功能可用。

## 一、结论（截至 03:30）

| 维度 | 结论 | 证据 |
|---|---|---|
| 入口 1:1 | ✅ 主窗口开机直接是官方前端（非 iframe 套壳的自绘界面） | `shot_main_pw.png` + 主进程 45111 提供页面 |
| 入口起源 | ✅ `http://127.0.0.1:45111`，不是 `file://`（后者必被 DSH 栅栏拒） | `GET /大肥鱼AI.html` → 200 / 302047B |
| iframe 连通 | ✅ 6811 上两条 Established，客户端是主程序 WebView2（PID 27104） | `Get-NetTCPConnection` |
| 市场 pnpm | ✅ 市场自己的 `probePnpm()` 返回 ok，版本 11.28.5 | `doctor probe` |
| 四类链路 | ✅ 安装 / 更新 / 卸载 / 安装中取消 全部真实 UI 通过 | 见下表 |
| 回归矩阵 | ✅ 13/13（单轮 341s） | `docs\dsh-regression.md`、`docs\dsh-regression-log.md` |
| 自愈 | ✅ 六项体检 + 5 项故障注入 → fix → 全绿 | `doctor_inject_test.py` 退出码 0 |
| 冷启动 | ✅ 脏 DSH_HOME + 坏垫片 + 停 6811，一次拉起成功 | `_t_cold_start.py`，宿主 16 插件 |
| 值守 | ✅ 看门狗连跑 3 轮全绿；cron 每 2h 轮巡 | `logs\dsh-watchdog-*.ndjson` |

## 二、回归矩阵明细（13 项）

| # | 项目 | 结果 | 关键观察 |
|---|---|---|---|
| 01 | 首页渲染 | ✅ | 新会话 / 插件 / 自动化任务 / 工作区 / Greeting 齐全 |
| 02 | 插件页渲染 | ✅ | 「已安装」「插件市场」两 tab 都在 |
| 03 | 内置插件配置 | ✅ | 子智能体 / 网页搜索 / Agent 循环 / 终端 4/4（需滚动触发懒渲染） |
| 04 | 实验性开关 | ✅ | switch 19 个，实验项 4/4 |
| 05 | 市场搜索 | ✅ | 搜索「qrcode」出 2 个安装按钮且命中插件名 |
| 06 | 市场分类 | ✅ | 切换分类按钮数 65→62，列表真的变了 |
| 07 | 兼容性检查 | ✅ | 页面含 DSH >= 版本文案 |
| 08 | 安装 | ✅ | 两步确认后插件总数 15→16 |
| 09 | 卸载 | ✅ | 详情页 → 模态框「卸载」→ 16→15，磁盘无残留 |
| 10 | 启用禁用持久化 | ✅ | 关→开，reload 后仍可切换（bundles 与宿主同步） |
| 11 | 更新检查 | ✅ | `GET /dsh-market/updates` status=200 |
| 12 | 日志导出 | ✅ | 触发下载 `dsh-market-log.txt` |
| 13 | 零 console error | ✅ | 0 条，非 2xx 响应 `[]` |

## 三、四条链路（真实 UI，非接口硬调）

| 链路 | 做法 | 结果 | 副作用检查 |
|---|---|---|---|
| 安装 | 卡片「安装」→「确认安装」 | 成功，16 插件，~24s | `.modules.yaml` packageManager 被写成 pnpm@11.28.5；业务插件零丢失 |
| 更新 | `POST /dsh-market/update {name}`（官方详情页**没有**更新按钮） | `dsh-bili-pond 1.3.13→1.3.14`，日志 exit=0 | `updateAvailable` 变 false |
| 卸载 | 详情页「卸载 X」→ 模态框「卸载」 | 16→15，目录删除 | `.ignored` 无新增 |
| 安装中取消 | 确认后 1.5s 点「取消」 | `exit=1 CANCELLED`，无残留 | package.json / node_modules 干净 |

## 四、三个根因（这部分是「以后不会再踩」的价值所在）

1. **`.cmd` 垫片正文含中文 → cmd 解码失败 → 市场探针永远 false**
   cmd.exe 按 OEM 代码页读批处理正文，中文路径变乱码 → 找不到 node.exe。
   PowerShell 里手工测永远「正常」，因为控制台代码页恰好是 UTF-8。
   修法：目录联接 `D:\dfy-rt` + 纯 ASCII 垫片；doctor 增加「垫片必须全 ASCII」「cmd 裸名解析必须通」两条断言。

2. **`virtualStoreDir` 必须字面对齐**
   `dfyweb\node_modules` 与 `desktop\node_modules` 用目录联接，物理同处，但 pnpm 按字面串比对
   `.modules.yaml`。写等价路径会抛 `ERR_PNPM_UNEXPECTED_VIRTUAL_STORE`（装能过、卸必挂）。

3. **`file://` 起源被 DSH 入口栅栏直接拒**
   同 token 三种加载方式实测：顶层开 ✅、http 起源 iframe ✅、file 起源 iframe ❌（Origin=null）。
   所以 pywebview 必须 `http_server=True` 且 `create_window` 传路径不拼 `file://`。

## 五、已知边界（**不是缺陷，别去"修"**）

1. 官方插件详情页没有「更新」按钮，更新只能走 `POST /dsh-market/update`。
2. `dsh web` 没有关闭鉴权的开关（`--help` 只有 host/port/public-url/trusted-host/tls）。
3. 安装确认后 **5 秒**再点取消会装完（已进 linking 阶段）；1.5s 内取消则干净回滚。
4. 从 pywebview 页面 `fetch(6811)` 会被 CORS 拦，`Failed to fetch` **不能**当鉴权判据。
5. 市场探针结果按**进程常驻缓存**：首探失败只能重启 6811（已做成 `open_ui` 自动重启复探）。
6. `desktop\node_modules\qrcode` 是 `dsh-bili-whale` 的依赖（1.5.4），**不是**残留，不可删。

## 六、回滚点

| 改动 | 回滚 |
|---|---|
| `dsh_host\host.mjs`（bundles 驱动） | `host.mjs.bak-pre1to1` |
| `dsh_host\host-plugins.json` | `host-plugins.json.bak-pre1to1` |
| dfyweb 三个新配置 | 删掉即回原状 |
| `desktop\node_modules\.modules.yaml` | `D:\大肥鱼缓存\_pnpm11_migrate\.modules.yaml.bak` |
| `大肥鱼AI.py` 的 create_window | 去掉 `http_server=True`、路径改回 `file://`（入口会回到鉴权失败） |
| `start.ps1` 的 doctor 预检 | 删掉预检块即可（不影响启动） |

## 七、S10 长稳轮巡（待收口）

每 2 小时一轮，止于 2026-11-21 10:00。轮次明细见 `docs\dsh-regression-log.md`。

| 轮次 | 时间 | 结果 | 耗时 |
|---|---|---|---|
| 1 | 2026-11-20 02:30 | 13/13 ✅ | 341s |
| 2–N | 04:00 起 | 待填 | — |

## 八、交付清单（文件级）

代码/配置：`dfy_dsh_doctor.py`、`dfy_dshui.py`、`dfy_dsh_watchdog.py`、`dfy_regress_round.py`、
`dsh_after_restart_check.py`、`dsh_host\host.mjs`、`dsh_host\probe_pnpm.mjs`、
`dsh_host\bin\pnpm.cmd|.ps1`、`start.ps1`、`大肥鱼AI.py`、`大肥鱼AI.html`。

文档：`docs\dsh-1to1-plan.md`（全过程）、`docs\dsh-runbook.md`（运维）、
`docs\dsh-package-manifest.md`（发版）、`docs\dsh-regression.md`（单轮报告）、
`docs\dsh-regression-log.md`（轮巡日志）、本文件。

证据：`D:\大肥鱼缓存\shot_main_pw.png`（真机主窗口）、`shot_entry_e2e.png`（e2e 窗口）、
`shot_entry_win.png`、`regress_*.png`、`entry_e2e.json`、`after_restart_check.json`、
`regress_result.json`。

脚本：`doctor_inject_test.py`、`pw_regress_all.mjs`、`dsh_entry_e2e.py`、`_t_openui2.py`、
`_t_probe_restart.py`、`_t_cold_start.py`、`_t_sync_checkonly.py`、`check_doc_paths.py`。
