@echo off
chcp 65001 >nul
title 日照房价探针 · 单文件演示版
echo ============================================================
echo   日照房价探针 · 捡漏雷达 —— 单文件演示版
echo   零依赖，只用 Python 标准库
echo ============================================================
cd /d "%~dp0"
python standalone\demo.py
if errorlevel 1 (
  echo.
  echo [错误] 启动失败。请确认已安装 Python 3.11+，并可用 python 命令。
  pause
)
