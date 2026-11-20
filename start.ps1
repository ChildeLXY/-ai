$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ollamaPath = "D:\本地部署大肥鱼环境\program\ollama.exe"
$env:OLLAMA_MODELS = "D:\本地部署大肥鱼环境\models"
$env:OLLAMA_ORIGINS = "*"
$env:OLLAMA_CACHE = "D:\大肥鱼缓存"
# 8GB 显卡（RTX 4060 Laptop）跑 7B + 8192 上下文时，不优化的话模型有 0.6GB 会被挤到内存里跑，
# 生成速度掉三成。开闪存注意力 + 把 KV cache 量化成 q8_0 后，模型占 5.00GB 全部在显存内，零溢出。
# 注意：如果以后 ollama 报 502 "upstream connect failed"，先检查是不是开着系统代理——
# 代理变量会被 ollama 继承，导致它连接自己拉起的 runner 时被拐走。
$env:OLLAMA_FLASH_ATTENTION = "1"
$env:OLLAMA_KV_CACHE_TYPE = "q8_0"
# 桌宠/插件的工作区（bili-duty、electron、profiles 等）已从 C:\Users\<用户>\.dsh 搬到 D 盘。
# 桌宠读的是 DSH_HOME 这个变量，这里再设一次，保证不管用户环境变量有没有刷新都指向 D。
$env:DSH_HOME = "D:\本地部署大肥鱼环境\dsh"

Set-Location "D:\"

$running = Get-Process ollama -ErrorAction SilentlyContinue
if (-not $running) {
    Start-Process $ollamaPath -ArgumentList "serve" -WorkingDirectory "D:\" -WindowStyle Hidden
    Start-Sleep 4
}

# 自动探测 pythonw.exe，不写死用户目录（按优先级依次尝试，全部落空则交给 PATH）
function Find-Pythonw {
    $cands = @()
    if ($env:LOCALAPPDATA) {
        $cands += (Join-Path $env:LOCALAPPDATA "Python\bin\pythonw.exe")
        foreach ($v in @("312", "313", "314")) {
            $cands += (Join-Path $env:LOCALAPPDATA "Programs\Python\Python$v\pythonw.exe")
        }
    }
    $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($cmd) { $cands += $cmd.Source }
    $py = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($py) { $cands += (Join-Path (Split-Path $py.Source -Parent) "pythonw.exe") }
    foreach ($c in $cands) { if ($c -and (Test-Path $c)) { return $c } }
    return "pythonw.exe"
}

$pythonw = Find-Pythonw
# 大肥鱼AI 主界面改用 Python 3.12 独立环境（3.14 + pythonnet 会因后台子进程崩窗口）
$pythonw_ai = "D:\本地部署大肥鱼环境\venv312\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw_ai)) { $pythonw_ai = $pythonw }

# ===== DSH 插件链路自愈预检（不阻断启动：修不动的只记日志，照常拉起主程序）=====
# 为什么放在启动前：pnpm 垫片 / allowBuilds / virtualStoreDir 任何一项被手动改坏，
# 插件市场就只会表现成"装不上、更新失败"，用户看到的是界面报错而不是根因。
# 这里静默跑一次六项体检，能修的直接修，修不动的写进 logs\dsh-doctor.log 便于回溯。
$doctor = Join-Path $dir "dfy_dsh_doctor.py"
$doctorLog = Join-Path $dir "logs\dsh-doctor.log"
if (Test-Path $doctor) {
    $pyExe = $pythonw_ai -replace 'pythonw\.exe$', 'python.exe'
    if (-not (Test-Path $pyExe)) {
        $cmdPy = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($cmdPy) { $pyExe = $cmdPy.Source }
    }
    if ($pyExe -and (Test-Path $pyExe)) {
        New-Item -ItemType Directory -Force -Path (Split-Path $doctorLog -Parent) | Out-Null
        $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
        $out = & $pyExe $doctor check --safe 2>&1 | Out-String
        $rc = $LASTEXITCODE
        if ($rc -ne 0) {
            $fixOut = & $pyExe $doctor fix --safe 2>&1 | Out-String
            $out2 = & $pyExe $doctor check --safe 2>&1 | Out-String
            $rc2 = $LASTEXITCODE
            Add-Content -LiteralPath $doctorLog -Encoding UTF8 -Value ("[$stamp] check=FAIL rc=$rc`n$out`n--- fix ---`n$fixOut`n--- recheck rc=$rc2 ---`n$out2`n")
        } else {
            Add-Content -LiteralPath $doctorLog -Encoding UTF8 -Value ("[$stamp] check=OK`n$out`n")
        }
        # 日志只留最近 512KB，避免长年累月把盘塞满
        if ((Get-Item -LiteralPath $doctorLog).Length -gt 524288) {
            $lines = Get-Content -LiteralPath $doctorLog -Encoding UTF8
            $lines[-2000..-1] | Set-Content -LiteralPath $doctorLog -Encoding UTF8
        }
        $script:doctorRc = if ($rc -ne 0) { $rc2 } else { $rc }
    }
}

# 只做体检、不拉起主程序：给自动化和排障用（DFY_DOCTOR_ONLY=1）
if ($env:DFY_DOCTOR_ONLY -eq "1") {
    if (-not $script:doctorRc) { $script:doctorRc = 0 }
    exit $script:doctorRc
}

Start-Process $pythonw_ai -ArgumentList (Join-Path $dir "大肥鱼AI.py") -WorkingDirectory $dir

$petDir = Join-Path $dir "pet_repo\dsh-pet-indesktop-main"
$petRunning = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" | Where-Object { $_.CommandLine -like '*-m*pet*' -and $_.CommandLine -notlike '*pet_mutter*' -and $_.CommandLine -notlike '*watcher*' }
if (-not $petRunning) {
    Start-Process $pythonw -ArgumentList "-m","pet" -WorkingDirectory $petDir -WindowStyle Hidden
}

# 碎碎念守望者：定时让大肥鱼在桌宠气泡里主动念叨
$watcherRunning = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" | Where-Object { $_.CommandLine -like "*pet_mutter_watcher*" }
if (-not $watcherRunning) {
    Start-Process $pythonw -ArgumentList (Join-Path $dir "pet_mutter_watcher.py") -WorkingDirectory $dir -WindowStyle Hidden
}

# 情绪守望者：更新情绪(mood.json)、检测离开/回来/深夜/高负载/锁屏/断网事件并触发气泡
$moodRunning = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" | Where-Object { $_.CommandLine -like "*mood_watcher*" }
if (-not $moodRunning) {
    Start-Process $pythonw -ArgumentList (Join-Path $dir "mood_watcher.py") -WorkingDirectory $dir -WindowStyle Hidden
}

# 网页客户端：手机/平板/PC 用浏览器就能聊（默认 8848）
# 不想要的话，把下面这段注释掉，或者设环境变量 DFY_NO_WEB=1
if ($env:DFY_NO_WEB -ne "1") {
    $webRunning = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" | Where-Object { $_.CommandLine -like "*web_client.py*" }
    if (-not $webRunning) {
        Start-Process $pythonw_ai -ArgumentList (Join-Path $dir "web_client.py"), "8848" -WorkingDirectory $dir -WindowStyle Hidden
    }
}

# ===== QQ 接入（内嵌桥接由大肥鱼AI.py 自己起；这里只负责把 QQ 客户端拉起来）=====
# 开关：D:/大肥鱼QQ/auto_start.flag 存在才自动拉 QQ（界面勾选"随大肥鱼启动"会写这个文件）。
# 这样 QQ 进程归当前桌面会话，不会被后台工具会话回收。
$qqFlag = "D:/大肥鱼QQ/auto_start.flag"
if (Test-Path $qqFlag) {
    $qqRunning = Get-Process QQ -ErrorAction SilentlyContinue
    if (-not $qqRunning) {
        $qqExe = "D:/QQ/QQ.exe"
        $launcher = "D:/NapCat/NapCatWinBootMain.exe"
        $hookDll = "D:/NapCat/NapCatWinBootHook.dll"
        if ((Test-Path $qqExe) -and (Test-Path $launcher)) {
            $env:ELECTRON_RUN_AS_NODE = ""
            $env:NODE_OPTIONS = ""
            $env:NODE_CHANNEL_FD = ""
            $env:VM_OPTIONS = ""
            $env:NAPCAT_PATCH_PACKAGE = "D:/NapCat/qqnt.json"
            $env:NAPCAT_LOAD_PATH = "D:/NapCat/loadNapCat.js"
            $env:NAPCAT_INJECT_PATH = $hookDll
            $env:NAPCAT_LAUNCHER_PATH = $launcher
            $env:NAPCAT_MAIN_PATH = "D:/NapCat/napcat.mjs"
            Start-Process -FilePath $launcher -ArgumentList $qqExe,$hookDll,"-q","1834347962" -WorkingDirectory "D:/NapCat" -WindowStyle Minimized
        }
    }
}
