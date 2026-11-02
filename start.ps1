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
