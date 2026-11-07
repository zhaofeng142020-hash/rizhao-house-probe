@echo off
chcp 65001 >nul
title 日照房价探针 · 本机一键启动
setlocal
cd /d "%~dp0"

echo ============================================================
echo   日照房价探针 · 捡漏雷达 —— 本机直跑
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 python，请先安装 Python 3.11+ 并勾选 Add to PATH。
  pause & exit /b 1
)
if not exist ".env" if exist ".env.example" copy ".env.example" ".env" >nul

echo [1/4] 准备 Python 虚拟环境 .venv ...
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv || (echo [错误] 创建虚拟环境失败 & pause & exit /b 1)
)
call ".venv\Scripts\activate.bat"

echo [2/4] 安装后端依赖 ...
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt || (echo [错误] 依赖安装失败，请检查网络 & pause & exit /b 1)

echo [3/4] 准备前端（已有构建产物就跳过，没 Node 也能跑）...
if exist "backend\app\webui\index.html" (
  echo     已存在 backend\app\webui，跳过构建。
) else (
  where npm >nul 2>nul
  if errorlevel 1 (
    echo     [提示] 未检测到 npm，跳过前端构建；接口与 /docs 仍可访问。
  ) else (
    pushd frontend
    if not exist "node_modules" call npm install --no-audit --no-fund
    call npm run build
    popd
  )
)

echo [4/4] 启动服务： http://127.0.0.1:8000
echo     API 文档： http://127.0.0.1:8000/docs
echo     手机同 Wi-Fi 访问： http://本机IP:8000 （用 ipconfig 查 IPv4 地址）
echo     按 Ctrl+C 停止
echo.
cd backend
start "" http://127.0.0.1:8000
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
