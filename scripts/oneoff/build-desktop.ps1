# 灵境制造桌面版构建脚本
# 功能：自动完成 PyInstaller 打包 + Tauri 构建，生成一体化安装包

param(
    [switch]$SkipPython,
    [switch]$SkipTauri,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$PythonDir = Join-Path $ProjectRoot "python"
$TauriDir = Join-Path $ProjectRoot "src-tauri"
$BinariesDir = Join-Path $TauriDir "binaries"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  灵境制造桌面版构建脚本" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 清理旧构建产物
if ($Clean) {
    Write-Host "[1/5] 清理旧构建产物..." -ForegroundColor Yellow
    $distDir = Join-Path $PythonDir "dist"
    $buildDir = Join-Path $PythonDir "build"
    $bundleDir = Join-Path $TauriDir "target\release\bundle"
    
    if (Test-Path $distDir) {
        Remove-Item -Path $distDir -Recurse -Force
    }
    if (Test-Path $buildDir) {
        Remove-Item -Path $buildDir -Recurse -Force
    }
    if (Test-Path $bundleDir) {
        Remove-Item -Path $bundleDir -Recurse -Force
    }
    
    Write-Host "  OK: 清理完成" -ForegroundColor Green
}

# Step 1: Python 后端打包
if (-not $SkipPython) {
    Write-Host "[2/5] 打包 Python 后端 (PyInstaller)..." -ForegroundColor Yellow
    Set-Location $PythonDir

    # 检查 PyInstaller
    if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
        Write-Host "  PyInstaller 未安装，正在安装..." -ForegroundColor Red
        pip install pyinstaller -q
    }

    # 执行打包
    pyinstaller --clean --noconfirm lingjing-backend.spec
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: PyInstaller 打包失败" -ForegroundColor Red
        exit 1
    }

    # 复制可执行文件到 Tauri binaries 目录
    $BackendExe = Join-Path $PythonDir "dist\lingjing-backend.exe"
    if (-not (Test-Path $BinariesDir)) {
        New-Item -ItemType Directory -Path $BinariesDir | Out-Null
    }
    Copy-Item -Path $BackendExe -Destination $BinariesDir -Force
    Write-Host "  OK: Python 后端打包完成" -ForegroundColor Green
    Write-Host "    输出: $(Join-Path $BinariesDir 'lingjing-backend.exe')" -ForegroundColor Gray
}
else {
    Write-Host "[2/5] 跳过 Python 后端打包" -ForegroundColor Yellow
}

# Step 2: 前端构建
if (-not $SkipTauri) {
    Write-Host "[3/5] 构建前端 (npm/pnpm)..." -ForegroundColor Yellow
    Set-Location $ProjectRoot

    # 检查包管理器
    $PackageManager = $null
    if (Get-Command pnpm -ErrorAction SilentlyContinue) {
        $PackageManager = "pnpm"
    }
    elseif (Get-Command npm -ErrorAction SilentlyContinue) {
        $PackageManager = "npm"
    }

    if (-not $PackageManager) {
        Write-Host "  ERROR: 未找到 npm 或 pnpm" -ForegroundColor Red
        exit 1
    }

    # 安装依赖（如果需要）
    if (-not (Test-Path "node_modules")) {
        Write-Host "  安装前端依赖..." -ForegroundColor Gray
        & $PackageManager install
    }

    # 构建前端
    & $PackageManager run build
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: 前端构建失败" -ForegroundColor Red
        exit 1
    }
    Write-Host "  OK: 前端构建完成" -ForegroundColor Green
}
else {
    Write-Host "[3/5] 跳过前端构建" -ForegroundColor Yellow
}

# Step 3: Tauri 构建
if (-not $SkipTauri) {
    Write-Host "[4/5] 构建 Tauri 安装包..." -ForegroundColor Yellow
    Set-Location $TauriDir

    # 检查 Rust 工具链
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
        Write-Host "  ERROR: Rust 工具链未安装" -ForegroundColor Red
        Write-Host "    请访问 https://rustup.rs 安装 Rust" -ForegroundColor Gray
        exit 1
    }

    # 执行 Tauri 构建
    cargo tauri build
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: Tauri 构建失败" -ForegroundColor Red
        exit 1
    }

    Write-Host "  OK: Tauri 安装包构建完成" -ForegroundColor Green
    $msiDir = Join-Path $TauriDir "target\release\bundle\msi"
    $nsisDir = Join-Path $TauriDir "target\release\bundle\nsis"
    Write-Host "    MSI 输出: $msiDir" -ForegroundColor Gray
    Write-Host "    NSIS 输出: $nsisDir" -ForegroundColor Gray
}
else {
    Write-Host "[4/5] 跳过 Tauri 构建" -ForegroundColor Yellow
}

# Step 4: 输出摘要
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  构建完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "安装包位置：" -ForegroundColor Yellow
if (-not $SkipTauri) {
    $msiPath = Join-Path $TauriDir "target\release\bundle\msi"
    $nsisPath = Join-Path $TauriDir "target\release\bundle\nsis"
    Write-Host "  - MSI: $msiPath\*.msi" -ForegroundColor Gray
    Write-Host "  - NSIS: $nsisPath\*.exe" -ForegroundColor Gray
}
Write-Host ""
Write-Host "提示：" -ForegroundColor Cyan
Write-Host "  - 首次运行需要安装 WebView2 Runtime（Windows 10/11 已内置）" -ForegroundColor Gray
Write-Host "  - 安装包会自动配置环境变量和启动脚本" -ForegroundColor Gray
Write-Host "  - 桌面版使用 SQLite + 内存缓存，无需外部依赖" -ForegroundColor Gray
Write-Host ""

Set-Location $ProjectRoot
