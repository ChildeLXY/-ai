# 打包成安装包（Windows · onedir + Inno Setup）

本目录是把「大肥鱼AI 源码版」打成 exe 安装包的全部配置。产物是一份**别人电脑上装完即用**的安装程序，不是压缩包。

## 一、为什么这样设计

| 决定 | 原因 |
|---|---|
| PyInstaller **onedir**（不是 onefile） | onefile 每次启动都要把 ~80MB 解压到临时目录，慢且杀软误报多；onedir 启动快、插件目录也能直接看见 |
| 装到 `%LOCALAPPDATA%\Programs\大肥鱼AI` | 免管理员、不写 Program Files、不往 C 盘系统目录塞东西 |
| 数据落 `%APPDATA%\大肥鱼AI\data` | 卸载保留聊天记录；重装接得上 |
| 不带 Ollama 模型 | 模型动辄几十 GB，装完由用户在设置里选云端或本地拉取 |
| 不带 `cloud_config.json` | 里面是作者的 API Key，安装包只放 `cloud_config.example.json` |

## 二、路径是怎么做到两边都能跑的

新增 `dfy_paths.py`，按优先级解析：

1. 环境变量 `DFY_DATA_DIR` / `DFY_CACHE_DIR` / `DSH_HOME` / `DFY_WV2_PROFILE` 显式指定 → 以它为准
2. 老机器上存在 `D:\本地部署大肥鱼环境\data`（作者本机）→ 继续用，聊天记录无缝
3. 都没有（新电脑）→ 落 `%APPDATA%\大肥鱼AI\data`、`%LOCALAPPDATA%\大肥鱼AI\cache`

`patch_paths.py` 负责把各模块里写死的 `D:\` 路径统一换成 `dfy_paths` 的解析结果，**只改打包副本，不动源码目录**。

## 三、重打一遍的步骤

```powershell
# 1) 建干净副本（只带真需要的文件，跳过 .bak / 探针脚本 / 缓存）
#    按 patch_paths.py 顶部的文件清单复制到 E:\dfy_build\app，再把 dfy_paths.py 放进去

# 2) 把写死路径换成可移植解析
python E:\dfy_build\patch_paths.py

# 3) 先编译检查，再打包
D:\本地部署大肥鱼环境\venv312\Scripts\python.exe -m PyInstaller --noconfirm 大肥鱼AI_install.spec

# 4) 打安装程序（ISCC 在 Inno Setup 6 目录下）
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" 大肥鱼AI安装包.iss
```

产物：`E:\大肥鱼AI安装包.exe`（约 27MB，装完占约 81MB）。

## 四、自检钩子（重要）

打包后的 exe 没有控制台，看不到 print。设置环境变量后运行，会把结果写到 `%TEMP%\dfy_selftest.txt` 和缓存目录：

```powershell
$env:DFY_SELFTEST = "1"
& "路径\大肥鱼AI.exe"   # 退出码 0 = 全部模块加载 OK
```

会验证：`api_base / llm_params / dfy_core / dfy_dsh / dfy_market / dfy_qzone / qq_bridge / dfy_tray / plugins / webview / webview.platforms.winforms / clr / PIL.Image`，以及 html、plugins 目录是否在包内。改完打包配置一定要先跑这一步。

## 五、已验证过的事实

- 打包 exe 自检：13 个模块全部 OK，`html_exists=True`、`plugins_exists=True`
- 真实启动：窗口正常出现（标题「大肥鱼AI · By Childe」），12 个插件全部加载，无崩溃
- 静默试装 + 静默卸载：安装目录正确创建、卸载后干净移除，`%APPDATA%` 数据保留
- 静默卸载**不会**自动删数据（`UninstallSilent` 分支直接退出），只有手动卸载才弹窗询问

## 六、边界与已知限制

- **没有代码签名证书**：首次运行会弹 SmartScreen「未知发布者」，杀软也可能对 PyInstaller 产物报警。要彻底解决只能买证书，不是打包能绕过的。
- **QQ / B站功能依赖本机登录态**：换电脑后需要重新扫码登录 QQ、登录 B 站，插件默认关闭，不会自己乱动。
- **DeepBuddy / DSH 宿主**：找不到对应目录时插件会报「未安装」并跳过，不影响主程序。
- **语音那套（faster_whisper / onnxruntime / PySide6）被排除在外**：主程序用 pywebview，不需要 Qt；要语音功能得另出一个扩展包。
- 头像等少量运行时写入仍落在安装目录 `_internal\ds图片`，因为装在 `%LOCALAPPDATA%` 是可写的；若将来改成装到 Program Files，需要把这些写入一起改到数据目录。
