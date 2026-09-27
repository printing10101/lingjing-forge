# start_experiments.ps1
# ============================================================
# 方案 B：实验启动脚本（被任务计划程序触发，或手动运行）
# ============================================================
# 功能：
#   1. pid 锁检查（防止重复启动）
#   2. 启动 WPS 守护脚本（方案 C，后台 24 小时）
#   3. 启动 A2 消融实验（方案 A checkpoint 已启用，崩溃可续跑）
#   4. 启动 AR-02 LOMO 实验（方案 A checkpoint 已启用）
#
# 运行方式：
#   手动：powershell -ExecutionPolicy Bypass -File start_experiments.ps1
#   任务计划：schtasks /Run /TN Lingjing_Experiments
#
# 日志位置：论文相关\脚本\results\task_scheduler_logs\
# ============================================================

$ErrorActionPreference = "Continue"

$PROJECT_ROOT = "C:\Users\<user>\Desktop\灵境制造（上线版）"
$PYTHON = "C:\Users\<user>\AppData\Local\Programs\Python\Python311\python.exe"
$PYTHONW = "C:\Users\<user>\AppData\Local\Programs\Python\Python311\pythonw.exe"
$RESULTS_DIR = Join-Path $PROJECT_ROOT "论文相关\脚本\results"
$LOGS_DIR = Join-Path $RESULTS_DIR "task_scheduler_logs"

if (-not (Test-Path $PYTHON)) {
    Write-Error "Python 3.11 不存在: $PYTHON"
    exit 1
}

New-Item -ItemType Directory -Force -Path $RESULTS_DIR | Out-Null
New-Item -ItemType Directory -Force -Path $LOGS_DIR | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$guardLog = Join-Path $LOGS_DIR "guard_${timestamp}.log"
$a2Log = Join-Path $LOGS_DIR "lomo_a2_${timestamp}.log"
$ar02Log = Join-Path $LOGS_DIR "lomo_loco_ar02_${timestamp}.log"
$launchLog = Join-Path $LOGS_DIR "launch_${timestamp}.log"

# 启动日志：记录本次启动的所有 PID 和日志路径
"=== 实验启动脚本运行于 $(Get-Date) ===" | Out-File $launchLog -Encoding UTF8

# === pid 锁检查函数 ===
function Test-PidAlive {
    param([string]$pidFile)
    if (Test-Path $pidFile) {
        $procId = Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($procId -and (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {
            return $true
        }
    }
    return $false
}

$guardPidFile = Join-Path $RESULTS_DIR "guard.pid"
$a2PidFile = Join-Path $RESULTS_DIR "lomo_a2.pid"
$ar02PidFile = Join-Path $RESULTS_DIR "lomo_loco_ar02.pid"

# === 1. 启动 WPS 守护脚本（方案 C）===
if (Test-PidAlive $guardPidFile) {
    $msg = "[$(Get-Date)] WPS 守护脚本已在运行，跳过"
    Write-Output $msg
    $msg | Out-File $launchLog -Append -Encoding UTF8
} else {
    $msg = "[$(Get-Date)] 启动 WPS 守护脚本..."
    Write-Output $msg
    $msg | Out-File $launchLog -Append -Encoding UTF8
    $proc = Start-Process -FilePath $PYTHONW `
        -ArgumentList @(
            "论文相关\脚本\guard_wps_update.py",
            "--duration", "86400",
            "--log", $guardLog
        ) `
        -WorkingDirectory $PROJECT_ROOT -WindowStyle Hidden -PassThru
    $proc.Id | Out-File $guardPidFile -Encoding ASCII
    "  PID=$($proc.Id), 日志=$guardLog" | Out-File $launchLog -Append -Encoding UTF8
}

Start-Sleep -Seconds 3

# === 2. 启动 A2 消融实验 ===
if (Test-PidAlive $a2PidFile) {
    $msg = "[$(Get-Date)] A2 实验已在运行，跳过"
    Write-Output $msg
    $msg | Out-File $launchLog -Append -Encoding UTF8
} else {
    $msg = "[$(Get-Date)] 启动 A2 消融实验 (LOMO, lambda_pcc=0)..."
    Write-Output $msg
    $msg | Out-File $launchLog -Append -Encoding UTF8
    $proc = Start-Process -FilePath $PYTHON `
        -ArgumentList @("-u", "论文相关\脚本\_lomo_ablation_a2.py") `
        -WorkingDirectory $PROJECT_ROOT -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $a2Log -RedirectStandardError "$a2Log.err"
    $proc.Id | Out-File $a2PidFile -Encoding ASCII
    "  PID=$($proc.Id), 日志=$a2Log" | Out-File $launchLog -Append -Encoding UTF8
}

# === 3. 启动 AR-02 LOMO 实验 (physics_aware=ON) ===
if (Test-PidAlive $ar02PidFile) {
    $msg = "[$(Get-Date)] AR-02 实验已在运行，跳过"
    Write-Output $msg
    $msg | Out-File $launchLog -Append -Encoding UTF8
} else {
    $msg = "[$(Get-Date)] 启动 AR-02 LOMO 实验 (physics_aware=ON)..."
    Write-Output $msg
    $msg | Out-File $launchLog -Append -Encoding UTF8
    $proc = Start-Process -FilePath $PYTHON `
        -ArgumentList @(
            "-u",
            "论文相关\脚本\lomo_loco_experiment.py",
            "--protocol", "LOMO",
            "--models", "DL-LNN",
            "--output_dir", "论文相关\脚本\results\lomo_loco_ar02_full",
            "--physics_aware",
            "--stage1_epochs", "50",
            "--stage2_epochs", "100"
        ) `
        -WorkingDirectory $PROJECT_ROOT -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $ar02Log -RedirectStandardError "$ar02Log.err"
    $proc.Id | Out-File $ar02PidFile -Encoding ASCII
    "  PID=$($proc.Id), 日志=$ar02Log" | Out-File $launchLog -Append -Encoding UTF8
}

$endMsg = "[$(Get-Date)] 所有实验启动指令已发出，详见各日志文件"
Write-Output $endMsg
$endMsg | Out-File $launchLog -Append -Encoding UTF8
"=== 启动脚本结束 ===" | Out-File $launchLog -Append -Encoding UTF8
