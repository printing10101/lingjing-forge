<#
.SYNOPSIS
    监控 Full 训练进程，完成后自动运行贝叶斯 UQ 实验。
.DESCRIPTION
    1. 等待 full_weights.pt 文件出现
    2. 等待训练进程退出（避免文件半写入状态）
    3. 启动 bayesian_uq_experiment.py
    4. 输出到 uq_experiment.log
.NOTES
    用法：
        powershell -ExecutionPolicy Bypass -File run_uq_after_training.ps1
#>

$ErrorActionPreference = "Stop"

# === 路径配置 ===
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$weightsFile = Join-Path $scriptDir "results\full_weights.pt"
$uqLog       = Join-Path $scriptDir "uq_experiment.log"
$uqErr       = Join-Path $scriptDir "uq_experiment.err.log"
$pythonExe   = "C:\Users\<user>\AppData\Local\Programs\Python\Python311\python.exe"
$uqScript    = Join-Path $scriptDir "bayesian_uq_experiment.py"

# 训练进程 PID（rerun_full_save_weights.py）—— 用于判断训练是否结束
$trainingPid = 28576  # 当前 Full 训练的 PID

Write-Host "=== 贝叶斯 UQ 自动监控脚本 ===" -ForegroundColor Cyan
Write-Host "权重文件路径: $weightsFile"
Write-Host "UQ 脚本: $uqScript"
Write-Host "训练 PID: $trainingPid"
Write-Host "开始监控时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host ""

# === 阶段 1: 等待权重文件出现 ===
Write-Host "[阶段 1] 等待权重文件出现..." -ForegroundColor Yellow
$waitStart = Get-Date
$lastLogTime = $waitStart

while (-not (Test-Path $weightsFile)) {
    Start-Sleep -Seconds 60

    # 检查训练进程是否还在运行
    $proc = Get-Process -Id $trainingPid -ErrorAction SilentlyContinue
    $now = Get-Date

    # 每 10 分钟打印一次状态
    if (($now - $lastLogTime).TotalMinutes -ge 10) {
        $elapsed = $now - $waitStart
        if ($proc) {
            Write-Host "  [等待 $($elapsed.ToString('hh\:mm\:ss'))] 训练运行中 (CPU=$($proc.CPU.ToString('F0'))%, WS=$([math]::Round($proc.WorkingSet64/1MB,1))MB)"
        } else {
            Write-Host "  [等待 $($elapsed.ToString('hh\:mm\:ss'))] 训练进程已退出，但权重文件尚未出现..." -ForegroundColor Red
            # 如果训练进程已退出但权重文件不存在，可能训练失败
            # 继续等待 5 分钟，如果仍未出现则报错
            if (($now - $waitStart).TotalMinutes -gt 30) {
                Write-Host "[错误] 训练进程已退出超过 30 分钟，权重文件仍未出现。请检查训练日志。" -ForegroundColor Red
                exit 1
            }
        }
        $lastLogTime = $now
    }
}

$weightsReady = Get-Date
Write-Host "[完成] 权重文件已生成: $weightsFile" -ForegroundColor Green
Write-Host "  文件大小: $([math]::Round((Get-Item $weightsFile).Length/1MB, 2)) MB"
Write-Host "  生成时间: $(($weightsReady - $waitStart).ToString('hh\:mm\:ss'))" -ForegroundColor Green

# === 阶段 2: 等待训练进程退出 ===
Write-Host ""
Write-Host "[阶段 2] 等待训练进程退出..." -ForegroundColor Yellow

$proc = Get-Process -Id $trainingPid -ErrorAction SilentlyContinue
if ($proc) {
    Write-Host "  训练进程仍在运行，等待其退出..."
    $proc.WaitForExit()
    Write-Host "[完成] 训练进程已退出" -ForegroundColor Green
} else {
    Write-Host "  训练进程已退出" -ForegroundColor Green
}

# 额外等待 10 秒，确保文件完全写入并释放句柄
Start-Sleep -Seconds 10

# === 阶段 3: 验证权重文件 ===
Write-Host ""
Write-Host "[阶段 3] 验证权重文件..." -ForegroundColor Yellow

try {
    $fileInfo = Get-Item $weightsFile
    Write-Host "  文件路径: $($fileInfo.FullName)"
    Write-Host "  文件大小: $([math]::Round($fileInfo.Length/1MB, 2)) MB"
    Write-Host "  修改时间: $($fileInfo.LastWriteTime)"
} catch {
    Write-Host "[错误] 无法访问权重文件: $_" -ForegroundColor Red
    exit 1
}

# === 阶段 4: 启动 UQ 实验 ===
Write-Host ""
Write-Host "[阶段 4] 启动贝叶斯 UQ 实验..." -ForegroundColor Yellow
Write-Host "  脚本: $uqScript"
Write-Host "  日志: $uqLog"
Write-Host "  开始时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host ""

if (Test-Path $uqLog) { Remove-Item $uqLog -Force }
if (Test-Path $uqErr) { Remove-Item $uqErr -Force }

$uqStart = Get-Date
$uqProc = Start-Process -FilePath $pythonExe `
    -ArgumentList "-u", $uqScript `
    -RedirectStandardOutput $uqLog `
    -RedirectStandardError $uqErr `
    -NoNewWindow -PassThru

Write-Host "  UQ 实验已启动，PID=$($uqProc.Id)"
$uqProc.WaitForExit()
$uqEnd = Get-Date
$uqDuration = $uqEnd - $uqStart

Write-Host ""
Write-Host "[完成] UQ 实验结束" -ForegroundColor Green
Write-Host "  退出码: $($uqProc.ExitCode)"
Write-Host "  耗时: $($uqDuration.ToString('hh\:mm\:ss'))"
Write-Host "  结束时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host ""

# === 阶段 5: 输出结果摘要 ===
Write-Host "=== UQ 实验日志（最后 50 行）===" -ForegroundColor Cyan
if (Test-Path $uqLog) {
    Get-Content $uqLog -Tail 50
} else {
    Write-Host "  日志文件不存在"
}

if (Test-Path $uqErr) {
    $errSize = (Get-Item $uqErr).Length
    if ($errSize -gt 0) {
        Write-Host ""
        Write-Host "=== 错误日志（最后 20 行）===" -ForegroundColor Red
        Get-Content $uqErr -Tail 20
    }
}

# === 阶段 6: 关键结果判定 ===
Write-Host ""
Write-Host "=== 关键结果判定 ===" -ForegroundColor Cyan

$jsonPath = Join-Path $scriptDir "results\bayesian_uq_results.json"
if (Test-Path $jsonPath) {
    Write-Host "结果文件: $jsonPath"
    Write-Host ""
    Write-Host "请手动检查 JSON 结果，关注以下关键指标：" -ForegroundColor Yellow
    Write-Host "  1. 6061-T6 的 std_mean 是否显著高于 45_Steel？"
    Write-Host "  2. OOD 检测 AUC 是否 > 0.7？"
    Write-Host "  3. 校准误差 ECE 是否 < 0.1？"
    Write-Host ""
    Write-Host "如果 6061-T6 不确定性显著高于其他材料，方向 D 成立，可以撰写论文。"
} else {
    Write-Host "[警告] 结果 JSON 文件未生成，请检查日志。" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== 全流程完成 ===" -ForegroundColor Green
Write-Host "完成时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
