//! Tauri 命令模块：暴露给前端调用的 IPC 接口

use crate::sidecar::{BackendState, SidecarManager};
use std::sync::Arc;
use tauri::{AppHandle, Manager, Runtime, State};

// 健康检查与日志导出相关的数据结构
// 这些结构体与前端 TypeScript 接口对齐：
//   - HealthItem           <-> src/components/HealthCheck.vue 中的 HealthItem
//   - InvokeExportLogsResult <-> src/composables/useSettings.ts 中的 InvokeExportLogsResult
// 字段命名采用 snake_case（serde 默认），与前端 invoke 返回的 JSON 一致。

/// 单个健康检查项（与前端 HealthItem 接口对齐）
#[derive(serde::Serialize)]
pub struct HealthItem {
    pub id: String,
    pub name: String,
    pub status: String,
    pub message: String,
    pub details: String,
    pub version: Option<String>,
    pub fix_action: Option<String>,
    pub fix_description: Option<String>,
    pub fix_auto: bool,
}

/// 日志导出命令的返回结果（与前端 InvokeExportLogsResult 接口对齐）
#[derive(serde::Serialize)]
pub struct InvokeExportLogsResult {
    pub success: bool,
    pub message: String,
    pub output_path: Option<String>,
    pub file_count: u64,
    pub total_size_bytes: u64,
}

/// 一键修复命令的返回结果（与前端 AutoFixResult 接口对齐）
///
/// `fixed=true` 表示已执行真实修复动作（如重启后端 sidecar）；
/// `fixed=false` 表示该组件无法自动修复，message 为诚实的操作指引
/// （前端应以 warning 而非 success 呈现，避免"假修复成功"）。
#[derive(serde::Serialize)]
pub struct AutoFixResult {
    pub fixed: bool,
    pub message: String,
}

/// 全局状态包装
pub struct AppState {
    pub sidecar: Arc<SidecarManager>,
}

/// 获取后端当前运行状态
#[tauri::command]
pub fn get_backend_state(state: State<'_, AppState>) -> BackendState {
    state.sidecar.state()
}

/// 主动启动后端
#[tauri::command]
pub async fn start_backend<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, AppState>,
) -> Result<BackendState, String> {
    state.sidecar.start(&app).await?;
    Ok(state.sidecar.state())
}

/// 主动停止后端
#[tauri::command]
pub async fn stop_backend<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, AppState>,
) -> Result<BackendState, String> {
    state.sidecar.stop(&app).await?;
    Ok(state.sidecar.state())
}

/// 重启后端
#[tauri::command]
pub async fn restart_backend<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, AppState>,
) -> Result<BackendState, String> {
    state.sidecar.restart(&app).await?;
    Ok(state.sidecar.state())
}

/// 健康检查：探测后端 HTTP 端点
#[tauri::command]
pub async fn ping_backend(state: State<'_, AppState>) -> Result<bool, String> {
    let port = state.sidecar.state().port;
    let url = format!("http://127.0.0.1:{port}/api/health/ping");
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .build()
        .map_err(|e| e.to_string())?;
    match client.get(&url).send().await {
        Ok(resp) if resp.status().is_success() => Ok(true),
        Ok(_) => Ok(false),
        Err(_) => Ok(false),
    }
}

/// 打开外部 URL（仅允许 http/https，防协议注入）
///
/// 「关于」页「前往下载」按钮使用：跳转 GitHub Releases 页面手动下载安装包
/// （自动更新过渡方案，见 docs/operations/工业级交付路线图.md 2.2）。
#[tauri::command]
pub fn open_external_url<R: Runtime>(app: AppHandle<R>, url: String) -> Result<(), String> {
    let lower = url.to_lowercase();
    if !(lower.starts_with("https://") || lower.starts_with("http://")) {
        return Err("Only http/https URLs are allowed".to_string());
    }
    use tauri_plugin_shell::ShellExt;
    app.shell().open(&url, None).map_err(|e| e.to_string())
}

/// 版本一致性状态（与前端 VersionStatus 接口对齐）
///
/// 用于前端展示 Rust/Python/前端三端版本一致性，
/// 字段命名采用 snake_case 以匹配前端 TypeScript 接口。
#[derive(serde::Serialize)]
pub struct VersionStatus {
    pub rust_version: String,
    pub rust_commit: String,
    pub python_version: Option<String>,
    pub python_commit: Option<String>,
    pub is_consistent: bool,
}

/// 获取应用版本信息（Rust + Python sidecar 版本及一致性）
///
/// 前端 `src/stores/version.ts` 通过 `invoke('get_version_info')` 调用，
/// 返回 `VersionStatus` 供前端显示版本一致性面板。
/// - `rust_version` 来自 `package_info()`（即 tauri.conf.json 的 version 字段）
/// - `rust_commit` 暂未接入 git，返回 "unknown"
/// - `python_version`/`python_commit` 暂未从 sidecar 获取，返回 None
/// - `is_consistent` 在缺少 python 版本时默认 true（前端会单独校验 frontend === rust）
#[tauri::command]
pub fn get_version_info<R: Runtime>(app: AppHandle<R>) -> VersionStatus {
    let rust_version = app.package_info().version.to_string();
    VersionStatus {
        rust_version,
        rust_commit: String::from("unknown"),
        python_version: None,
        python_commit: None,
        // python_version 缺失时视为一致；前端会再次校验 frontend === rust
        is_consistent: true,
    }
}

/// 获取后端服务监听端口
#[tauri::command]
pub fn get_backend_port(state: State<'_, AppState>) -> u16 {
    state.sidecar.state().port
}

// 健康检查与日志导出 IPC 命令实现
// 这 6 个命令对应前端 HealthCheck.vue 与 useSettings.ts 的调用：
//   - run_health_check        : 全量健康检查（GET /api/v1/health/system）
//   - run_single_health_check : 单项重试（同端点，按 component 过滤）
//   - auto_fix_health         : 一键修复（后端暂无该端点，返回操作指引）
//   - get_diagnostics_text    : 汇总诊断文本（用于复制到剪贴板）
//   - export_logs_cmd         : 导出最近 N 天日志文件信息
//   - retry_launch_step       : splashscreen 重试某启动步骤
//
// 设计要点：
//   1. 后端不可达时返回空数组/默认值，不向前端抛错，避免 UI 整体崩溃
//   2. details 字段在 HealthItem 中是 String，需要把后端返回的 dict 序列化为
//      JSON 字符串，前端再展示
//   3. 所有错误路径均记录日志（log::warn/error），便于 release 模式排查

/// 后端 /api/v1/health/system 端点返回的原始结构（用于反序列化）
#[derive(serde::Deserialize)]
struct BackendHealthResponse {
    #[allow(dead_code)]
    status: String,
    #[allow(dead_code)]
    timestamp: f64,
    #[allow(dead_code)]
    app_version: String,
    #[allow(dead_code)]
    uptime_seconds: f64,
    items: Vec<BackendHealthItem>,
}

/// 后端健康检查单项原始结构
#[derive(serde::Deserialize)]
struct BackendHealthItem {
    component: String,
    name: String,
    status: String,
    version: Option<serde_json::Value>,
    details: serde_json::Value,
}

/// 将后端返回的原始 item 映射为前端 HealthItem
fn map_backend_item(item: BackendHealthItem) -> HealthItem {
    // version 字段后端可能返回字符串、数字或 null，统一转为 String
    let version_str = match item.version {
        Some(serde_json::Value::String(s)) => Some(s),
        Some(serde_json::Value::Number(n)) => Some(n.to_string()),
        Some(other) => Some(other.to_string()),
        None => None,
    };

    // details 字段后端返回任意 JSON，序列化为格式化字符串便于前端展示
    let details_str = match serde_json::to_string_pretty(&item.details) {
        Ok(s) => s,
        Err(_) => format!("{:?}", item.details),
    };

    // message 字段：后端未单独提供，根据 status 生成简短描述
    let message = match item.status.as_str() {
        "ok" => "运行正常".to_string(),
        "warning" => "存在警告".to_string(),
        "error" => "运行异常".to_string(),
        _ => item.status.clone(),
    };

    // fix_action / fix_description / fix_auto：后端未提供修复端点，根据 component 给出静态指引
    let (fix_action, fix_description, fix_auto) = match item.component.as_str() {
        "ollama" => (
            Some("restart_ollama".to_string()),
            Some(
                "请确认 Ollama 服务已安装并启动。可执行 `ollama serve` 或在系统服务中启动 Ollama。"
                    .to_string(),
            ),
            false,
        ),
        "models" => (
            Some("pull_model".to_string()),
            Some("请执行 `ollama pull qwen2.5:7b` 拉取所需模型。".to_string()),
            false,
        ),
        "disk" => (
            None,
            Some("磁盘空间不足，请清理临时文件或扩展磁盘容量。".to_string()),
            false,
        ),
        "memory" => (
            None,
            Some("内存占用过高，请关闭其他大型程序后重试。".to_string()),
            false,
        ),
        "postgresql" | "redis" | "tdengine" => (
            Some("start_service".to_string()),
            Some(format!(
                "请检查 {} 服务是否已启动，并确认连接配置正确。",
                item.component
            )),
            false,
        ),
        _ => (None, None, false),
    };

    HealthItem {
        id: item.component,
        name: item.name,
        status: item.status,
        message,
        details: details_str,
        version: version_str,
        fix_action,
        fix_description,
        fix_auto,
    }
}

/// 内部辅助：调用后端 /api/v1/health/system 端点
async fn fetch_backend_health(port: u16) -> Result<Vec<HealthItem>, String> {
    let url = format!("http://127.0.0.1:{port}/api/v1/health/system");
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(8))
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client
        .get(&url)
        .send()
        .await
        .map_err(|e| format!("无法连接后端健康检查端点: {e}"))?;
    if !resp.status().is_success() {
        return Err(format!("后端健康检查返回非成功状态: {}", resp.status()));
    }
    let parsed: BackendHealthResponse = resp
        .json()
        .await
        .map_err(|e| format!("解析后端健康检查响应失败: {e}"))?;
    Ok(parsed.items.into_iter().map(map_backend_item).collect())
}

/// 全量健康检查：调用后端 /api/v1/health/system 并返回所有组件状态
///
/// 前端 HealthCheck.vue `runAllChecks` 调用。
/// 后端不可达时返回空数组（不抛错），让前端 UI 仍可正常渲染。
#[tauri::command]
pub async fn run_health_check(state: State<'_, AppState>) -> Result<Vec<HealthItem>, String> {
    let port = state.sidecar.state().port;
    Ok(match fetch_backend_health(port).await {
        Ok(items) => items,
        Err(e) => {
            log::warn!("[run_health_check] 拉取健康检查失败: {e}");
            Vec::new()
        }
    })
}

/// 单项健康检查重试：调用全量端点后按 component 过滤
///
/// 前端 HealthCheck.vue `retrySingleCheck` 调用。
/// 若未找到对应 component，返回一个 status=error 的占位项。
#[tauri::command]
pub async fn run_single_health_check(
    state: State<'_, AppState>,
    component: String,
) -> Result<HealthItem, String> {
    let port = state.sidecar.state().port;
    let items = fetch_backend_health(port)
        .await
        .map_err(|e| format!("健康检查失败: {e}"))?;
    let found = items.into_iter().find(|i| i.id == component);
    match found {
        Some(item) => Ok(item),
        None => Ok(HealthItem {
            id: component.clone(),
            name: component.clone(),
            status: "error".to_string(),
            message: "未找到该组件".to_string(),
            details: format!("Backend did not return a component named '{}'", component),
            version: None,
            fix_action: None,
            fix_description: None,
            fix_auto: false,
        }),
    }
}

/// 一键自动修复
///
/// 前端 HealthCheck.vue `runAutoFix` 调用。
/// - `backend`：真实修复动作——重启后端 sidecar 并等待拉起；
/// - 其他组件（ollama/models/postgresql/redis/tdengine/disk/memory 等）：
///   属外部服务或宿主机资源，桌面端无可靠的自动修复手段，返回 `fixed=false`
///   与操作指引文本（前端以 warning 呈现），不再伪装成修复成功。
#[tauri::command]
pub async fn auto_fix_health<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, AppState>,
    component: String,
) -> Result<AutoFixResult, String> {
    log::info!("[auto_fix_health] component={component}");
    match component.as_str() {
        "backend" => {
            let pre_pid = state.sidecar.state().pid;
            state.sidecar.restart(&app).await?;
            let st = state.sidecar.state();
            log::info!(
                "[auto_fix_health] backend restarted: pre_pid={pre_pid:?} new_pid={:?} port={}",
                st.pid,
                st.port
            );
            Ok(AutoFixResult {
                fixed: true,
                message: format!(
                    "后端已重启（端口 {}），正在重新检查健康状态…",
                    st.port
                ),
            })
        }
        "ollama" => Ok(AutoFixResult {
            fixed: false,
            message: "该组件为外部服务，暂不支持自动修复。请手动启动 Ollama 服务：执行 `ollama serve` 或在系统服务中启动。".to_string(),
        }),
        "models" => Ok(AutoFixResult {
            fixed: false,
            message: "模型拉取需手动执行：`ollama pull qwen2.5:7b`。".to_string(),
        }),
        "postgresql" => Ok(AutoFixResult {
            fixed: false,
            message: "该组件为外部服务，暂不支持自动修复。请启动 PostgreSQL 服务并检查连接配置。".to_string(),
        }),
        "redis" => Ok(AutoFixResult {
            fixed: false,
            message: "该组件为外部服务，暂不支持自动修复。请启动 Redis 服务并检查连接配置。".to_string(),
        }),
        "tdengine" => Ok(AutoFixResult {
            fixed: false,
            message: "该组件为外部服务，暂不支持自动修复。请启动 TDengine 服务并检查连接配置。".to_string(),
        }),
        "disk" => Ok(AutoFixResult {
            fixed: false,
            message: "磁盘空间无法自动清理。请手动清理临时文件，将剩余空间恢复至 5GB 以上。".to_string(),
        }),
        "memory" => Ok(AutoFixResult {
            fixed: false,
            message: "内存占用无法自动释放。请手动关闭其他大型程序后重试。".to_string(),
        }),
        _ => Ok(AutoFixResult {
            fixed: false,
            message: format!("组件 {component} 暂不支持自动修复，请参考详情中的修复指引。"),
        }),
    }
}

/// 汇总系统诊断信息为文本（用于复制到剪贴板）
///
/// 前端 HealthCheck.vue `copyDiagnostics` 调用。
/// 包含：应用版本、后端状态、端口、PID、最近错误、健康检查项摘要。
#[tauri::command]
pub async fn get_diagnostics_text<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, AppState>,
) -> Result<String, String> {
    let mut buf = String::new();
    let now = chrono::Utc::now().format("%Y-%m-%d %H:%M:%S UTC");
    buf.push_str(&format!("=== 灵境制造 V4 诊断报告 ===\n"));
    buf.push_str(&format!("生成时间: {now}\n\n"));

    // 应用版本
    let rust_ver = app.package_info().version.to_string();
    buf.push_str(&format!("应用版本 (Rust): {rust_ver}\n"));

    // 后端状态
    let bs = state.sidecar.state();
    buf.push_str(&format!("后端状态: {:?}\n", bs.status));
    buf.push_str(&format!("后端端口: {}\n", bs.port));
    if let Some(pid) = bs.pid {
        buf.push_str(&format!("后端 PID: {pid}\n"));
    }
    if let Some(started) = bs.started_at {
        buf.push_str(&format!("启动时间: {started}\n"));
    }
    if let Some(err) = &bs.last_error {
        buf.push_str(&format!("最近错误: {err}\n"));
    }
    buf.push_str(&format!("重启次数: {}\n\n", bs.restart_count));

    // 健康检查摘要
    buf.push_str("=== 组件健康检查 ===\n");
    match fetch_backend_health(bs.port).await {
        Ok(items) => {
            if items.is_empty() {
                buf.push_str("(无健康检查数据)\n");
            }
            for item in &items {
                buf.push_str(&format!(
                    "- [{}] {}: {} | {}\n",
                    item.status, item.id, item.name, item.message
                ));
                if let Some(v) = &item.version {
                    buf.push_str(&format!("    version: {v}\n"));
                }
            }
        }
        Err(e) => {
            buf.push_str(&format!("(健康检查失败: {e})\n"));
        }
    }

    Ok(buf)
}

/// 导出最近 N 天的日志文件信息
///
/// 前端 useSettings.ts `exportSystemLogs` 调用。
/// 扫描应用日志目录，统计指定天数内修改的日志文件。
/// 不实际打包文件（避免大文件 IPC 传输），仅返回路径与统计信息。
#[tauri::command]
pub async fn export_logs_cmd<R: Runtime>(
    app: AppHandle<R>,
    days: u64,
) -> Result<InvokeExportLogsResult, String> {
    log::info!("[export_logs_cmd] days={days}");

    let log_dir = match app.path().app_log_dir() {
        Ok(d) => d,
        Err(e) => {
            return Ok(InvokeExportLogsResult {
                success: false,
                message: format!("无法获取日志目录: {e}"),
                output_path: None,
                file_count: 0,
                total_size_bytes: 0,
            });
        }
    };

    if !log_dir.exists() {
        return Ok(InvokeExportLogsResult {
            success: true,
            message: "日志目录不存在（可能是首次启动）".to_string(),
            output_path: Some(log_dir.to_string_lossy().to_string()),
            file_count: 0,
            total_size_bytes: 0,
        });
    }

    let cutoff = {
        let now = chrono::Utc::now();
        // M2 bug 修复：days as i64 在极端值会溢出。用 try_from 安全转换。
        let days_i64 = i64::try_from(days).unwrap_or(i64::MAX);
        now - chrono::Duration::days(days_i64)
    };
    let cutoff_ts = cutoff.timestamp();

    let mut file_count: u64 = 0;
    let mut total_size: u64 = 0;
    let mut matched: Vec<String> = Vec::new();

    // M1 bug 修复：原 walk_logs 无深度限制且不跳过符号链接，
    // 遇到循环符号链接会爆栈，或深层目录耗尽文件描述符。
    // 加入 max_depth=10 与 symlink 跳过（与同项目 walkdir 函数一致）。
    fn walk_logs(
        dir: &std::path::Path,
        cutoff_ts: i64,
        out: &mut Vec<String>,
        count: &mut u64,
        size: &mut u64,
        depth: u32,
    ) {
        if depth > 10 {
            return;
        }
        if let Ok(entries) = std::fs::read_dir(dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                // 跳过符号链接，防止循环引用
                if path.is_symlink() {
                    continue;
                }
                if path.is_dir() {
                    walk_logs(&path, cutoff_ts, out, count, size, depth + 1);
                } else if let Ok(meta) = entry.metadata() {
                    if let Ok(modified) = meta.modified() {
                        if let Ok(ts) = modified.duration_since(std::time::UNIX_EPOCH) {
                            if (ts.as_secs() as i64) >= cutoff_ts {
                                *count += 1;
                                *size += meta.len();
                                if let Some(p) = path.to_str() {
                                    out.push(p.to_string());
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    walk_logs(
        &log_dir,
        cutoff_ts,
        &mut matched,
        &mut file_count,
        &mut total_size,
        0,
    );

    log::info!(
        "[export_logs_cmd] 扫描完成: {} 个文件, {} 字节",
        file_count,
        total_size
    );

    Ok(InvokeExportLogsResult {
        success: true,
        message: format!("已扫描到 {file_count} 个日志文件，共 {} 字节", total_size),
        output_path: Some(log_dir.to_string_lossy().to_string()),
        file_count,
        total_size_bytes: total_size,
    })
}

/// 关闭 splashscreen 窗口并显示主窗口
///
/// 前端在 Vue 应用挂载完成、首屏渲染就绪后调用此命令，
/// 实现"启动动画 → 主应用"的平滑切换，避免出现白屏过渡。
#[tauri::command]
pub fn close_splashscreen<R: Runtime>(app: AppHandle<R>) -> Result<(), String> {
    log::info!("[close_splashscreen] 前端调用 close_splashscreen IPC，开始切换窗口");
    // 先显示主窗口（避免先关 splash 再显主窗口造成的视觉空档）
    if let Some(main_window) = app.get_webview_window("main") {
        main_window
            .show()
            .map_err(|e| format!("显示主窗口失败: {e}"))?;
        // 把焦点切到主窗口
        let _ = main_window.set_focus();
        log::info!("[close_splashscreen] main 窗口已显示");
    } else {
        log::warn!("[close_splashscreen] 未找到 main 窗口，无法切换");
    }

    // 关闭 splashscreen 窗口
    if let Some(splash_window) = app.get_webview_window("splashscreen") {
        splash_window
            .close()
            .map_err(|e| format!("关闭 splashscreen 窗口失败: {e}"))?;
        log::info!("[close_splashscreen] splashscreen 窗口已关闭");
    } else {
        log::warn!("[close_splashscreen] 未找到 splashscreen 窗口，可能已被关闭");
    }

    Ok(())
}
