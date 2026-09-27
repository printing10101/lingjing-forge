# 安装常见错误处理手册

> 适用脚本：[scripts/install.ps1](file:///c:/Users/Lenovo/Desktop/%E7%81%B5%E5%A2%83%E5%88%B6%E9%80%A0%EF%BC%88%E4%B8%8A%E7%BA%BF%E7%89%88%EF%BC%89/scripts/install.ps1)
> 排错第一步：打开 `install-latest.log` 并搜索 `\[ERROR\]` / `\[WARN\]`。

---

## 目录

- [A. 系统环境类](#a-系统环境类)
  - [A1. 操作系统版本过低](#a1-操作系统版本过低)
  - [A2. 磁盘空间不足](#a2-磁盘空间不足)
  - [A3. 网络不可用](#a3-网络不可用)
  - [A4. 缺少管理员权限](#a4-缺少管理员权限)
- [B. 下载与校验类](#b-下载与校验类)
  - [B1. 应用包下载失败](#b1-应用包下载失败)
  - [B2. SHA256 校验失败](#b2-sha256-校验失败)
  - [B3. 模型文件下载失败](#b3-模型文件下载失败)
- [C. 组件安装类](#c-组件安装类)
  - [C1. VC++ 运行库安装失败](#c1-vc-运行库安装失败)
  - [C2. Git / Git LFS 安装失败](#c2-git--git-lfs-安装失败)
  - [C3. Python 嵌入式安装失败](#c3-python-嵌入式安装失败)
  - [C4. Rust 安装失败](#c4-rust-安装失败)
  - [C5. Node.js 安装失败](#c5-nodejs-安装失败)
  - [C6. Ollama 安装/启动失败](#c6-ollama-安装启动失败)
- [D. 应用部署类](#d-应用部署类)
  - [D1. 数据库初始化失败](#d1-数据库初始化失败)
  - [D2. 桌面快捷方式创建失败](#d2-桌面快捷方式创建失败)
- [E. 升级与重装](#e-升级与重装)
- [F. 一键诊断命令](#f-一键诊断命令)

---

## A. 系统环境类

### A1. 操作系统版本过低

**日志特征**

```
[ERROR] 不支持的操作系统：Windows 10 xxx。版本过低（Build 10240），请升级到 1809 (Build 17763) 或更高。
```

**修复**

1. 打开「设置 → 更新和安全 → Windows 更新」安装最新累积更新。
2. 若设备长期未更新，可使用 [Windows 10 易升](https://www.microsoft.com/zh-cn/software-download/windows10) 触发版本升级。
3. 升级后重新运行 `install.ps1`。

### A2. 磁盘空间不足

**日志特征**

```
[ERROR] 磁盘空间不足：C: 仅剩 4.21 GB，至少需要 10 GB。
```

**修复**

- 清理临时文件：`cleanmgr /d C` → 勾选"临时文件"和"Windows 更新清理"。
- 卸载未使用的应用程序。
- 调整 `%LOCALAPPDATA%` 所在盘符（高级）：将 `%LOCALAPPDATA%` 软链到其他盘。

  ```powershell
  # 谨慎操作；建议先备份
  robocopy "$env:LOCALAPPDATA" "D:\AppData\Local" /MIR /XJ
  rmdir "$env:LOCALAPPDATA"
  New-Item -ItemType Junction -Path "$env:LOCALAPPDATA" -Target "D:\AppData\Local"
  ```

### A3. 网络不可用

**日志特征**

```
[WARN] 网络不通：Rust  (The remote name could not be resolved: sh.rustup.rs)
[ERROR] 网络完全不可用，请检查代理/防火墙设置。
```

**修复**

1. 打开浏览器访问 https://github.com 验证基础连通性。
2. 企业代理：在脚本开头追加：

   ```powershell
   [System.Net.WebRequest]::DefaultWebProxy = New-Object System.Net.WebProxy("http://proxy.corp:8080", $true)
   [System.Net.WebRequest]::DefaultWebProxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials
   ```

3. 防火墙拦截：允许 `powershell.exe`、`curl.exe`、`msiexec.exe` 出站。
4. 镜像源临时不可用：编辑脚本中 `$Script:NpmRegistry` / `$Script:CargoRegistry` / `$Script:PypiIndex` 三个常量。

### A4. 缺少管理员权限

**日志特征**

```
[WARN] 当前进程非管理员权限，正在尝试提升…
[ERROR] 无法提升到管理员权限：拒绝访问。
```

或 UAC 弹窗被静默拒绝。

**修复**

- 始终通过 **右键 → 以管理员身份运行** PowerShell。
- 企业域环境 GPO 关闭了 UAC：联系 IT 在 GPMC 中调整 `User Account Control: Run all administrators in Admin Approval Mode`。
- 远程场景使用 `PsExec -s` 或 WinRM：

  ```powershell
  Invoke-Command -ComputerName TARGET -FilePath .\install.ps1 -ArgumentList '-silent' -Authentication Kerberos
  ```

---

## B. 下载与校验类

### B1. 应用包下载失败

**日志特征**

```
[ERROR] 下载失败：https://downloads.lingjing-manufacturing.example.com/latest/lingjing-manufacturing.zip
```

**修复**

1. 检查 `-downloadUrl` 是否能直接浏览器访问。
2. 重新执行脚本（脚本会跳过已存在且校验通过的文件）。
3. 手动下载后放置到 `%TEMP%`：

   ```powershell
   # 重新触发安装
   .\scripts\install.ps1 -downloadUrl "file:///C:/Users/admin/Downloads/lingjing-manufacturing.zip"
   ```

### B2. SHA256 校验失败

**日志特征**

```
[ERROR] SHA256 校验失败：期望 <hash>，实际 <hash>
```

**修复**

- 校验值不一致说明下载过程被劫持 / 中断 / 镜像源同步出错。重新执行安装。
- 长期方案：在脚本中维护 `Script:AppSha256` 常量时，**每次发布**同步更新并通过签名包分发。

### B3. 模型文件下载失败

**日志特征**

```
[WARN] 模型下载失败：https://.../models/lnn-base.bin  (The remote server returned an error: (403) Forbidden)
```

**说明**

- 脚本对此类错误**不会失败退出**；应用可在启动后再补齐模型。
- 若在离线环境安装，需预先将 `lnn-base.bin` / `embedding-base.bin` 拷贝到 `%LOCALAPPDATA%\LingjingManufacturing\models\` 后重新运行脚本，脚本会跳过已存在文件。

---

## C. 组件安装类

### C1. VC++ 运行库安装失败

**日志特征**

```
[ERROR] VC++ 运行库安装失败，退出码 1603
```

**退出码速查**

| 码 | 含义 | 处理 |
|----|------|------|
| 0 / 3010 | 成功 / 需重启 | 忽略 |
| 1602 | 用户取消 | 重新运行，不要在脚本运行时手动操作安装器 |
| 1603 | 安装过程中发生致命错误 | 见下方排查 |
| 1638 / 1639 | 已有更新版本 | 脚本已视为成功，可忽略 |

**1603 排查**

1. 检查是否有残留 MSI 安装：结束 `msiexec.exe` 后重试。
2. 清理 `%TEMP%` 中的 `dd_*` 目录。
3. 运行官方 `vc_redist.x64.exe /repair` 修复已安装版本。
4. 极少见：磁盘写入权限问题；用 `icacls "%LOCALAPPDATA%"` 检查 ACL。

### C2. Git / Git LFS 安装失败

**日志特征**

```
[ERROR] Git 安装失败，退出码 1
```

**修复**

- 残留旧版 Git：通过「控制面板 → 程序与功能」卸载 `Git` 后重试。
- 杀毒软件拦截：临时关闭 Defender 实时保护，或将 `git-for-windows` 加入白名单。
- LFS 解压失败：检查 `%TEMP%` 剩余空间（Git LFS 包约 5 MB）。

### C3. Python 嵌入式安装失败

**日志特征**

```
[ERROR] pip 引导失败
```

或：

```
[ERROR] 下载失败：https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip
```

**修复**

- 公司网络屏蔽 `python.org` 时，将 URL 改为内网镜像（如 `https://mirrors.aliyun.com/python/3.11.9/`）后修改脚本中的 `$PythonVersion` 拼接逻辑。
- 已有同名目录但非合法嵌入式发行版：删除 `%LOCALAPPDATA%\LingjingManufacturing\tools\python` 后重试。
- 杀毒软件把 `get-pip.py` 当作可疑脚本：临时禁用启发式扫描。

### C4. Rust 安装失败

**日志特征**

```
[ERROR] rustup-init 失败，退出码 1
```

**修复**

1. 手动下载 `rustup-init.exe` 双击安装，确认错误信息。
2. 国内网络：脚本已默认走 `rsproxy` 镜像，若被阻断请在脚本中临时改回 `https://sh.rustup.rs`。
3. **已有 rustup 但版本老旧**：`rustup self update && rustup default stable`。

### C5. Node.js 安装失败

**日志特征**

```
[ERROR] Node.js 安装失败，退出码 1603
```

**修复**

- 残留旧版 Node：通过「控制面板 → 程序与功能」卸载。
- msi 日志：在命令中追加 `/l*v "C:\msi.log"` 重定向，联系 IT 时附上日志。
- 镜像源不可用：脚本中 `Script:NpmRegistry` 改为 `https://registry.npmjs.org` 临时切换官方源。

### C6. Ollama 安装/启动失败

**日志特征（启动 30s 内未就绪）**

```
[WARN] Ollama 服务在 30s 内未就绪，可在安装后通过"灵境制造"应用手动启动。
```

**修复**

1. 检查 `OLLAMA_HOME` 目录权限：

   ```powershell
   icacls "$env:LOCALAPPDATA\LingjingManufacturing\ollama"
   # 应至少包含 Users:(OI)(CI)(M)
   ```

2. 端口冲突：`netstat -ano | findstr :11434` → 结束占用进程。
3. 显卡驱动缺失：Ollama 在 Windows 上需要 WSL2 或 NVIDIA 驱动；纯 CPU 模式也可工作但首次推理较慢。
4. 完全卸载后重装：

   ```powershell
   & "$env:LOCALAPPDATA\Programs\Ollama\unins000.exe" /S
   Remove-Item -LiteralPath "$env:LOCALAPPDATA\Programs\Ollama" -Recurse -Force -ErrorAction SilentlyContinue
   ```

---

## D. 应用部署类

### D1. 数据库初始化失败

**日志特征**

```
[WARN] 数据库初始化脚本返回非零退出码 1
```

**修复**

- 脚本对此类错误仅记录 WARN，不退出；可在应用内手动重试。
- 排查命令：

  ```powershell
  & "$env:LOCALAPPDATA\LingjingManufacturing\tools\python\python.exe" `
      "$env:LOCALAPPDATA\LingjingManufacturing\scripts\init_db.py"
  ```

- 常见原因：SQLite 锁（其他进程占用）、磁盘权限、`init_db.py` 内部缺依赖包。

### D2. 桌面快捷方式创建失败

**日志特征**

```
[WARN] 创建桌面快捷方式失败：<message>
```

**修复**

- 桌面目录不存在（OneDrive 重定向场景）：

  ```powershell
  $desk = [Environment]::GetFolderPath('Desktop')
  if (-not (Test-Path $desk)) { New-Item -ItemType Directory -Path $desk -Force }
  ```

- 第三方桌面整理工具锁定：临时关闭后重试。
- 终极方案：从「开始菜单」启动 `%LOCALAPPDATA%\LingjingManufacturing\LingjingManufacturing.exe`。

---

## E. 升级与重装

| 场景 | 操作 |
|------|------|
| 升级到新版本 | 重新运行 `install.ps1`；脚本会跳过已存在且 SHA256 一致的组件 |
| 完全重装 | 先按"卸载"章节清理 `LingjingManufacturing` 目录，再运行 `install.ps1` |
| 切换 Python / Node / Rust 版本 | 修改脚本顶部 `$Script:PythonVersion` / `$Script:NodeMajor` / `$Script:RustToolchain` 后重跑 |
| 仅重跑应用部署 | 当前版本不支持分段重跑；可临时把脚本前 7 个步骤注释掉 |

---

## F. 一键诊断命令

> 一键导出诊断包，便于提交工单。

```powershell
# 保存到桌面
$zip = "$env:USERPROFILE\Desktop\lj-diag.zip"
$tmpDir = Join-Path $env:TEMP ("lj-diag-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null

# 1. 安装日志
Copy-Item -LiteralPath "$env:LOCALAPPDATA\LingjingManufacturing\logs" -Destination (Join-Path $tmpDir "logs") -Recurse -Force

# 2. 各组件版本
"=== System ===" | Out-File (Join-Path $tmpDir "versions.txt")
Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, BuildNumber | Format-List | Out-File (Join-Path $tmpDir "versions.txt") -Append
"=== PowerShell ===" | Out-File (Join-Path $tmpDir "versions.txt") -Append
$PSVersionTable | Format-List | Out-File (Join-Path $tmpDir "versions.txt") -Append

# 3. 已装组件
$probes = @{
    'python' = "$env:LOCALAPPDATA\LingjingManufacturing\tools\python\python.exe"
    'git'    = (Get-Command git.exe -ErrorAction SilentlyContinue).Source
    'node'   = (Get-Command node.exe -ErrorAction SilentlyContinue).Source
    'cargo'  = (Get-Command cargo.exe -ErrorAction SilentlyContinue).Source
    'rustc'  = (Get-Command rustc.exe -ErrorAction SilentlyContinue).Source
    'ollama' = (Get-Command ollama.exe -ErrorAction SilentlyContinue).Source
}
foreach ($k in $probes.Keys) {
    $p = $probes[$k]
    if ($p -and (Test-Path $p)) { & $p --version 2>&1 | Out-File (Join-Path $tmpDir "versions.txt") -Append }
}

# 4. 打包
Compress-Archive -Path $tmpDir -DestinationPath $zip -Force
Remove-Item -LiteralPath $tmpDir -Recurse -Force

Write-Host "诊断包已生成：$zip" -ForegroundColor Green
```

---

## 反馈渠道

- 提交 Issue：附上 `lj-diag.zip` 与 `install-latest.log`。
- 紧急情况（车间生产阻塞）：拨打 7×24 运维热线（见内部 Wiki）。
