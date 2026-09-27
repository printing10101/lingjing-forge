# ============================================================
#  灵境AI助手 — DeepSeek Harness 桌面端启动器
#  功能：
#    1. 检测本地 DSH 服务（127.0.0.1:3080）是否在线
#    2. 在线：用浏览器 App 模式（套壳）打开独立工作台窗口
#       - 使用专属配置目录，保证窗口始终独立弹出，
#         不与日常浏览器标签页混用
#    3. 离线：弹出提示，可重试
#  用法：powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "灵境AI助手.ps1"
# ============================================================

$ErrorActionPreference = 'SilentlyContinue'

$Url  = 'http://127.0.0.1:3080'
$Port = 3080

# ---------- 1. 探测服务是否在线（TCP 快速探测） ----------
function Test-ServerUp {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $task = $client.ConnectAsync('127.0.0.1', $Port)
        if ($task.Wait(2500) -and $client.Connected) {
            $client.Close()
            return $true
        }
        $client.Close()
    } catch {}
    return $false
}

# ---------- 2. 解析可用的套壳浏览器（Edge → Chrome → 默认浏览器） ----------
function Get-AppBrowser {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    )
    $exe = ($candidates | Where-Object { Test-Path $_ } | Select-Object -First 1)
    if (-not $exe) { return $null }

    # 专属配置目录：浏览器各自独立，避免冲突
    $name = if ($exe -match 'msedge\.exe$') { 'edge' }
            elseif ($exe -match 'chrome\.exe$') { 'chrome' }
            else { 'browser' }
    $profile = Join-Path "$env:LOCALAPPDATA\灵境AI助手" "$name-profile"

    return @{ Exe = $exe; Profile = $profile }
}

# ---------- 3. 主流程 ----------
$browser = Get-AppBrowser
$online  = Test-ServerUp

if ($online) {
    if ($browser) {
        $args = @(
            "--user-data-dir=$($browser.Profile)",
            "--app=$Url",
            '--window-size=1440,920',
            '--window-position=80,40',
            '--no-first-run',
            '--no-default-browser-check'
        )
        Start-Process -FilePath $browser.Exe -ArgumentList $args | Out-Null
    } else {
        Start-Process $Url
    }
} else {
    Add-Type -AssemblyName System.Windows.Forms
    $msg = "无法连接到 DeepSeek Harness 服务（$Url）。" + [Environment]::NewLine + [Environment]::NewLine + "请先启动 DSH 服务，然后点击 [重试] 按钮。"
    $r = [System.Windows.Forms.MessageBox]::Show($msg, '灵境AI助手', 'RetryCancel', 'Exclamation')
    if ($r -eq 'Retry') {
        $self = (Get-Process -Id $PID).Path
        Start-Process -FilePath $self -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', $MyInvocation.MyCommand.Path) | Out-Null
    }
}
