@echo off
chcp 65001 >nul
title 大肥鱼AI · 云端模型兼容代理

cd /d D:\大肥鱼AI
echo ============================================
echo   云端模型兼容代理  (127.0.0.1:11435)
echo ============================================
echo.
echo   平时不用手动开这个：在 设置 - 云端模型 里点「保存并应用」
echo   主程序会自动拉起本代理。
echo.
echo   当前配置：
type cloud_config.json
echo.
echo   按 Ctrl+C 停止。
echo.
"D:\本地部署大肥鱼环境\venv312\Scripts\python.exe" cloud_proxy.py 11435
pause
