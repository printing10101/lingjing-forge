# 安装日志输出规范

> 适用脚本：[scripts/install.ps1](file:///c:/Users/Lenovo/Desktop/%E7%81%B5%E5%A2%83%E5%88%B6%E9%80%A0%EF%BC%88%E4%B8%8A%E7%BA%BF%E7%89%88%EF%BC%89/scripts/install.ps1)

## 1. 日志文件位置

| 场景 | 路径 |
|------|------|
| 默认（用户级） | `%LOCALAPPDATA%\LingjingManufacturing\logs\` |
| 自定义 | `-logDir` 参数指定 |
| 兜底（系统临时） | `%TEMP%\lingjing-install-yyyyMMdd-HHmmss.log`（仅当日志目录初始化失败时） |

每次安装会在 `logs/` 下生成两份文件：

- `install-yyyyMMdd-HHmmss.log` — 按时间戳归档的本次安装日志，**不可变**。
- `install-latest.log` — 软链别名（Windows 下用文件副本覆盖），始终指向**最近一次**安装。

> 上述命名是脚本中 `$Script:LatestLog` 的约定；若部署到只读共享，请改为追加 `Get-Date` 后缀。

---

## 2. 日志格式

每行一条记录，结构如下：

```
[<时间戳>] [<级别>] <消息体>
```

- **时间戳**：`yyyy-MM-dd HH:mm:ss.fff`，本地时区（默认 `Asia/Shanghai`），毫秒级。
- **级别**：`INFO` / `WARN` / `ERROR` / `DEBUG` / `STEP`，全部大写。
- **消息体**：单行 UTF-8 文本；含路径、URL、退出码、错误堆栈等关键信息。

示例：

```
[2026-06-10 14:23:01.142] [INFO] === 灵境制造 安装开始  版本 1.0.0 ===
[2026-06-10 14:23:01.143] [INFO] PowerShell 版本: 5.1.19041.2673
[2026-06-10 14:23:01.144] [INFO] 静默模式: False
[2026-06-10 14:23:01.145] [INFO] 日志文件: C:\Users\engineer\AppData\Local\LingjingManufacturing\logs\install-20260610-142301.log
[2026-06-10 14:23:02.331] [INFO] 检测到操作系统：Microsoft Windows 11 Pro  版本=10.0.22631
[2026-06-10 14:23:02.812] [INFO] 目标盘符 C: 剩余空间 124.36 GB
[2026-06-10 14:23:05.104] [STEP] (1/9  11%) 系统环境检查
[2026-06-10 14:23:05.207] [INFO] 网络连通：GitHub
[2026-06-10 14:23:05.421] [WARN] 网络不通：Rust  (The remote name could not be resolved: sh.rustup.rs)
...
[2026-06-10 14:25:18.776] [STEP] (9/9  100%) 收尾与自检
[2026-06-10 14:25:19.001] [INFO]  安装目录 : C:\Users\engineer\AppData\Local\LingjingManufacturing
[2026-06-10 14:25:19.002] [INFO]  日志文件 : C:\...\install-20260610-142301.log
[2026-06-10 14:25:19.003] [INFO]  耗时     : 00:02:18
```

---

## 3. 级别语义

| 级别 | 含义 | 触发场景 | 是否阻塞 |
|------|------|----------|----------|
| `INFO` | 普通进度信息 | 步骤开始 / 子任务完成 / 关键参数回显 | 否 |
| `WARN` | 可恢复的异常 | 单个网络探测失败 / 模型下载失败 / 快捷方式创建失败 / 服务暂未就绪 | 否（脚本继续） |
| `ERROR` | 致命错误 | 磁盘不足 / OS 不支持 / 下载全部失败 / 关键组件安装失败 | **是**（脚本退出码 1） |
| `DEBUG` | 调试详情 | 默认不写；如需开启请将 `$LogLevel` 调整为包含 DEBUG | 否 |
| `STEP` | 阶段切换 | 9 个大步骤的开始标记，便于按段落切片 | 否 |

> 当前版本不直接输出 `DEBUG`，如需更细粒度日志请修改 `Write-Log` 的 switch 分支。

---

## 4. 步骤约定（STEP 行）

`STEP` 行的格式固定为：

```
[STEP] (<current>/<total>  <percent>%) <title>
```

- `current` / `total` 为 `1..9`，与 `Invoke-Main` 中的调用顺序一一对应：

  | # | 模块 |
  |---|------|
  | 1 | 系统环境检查 |
  | 2 | 安装 Visual C++ 运行库 |
  | 3 | 安装 Git 与 Git LFS |
  | 4 | 安装嵌入式 Python |
  | 5 | 安装 Rust 工具链 |
  | 6 | 安装 Node.js LTS |
  | 7 | 安装 Ollama 本地推理引擎 |
  | 8 | 下载并部署应用包 |
  | 9 | 收尾与自检 |

- `percent` 整数百分比，等于 `floor(current / total * 100)`，用于日志聚合仪表盘绘制甘特图。

**可被解析的正则**（用于自动化采集）：

```regex
^\[STEP\] \((\d+)/(\d+)\s+(\d+)%\) (.+)$
```

---

## 5. 错误日志规范

当 `catch` 块触发时，错误日志至少包含：

1. 异常 `Message`
2. `ScriptStackTrace`（多行缩进，**整段**保留以便回溯调用链）
3. 失败子步骤对应的 `STEP` 行（通过时间戳区间定位）

示例：

```
[2026-06-10 14:24:31.512] [STEP] (7/9  77%) 安装 Ollama 本地推理引擎
[2026-06-10 14:24:51.103] [ERROR] 安装失败：下载失败：https://ollama.com/download/OllamaSetup.exe
    at Invoke-HttpDownload, C:\Users\engineer\scripts\install.ps1: line 312
    at Install-Ollama, C:\Users\engineer\scripts\install.ps1: line 487
    at Invoke-Main, C:\Users\engineer\scripts\install.ps1: line 765
```

---

## 6. 采集与轮转建议

| 项 | 建议 |
|----|------|
| 归档周期 | 单次安装；旧文件不主动清理（避免被覆盖） |
| 远程推送 | IT 批量部署建议通过 `-logDir "\\fileserver\deploy$\%COMPUTERNAME%"` 集中收集 |
| 大小估算 | 一次完整安装约 30 ~ 80 KB，可忽略 |
| 轮转策略 | 文件系统级：保留最近 30 天 / 50 份；应用级：不主动 `Remove-Item` |
| 编码 | UTF-8（无 BOM），与 Windows 记事本兼容 |
| 行尾 | `\n`（脚本使用 `[String]::Format` + `Add-Content`，默认 LF） |

---

## 7. 实时观察（仅交互模式）

交互模式下，脚本会同步调用 `Write-Progress`，Windows 任务栏图标会显示百分比进度条。日志文件与控制台双写，控制台带颜色（`STEP`=青色、`WARN`=黄色、`ERROR`=红色），便于车间工程师目视识别。

静默模式下请**只**查阅日志文件，控制台会保持静默。

---

## 8. 快速过滤命令

```powershell
# 仅看错误
Select-String -Path "$env:LOCALAPPDATA\LingjingManufacturing\logs\install-latest.log" -Pattern '\[ERROR\]'

# 仅看步骤切换
Select-String -Path "$env:LOCALAPPDATA\LingjingManufacturing\logs\install-latest.log" -Pattern '\[STEP\]'

# 计算总耗时（取首末两行时间戳差）
$lines = Get-Content "$env:LOCALAPPDATA\LingjingManufacturing\logs\install-latest.log"
$start = [datetime]::ParseExact(($lines[0] -split ' ')[0].Trim('[]'), 'yyyy-MM-dd', $null)
$end   = [datetime]::ParseExact(($lines[-1] -split ' ')[0].Trim('[]'), 'yyyy-MM-dd', $null)
"耗时: {0}" -f ($end - $start)
```
