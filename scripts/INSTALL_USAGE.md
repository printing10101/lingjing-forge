# 灵境制造 一键安装脚本 — 使用说明

> 对应脚本：[scripts/install.ps1](file:///c:/Users/Lenovo/Desktop/%E7%81%B5%E5%A2%83%E5%88%B6%E9%80%A0%EF%BC%88%E4%B8%8A%E7%BA%BF%E7%89%88%EF%BC%89/scripts/install.ps1)
> 适用系统：Windows 10 1809 (Build 17763) 及以上 / Windows 11
> 适用 PowerShell：5.1 及以上（无需安装 PowerShell 7）

---

## 1. 快速上手

### 1.1 交互模式（推荐车间工程师使用）

1. 解压安装包到任意目录（**不要**放在 `C:\Program Files\` 等受保护路径下）。
2. 右键 **PowerShell** → "以管理员身份运行"。
3. 切换到脚本所在目录并执行：

   ```powershell
   cd <安装包解压目录>
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   .\scripts\install.ps1
   ```

4. 脚本会按 9 个步骤依次执行：环境检查 → VC++ → Git/LFS → Python → Rust → Node.js → Ollama → 应用部署 → 收尾。
5. 全部完成后，桌面会出现 **"灵境制造"** 快捷方式，双击即可启动。

### 1.2 静默模式（IT 批量部署 / 远程推送）

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -silent
```

静默模式下：

- 不输出彩色控制台 UI，不弹出 `Read-Host` 暂停。
- 所有进度信息写入日志文件，便于回看。
- 退出码：`0` = 成功；`非 0` = 失败，可由 SCCM / Intune / Ansible 等外围工具捕获。

---

## 2. 命令行参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `-silent` | switch | off | 启用静默安装模式（仅写日志） |
| `-logDir <path>` | string | `%LOCALAPPDATA%\LingjingManufacturing\logs` | 自定义日志输出目录 |
| `-downloadUrl <url>` | string | 脚本内置默认值 | 自定义应用包下载地址（内网 / 灰度场景） |

使用示例：

```powershell
# 指定内网 CDN
.\scripts\install.ps1 -downloadUrl "https://cdn.internal.lingjing/lingjing-1.0.0.zip"

# 日志写到 D 盘
.\scripts\install.ps1 -logDir "D:\lj-install-logs"

# 组合使用
.\scripts\install.ps1 -silent -logDir "\\fileserver\deploy$\lj-logs" -downloadUrl "https://cdn.internal.lingjing/lingjing-1.0.0.zip"
```

---

## 3. 安装产物清单

安装完成后会在 `%LOCALAPPDATA%\LingjingManufacturing\` 下生成以下结构：

```
%LOCALAPPDATA%\LingjingManufacturing\
├── tools\
│   ├── python\              # 嵌入式 Python 3.11.9（独立 LJ_PYTHON_HOME）
│   └── git-lfs\             # Git LFS 3.5.1
├── ollama\                  # Ollama 模型与配置目录（OLLAMA_HOME）
├── models\                  # LNN 基础模型 + Embedding 模型
├── logs\                    # 安装日志（install-yyyyMMdd-HHmmss.log + install-latest.log）
├── assets\app.ico           # 桌面快捷方式图标（来自应用包）
├── LingjingManufacturing.exe # 主程序入口（来自应用包）
└── scripts\init_db.py       # 数据库初始化脚本（来自应用包）
```

此外，桌面会生成 `灵境制造.lnk`。

---

## 4. 卸载

> 卸载脚本需要管理员权限。

```powershell
# 1. 停止 Ollama 与应用进程
Stop-Process -Name ollama -Force -ErrorAction SilentlyContinue
Stop-Process -Name LingjingManufacturing -Force -ErrorAction SilentlyContinue

# 2. 移除应用目录
Remove-Item -LiteralPath "$env:LOCALAPPDATA\LingjingManufacturing" -Recurse -Force

# 3. 移除用户级环境变量
[Environment]::SetEnvironmentVariable('LJ_PYTHON_HOME',  $null, 'User')
[Environment]::SetEnvironmentVariable('LJ_PYTHON_BIN',   $null, 'User')
[Environment]::SetEnvironmentVariable('OLLAMA_HOME',     $null, 'User')
[Environment]::SetEnvironmentVariable('OLLAMA_HOST',     $null, 'User')

# 4. 移除桌面快捷方式
Remove-Item -LiteralPath "$env:USERPROFILE\Desktop\灵境制造.lnk" -Force -ErrorAction SilentlyContinue

# 5. 卸载 Ollama（系统级安装）
& "$env:LOCALAPPDATA\Programs\Ollama\unins000.exe" /S
```

Rust / Node.js / Git 仍保留在系统中；若需彻底清理请分别运行 `rustup self uninstall`、`控制面板 → 卸载程序`。

---

## 5. 常见问题入口

- 找不到对应错误？请查阅 [INSTALL_ERROR_HANDBOOK.md](./INSTALL_ERROR_HANDBOOK.md)。
- 日志如何解析？请查阅 [INSTALL_LOG_SPEC.md](./INSTALL_LOG_SPEC.md)。
- 需要批量分发到多台机器？请参考第 1.2 节，结合 SCCM / Intune / Ansible 等工具使用 `-silent` 模式。
