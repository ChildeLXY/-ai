# DSH 1:1 宿主 —— 运行手册（Runbook）

> 适用：大肥鱼AI 内嵌官方 DSH 前端（插件市场 + 插件管理 100% 接管）。
> 最后更新：2026-11-20

## 1. 三个进程的分工

| 端口 | 进程 | 职责 | 挂了会怎样 |
|---|---|---|---|
| 6811 | `dsh web`（profile=dfyweb） | **官方彩色前端 + 市场接口**（真正在装插件的那个） | 大肥鱼里 DSH 区域白屏、市场点安装无反应 |
| 6800 | `dsh_host\host.mjs` | 兼容宿主，把 profile 的插件加载成大肥鱼自己的工具 | 插件在界面里"已安装"但主程序用不到 |
| 8848 | `web_client.py` | 手机/平板浏览器访问大肥鱼 | 只影响移动端 |

三者独立，任意一个挂了不影响另外两个。前端只认 6811，主程序只认 6800。

## 2. 启动顺序（已自动化，不必手动）

双击 `大肥鱼AI.bat` → `start.ps1`：

1. 拉 ollama（已在跑就跳过）
2. **DSH 自愈预检**：`dfy_dsh_doctor.py check --safe` → 不绿则 `fix --safe` → 复检，
   结果写 `logs\dsh-doctor.log`。**修不动不阻断启动**，主程序照常起来。
3. 拉起 `大肥鱼AI.py`（pywebview 6.2.1，`http_server=True`，页面发到 `http://127.0.0.1:<随机端口>`）
4. 起桌宠 / 碎碎念 / 情绪守望 / web_client / （可选）QQ

DSH 本身不在这里起：主界面加载到「DSH 原生」时会调 `dfy_dshui.open_ui()`，
由它 `_doctor_fix()` → `_child_env()` → 起 6811。

## 3. 四条硬约束（改错任何一条，市场就废）

### 3.1 代理必须在 7897
git 全局把 `github.com` 指到 7897（Clash）。代理死了，所有 GitHub 源插件装不上，
表现是市场一直转圈或者 `ECONNREFUSED`。

### 3.2 pnpm 必须是 11.28.5，不能是 12.x
PATH 上默认解析到 12.21.0（豆包沙箱自带）。profile 的 `node_modules` 是 11.28.5 建的，
主版本漂移会改写 lockfile 语义、把插件搬进 `.ignored`。
`dsh_host\bin\pnpm.cmd|.ps1` 这两个垫片负责把 11.28.5 钉在 PATH 最前。

### 3.3 垫片正文必须**全 ASCII**
`cmd.exe` 读批处理按 OEM 代码页解码，正文里只要出现中文路径（`D:\本地部署大肥鱼环境\...`）
就变乱码 → 找不到 node.exe → 市场探针永远 false → 界面永远显示
"安装软件前需要先配置 pnpm 环境"横幅。
所以垫片里只能写 `D:\dfy-rt\...`（目录联接），**不要**改回中文绝对路径。
（8.3 短路径在本机被禁用，`%~sI` 拿到的还是长路径，绕不过去，只能靠联接。）

### 3.4 `virtualStoreDir` 必须**字面对齐**
`dfyweb\node_modules` 是指向 `desktop\node_modules` 的目录联接，两处 `.pnpm` 物理同一，
但 pnpm 按**字面串**比对 `.modules.yaml`。写成 `dfyweb` 版本会抛
`ERR_PNPM_UNEXPECTED_VIRTUAL_STORE`（安装能过、卸载必挂）。
三处必须统一写 desktop 物理路径：
`profiles\dfyweb\pnpm-workspace.yaml`、`profiles\desktop\node_modules\.modules.yaml`、doctor 的期望值。

## 4. 故障速查

| 症状 | 判据 | 处置 |
|---|---|---|
| 市场页出现"需要先配置 pnpm 环境" | `doctor check` 的 pnpm 项 FAIL；或市场 `probePnpm()` 返回 `failure.kind=failed` | `python dfy_dsh_doctor.py fix`（会重建 ASCII 垫片） |
| 安装能过、一卸载就报错 | `.modules.yaml` 的 `virtualStoreDir` 与 workspace 不一致 | 改回 desktop 物理路径（doctor 会报 store/modules 项） |
| git 源插件全装不上 | 7897 没监听 | 开 Clash；`doctor fix`（非 `--safe`）会尝试拉起 |
| 界面已安装，主程序里没有 | 6800 的 `/health` plugins 数量与界面不符 | 查 `host-plugins.json.disabled`；bundles 是唯一真源 |
| DSH 区域显示 `authentication required` | 页面起源是 `file://` | 确认 pywebview 用 `http_server=True` 且 `create_window` 传的是路径不是 `file://` |
| 装机后界面查无此插件 | profile `package.json` 的 `dsh.profile.bundles` 没写进去 | 重装一次；或手工把它加进 bundles 列表 |
| 市场探针重启前一直 false | 探针结果**按进程常驻缓存** | 重启 6811（`dfy_dshui.close_ui(); open_ui()`） |

## 5. 自愈脚本用法

```powershell
$py = "D:\本地部署大肥鱼环境\venv312\Scripts\python.exe"
$doc = "D:\大肥鱼AI\dfy_dsh_doctor.py"

& $py $doc check              # 六项体检，人读
& $py $doc check --json       # 机器读（自动化/看门狗用）
& $py $doc fix                # 修，包含尝试拉起 Clash
& $py $doc fix --safe         # 只修配置，不启动任何外部程序（启动脚本用这个）
```

六项：`proxy` / `pnpm` / `store` / `allowbuilds` / `modules` / `ports`。
`ports` 只报告归属、**绝不杀进程**。

只跑预检不拉主程序（排障用）：

```powershell
$env:DFY_DOCTOR_ONLY = "1"; pwsh -NoProfile -File "D:\大肥鱼AI\start.ps1"
```

退出码：`0` 全绿；`1` 仍有未修项（详细原因在 `logs\dsh-doctor.log`）。

## 6. 日志位置

| 日志 | 内容 |
|---|---|
| `logs\dsh-doctor.log` | 每次启动的六项体检结果，含 fix 前后对比（>512KB 自动截到最近 2000 行） |
| `profiles\dfyweb\.dsh-market\log.ndjson` | 市场每一次安装/更新/卸载的 pnpm 调用与退出码 |
| `profiles\dfyweb\.plugin-manager\logs\operation-*\pnpm.log` | 插件管理器的底层 pnpm 输出 |
| `D:\大肥鱼缓存\_dshui.log` | 6811 的启动输出（token 从里面取） |

## 7. 回滚点

| 改动 | 回滚方式 |
|---|---|
| `dsh_host\host.mjs`（bundles 驱动） | 同目录 `host.mjs.bak-pre1to1` |
| `dsh_host\host-plugins.json` | 同目录 `host-plugins.json.bak-pre1to1` |
| dfyweb 三个新配置（`pnpm-workspace.yaml` / `.npmrc` / `pnpm-lock.yaml`） | 直接删掉即回原状 |
| `desktop\node_modules\.modules.yaml` | `D:\大肥鱼缓存\_pnpm11_migrate\.modules.yaml.bak` |
| `大肥鱼AI.py` 的 `create_window` / `webview.start` | 去掉 `http_server=True`、路径改回 `f"file:///{html_path}"`（但入口会回到鉴权失败） |

## 8. 已验证的官方语义边界（不是缺陷，别去"修"）

1. **插件详情页没有"更新"按钮**：更新只能由 `POST /dsh-market/update {name}` 驱动。
2. **`dsh web` 没有关闭鉴权的开关**：`--help` 只有 host/port/public-url/trusted-host/tls，
   所以只能靠"http 起源 + 同站"进 iframe，`file://` 一律被拒。
3. **安装确认后 5s 再点取消会装完**：那时已进入 linking 阶段，取消只对 resolving/downloading 有效。
   1.5s 内取消则干净回滚（`exit=1 CANCELLED`，package.json 与 node_modules 无残留）。
4. **`fetch(6811)` 从 pywebview 页面发起会被 CORS 拦**，不能拿 `Failed to fetch` 当鉴权判据。

## 9. 验收现状

- 回归矩阵 13/13 全绿（`docs\dsh-regression.md`）。
- 故障注入 5 项 → fix → 全绿（`D:\大肥鱼缓存\doctor_inject_test.py`）。
- 入口真机截图：官方前端直接渲染在 pywebview 窗口内（`D:\大肥鱼缓存\shot_entry_win.png`）。
