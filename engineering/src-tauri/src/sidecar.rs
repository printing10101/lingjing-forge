//! Python 后端 Sidecar 进程管理模块
//!
//! 负责启动、监控、重启和优雅终止 Python 后端进程。
//!
//! 实现说明（P0 修复）：
//! 原实现通过 `tauri-plugin-shell` 的 sidecar API 启动 PyInstaller 打包的
//! `lingjing-backend` 二进制。但由于 binaries 目录为空（旧二进制过时已删除），
//! sidecar spawn 始终失败，导致应用永远卡在"启动中"界面。
//!
//! 现改为直接使用 `tokio::process::Command` 运行 Python 解释器，
//! 执行 `start_server.py` 脚本，绕过 PyInstaller 二进制依赖。
//! 这样在开发模式下可以直接使用最新修复的 Python 源码。

use std::sync::Arc;
use std::time::Duration;

use chrono::{DateTime, Utc};
use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, Runtime};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::Mutex;
use tokio::time::sleep;

/// 后端进程运行状态
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum BackendStatus {
    /// 尚未启动
    Idle,
    /// 正在启动（启动中、等待监听端口）
    Starting,
    /// 正常运行
    Running,
    /// 正在停止
    Stopping,
    /// 异常退出
    Crashed,
    /// 启动失败
    Failed,
    /// 已被用户终止
    Stopped,
}

/// 后端进程状态信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BackendState {
    pub status: BackendStatus,
    pub pid: Option<u32>,
    /// 0-100 启动进度
    pub progress: u8,
    pub message: String,
    pub last_error: Option<String>,
    pub started_at: Option<DateTime<Utc>>,
    pub restart_count: u32,
    pub port: u16,
}

impl BackendState {
    pub fn new(port: u16) -> Self {
        Self {
            status: BackendStatus::Idle,
            pid: None,
            progress: 0,
            message: "等待启动后端服务".to_string(),
            last_error: None,
            started_at: None,
            restart_count: 0,
            port,
        }
    }
}

/// Sidecar 管理器
pub struct SidecarManager {
    state: Arc<RwLock<BackendState>>,
    /// 当前正在运行的子进程句柄
    child: Arc<Mutex<Option<Child>>>,
    /// 健康检查客户端
    http: reqwest::Client,
    /// 是否在用户主动关闭过程中（用于抑制崩溃事件）
    shutdown_in_progress: Arc<RwLock<bool>>,
}

impl SidecarManager {
    pub fn new(port: u16) -> Result<Self, String> {
        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(3))
            .build()
            .map_err(|e| format!("failed to build reqwest client: {e}"))?;
        Ok(Self {
            state: Arc::new(RwLock::new(BackendState::new(port))),
            child: Arc::new(Mutex::new(None)),
            http,
            shutdown_in_progress: Arc::new(RwLock::new(false)),
        })
    }

    pub fn state(&self) -> BackendState {
        self.state.read().clone()
    }

    /// 更新状态并通过事件总线推送给前端
    pub fn update_status<R: Runtime>(
        &self,
        app: &AppHandle<R>,
        status: BackendStatus,
        progress: u8,
        message: impl Into<String>,
    ) {
        {
            let mut s = self.state.write();
            s.status = status.clone();
            s.progress = progress;
            s.message = message.into();
        }
        self.emit_state(app);
    }

    pub fn set_error<R: Runtime>(&self, app: &AppHandle<R>, err: impl Into<String>) {
        {
            let mut s = self.state.write();
            s.last_error = Some(err.into());
        }
        self.emit_state(app);
    }

    pub fn emit_state<R: Runtime>(&self, app: &AppHandle<R>) {
        let snapshot = self.state();
        let _ = app.emit("sidecar://state", &snapshot);
    }

    /// 发射 `launch-progress` 事件供 splashscreen 监听
    ///
    /// splashscreen.html 的 `onProgress(payload)` 期望 payload 结构：
    ///   - step: 步骤标识（如 "start_backend"、"wait_ready"、"ready"、"failed"）
    ///   - progress: 0-100 进度数值
    ///   - description: 人类可读的描述文本
    ///   - status: "loading" | "success" | "error" | "complete"
    fn emit_launch_progress<R: Runtime>(
        &self,
        app: &AppHandle<R>,
        step: &str,
        progress: u8,
        description: &str,
        status: &str,
    ) {
        let payload = serde_json::json!({
            "step": step,
            "progress": progress,
            "description": description,
            "status": status,
        });
        let _ = app.emit("launch-progress", &payload);
    }

    /// 启动 Python 后端
    pub async fn start<R: Runtime>(&self, app: &AppHandle<R>) -> Result<(), String> {
        {
            let current = self.state.read().status.clone();
            if matches!(
                current,
                BackendStatus::Starting | BackendStatus::Running | BackendStatus::Stopping
            ) {
                return Err(format!("后端进程当前处于 {:?} 状态，无法重复启动", current));
            }
        }

        // 动态端口分配（端口冲突修复）：
        let preferred_port = self.state.read().port;
        let port = find_available_port(preferred_port);
        if port != preferred_port {
            log::warn!("[sidecar] 首选端口 {preferred_port} 已被占用，自动切换到空闲端口 {port}");
        }
        {
            let mut s = self.state.write();
            s.port = port;
        }
        self.update_status(
            app,
            BackendStatus::Starting,
            5,
            "正在启动 Python 后端进程...",
        );
        self.emit_launch_progress(
            app,
            "start_backend",
            5,
            "正在启动 Python 后端进程...",
            "loading",
        );

        // 清理上一次启动的 sidecar.json 和后端日志文件
        {
            let log_dir_path = self.log_dir(app);
            let log_dir = std::path::Path::new(&log_dir_path);
            let state_file = log_dir.join("sidecar.json");
            if state_file.exists() {
                if let Err(e) = std::fs::remove_file(&state_file) {
                    log::warn!("[sidecar] failed to remove old state file: {e}");
                }
            }
            for name in ["backend.stdout.log", "backend.stderr.log"] {
                let p = log_dir.join(name);
                if p.exists() {
                    if let Err(e) = std::fs::remove_file(&p) {
                        log::warn!("[sidecar] failed to remove old log file {}: {e}", name);
                    }
                }
            }
        }

        // 准备状态文件路径
        let log_dir_for_sidecar = self.log_dir(app);
        let state_file_arg = std::path::Path::new(&log_dir_for_sidecar)
            .join("sidecar.json")
            .to_string_lossy()
            .to_string();

        // 核心：直接运行 Python 脚本
        // 嵌入式优先：打包分发时使用 bundle.resources 内的自包含运行时
        // （desktop_runtime/runtime/python.exe + desktop_runtime/backend/start_server.py），
        // 目标机器无需预装 Python；开发模式回退宿主 Python。
        let resource_dir = app.path().resource_dir().ok();
        let python_path = resolve_python_path(resource_dir.as_deref());
        let (script_path, python_dir) = resolve_python_script_and_dir(resource_dir.as_deref());

        log::info!(
            "[sidecar] 启动 Python 后端: python={} script={} cwd={}",
            python_path,
            script_path,
            python_dir.display()
        );

        let mut command = Command::new(&python_path);
        command
            .arg(&script_path)
            .current_dir(&python_dir)
            .env("SERVER_HOST", "127.0.0.1")
            .env("SERVER_PORT", port.to_string())
            .env("LNN_HOST", "127.0.0.1")
            .env("LNN_PORT", port.to_string())
            .env("LNN_ENV", "dev")
            .env("LNN_LOG_DIR", &log_dir_for_sidecar)
            .env("LNN_STATE_FILE", &state_file_arg)
            .env("PYTHONUNBUFFERED", "1")
            .env("PYTHONIOENCODING", "utf-8")
            // Windows 下禁用 Python 的 IO 读缓冲，确保日志实时输出
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            // Windows 下不创建控制台窗口
            ;
        #[cfg(target_os = "windows")]
        {
            const CREATE_NO_WINDOW: u32 = 0x08000000;
            command.creation_flags(CREATE_NO_WINDOW);
        }

        let mut child = command.spawn().map_err(|e| {
            let msg = format!(
                "Python 后端启动失败: {e}\n  python={}\n  script={}\n  cwd={}",
                python_path,
                script_path,
                python_dir.display()
            );
            self.set_error(app, &msg);
            msg
        })?;

        let pid = child.id().unwrap_or(0);
        {
            let mut s = self.state.write();
            s.pid = if pid > 0 { Some(pid) } else { None };
            s.started_at = Some(Utc::now());
            s.last_error = None;
        }

        // 取出 stdout/stderr 用于异步读取
        let stdout = child.stdout.take();
        let stderr = child.stderr.take();
        *self.child.lock().await = Some(child);
        self.emit_state(app);

        // 启动子任务：读取 stdout/stderr 并发出日志事件
        let log_dir_for_io = self.log_dir(app).to_string();

        // stdout 读取任务
        if let Some(stdout) = stdout {
            let app_for_stdout = app.clone();
            let log_dir_for_stdout = log_dir_for_io.clone();
            tokio::spawn(async move {
                let stdout_path =
                    std::path::Path::new(&log_dir_for_stdout).join("backend.stdout.log");
                let reader = BufReader::new(stdout);
                let mut lines = reader.lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    let _ = app_for_stdout.emit("sidecar://stdout", &line);
                    append_log_line(&stdout_path, &line);
                }
            });
        }

        // stderr 读取任务
        if let Some(stderr) = stderr {
            let app_for_stderr = app.clone();
            let log_dir_for_stderr = log_dir_for_io.clone();
            tokio::spawn(async move {
                let stderr_path =
                    std::path::Path::new(&log_dir_for_stderr).join("backend.stderr.log");
                let reader = BufReader::new(stderr);
                let mut lines = reader.lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    let _ = app_for_stderr.emit("sidecar://stderr", &line);
                    log::warn!("[sidecar] {}", line);
                    append_log_line(&stderr_path, &line);
                }
                log::info!("[sidecar] stderr 流已结束，进程可能已退出");
            });
        }

        // 健康检查轮询：等待后端就绪
        self.wait_ready(app).await
    }

    /// 轮询健康检查端点直到就绪或超时
    async fn wait_ready<R: Runtime>(&self, app: &AppHandle<R>) -> Result<(), String> {
        let port = self.state.read().port;
        let url = format!("http://127.0.0.1:{port}/api/health/ping");
        let mut last_progress: u8 = 5;
        let max_attempts: u32 = 90; // 90 * 500ms = 45s
        let state_file_path = std::path::Path::new(&self.log_dir(app)).join("sidecar.json");
        for attempt in 0..max_attempts {
            if *self.shutdown_in_progress.read() {
                return Err("应用关闭中，已取消启动".to_string());
            }
            // 检测进程是否已退出（try_wait 是同步的，不会阻塞 async 运行时）
            {
                let mut guard = self.child.lock().await;
                if let Some(child) = guard.as_mut() {
                    match child.try_wait() {
                        Ok(Some(status)) => {
                            // 进程已退出
                            let code = status.code();
                            log::error!("[sidecar] wait_ready 检测到进程已退出: code={:?}", code);
                            *guard = None; // 清空 child
                            drop(guard); // 释放锁

                            let err_msg = format!("后端进程意外退出 (code={:?})", code);
                            let mut s = self.state.write();
                            s.status = BackendStatus::Crashed;
                            s.pid = None;
                            s.message = err_msg.clone();
                            s.last_error = Some(err_msg.clone());
                            drop(s);

                            self.emit_state(app);
                            self.emit_launch_progress(
                                app,
                                "backend_crashed",
                                last_progress,
                                &err_msg,
                                "error",
                            );
                            return Err(err_msg);
                        }
                        Ok(None) => {
                            // 仍在运行，继续
                        }
                        Err(e) => {
                            log::warn!("[sidecar] try_wait 失败: {e}");
                        }
                    }
                } else {
                    // child 已被清空（进程已退出）
                    let current_status = self.state.read().status.clone();
                    if matches!(
                        current_status,
                        BackendStatus::Crashed | BackendStatus::Failed
                    ) {
                        let err = self
                            .state
                            .read()
                            .last_error
                            .clone()
                            .unwrap_or_else(|| "后端进程已退出".to_string());
                        log::error!(
                            "[sidecar] wait_ready: child 已清空，状态={:?}, err={}",
                            current_status,
                            err
                        );
                        return Err(err);
                    }
                }
            }
            // 检测 sidecar.json 文件：如果 Python 端已写入 failed/stopped，立即返回
            if state_file_path.exists() {
                if let Ok(content) = std::fs::read_to_string(&state_file_path) {
                    if let Ok(json) = serde_json::from_str::<serde_json::Value>(&content) {
                        if let Some(status) = json.get("status").and_then(|v| v.as_str()) {
                            if status == "failed" {
                                let err = json
                                    .get("error")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("后端启动失败")
                                    .to_string();
                                log::error!("[sidecar] Python 端报告启动失败: {}", err);
                                self.update_status(
                                    app,
                                    BackendStatus::Failed,
                                    last_progress,
                                    "后端启动失败",
                                );
                                self.set_error(app, err.clone());
                                self.emit_launch_progress(
                                    app,
                                    "backend_failed",
                                    last_progress,
                                    &format!("后端启动失败: {err}"),
                                    "error",
                                );
                                if let Some(mut child) = self.child.lock().await.take() {
                                    let _ = child.start_kill();
                                }
                                return Err(format!("后端启动失败: {err}"));
                            }
                            if status == "stopped" {
                                log::warn!("[sidecar] Python 端报告已停止");
                                self.update_status(
                                    app,
                                    BackendStatus::Crashed,
                                    last_progress,
                                    "后端进程意外停止",
                                );
                                self.emit_launch_progress(
                                    app,
                                    "backend_stopped",
                                    last_progress,
                                    "后端进程意外停止",
                                    "error",
                                );
                                if let Some(mut child) = self.child.lock().await.take() {
                                    let _ = child.start_kill();
                                }
                                return Err("后端进程意外停止".to_string());
                            }
                        }
                    }
                }
            }
            match self.http.get(&url).send().await {
                Ok(resp) if resp.status().is_success() => {
                    self.update_status(app, BackendStatus::Running, 100, "后端服务已就绪");
                    self.emit_launch_progress(app, "ready", 100, "后端服务已就绪", "complete");
                    return Ok(());
                }
                Ok(_) | Err(_) => {
                    let progress = std::cmp::min(95, 5 + (attempt * 90 / max_attempts) as u8);
                    if progress > last_progress {
                        last_progress = progress;
                        let desc = format!("等待后端就绪 ({progress}%)");
                        self.update_status(app, BackendStatus::Starting, progress, desc.as_str());
                        if attempt % 5 == 0 {
                            self.emit_launch_progress(
                                app,
                                "wait_ready",
                                progress,
                                &desc,
                                "loading",
                            );
                        }
                    }
                }
            }
            sleep(Duration::from_millis(500)).await;
        }

        // 启动超时
        self.update_status(app, BackendStatus::Failed, last_progress, "后端启动超时");
        self.set_error(app, format!("健康检查超时: {url}"));
        self.emit_launch_progress(
            app,
            "timeout",
            last_progress,
            "后端启动超时，请检查日志",
            "error",
        );
        if let Some(mut child) = self.child.lock().await.take() {
            let _ = child.start_kill();
        }
        Err(format!("后端在 {} 秒内未响应健康检查", max_attempts / 2))
    }

    /// 优雅停止后端进程
    pub async fn stop<R: Runtime>(&self, app: &AppHandle<R>) -> Result<(), String> {
        *self.shutdown_in_progress.write() = true;
        self.update_status(app, BackendStatus::Stopping, 100, "正在关闭后端服务...");

        let port = self.state.read().port;
        let shutdown_url = format!("http://127.0.0.1:{}/api/v1/admin/shutdown", port);

        // 第 1 步：尝试 HTTP 通知 Python 端优雅关闭
        let mut graceful_succeeded = false;
        match reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(2))
            .build()
        {
            Ok(client) => match client.post(&shutdown_url).send().await {
                Ok(resp) => {
                    log::info!(
                        "[stop] graceful shutdown endpoint responded: {}",
                        resp.status()
                    );
                    graceful_succeeded = true;
                }
                Err(e) => {
                    log::debug!("[stop] HTTP shutdown 通知失败: {e}");
                }
            },
            Err(e) => {
                log::warn!("[stop] 构建 HTTP 客户端失败: {e}");
            }
        }

        // 第 2 步：take child 并 kill
        let child_opt = self.child.lock().await.take();
        if let Some(mut child) = child_opt {
            if graceful_succeeded {
                sleep(Duration::from_secs(5)).await;
                log::info!("[stop] 等待 5s 完成，调用 kill 兜底");
            }
            if let Err(e) = child.kill().await {
                log::debug!("[stop] kill 返回错误（进程可能已退出）: {e}");
            }
        }
        {
            let mut s = self.state.write();
            s.status = BackendStatus::Stopped;
            s.pid = None;
            s.message = "后端服务已停止".to_string();
            s.progress = 0;
        }
        self.emit_state(app);
        *self.shutdown_in_progress.write() = false;
        Ok(())
    }

    /// 重启后端进程
    pub async fn restart<R: Runtime>(&self, app: &AppHandle<R>) -> Result<(), String> {
        {
            let mut s = self.state.write();
            s.restart_count = s.restart_count.saturating_add(1);
        }
        if let Err(e) = self.stop(app).await {
            log::warn!("[sidecar] stop() before restart failed: {e}; attempting start anyway");
        }
        sleep(Duration::from_millis(800)).await;
        self.start(app).await
    }

    /// 同步强制终止后端进程（用于 RunEvent::Exit 等无法 await 的场景）
    ///
    /// 使用 `try_lock` 非阻塞获取子进程锁，避免在进程退出阶段死锁 tokio 运行时。
    /// `start_kill()` 是同步方法，发送 TerminateProcess (Windows) 或 SIGKILL (Unix)。
    pub fn force_kill_sync(&self) {
        match self.child.try_lock() {
            Ok(mut guard) => {
                if let Some(child) = guard.as_mut() {
                    match child.start_kill() {
                        Ok(()) => log::info!("[force_kill_sync] 后端子进程已终止"),
                        Err(e) => log::warn!("[force_kill_sync] 终止子进程失败: {e}"),
                    }
                } else {
                    log::info!("[force_kill_sync] 子进程句柄为空，无需清理");
                }
            }
            Err(_) => {
                log::warn!("[force_kill_sync] 子进程锁被占用，跳过同步终止（依赖 OS 清理）");
            }
        }
    }

    fn log_dir<R: Runtime>(&self, app: &AppHandle<R>) -> String {
        if let Ok(dir) = app.path().app_log_dir() {
            if dir.exists() {
                return dir.to_string_lossy().to_string();
            }
            if std::fs::create_dir_all(&dir).is_ok() {
                return dir.to_string_lossy().to_string();
            }
            log::warn!("[sidecar] app_log_dir 创建失败，回退到 temp 目录");
        }
        let temp_logs = std::env::temp_dir().join("lingjing-logs");
        if let Err(e) = std::fs::create_dir_all(&temp_logs) {
            log::warn!("[sidecar] 创建日志目录失败（含 temp 回退）: {e}");
        }
        temp_logs.to_string_lossy().to_string()
    }
}

/// 探测指定端口是否空闲（能否在 127.0.0.1 上绑定）
fn port_is_free(port: u16) -> bool {
    std::net::TcpListener::bind(("127.0.0.1", port)).is_ok()
}

/// 寻找可用端口
fn find_available_port(preferred: u16) -> u16 {
    for offset in 0..=20u16 {
        let candidate = preferred.saturating_add(offset);
        if port_is_free(candidate) {
            return candidate;
        }
    }
    if let Ok(listener) = std::net::TcpListener::bind(("127.0.0.1", 0)) {
        if let Ok(addr) = listener.local_addr() {
            log::warn!(
                "[sidecar] {preferred}~{} 全部被占用，使用 OS 随机分配端口 {}",
                preferred.saturating_add(20),
                addr.port()
            );
            return addr.port();
        }
    }
    log::error!("[sidecar] 端口探测完全失败，回退首选端口 {preferred}");
    preferred
}

/// 将一行日志追加写入文件
fn append_log_line(path: &std::path::Path, line: &str) {
    use std::io::Write;
    let ts = chrono::Utc::now().format("%H:%M:%S%.3f");
    let content = format!("[{ts}] {line}\n");
    if let Some(parent) = path.parent() {
        if !parent.exists() {
            let _ = std::fs::create_dir_all(parent);
        }
    }
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
    {
        let _ = f.write_all(content.as_bytes());
    }
}

/// 解析 Python 解释器路径
///
/// 优先级（P2-2 嵌入式运行时改造）：
/// 1. 资源目录内的嵌入式运行时 `desktop_runtime/runtime/python.exe`（打包分发，自包含）
/// 2. 环境变量 `LINGJING_PYTHON_PATH`（用户自定义，推荐打包分发时使用）
/// 3. 系统级 Python 安装路径（ProgramData、C:\Python3xx）
/// 4. 用户级 Python 安装路径（通过 %LOCALAPPDATA% 动态获取）
/// 5. 回退到 `python`（依赖 PATH）
///
/// 安全修复 (P0): 原有代码硬编码了 `C:\Users\<user>` 路径，分发到其他用户机器
/// 会自动失败。现已移除所有硬编码个人路径，改用环境变量动态获取。
fn resolve_python_path(resource_dir: Option<&std::path::Path>) -> String {
    // 1. 嵌入式运行时（自包含，目标机器无需 Python）
    if let Some(rd) = resource_dir {
        let embedded = rd
            .join("desktop_runtime")
            .join("runtime")
            .join(if cfg!(windows) {
                "python.exe"
            } else {
                "bin/python3"
            });
        if embedded.exists() {
            log::info!("[sidecar] 使用嵌入式运行时: {}", embedded.display());
            return embedded.to_string_lossy().to_string();
        }
    }
    if let Ok(p) = std::env::var("LINGJING_PYTHON_PATH") {
        if std::path::Path::new(&p).exists() {
            return p;
        }
    }
    #[cfg(target_os = "windows")]
    {
        // 系统级路径（所有用户共享）
        let system_candidates = [
            r"C:\ProgramData\anaconda3\python.exe",
            r"C:\Python313\python.exe",
            r"C:\Python312\python.exe",
            r"C:\Python311\python.exe",
        ];
        for c in &system_candidates {
            if std::path::Path::new(c).exists() {
                return c.to_string();
            }
        }

        // 用户级路径（通过 %LOCALAPPDATA% 动态获取，避免硬编码用户名）
        if let Ok(local_appdata) = std::env::var("LOCALAPPDATA") {
            let local_programs = std::path::Path::new(&local_appdata)
                .join("Programs")
                .join("Python");
            for ver in ["Python313", "Python312", "Python311"] {
                let candidate = local_programs.join(ver).join("python.exe");
                if candidate.exists() {
                    return candidate.to_string_lossy().to_string();
                }
            }
        }
    }
    "python".to_string()
}

/// 解析 Python 脚本路径和工作目录
///
/// 优先级（P2-2 嵌入式运行时改造）：
/// 1. 资源目录内的嵌入式后端 `desktop_runtime/backend/start_server.py`（打包分发）
/// 2. 环境变量 `LINGJING_PYTHON_SCRIPT`（脚本路径）
/// 3. 编译时 `CARGO_MANIFEST_DIR` 推导（开发模式：src-tauri/../python/start_server.py）
/// 4. 回退到当前目录下的 `start_server.py`
fn resolve_python_script_and_dir(
    resource_dir: Option<&std::path::Path>,
) -> (String, std::path::PathBuf) {
    // 1. 嵌入式后端（与运行时同目录打包）
    if let Some(rd) = resource_dir {
        let script = rd
            .join("desktop_runtime")
            .join("backend")
            .join("start_server.py");
        if script.exists() {
            let dir = script
                .parent()
                .map(|p| p.to_path_buf())
                .unwrap_or_else(|| std::env::current_dir().unwrap_or_default());
            return (script.to_string_lossy().to_string(), dir);
        }
    }
    // 2. 环境变量
    if let Ok(p) = std::env::var("LINGJING_PYTHON_SCRIPT") {
        let path = std::path::Path::new(&p);
        if path.exists() {
            let dir = path
                .parent()
                .map(|p| p.to_path_buf())
                .unwrap_or_else(|| std::env::current_dir().unwrap_or_default());
            return (p, dir);
        }
    }
    // 2. 编译时路径推导（开发模式）
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    let python_dir = std::path::Path::new(manifest_dir).join("..").join("python");
    let script = python_dir.join("start_server.py");
    if script.exists() {
        return (script.to_string_lossy().to_string(), python_dir.clone());
    }
    // 3. 回退
    log::warn!(
        "[sidecar] 无法定位 start_server.py (尝试过: {})，回退到当前目录",
        script.display()
    );
    (
        "start_server.py".to_string(),
        std::env::current_dir().unwrap_or_default(),
    )
}
