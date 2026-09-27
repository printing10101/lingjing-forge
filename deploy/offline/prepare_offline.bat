@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ========================================
echo  灵境制造 - Windows 离线包准备脚本
echo ========================================
echo.

:: 获取项目根目录
set SCRIPT_DIR=%~dp0
set PROJECT_ROOT=%SCRIPT_DIR%..\..
cd /d "%PROJECT_ROOT%"

:: 检查 Python
echo [1/4] 检查 Python 环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python
    pause
    exit /b 1
)
echo [√] Python 环境正常

:: 创建离线包目录
echo.
echo [2/4] 创建离线包目录结构...
set OFFLINE_DIR=%PROJECT_ROOT%\deploy\offline\offline_package
if exist "%OFFLINE_DIR%" rd /s /q "%OFFLINE_DIR%"
mkdir "%OFFLINE_DIR%\wheels"
mkdir "%OFFLINE_DIR%\python"
mkdir "%OFFLINE_DIR%\config"
mkdir "%OFFLINE_DIR%\scripts"
echo [√] 目录结构创建完成

:: 下载 pip 依赖到 wheels/ 目录
echo.
echo [3/4] 下载 Python 依赖包（wheel 格式）...
echo 这可能需要几分钟，请耐心等待...

pip download -r requirements.txt -d "%OFFLINE_DIR%\wheels" ^
    -i https://mirrors.aliyun.com/pypi/simple/ ^
    --trusted-host mirrors.aliyun.com ^
    --platform win_amd64 ^
    --python-version 3.11 ^
    --only-binary=:all:

if errorlevel 1 (
    echo.
    echo [警告] 部分包无法下载预编译 wheel，尝试下载源码包...
    pip download -r requirements.txt -d "%OFFLINE_DIR%\wheels" ^
        -i https://mirrors.aliyun.com/pypi/simple/ ^
        --trusted-host mirrors.aliyun.com
)

if errorlevel 1 (
    echo [错误] 依赖下载失败
    pause
    exit /b 1
)
echo [√] 依赖包下载完成

:: 复制项目文件
echo.
echo [4/4] 复制项目文件...
:: P0-2 修复：阶段2解耦后后端代码位于 engineering\python（原 python\ 已不存在）
xcopy /E /I /Q /Y "%PROJECT_ROOT%\engineering\python\app" "%OFFLINE_DIR%\python\app" >nul
:: P0-2 修复：离线包内 requirements.txt 必须为真实依赖清单（根目录薄包装在离线包内无法解析）
copy /Y "%PROJECT_ROOT%\engineering\python\requirements.txt" "%OFFLINE_DIR%\requirements.txt" >nul
copy /Y "%PROJECT_ROOT%\.env.example" "%OFFLINE_DIR%\" >nul 2>nul
copy /Y "%PROJECT_ROOT%\docker-compose.yml" "%OFFLINE_DIR%\" >nul 2>nul
copy /Y "%PROJECT_ROOT%\docker-compose-cn.yml" "%OFFLINE_DIR%\" >nul 2>nul
copy /Y "%PROJECT_ROOT%\docker-compose-sqlite.yml" "%OFFLINE_DIR%\" >nul 2>nul
xcopy /E /I /Q /Y "%PROJECT_ROOT%\config" "%OFFLINE_DIR%\config" >nul 2>nul
xcopy /E /I /Q /Y "%PROJECT_ROOT%\deploy\nginx" "%OFFLINE_DIR%\nginx" >nul 2>nul

:: 复制离线安装脚本
copy /Y "%SCRIPT_DIR%install_offline.bat" "%OFFLINE_DIR%\" >nul
copy /Y "%SCRIPT_DIR%install_offline.sh" "%OFFLINE_DIR%\" >nul 2>nul
copy /Y "%SCRIPT_DIR%..\..\deploy\offline\README.md" "%OFFLINE_DIR%\" >nul 2>nul

echo [√] 项目文件复制完成

:: 生成打包
echo.
echo ========================================
echo  离线包准备完成！
echo ========================================
echo.
echo  离线包位置: %OFFLINE_DIR%
echo.
echo  下一步操作：
echo    1. 将整个 offline_package 文件夹打包为 zip
echo    2. 传输到目标机器
echo    3. 解压后运行 install_offline.bat
echo.

:: 询问是否自动打包
set /p ZIP_NOW="是否现在打包为 zip? [y/N]: "
if /i "%ZIP_NOW%"=="y" (
    echo 正在打包...
    powershell -Command "Compress-Archive -Path '%OFFLINE_DIR%\*' -DestinationPath '%PROJECT_ROOT%\deploy\offline\lingjing_offline_package.zip' -Force"
    echo [√] 打包完成: deploy\offline\lingjing_offline_package.zip
)

pause
