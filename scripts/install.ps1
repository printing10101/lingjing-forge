<#
.SYNOPSIS
    灵境制造 - 一键自动化安装脚本（Windows）。

.DESCRIPTION
    为普通车间工程师在 Windows 10 1809+/Windows 11 上自动化部署
    Python(嵌入式) / Rust / Node.js LTS / Ollama / Git LFS / VC++ 运行库
    以及"灵境制造"应用本体、数据库、模型与桌面快捷方式。

    使用方式：
        # 交互模式
        powershell -ExecutionPolicy Bypass -File scripts\install.ps1

        # 静默模式（仅输出日志，不弹出交互界面）
        powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -silent

.PARAMETER silent
    启用静默安装模式。脚本将不输出彩色交互 UI，仅写入日志文件。

.PARAMETER logDir
    自定义日志输出目录，默认为 %LOCALAPPDATA%\LingjingManufacturing\logs。

.PARAMETER downloadUrl
    自定义灵境制造应用包下载地址（用于内网/灰度场景）。

.NOTES
    Version : 1.0.0
    Author  : 灵境制造 SRE
    Requires: PowerShell 5.1 或更高版本
#>

[CmdletBinding()]
param(
    [switch]$silent,
    [string]$logDir,
    [string]$downloadUrl
)

# -----------------------------------------------------------------------------
# 严格模式与全局常量
# -----------------------------------------------------------------------------
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

# UTF-8 控制台输出，避免中文乱码
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$Script:AppName         = 'LingjingManufacturing'
$Script:DisplayName     = '灵境制造'
$Script:AppVersion      = '1.0.0'
$Script:RequiredOS      = '10.0'              # Windows 10 1809 == 10.0.17763
$Script:RequiredDiskGB  = 10
$Script:InstallRoot     = Join-Path $env:LOCALAPPDATA $Script:AppName
$Script:PythonVersion   = '3.11.9'
$Script:NodeMajor       = 20                  # LTS
$Script:RustToolchain   = 'stable'
$Script:LogFile         = $null
$Script:IsSilent        = [bool]$silent
$Script:StepCount       = 0
$Script:CurrentStep     = 0
$Script:StartTime       = Get-Date

# 应用包默认下载地址（生产环境请替换为内网 CDN）
if ([string]::IsNullOrWhiteSpace($downloadUrl)) {
    $Script:AppDownloadUrl = 'https://downloads.lingjing-manufacturing.example.com/latest/lingjing-manufacturing.zip'
    $Script:AppSha256      = ''
} else {
    $Script:AppDownloadUrl = $downloadUrl
    $Script:AppSha256      = ''
}

# 模型清单（文件名 / 远端相对路径 / 期望 SHA256 / 期望字节数）
$Script:Models = @(
    @{
        Name       = 'lnn-base'
        Relative   = 'models/lnn-base.bin'
        Sha256     = ''
        SizeBytes  = 0
    },
    @{
        Name       = 'embedding-base'
        Relative   = 'models/embedding-base.bin'
        Sha256     = ''
        SizeBytes  = 0
    }
)

# 国内镜像（按需调整；失败时脚本会回退到官方源）
$Script:NpmRegistry     = 'https://registry.npmmirror.com'
$Script:CargoRegistry   = 'rsproxy-sparse'
$Script:PypiIndex       = 'https://pypi.tuna.tsinghua.edu.cn/simple'

# -----------------------------------------------------------------------------
# 日志与 UI 工具
# -----------------------------------------------------------------------------
function Initialize-Log {
    [CmdletBinding()]
    param()
    try {
        if ([string]::IsNullOrWhiteSpace($logDir)) {
            $logDir = Join-Path $Script:InstallRoot 'logs'
        }
        if (-not (Test-Path $logDir)) {
            New-Item -ItemType Directory -Path $logDir -Force | Out-Null
        }
        $timestamp   = Get-Date -Format 'yyyyMMdd-HHmmss'
        $Script:LogFile = Join-Path $logDir "install-$timestamp.log"
        $Script:LatestLog = Join-Path $logDir 'install-latest.log'
    } catch {
        # 日志初始化失败不应阻塞安装；写入系统临时目录
        $Script:LogFile = Join-Path $env:TEMP "lingjing-install-$(Get-Date -Format 'yyyyMMdd-HHmmss').log"
    }
}

function Write-Log {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][ValidateSet('INFO','WARN','ERROR','DEBUG','STEP')][string]$Level,
        [Parameter(Mandatory)][string]$Message
    )
    $line = "[{0}] [{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'), $Level, $Message
    try {
        if ($Script:LogFile) {
            Add-Content -LiteralPath $Script:LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
            if ($Script:LatestLog -and ($Script:LatestLog -ne $Script:LogFile)) {
                Add-Content -LiteralPath $Script:LatestLog -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
            }
        }
    } catch { }
    if (-not $Script:IsSilent) {
        $color = switch ($Level) {
            'ERROR' { 'Red' }
            'WARN'  { 'Yellow' }
            'STEP'  { 'Cyan' }
            'DEBUG' { 'DarkGray' }
            default { 'Gray' }
        }
        try { Write-Host $line -ForegroundColor $color } catch { Write-Host $line }
    }
}

function Write-Step {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][int]$Step,
        [Parameter(Mandatory)][int]$Total,
        [Parameter(Mandatory)][string]$Title
    )
    $Script:CurrentStep = $Step
    $Script:StepCount   = $Total
    $percent = [math]::Floor(($Step / $Total) * 100)
    Write-Log -Level STEP -Message "($Step/$Total  ${percent}%) $Title"
    if (-not $Script:IsSilent) {
        Write-Host ""
        Write-Host "==> [$Step/$Total] $Title  (${percent}%)" -ForegroundColor Cyan
        Write-Host ("-" * 60) -ForegroundColor DarkCyan
    }
}

function Write-Progress-Step {
    [CmdletBinding()]
    param([string]$Activity, [string]$Status)
    if ($Script:IsSilent) { return }
    $overall = if ($Script:StepCount -gt 0) {
        [math]::Floor(($Script:CurrentStep / $Script:StepCount) * 100)
    } else { 0 }
    try {
        Write-Progress -Id 1 -Activity $Activity -Status $Status -PercentComplete $overall
    } catch { }
}

function Test-IsAdministrator {
    [CmdletBinding()] param()
    $id  = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr  = New-Object Security.Principal.WindowsPrincipal($id)
    return $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Request-Administrator {
    [CmdletBinding()]
    param([string]$ScriptPath)
    if (Test-IsAdministrator) { return $true }
    Write-Log -Level WARN -Message '当前进程非管理员权限，正在尝试提升…'
    $args = @('-NoProfile','-ExecutionPolicy','Bypass','-File', "`"$ScriptPath`"")
    if ($Script:IsSilent) { $args += '-silent' }
    try {
        $proc = Start-Process -FilePath 'powershell.exe' -ArgumentList $args -Verb RunAs -PassThru -WindowStyle Hidden
        $proc.WaitForExit()
        return ($proc.ExitCode -eq 0)
    } catch {
        Write-Log -Level ERROR -Message "无法提升到管理员权限：$($_.Exception.Message)"
        return $false
    }
}

# -----------------------------------------------------------------------------
# 系统环境检查
# -----------------------------------------------------------------------------
function Test-OperatingSystem {
    [CmdletBinding()]
    param()
    try {
        $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
    } catch {
        throw "无法获取操作系统信息：$($_.Exception.Message)"
    }
    $version = [Version]$os.Version
    Write-Log -Level INFO -Message "检测到操作系统：$($os.Caption)  版本=$($os.Version)"
    if ($version.Major -lt 10) {
        throw "不支持的操作系统：$($os.Caption)。需要 Windows 10 1809 或更高版本。"
    }
    if (($version.Major -eq 10) -and ($version.Build -lt 17763)) {
        throw "Windows 10 版本过低（Build $($version.Build)），请升级到 1809 (Build 17763) 或更高。"
    }
    return $true
}

function Test-DiskSpace {
    [CmdletBinding()]
    param([string]$Path = $Script:InstallRoot, [int]$RequiredGB = $Script:RequiredDiskGB)
    $drive = (Get-Item $Path).PSDrive.Name
    if (-not $drive) { $drive = (Get-Item $Path -ErrorAction SilentlyContinue).PSDrive.Name }
    if (-not $drive) {
        # 兜底：使用 %LOCALAPPDATA% 所在盘符
        $drive = (Split-Path -Qualifier $env:LOCALAPPDATA) -replace ':',''
    }
    $freeBytes = (Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DeviceID='${drive}:'" -ErrorAction SilentlyContinue).FreeSpace
    if (-not $freeBytes) { throw "无法获取磁盘 ${drive}: 剩余空间。" }
    $freeGB = [math]::Round($freeBytes / 1GB, 2)
    Write-Log -Level INFO -Message "目标盘符 ${drive}: 剩余空间 ${freeGB} GB"
    if ($freeGB -lt $RequiredGB) {
        throw "磁盘空间不足：${drive}: 仅剩 ${freeGB} GB，至少需要 ${RequiredGB} GB。"
    }
    return $true
}

function Test-NetworkConnectivity {
    [CmdletBinding()]
    param()
    $probes = @(
        @{ Name = 'GitHub';     Url = 'https://github.com' },
        @{ Name = 'Ollama';     Url = 'https://ollama.mirror.cn' },
        @{ Name = 'Rust';       Url = 'https://rsproxy.cn' },
        @{ Name = 'Node.js';    Url = 'https://npmmirror.com' },
        @{ Name = 'Microsoft';  Url = 'https://aka.ms' }
    )
    $results = @()
    foreach ($p in $probes) {
        try {
            $null = Invoke-WebRequest -Uri $p.Url -UseBasicParsing -Method Head -TimeoutSec 8 -ErrorAction Stop
            $results += @{ Name = $p.Name; Ok = $true }
        } catch {
            $results += @{ Name = $p.Name; Ok = $false; Err = $_.Exception.Message }
        }
    }
    $failed = $results | Where-Object { -not $_.Ok }
    foreach ($r in $results) {
        if ($r.Ok) {
            Write-Log -Level INFO -Message "网络连通：$($r.Name)"
        } else {
            Write-Log -Level WARN -Message "网络不通：$($r.Name)  ($($r.Err))"
        }
    }
    if ($failed.Count -ge $results.Count) {
        throw "网络完全不可用，请检查代理/防火墙设置。"
    }
    return $true
}

function Invoke-SystemChecks {
    [CmdletBinding()]
    param()
    Write-Step -Step 1 -Total 9 -Title '系统环境检查'
    Write-Progress-Step -Activity '系统环境检查' -Status '检查操作系统版本…'
    Test-OperatingSystem

    Write-Progress-Step -Activity '系统环境检查' -Status '检查磁盘空间…'
    Test-DiskSpace

    Write-Progress-Step -Activity '系统环境检查' -Status '检测管理员权限…'
    if (-not (Test-IsAdministrator)) {
        $self = $MyInvocation.ScriptName
        if ([string]::IsNullOrEmpty($self)) { $self = $PSCommandPath }
        if ([string]::IsNullOrEmpty($self)) { $self = "$PSScriptRoot\install.ps1" }
        if (-not (Request-Administrator -ScriptPath $self)) {
            throw '需要管理员权限才能完成安装，请右键以管理员身份运行。'
        }
        # 当前进程应当被替换，理论上不会执行到这里
        exit 0
    }

    Write-Progress-Step -Activity '系统环境检查' -Status '测试网络连通性…'
    Test-NetworkConnectivity

    Write-Log -Level INFO -Message '系统环境检查通过。'
}

# -----------------------------------------------------------------------------
# 通用下载 / 解压 / 校验工具
# -----------------------------------------------------------------------------
function Invoke-HttpDownload {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Url,
        [Parameter(Mandatory)][string]$OutFile,
        [string]$ExpectedSha256,
        [int]$TimeoutSec = 1800
    )
    if (Test-Path $OutFile) { Remove-Item $OutFile -Force -ErrorAction SilentlyContinue }
    Write-Log -Level INFO -Message "下载 $Url -> $OutFile"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor `
                                                        [Net.SecurityProtocolType]::Tls13
    } catch {}
    $dir = Split-Path -Parent $OutFile
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $ok = $false
    try {
        Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
        $ok = $true
    } catch {
        Write-Log -Level WARN -Message "Invoke-WebRequest 失败，尝试 curl：$($_.Exception.Message)"
        try {
            $curl = Get-Command curl.exe -ErrorAction Stop
            & $curl.Source -L --fail --silent --show-error -o $OutFile $Url
            if ($LASTEXITCODE -eq 0) { $ok = $true }
        } catch {
            Write-Log -Level WARN -Message "curl 不可用：$($_.Exception.Message)"
        }
    }
    if (-not $ok) { throw "下载失败：$Url" }
    if (-not (Test-Path $OutFile)) { throw "下载后文件不存在：$OutFile" }

    if (-not [string]::IsNullOrWhiteSpace($ExpectedSha256)) {
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $OutFile).Hash
        if ($actual -ne $ExpectedSha256) {
            throw "SHA256 校验失败：期望 $ExpectedSha256，实际 $actual"
        }
        Write-Log -Level INFO -Message 'SHA256 校验通过。'
    }
    return $true
}

function Expand-ArchiveSafe {
    [CmdletBinding()]
    param([string]$Path, [string]$Destination)
    if (-not (Test-Path $Destination)) { New-Item -ItemType Directory -Path $Destination -Force | Out-Null }
    Write-Log -Level INFO -Message "解压 $Path -> $Destination"
    Expand-Archive -LiteralPath $Path -DestinationPath $Destination -Force -ErrorAction Stop
}

# -----------------------------------------------------------------------------
# 组件安装模块
# -----------------------------------------------------------------------------

# --- VC++ 运行库 ---
function Install-VCRedist {
    [CmdletBinding()]
    param()
    Write-Step -Step 2 -Total 9 -Title '安装 Visual C++ 运行库'
    Write-Progress-Step -Activity 'VC++ 运行库' -Status '检测已安装版本…'

    $installed = $false
    try {
        $keys = @(
            'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64',
            'HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64',
            'HKLM:\SOFTWARE\Microsoft\VisualStudio\2015-2022\BuildTools\VC\Runtimes\x64'
        )
        foreach ($k in $keys) {
            $v = Get-ItemProperty -Path $k -ErrorAction SilentlyContinue
            if ($v -and $v.Major -ge 14) { $installed = $true; break }
        }
    } catch {}

    if ($installed) {
        Write-Log -Level INFO -Message 'VC++ 运行库已安装，跳过。'
        return
    }

    $tmp = Join-Path $env:TEMP "vcredist_x64_$Script:AppVersion.exe"
    $url = 'https://aka.ms/vs/17/release/vc_redist.x64.exe'
    Invoke-HttpDownload -Url $url -OutFile $tmp
    Write-Progress-Step -Activity 'VC++ 运行库' -Status '执行安装程序…'
    $p = Start-Process -FilePath $tmp -ArgumentList '/install','/passive','/norestart' -Wait -PassThru
    if ($p.ExitCode -notin @(0, 3010, 1638, 1639)) {
        throw "VC++ 运行库安装失败，退出码 $($p.ExitCode)"
    }
    Write-Log -Level INFO -Message "VC++ 运行库安装完成，退出码 $($p.ExitCode)。"
}

# --- Git + Git LFS ---
function Install-GitLfs {
    [CmdletBinding()]
    param()
    Write-Step -Step 3 -Total 9 -Title '安装 Git 与 Git LFS'
    Write-Progress-Step -Activity 'Git & Git LFS' -Status '检测已安装版本…'

    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($git) {
        $ver = & git --version
        Write-Log -Level INFO -Message "已检测到 Git：$ver"
    } else {
        $tmp = Join-Path $env:TEMP "git-installer_$Script:AppVersion.exe"
        $url = 'https://github.com/git-for-windows/git/releases/download/v2.46.0.windows.1/Git-2.46.0-64-bit.exe'
        Invoke-HttpDownload -Url $url -OutFile $tmp
        Write-Progress-Step -Activity 'Git & Git LFS' -Status '安装 Git…'
        $p = Start-Process -FilePath $tmp -ArgumentList '/VERYSILENT','/NORESTART','/NOCANCEL','/SP-','/CLOSEAPPLICATIONS','/RESTARTAPPLICATIONS' -Wait -PassThru
        if ($p.ExitCode -notin @(0, 3010)) {
            throw "Git 安装失败，退出码 $($p.ExitCode)"
        }
        # 刷新 PATH
        $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path','User')
    }

    Write-Progress-Step -Activity 'Git & Git LFS' -Status '安装 Git LFS…'
    $lfs = Join-Path $Script:InstallRoot 'tools\git-lfs'
    if (-not (Test-Path $lfs)) { New-Item -ItemType Directory -Path $lfs -Force | Out-Null }
    $tmpLfs = Join-Path $env:TEMP 'git-lfs-windows-amd64.zip'
    Invoke-HttpDownload -Url 'https://github.com/git-lfs/git-lfs/releases/download/v3.5.1/git-lfs-windows-amd64-v3.5.1.zip' -OutFile $tmpLfs
    Expand-ArchiveSafe -Path $tmpLfs -Destination $lfs
    $lfsExe = Join-Path $lfs 'git-lfs.exe'
    if (-not (Test-Path $lfsExe)) { throw 'Git LFS 解压后未找到 git-lfs.exe' }

    & $lfsExe install | Out-Null
    Write-Log -Level INFO -Message 'Git LFS 已安装并初始化。'

    Write-Progress-Step -Activity 'Git & Git LFS' -Status '配置全局用户信息…'
    try {
        & git config --global user.name  'lingjing-engineer' | Out-Null
        & git config --global user.email 'engineer@lingjing.local' | Out-Null
        & git config --global init.defaultBranch main | Out-Null
        & git config --global core.autocrlf false | Out-Null
        & git config --global core.longpaths true | Out-Null
    } catch {
        Write-Log -Level WARN -Message "设置 Git 全局配置失败：$($_.Exception.Message)"
    }
}

# --- Python (嵌入式) ---
function Install-PythonEmbedded {
    [CmdletBinding()]
    param()
    Write-Step -Step 4 -Total 9 -Title '安装嵌入式 Python'
    Write-Progress-Step -Activity 'Python' -Status '下载嵌入式发行版…'

    $pyDir = Join-Path $Script:InstallRoot 'tools\python'
    if (Test-Path (Join-Path $pyDir 'python.exe')) {
        $ver = & (Join-Path $pyDir 'python.exe') -c 'import sys;print(sys.version.split()[0])' 2>$null
        if ($ver -and $ver.StartsWith('3.11.')) {
            Write-Log -Level INFO -Message "嵌入式 Python 已存在：$ver"
            Add-PythonEnv -PyDir $pyDir
            return
        }
    }
    if (Test-Path $pyDir) { Remove-Item $pyDir -Recurse -Force }
    New-Item -ItemType Directory -Path $pyDir -Force | Out-Null

    $zip = Join-Path $env:TEMP "python-$Script:PythonVersion-embed-amd64.zip"
    $url = "https://www.python.org/ftp/python/$Script:PythonVersion/python-$Script:PythonVersion-embed-amd64.zip"
    Invoke-HttpDownload -Url $url -OutFile $zip
    Expand-ArchiveSafe -Path $zip -Destination $pyDir

    # 启用 site-packages + pip
    $pth = Join-Path $pyDir 'python311._pth'
    if (Test-Path $pth) {
        (Get-Content $pth) -replace '^#import site', 'import site' | Set-Content $pth -Encoding ASCII
    }
    $pipPyz = Join-Path $env:TEMP 'get-pip.py'
    if (-not (Test-Path $pipPyz)) {
        Invoke-HttpDownload -Url 'https://bootstrap.pypa.io/get-pip.py' -OutFile $pipPyz
    }
    $pythonExe = Join-Path $pyDir 'python.exe'
    Write-Progress-Step -Activity 'Python' -Status '引导 pip…'
    & $pythonExe $pipPyz -i $Script:PypiIndex --no-warn-script-location | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'pip 引导失败' }

    Add-PythonEnv -PyDir $pyDir
    Write-Log -Level INFO -Message "嵌入式 Python $Script:PythonVersion 安装完成。"
}

function Add-PythonEnv {
    [CmdletBinding()]
    param([string]$PyDir)
    $pyBin = Join-Path $PyDir
    # 进程内 PATH 立即生效
    $env:Path = "$PyDir;$PyBin\Scripts;$env:Path"
    # 持久化到用户环境变量（不污染系统全局）
    [Environment]::SetEnvironmentVariable('LJ_PYTHON_HOME', $PyDir, 'User')
    [Environment]::SetEnvironmentVariable('LJ_PYTHON_BIN', "$PyDir;$PyDir\Scripts", 'User')
}

# --- Rust ---
function Install-Rust {
    [CmdletBinding()]
    param()
    Write-Step -Step 5 -Total 9 -Title '安装 Rust 工具链'
    Write-Progress-Step -Activity 'Rust' -Status '检测已安装版本…'

    $cargo = Get-Command cargo.exe -ErrorAction SilentlyContinue
    if ($cargo) {
        Write-Log -Level INFO -Message "已检测到 Cargo：$(& cargo --version)"
    } else {
        $tmp = Join-Path $env:TEMP 'rustup-init.exe'
        Invoke-HttpDownload -Url 'https://win.rustup.rs/x86_64' -OutFile $tmp
        Write-Progress-Step -Activity 'Rust' -Status '运行 rustup-init…'
        $p = Start-Process -FilePath $tmp -ArgumentList '-y','--default-toolchain',$Script:RustToolchain,'--profile','minimal','--no-modify-path' -Wait -PassThru
        if ($p.ExitCode -ne 0) { throw "rustup-init 失败，退出码 $($p.ExitCode)" }
        # 注入 PATH
        $cargoHome = Join-Path $env:USERPROFILE '.cargo\bin'
        $env:Path = "$cargoHome;$env:Path"
    }

    $cargoHome = Join-Path $env:USERPROFILE '.cargo'
    Write-Progress-Step -Activity 'Rust' -Status '配置国内镜像…'
    $cfgDir = Join-Path $cargoHome 'config.toml'
    $cfgBody = @"
[source.crates-io]
replace-with = "rsproxy-sparse"

[source.rsproxy]
registry = "https://rsproxy.cn/crates.io-sparse"

[source.rsproxy-sparse]
registry = "sparse+https://rsproxy.cn/index/"

[registries.rsproxy]
index = "https://rsproxy.cn/crates.io-sparse"

[net]
git-fetch-with-cli = true
"@
    Set-Content -LiteralPath $cfgDir -Value $cfgBody -Encoding UTF8 -Force
    Write-Log -Level INFO -Message "Rust 配置已写入 $cfgDir"
}

# --- Node.js LTS ---
function Install-NodeJs {
    [CmdletBinding()]
    param()
    Write-Step -Step 6 -Total 9 -Title '安装 Node.js LTS'
    Write-Progress-Step -Activity 'Node.js' -Status '检测已安装版本…'

    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($node) {
        $ver = & node --version
        Write-Log -Level INFO -Message "已检测到 Node.js：$ver"
    } else {
        $tmp = Join-Path $env:TEMP "node-lts-installer_$Script:AppVersion.msi"
        $url = 'https://nodejs.org/dist/v20.17.0/node-v20.17.0-x64.msi'
        Invoke-HttpDownload -Url $url -OutFile $tmp
        Write-Progress-Step -Activity 'Node.js' -Status '执行 MSI 安装…'
        $p = Start-Process -FilePath 'msiexec.exe' -ArgumentList '/i', $tmp, '/qn', '/norestart', 'ADDLOCAL=ALL' -Wait -PassThru
        if ($p.ExitCode -notin @(0, 3010, 1602, 1603)) {
            throw "Node.js 安装失败，退出码 $($p.ExitCode)"
        }
        $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path','User')
    }

    Write-Progress-Step -Activity 'Node.js' -Status '配置 npm 镜像…'
    try {
        & npm config set registry $Script:NpmRegistry | Out-Null
        & npm config set fund false | Out-Null
        & npm config set audit false | Out-Null
    } catch {
        Write-Log -Level WARN -Message "设置 npm registry 失败：$($_.Exception.Message)"
    }
    Write-Log -Level INFO -Message "Node.js / npm 就绪。"
}

# --- Ollama ---
function Install-Ollama {
    [CmdletBinding()]
    param()
    Write-Step -Step 7 -Total 9 -Title '安装 Ollama 本地推理引擎'
    Write-Progress-Step -Activity 'Ollama' -Status '检测已安装版本…'

    $ollama = Get-Command ollama.exe -ErrorAction SilentlyContinue
    if ($ollama) {
        Write-Log -Level INFO -Message "已检测到 Ollama：$(& ollama --version)"
    } else {
        $tmp = Join-Path $env:TEMP "OllamaSetup_$Script:AppVersion.exe"
        # 优先使用国内镜像，失败时回退到官方源
        $url = 'https://ollama.mirror.cn/download/OllamaSetup.exe'
        try {
            Invoke-HttpDownload -Url $url -OutFile $tmp
        } catch {
            Write-Log -Level WARN -Message "国内镜像下载失败，尝试官方源..."
            $url = 'https://ollama.com/download/OllamaSetup.exe'
            Invoke-HttpDownload -Url $url -OutFile $tmp
        }
        Write-Progress-Step -Activity 'Ollama' -Status '执行安装程序…'
        $p = Start-Process -FilePath $tmp -ArgumentList '/S' -Wait -PassThru
        if ($p.ExitCode -ne 0) { throw "Ollama 安装失败，退出码 $($p.ExitCode)" }
        $ollamaExe = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
        if (-not (Test-Path $ollamaExe)) { $ollamaExe = (Get-Command ollama.exe -ErrorAction SilentlyContinue).Source }
        if (-not $ollamaExe) { throw 'Ollama 安装后无法定位可执行文件。' }
    }

    Write-Progress-Step -Activity 'Ollama' -Status '配置并启动服务…'
    $ollamaHome = Join-Path $Script:InstallRoot 'ollama'
    if (-not (Test-Path $ollamaHome)) { New-Item -ItemType Directory -Path $ollamaHome -Force | Out-Null }
    [Environment]::SetEnvironmentVariable('OLLAMA_HOME', $ollamaHome, 'User')
    [Environment]::SetEnvironmentVariable('OLLAMA_HOST', '127.0.0.1:11434', 'User')

    # 启动服务（前台/后台均可，此处采用后台方式并验证 200 OK）
    $svc = Get-Process -Name 'ollama' -ErrorAction SilentlyContinue
    if (-not $svc) {
        try {
            Start-Process -FilePath 'ollama.exe' -ArgumentList 'serve' -WindowStyle Hidden -ErrorAction SilentlyContinue
        } catch {
            Write-Log -Level WARN -Message "无法直接启动 ollama serve：$($_.Exception.Message)，尝试任务计划程序…"
            # 可选：注册 Windows 服务并启动
            try {
                & ollama.exe serve --install 2>&1 | Out-Null
                Start-Sleep -Seconds 2
            } catch {}
        }
    }

    Write-Progress-Step -Activity 'Ollama' -Status '验证服务连通性…'
    $ok = $false
    for ($i = 0; $i -lt 15; $i++) {
        try {
            $r = Invoke-WebRequest -Uri 'http://127.0.0.1:11434/api/tags' -UseBasicParsing -TimeoutSec 4 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { $ok = $true; break }
        } catch { Start-Sleep -Seconds 2 }
    }
    if (-not $ok) {
        Write-Log -Level WARN -Message 'Ollama 服务在 30s 内未就绪，可在安装后通过"灵境制造"应用手动启动。'
    } else {
        Write-Log -Level INFO -Message 'Ollama 服务已就绪。'
    }
}

# -----------------------------------------------------------------------------
# 应用部署
# -----------------------------------------------------------------------------
function Install-ApplicationPackage {
    [CmdletBinding()]
    param()
    Write-Step -Step 8 -Total 9 -Title '下载并部署应用包'
    Write-Progress-Step -Activity '应用部署' -Status '下载应用包…'

    $appDir = $Script:InstallRoot
    if (-not (Test-Path $appDir)) { New-Item -ItemType Directory -Path $appDir -Force | Out-Null }

    $zip = Join-Path $env:TEMP 'lingjing-manufacturing.zip'
    Invoke-HttpDownload -Url $Script:AppDownloadUrl -OutFile $zip -ExpectedSha256 $Script:AppSha256
    Expand-ArchiveSafe -Path $zip -Destination $appDir
    Remove-Item $zip -Force

    Write-Progress-Step -Activity '应用部署' -Status '初始化数据库…'
    $dbScript = Join-Path $appDir 'scripts\init_db.py'
    if (Test-Path $dbScript) {
        $py = Join-Path $Script:InstallRoot 'tools\python\python.exe'
        & $py $dbScript 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Log -Level WARN -Message "数据库初始化脚本返回非零退出码 $LASTEXITCODE"
        } else {
            Write-Log -Level INFO -Message '数据库初始化完成。'
        }
    } else {
        Write-Log -Level WARN -Message "未找到数据库初始化脚本：$dbScript，跳过。"
    }

    Write-Progress-Step -Activity '应用部署' -Status '下载基础模型…'
    $modelsDir = Join-Path $appDir 'models'
    if (-not (Test-Path $modelsDir)) { New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null }
    foreach ($m in $Script:Models) {
        $target = Join-Path $modelsDir (Split-Path -Leaf $m.Relative)
        if (Test-Path $target) {
            Write-Log -Level INFO -Message "模型已存在，跳过：$target"
            continue
        }
        $url = "$($Script:AppDownloadUrl.TrimEnd('/'))/$($m.Relative -replace '\\','/')"
        try {
            Invoke-HttpDownload -Url $url -OutFile $target -ExpectedSha256 $m.Sha256
            Write-Log -Level INFO -Message "模型下载完成：$target"
        } catch {
            Write-Log -Level WARN -Message "模型下载失败：$url  ($($_.Exception.Message))，可在应用内重试。"
        }
    }

    Write-Progress-Step -Activity '应用部署' -Status '创建桌面快捷方式…'
    New-DesktopShortcut
}

function New-DesktopShortcut {
    [CmdletBinding()]
    param()
    try {
        $shell    = New-Object -ComObject WScript.Shell
        $desktop  = [Environment]::GetFolderPath('Desktop')
        $lnkPath  = Join-Path $desktop "$Script:DisplayName.lnk"
        $startExe = Join-Path $Script:InstallRoot 'LingjingManufacturing.exe'
        if (-not (Test-Path $startExe)) {
            $startExe = Join-Path $Script:InstallRoot 'lingjing-manufacturing.exe'
        }
        if (-not (Test-Path $startExe)) {
            $startExe = Join-Path $Script:InstallRoot 'start.bat'
        }
        $iconFile = Join-Path $Script:InstallRoot 'assets\app.ico'
        if (-not (Test-Path $iconFile)) { $iconFile = $startExe }

        $shortcut = $shell.CreateShortcut($lnkPath)
        $shortcut.TargetPath       = $startExe
        $shortcut.WorkingDirectory = $Script:InstallRoot
        $shortcut.IconLocation     = "$iconFile,0"
        $shortcut.Description      = "$Script:DisplayName  桌面启动"
        $shortcut.WindowStyle      = 1
        $shortcut.Save()
        Write-Log -Level INFO -Message "桌面快捷方式已创建：$lnkPath"
    } catch {
        Write-Log -Level WARN -Message "创建桌面快捷方式失败：$($_.Exception.Message)"
    }
}

# -----------------------------------------------------------------------------
# 完成
# -----------------------------------------------------------------------------
function Complete-Installation {
    [CmdletBinding()]
    param()
    Write-Step -Step 9 -Total 9 -Title '收尾与自检'
    Write-Progress-Step -Activity '收尾' -Status '清理临时文件…'
    try {
        Get-ChildItem -Path $env:TEMP -Filter '*.tmp' -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTime -lt (Get-Date).AddHours(-1) } |
            Remove-Item -Force -ErrorAction SilentlyContinue
    } catch {}

    Write-Progress-Step -Activity '收尾' -Status '汇总安装结果…'
    $duration = (Get-Date) - $Script:StartTime
    $summary = @"

============================================================
 $Script:DisplayName  安装完成
------------------------------------------------------------
 安装目录 : $Script:InstallRoot
 日志文件 : $($Script:LogFile)
 耗时     : $($duration.ToString('hh\:mm\:ss'))
============================================================
"@
    Write-Log -Level INFO -Message $summary
    try { Write-Progress -Id 1 -Activity '完成' -Completed } catch {}
    if (-not $Script:IsSilent) {
        Write-Host $summary -ForegroundColor Green
    }
}

# -----------------------------------------------------------------------------
# 主流程
# -----------------------------------------------------------------------------
function Invoke-Main {
    [CmdletBinding()]
    param()
    Initialize-Log
    Write-Log -Level INFO -Message "=== 灵境制造 安装开始  版本 $Script:AppVersion ==="
    Write-Log -Level INFO -Message "PowerShell 版本: $($PSVersionTable.PSVersion)"
    Write-Log -Level INFO -Message "静默模式: $Script:IsSilent"
    Write-Log -Level INFO -Message "日志文件: $Script:LogFile"

    try {
        Invoke-SystemChecks
        Install-VCRedist
        Install-GitLfs
        Install-PythonEmbedded
        Install-Rust
        Install-NodeJs
        Install-Ollama
        Install-ApplicationPackage
        Complete-Installation
    } catch {
        $msg = "安装失败：$($_.Exception.Message)`n$($_.ScriptStackTrace)"
        Write-Log -Level ERROR -Message $msg
        if (-not $Script:IsSilent) {
            Write-Host ""
            Write-Host "[错误] 安装未完成：$($_.Exception.Message)" -ForegroundColor Red
            Write-Host "请查看日志：$Script:LogFile" -ForegroundColor Yellow
        }
        exit 1
    } finally {
        if (-not $Script:IsSilent) {
            try { Read-Host '按 Enter 键退出' | Out-Null } catch {}
        }
    }
}

Invoke-Main
