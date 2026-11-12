@echo off
chcp 65001 >nul
title 大肥鱼 · 下载服务

cd /d D:\大肥鱼AI

echo ============================================
echo   正在启动下载服务（APK / 模型 / 动作包）
echo ============================================
echo.

REM 已经开着就不重复启动
netstat -ano | findstr ":8848" | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo [提示] 8848 端口已在监听，服务应该已经开着
    goto show
)

start "" "D:\本地部署大肥鱼环境\venv312\Scripts\pythonw.exe" "D:\大肥鱼AI\web_client.py" 8848
timeout /t 4 >nul

netstat -ano | findstr ":8848" | findstr "LISTENING" >nul
if errorlevel 1 (
    echo [失败] 服务没起来。用下面的命令看报错：
    echo    "D:\本地部署大肥鱼环境\venv312\Scripts\python.exe" "D:\大肥鱼AI\web_client.py" 8848
    pause
    exit /b 1
)
echo [成功] 服务已启动
echo.

:show
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do set IP=%%a
set IP=%IP: =%
echo   手机打开（同一个 WiFi）：
echo     http://%IP%:8848/dl/
echo.
echo   电脑本机打开：
echo     http://127.0.0.1:8848/dl/
echo.
echo   注意：如果手机打不开，去 Windows 防火墙放行 8848 端口，
echo         或者允许 Python 访问「专用网络」。
echo.
pause
