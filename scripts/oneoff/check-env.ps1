# 灵境制造 - 构建诊断脚本
# 用途：检查环境是否满足构建要求

$ErrorCount = 0

# 检查 1: Node.js
Write-Host "[1/6] 检查 Node.js..." -ForegroundColor Yellow
try {
    $nodeVersion = node --version 2>&1
    Write-Host "  [OK] Node.js 已安装: $nodeVersion" -ForegroundColor Green
} catch {
    Write-Host "  [FAIL] Node.js 未安装或未添加到 PATH" -ForegroundColor Red
    $ErrorCount++
}

# 检查 2: pnpm 或 npm
Write-Host "[2/6] 检查包管理器..." -ForegroundColor Yellow
$PackageManager = ""
try {
    $pnpmVersion = pnpm --version 2>&1
    Write-Host "  [OK] pnpm 已安装: v$pnpmVersion" -ForegroundColor Green
    $PackageManager = "pnpm"
} catch {
    try {
        $npmVersion = npm --version 2>&1
        Write-Host "  [OK] npm 已安装: v$npmVersion" -ForegroundColor Green
        $PackageManager = "npm"
    } catch {
        Write-Host "  [FAIL] pnpm 和 npm 都未找到" -ForegroundColor Red
        $ErrorCount++
    }
}

# 检查 3: Rust
Write-Host "[3/6] 检查 Rust..." -ForegroundColor Yellow
try {
    $rustVersion = rustc --version 2>&1
    Write-Host "  [OK] Rust 已安装: $rustVersion" -ForegroundColor Green
} catch {
    Write-Host "  [FAIL] Rust 未安装" -ForegroundColor Red
    Write-Host "    请运行: winget install Rustlang.Rustup" -ForegroundColor Yellow
    $ErrorCount++
}

# 检查 4: Cargo
Write-Host "[4/6] 检查 Cargo..." -ForegroundColor Yellow
try {
    $cargoVersion = cargo --version 2>&1
    Write-Host "  [OK] Cargo 已安装: $cargoVersion" -ForegroundColor Green
} catch {
    Write-Host "  [FAIL] Cargo 未安装" -ForegroundColor Red
    $ErrorCount++
}

# 检查 5: Visual Studio Build Tools
Write-Host "[5/6] 检查 Visual Studio Build Tools..." -ForegroundColor Yellow
$vsWhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path $vsWhere) {
    $vsInfo = & $vsWhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property displayName 2>&1
    if ($vsInfo) {
        Write-Host "  [OK] Visual Studio Build Tools 已安装: $vsInfo" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] 缺少 C++ 构建工具" -ForegroundColor Red
        Write-Host "    请安装 Visual Studio Build Tools 并勾选 使用C++的桌面开发" -ForegroundColor Yellow
        $ErrorCount++
    }
} else {
    Write-Host "  [WARN] 未检测到 Visual Studio Installer" -ForegroundColor Yellow
    Write-Host "    如果已安装 Build Tools，可忽略此警告" -ForegroundColor Yellow
}

# 检查 6: 当前路径是否包含非 ASCII 字符
Write-Host "[6/6] 检查当前路径..." -ForegroundColor Yellow
$CurrentPath = Get-Location
$PathString = $CurrentPath.Path
$HasNonAscii = [regex]::IsMatch($PathString, '[^\x00-\x7F]')

if ($HasNonAscii) {
    Write-Host "  [FAIL] 当前路径包含中文: $PathString" -ForegroundColor Red
    Write-Host "    Rust 编译器无法处理非英文路径" -ForegroundColor Yellow
    Write-Host "    请将项目移动到纯英文路径，例如: C:\Projects\lingjing-manufacturing" -ForegroundColor Yellow
    $ErrorCount++
} else {
    Write-Host "  [OK] 当前路径是纯英文: $PathString" -ForegroundColor Green
}

Write-Host ""

if ($ErrorCount -eq 0) {
    Write-Host "环境检查通过！可以执行构建" -ForegroundColor Green
    Write-Host ""
    Write-Host "构建命令:" -ForegroundColor White
    Write-Host "  $PackageManager install" -ForegroundColor Gray
    Write-Host "  npm run tauri build" -ForegroundColor Gray
} else {
    Write-Host "发现 $ErrorCount 个问题需要解决" -ForegroundColor Red
    Write-Host ""
    Write-Host "请修复上述问题后重新尝试构建" -ForegroundColor Yellow
}
