# 大肥鱼AI 内置 Agent 引擎（引擎化交付）

> 目标：别人在自己电脑上装上大肥鱼，就能直接用插件市场的一万多个插件，
> **不需要那台电脑预先装过 DSH**。

## 一句话架构

```
大肥鱼AI（Python 主程序 + 自己的 UI）
   └── engine/            ← 随产品分发的完整 Agent 引擎（基于 DeepSeek Harness，MIT）
         ├── DaFeiyuEngine.exe          主程序（原可执行文件已改名）
         ├── resources/app.asar         引擎本体（插件容器 cordis、加载器、工具链）
         ├── resources/app.asar.unpacked/dsh/node_modules   未压缩的运行时依赖
         ├── resources/runtime/         自带 node 24 / pnpm 11 / python 3.12（不依赖系统环境）
         └── THIRD_PARTY_LICENSES/      第三方许可清单（620 个组件，已扫描留存）
```

数据目录与程序目录分离：

| 用途 | 位置 | 说明 |
| --- | --- | --- |
| 引擎程序 | `<程序目录>/engine`（本机为 `D:\本地部署大肥鱼环境\engine`） | 只读，随产品分发 |
| 引擎数据 | `DSH_HOME`（默认 `<数据根>/dsh`） | profiles、插件、会话、凭证 |

## 为什么这么做（而不是自己写插件加载器）

实测数据：从市场 11706 个插件里分层抽样 64 个、下载真实 tarball 解析依赖，结果是

| 指标 | 数值 |
| --- | --- |
| 自建无头兼容宿主兼容率 | **15.5%**（58 个可解析里只有 9 个能用） |
| 依赖 UI 插槽 `slots` 的比例 | **55%**（32/58） |
| 其他缺口 | locale 33%、remote 21%、fiber 17%、configForms 14% |

**过半插件是"界面插件"而不是"逻辑插件"**，它们要往 DSH 的侧栏/面板/按钮插槽里塞东西。
无头宿主把它们装上也是死的。所以自建加载器的上限就在 30–40%，永远到不了 100%。

结论：要"一万个插件真能用"，只能让插件跑在**原生的 cordis 运行时**里 ——
也就是把引擎本身带上，而不是复刻它。

## 合规边界（这是"不抄袭"的关键）

DSH 以 **MIT** 为主（少量 Apache-2.0），允许修改再分发、允许商用。
MIT 的硬义务只有一条：**保留版权声明与许可文本**。

已做的事：

1. **保留并如实披露**：`THIRD_PARTY_LICENSES/` 下 620 个组件的完整清单
   （MIT 517 / Apache-2.0 53 / BSD 3-Clause 15 / ISC 15 / MPL-2.0 2 / 其他若干），
   含 `DSH-LICENSE.txt`（`Copyright (c) 2026 DeepSeek` 原文）。
2. **不声称内核为原创**：`NOTICE.md` 明确写「引擎基于开源 DeepSeek Harness 构建」。
3. **外壳与品牌换成自己的**：主程序改名、CLI 启动器同步修补、数据目录重定向；
   前端不用它的界面，用大肥鱼自己的 UI。
4. **裁掉会干扰产品的东西**：官方卸载器、`app-update.yml`（自动更新器，留着会去拉官方更新）。

保留的 `@deepseek-ai/*` 是 **npm 包命名空间**，不能改 —— 改了插件依赖解析全断。

### 特殊许可组件的处理

| 组件 | 许可 | 处理 |
| --- | --- | --- |
| `@deepseek-ai/libreoffice-kit` ×2 | MPL-2.0 | 原样分发、未修改 → 不触发源码公开义务，仅需声明 |
| `@img/sharp-win32-x64` | Apache-2.0 AND LGPL-3.0-or-later | 原样分发预编译二进制、动态加载、独立目录可替换 → 符合 LGPL 分发条件 |

**不含 GPL-2.0 / GPL-3.0 等强 copyleft 组件，无代码传染义务。**

## 大肥鱼怎么用它

`dfy_engine.py` 是唯一入口，四件事：

| 能力 | 方法 | 说明 |
| --- | --- | --- |
| 定位 | `available()` | 返回引擎根、CLI、主程序、DSH_HOME |
| 版本 | `version()` | `dsh --version` |
| 插件 | `list_plugins()` / `add_plugin()` / `remove_plugin()` | 走 `dsh plugin --profile desktop add`，与 DSH 本体同一条链路 |
| web 服务 | `web_start()` / `web_status()` / `web_stop()` / `embed_url()` | 起带 token 的完整 UI 服务，供大肥鱼 DSH 模式嵌入 |

### 插件安装为什么 100% 通

`add_plugin()` 调的就是引擎原生命令：

```
cmd /c <engine>/resources/runtime/cli/bin/dsh.cmd plugin --profile desktop add <spec>
```

依赖解析、peer 冲突、patch 登记全交给引擎自己。实测装 `dsh-tiddlywiki` 用时 2.1–22.9 秒。

`dfy_market.py` 的 `install()` 已改为**优先走引擎链路**（返回 `via: "engine"`），
引擎缺失时自动回退到原来的「npm 暂存 + 临时宿主自测」链路，不会崩。

### web 服务的认证机制（重要，踩过坑）

引擎的 web UI 不是"URL 带 token 就通"，而是：

```
GET /?token=XXX
  → 303 See Other，Location: ./
  → Set-Cookie: dsh-auth-XXXX=v1.<base64>（HttpOnly, SameSite=Strict, Max-Age=30 天）
  → 浏览器带 cookie 再请求 ./ → 200（41304 字节 HTML）
```

所以：

- **浏览器/WebView 嵌入**：直接用 `embed_url()` 给出的带 token 地址即可，
  浏览器会自动完成 303 → 种 cookie → 200。
- **程序化探测**：必须用 CookieJar（`dfy_engine._probe()` 已实现），
  否则会停在 401，误判为"服务没起来"。

## 可移植性（给别人的电脑）

三个模块的路径解析都不允许写死作者盘符：

```python
优先级：DFY_ENGINE_DIR 环境变量
      → 程序目录下的 engine（安装包形态）
      → DSH_HOME 的兄弟目录 engine（源码形态）
      → 老机器 D 盘兜底（仅在目录真实存在时生效）
```

`tools/test_engine_web.py` 第 5 项会用正则扫描三个模块，把裸写 `E:\DSH` 的行抓出来报错。

引擎自带 node 24.21.0 / pnpm 11.28.5 / python 3.12.14，**不依赖用户系统里装了什么**。

## 验收结果

| 测试 | 项数 | 结果 |
| --- | --- | --- |
| `tools/test_engine_integration.py` | 34 | 全绿（1 项网络 SKIP） |
| `tools/test_engine_web.py` | 31 | 全绿 |
| 合计 | **65** | **0 失败** |

覆盖：引擎定位、独立启动、版本、插件列表、安装链路优先性、缺位回退、
品牌替换、裁剪到位、许可清单完整性、web 全生命周期、认证机制、路径可移植性、自带运行时。

## 重新生成许可清单

引擎升级后重跑（会覆盖 `packages.json`）：

```powershell
& "<venv>\Scripts\python.exe" "D:\本地部署大肥鱼环境\engine\tools\collect_asar_licenses.py"
```

脚本解析 `app.asar` 的目录表，逐个读内部 `package.json` 的 license 字段。
