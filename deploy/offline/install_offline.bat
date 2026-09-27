@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ========================================
echo  灵境制造 - Windows 离线安装脚本
echo ========================================
echo.

:: 获取脚本所在目录（离线包根目录）
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

:: ------------------------------------------------------------------
:: [1/5] 检查 Python
:: ------------------------------------------------------------------
echo [1/5] 检查 Python 环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)
echo [√] Python 环境正常

:: ------------------------------------------------------------------
:: [2/5] 创建虚拟环境
:: ------------------------------------------------------------------
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
    echo [√] 虚拟环境已存在
)

call venv\Scripts\activate.bat

:: ------------------------------------------------------------------
:: [3/5] 从 wheels/ 离线安装依赖
:: ------------------------------------------------------------------
echo.
echo [3/5] 离线安装 Python 依赖...

if not exist "wheels" (
    echo [错误] 未找到 wheels 目录，请确认离线包完整性
    pause
    exit /b 1
)

pip install --upgrade pip --no-index --find-links=wheels >nul 2>&1

pip install --no-index --find-links=wheels -r requirements.txt
if errorlevel 1 (
    echo [错误] 离线依赖安装失败
    echo 请确认 wheels 目录中包含所有必要的依赖包
    pause
    exit /b 1
)
echo [√] 依赖安装完成

:: ------------------------------------------------------------------
:: [4/5] 加载 Docker 镜像（如果存在）
:: ------------------------------------------------------------------
echo.
echo [4/5] 加载 Docker 镜像...
if exist "docker_images" (
    where docker >nul 2>&1
    if not errorlevel 1 (
        for %%f in (docker_images\*.tar) do (
            echo 正在加载: %%~nxf
            docker load -i "%%f"
            if errorlevel 1 (
                echo [警告] 镜像 %%~nxf 加载失败
            ) else (
                echo [√] 镜像 %%~nxf 加载成功
            )
        )
        echo [√] Docker 镜像加载完成
    ) else (
        echo [警告] 未检测到 Docker，跳过镜像加载
        echo 如需使用 Docker 部署，请先安装 Docker Desktop
    )
) else (
    echo [信息] 未包含 Docker 镜像，跳过
)

:: ------------------------------------------------------------------
:: [5/5] 初始化并启动服务
:: ------------------------------------------------------------------
echo.
echo [5/5] 初始化数据库...

:: 复制环境配置文件
if not exist ".env" (
    if exist ".env.example" (
        copy /Y ".env.example" ".env" >nul
        echo [√] 已从 .env.example 创建 .env 配置文件
        echo [警告] 请根据实际情况修改 .env 中的配置项
    )
)

cd python
python -c "from app.database import engine; from app.models import Base; Base.metadata.create_all(bind=engine); print('[√] 数据库初始化完成')" 2>nul
if errorlevel 1 (
    echo [警告] 数据库初始化失败，请检查 .env 配置
)
cd ..

echo.
echo ========================================
echo  离线安装完成！
echo ========================================
echo.
echo  启动方式：
echo    手动启动: cd python ^&^& ..\venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
echo    服务地址: http://localhost:8765
echo    API 文档: http://localhost:8765/docs
echo.
echo  如已加载 Docker 镜像：
echo    docker compose up -d
echo.

:: 询问是否立即启动
set /p START_NOW="是否立即启动服务? [y/N]: "
if /i "%START_NOW%"=="y" (
    echo.
    echo 正在启动服务...
    echo 按 Ctrl+C 停止服务
    echo.
    cd python
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --log-level info
)

pause
