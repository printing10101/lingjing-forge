@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ========================================
echo  灵境制造 - Windows 一键安装脚本
echo ========================================
echo.

:: 检查 Python 版本
echo [1/5] 检查 Python 环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10 或更高版本
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYTHON_VERSION=%%v
echo [√] 检测到 Python %PYTHON_VERSION%

:: 提取主版本号进行对比
for /f "tokens=1,2 delims=." %%a in ("%PYTHON_VERSION%") do (
    set MAJOR=%%a
    set MINOR=%%b
)

if %MAJOR% LSS 3 (
    echo [错误] Python 版本过低，需要 3.10 或更高版本
    pause
    exit /b 1
)
if %MAJOR% EQU 3 if %MINOR% LSS 10 (
    echo [错误] Python 版本过低，需要 3.10 或更高版本
    pause
    exit /b 1
)

:: 创建虚拟环境
echo.
echo [2/5] 创建 Python 虚拟环境...
if not exist "venv" (
    python -m venv venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
    echo [√] 虚拟环境创建成功
) else (
    echo [√] 虚拟环境已存在，跳过创建
)

:: 激活虚拟环境并安装依赖
echo.
echo [3/5] 安装 Python 依赖（使用阿里云镜像）...
call venv\Scripts\activate.bat

pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com >nul 2>&1

pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)
echo [√] 依赖安装完成

:: 初始化数据库
echo.
echo [4/5] 初始化数据库...
cd engineering\python
python -c "import asyncio; from app.database.models import init_db; asyncio.run(init_db()); print('[√] 数据库初始化完成')" 2>nul
if errorlevel 1 (
    echo [警告] 数据库初始化失败，可能是首次运行或配置问题
    echo 请检查 .env 文件中的数据库配置
)
cd ..

:: 启动服务
echo.
echo [5/5] 启动服务...
echo.
echo ========================================
echo  安装完成！
echo  服务地址: http://localhost:8765
echo  API 文档: http://localhost:8765/docs
echo ========================================
echo.
echo 按 Ctrl+C 停止服务
echo.

cd engineering\python
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --log-level info

pause
