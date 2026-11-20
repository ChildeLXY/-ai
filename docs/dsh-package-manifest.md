# DSH 1:1 宿主 —— 打包分发清单

打包/移植时**逐项核对**，缺任何一项都会让插件市场在目标机上装不上，
而且症状通常是"界面报错"而不是"文件缺失"，极难排查。

## 必须随包的运行期文件

| 路径 | 作用 | 缺失后的症状 |
|---|---|---|
| `大肥鱼AI.py` | 主程序，含 `create_window`（`http_server=True` + 传路径） | DSH 区域永远 `authentication required` |
| `大肥鱼AI.html` | 主界面 + DSH 入口（骨架屏/浮控/开机直进偏好） | 进不去官方前端 |
| `dfy_dshui.py` | 6811 桥：`_doctor_fix` / `_child_env` / `open_ui` / `close_ui` | 「DSH 原生」按钮无效 |
| `dfy_dsh_doctor.py` | 六项自愈体检 | 启动预检静默跳过，故障只能靠人工 |
| `dsh_host\host.mjs` | 兼容宿主（bundles 驱动加载） | 主程序用不到任何 DSH 插件 |
| `dsh_host\host-plugins.json` | 手工基础名单 + `disabled` + `entryOverrides` | 部分插件加载不到（尤其 hindsight 需 `dist/dsh.js`） |
| `dsh_host\bin\pnpm.cmd` | pnpm 11.28.5 垫片，**正文必须全 ASCII** | 市场永远显示"需要先配置 pnpm 环境" |
| `dsh_host\bin\pnpm.ps1` | 同上（PowerShell 侧） | PowerShell 手工调用时版本漂移 |
| `dsh_web\` | 独立 DSH Web（`node_modules\@deepseek-ai\dsh\lib\bin.js`） | 6811 起不来 |

## 必须随包的运行期目录联接（不是普通文件夹，重装/拷贝后要重建）

```
D:\dfy-rt  →  D:\本地部署大肥鱼环境\dsh\dsh-runtimes\dsh-primary-runtime\dependencies
```

建法（PowerShell，管理员）：

```powershell
New-Item -ItemType Junction -Path "D:\dfy-rt" -Target "D:\本地部署大肥鱼环境\dsh\dsh-runtimes\dsh-primary-runtime\dependencies"
```

**为什么不能直接写中文路径**：垫片正文里出现中文，cmd.exe 按 OEM 代码页解码成乱码，
找不到 node.exe，市场探针永远 false。8.3 短路径在本机被禁用，`%~sI` 绕不过去。

## 目标机必须存在的运行时

| 组件 | 版本要求 | 说明 |
|---|---|---|
| node | 24.21.0 | `...\dsh-primary-runtime\dependencies\node\bin\node.exe` |
| pnpm | **11.28.5（不能 12.x）** | `...\dependencies\pnpm\bin\pnpm.cjs` |
| pnpm store | 单一位置 | 默认 `C:\Users\<用户>\AppData\Local\pnpm\store\v11`，**不要**指向 `D:\.pnpm-store` |
| Clash / 代理 | 7897 监听 | git 全局把 github.com 指到它 |
| python | 3.12（venv312） | 主界面用它；3.14 + pythonnet 会崩窗口 |

## 必须随包的用户配置（`D:\本地部署大肥鱼环境\dsh\profiles\`）

| 文件 | 关键字段 |
|---|---|
| `dfyweb\package.json` | `dsh.profile.bundles`（**唯一真源**，安装/启用会写这里） |
| `dfyweb\pnpm-workspace.yaml` | `storeDir`=C 盘 store、`packageImportMethod: copy`、`virtualStoreDir`=**desktop 物理路径**、`nodeLinker: hoisted`、`allowBuilds` 里 nekowhale 两条 commit |
| `dfyweb\.npmrc` | `store-dir` + `package-import-method` + proxy 7897 |
| `dfyweb\pnpm-lock.yaml` | 从 desktop 复制，保证首装不重解 |
| `desktop\node_modules\.modules.yaml` | `packageManager: pnpm@11.28.5`、`virtualStoreDir`=desktop 物理路径（**字面串**） |

## 打包后自检（必须全过再发）

```powershell
$py = "D:\本地部署大肥鱼环境\venv312\Scripts\python.exe"
& $py "D:\大肥鱼AI\dfy_dsh_doctor.py" check                  # 六项全 OK
& $py "D:\大肥鱼AI\dfy_dsh_doctor.py" fix --safe             # 再来一次仍全 OK
Test-Path "D:\大肥鱼AI\dsh_host\bin\pnpm.cmd"                # True
& "D:\大肥鱼AI\dsh_host\bin\pnpm.cmd" --version              # 11.28.5
& $env:DFY_DOCTOR_ONLY="1"; pwsh -NoProfile -File "D:\大肥鱼AI\start.ps1"   # 退出码 0
```

## 不要打进包的东西

- `D:\大肥鱼缓存\`（全部是调试脚本与截图，含 token URL）
- `profiles\*\node_modules\.ignored`（历史误搬的残留）
- `desktop\node_modules\qrcode`（手动 `pnpm add` 的副产物，不是 bundles 依赖）
- `.dsh-market\log.ndjson`（含本机路径，且会误导排障）
