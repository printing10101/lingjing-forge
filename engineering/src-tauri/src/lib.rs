//! 灵境制造 V4 - Tauri 桌面应用入口
//!
//! 该 crate 负责：
//! 1. 启动 Tauri 运行时与窗口
//! 2. 启动由 PyInstaller 打包的 Python 后端 Sidecar 进程
//! 3. 暴露 IPC 命令供前端调用
//! 4. 监听应用退出事件，优雅终止后端

mod commands;
mod sidecar;

use std::sync::Arc;

use tauri::{Manager, RunEvent, WebviewWindowBuilder, WindowEvent};

use crate::commands::{
    auto_fix_health, close_splashscreen, export_logs_cmd, get_backend_port, get_backend_state,
    get_diagnostics_text, get_version_info, open_external_url, ping_backend, restart_backend,
    run_health_check, run_single_health_check, start_backend, stop_backend, AppState,
};
use crate::sidecar::SidecarManager;

/// 默认后端端口（与 `python/app/main.py` 中 uvicorn 启动端口保持一致）
// P0-9 修复：原本为 8000，与 Python 端 config.server.port=8765 不一致，
// 导致 Tauri 默认连接 8000 端口而 Python 监听 8765，前端调用全部失败。
pub const DEFAULT_BACKEND_PORT: u16 = 8765;

/// 获取应用数据目录路径（com.lingjing.manufacturing）
fn app_data_dir() -> Option<std::path::PathBuf> {
    let local_app_data = std::env::var("LOCALAPPDATA").ok()?;
    Some(std::path::Path::new(&local_app_data).join("com.lingjing.manufacturing"))
}

/// 获取日志目录路径并确保目录存在
/// 优先使用 app_data_dir/logs，如果权限不足则回退到 temp 目录
fn ensure_logs_dir() -> Option<std::path::PathBuf> {
    // 优先尝试标准位置
    if let Some(app_data) = app_data_dir() {
        let dir = app_data.join("logs");
        if dir.exists() {
            return Some(dir);
        }
        if std::fs::create_dir_all(&dir).is_ok() {
            return Some(dir);
        }
        // 标准位置创建失败（权限不足），继续尝试 temp
    }
    // 回退到 temp 目录（始终可写）
    let temp_logs = std::env::temp_dir().join("lingjing-logs");
    match std::fs::create_dir_all(&temp_logs) {
        Ok(()) => {
            eprintln!("[INFO] 日志目录回退到: {}", temp_logs.display());
            Some(temp_logs)
        }
        Err(e) => {
            eprintln!("[WARN] 创建日志目录失败（含 temp 回退）: {e}");
            None
        }
    }
}

/// 文件直写诊断日志（绕过 env_logger 的 stderr 缓冲问题）
/// 写入 app_log_dir/startup-debug.log，每次调用立即 flush。
fn diag_log(msg: &str) {
    log::info!("{}", msg);
    #[cfg(target_os = "windows")]
    {
        if let Some(logs_dir) = ensure_logs_dir() {
            let log_path = logs_dir.join("startup-debug.log");
            if let Ok(mut f) = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&log_path)
            {
                use std::io::Write;
                let ts = chrono::Local::now().format("%H:%M:%S%.3f");
                let _ = writeln!(f, "[{ts}] {msg}");
                let _ = f.flush();
            }
        }
    }
}

/// 清理上一次运行可能残留的 WebView2 状态并准备干净的运行环境
///
/// 核心策略：通过设置 WEBVIEW2_USER_DATA_FOLDER 环境变量，让 WebView2
/// 使用一个全新的、可写的临时目录，从根本上避免以下问题：
///
/// 1. HRESULT(0x800700AA) "请求的资源在使用中"
///    - 原因：EBWebView 目录被上一次崩溃的进程锁住，或 LevelDB LOCK 文件残留
///    - 原方案：删除 EBWebView 目录 → 失败，因为 app_data_dir 权限不足
///    - 新方案：使用全新目录，完全绕过锁问题
///
/// 2. 日志目录创建失败 "拒绝访问 (os error 5)"
///    - 原因：NSIS perMachine 安装模式创建了受限权限的目录
///    - 新方案：ensure_logs_dir() 回退到 temp 目录
///
/// 3. remove_dir_all 挂起
///    - 原因：EBWebView 目录中某些文件被系统进程锁定
///    - 新方案：不删除任何文件，改用新目录
#[cfg(target_os = "windows")]
fn cleanup_orphaned_webview2() {
    // 第一步：精准终止孤儿 WebView2 进程
    let ps_script = r#"
        $killed = 0
        Get-CimInstance Win32_Process -Filter "name='msedgewebview2.exe'" |
          Where-Object { $_.CommandLine -like '*com.lingjing.manufacturing*' } |
          ForEach-Object {
            Write-Host "KILL:$($_.ProcessId)"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            $killed++
          }
        Write-Host "COUNT:$killed"
    "#;

    let mut killed_count = 0u32;
    match std::process::Command::new("powershell")
        .args(["-NoProfile", "-NonInteractive", "-Command", ps_script])
        .output()
    {
        Ok(out) => {
            let stdout = String::from_utf8_lossy(&out.stdout);
            for line in stdout.lines() {
                if let Some(pid_str) = line.strip_prefix("KILL:") {
                    diag_log(&format!(
                        "[cleanup] 终止孤儿 WebView2 进程 PID={}",
                        pid_str.trim()
                    ));
                } else if let Some(cnt_str) = line.strip_prefix("COUNT:") {
                    killed_count = cnt_str.trim().parse().unwrap_or(0);
                }
            }
            if killed_count > 0 {
                diag_log(&format!(
                    "[cleanup] 共终止 {} 个孤儿 WebView2 进程",
                    killed_count
                ));
                // 等待进程退出
                std::thread::sleep(std::time::Duration::from_millis(800));
            } else {
                diag_log("[cleanup] 未发现孤儿 WebView2 进程");
            }
        }
        Err(e) => {
            diag_log(&format!("[cleanup] 启动 PowerShell 清理失败: {e}"));
        }
    }

    // 第二步：设置 WEBVIEW2_USER_DATA_FOLDER 到可写的临时目录
    // 这是核心修复：不再尝试删除可能被锁/权限不足的 EBWebView 目录，
    // 而是让 WebView2 使用一个全新的目录，从根本上避免锁冲突。
    let webview2_dir = std::env::temp_dir().join("lingjing-webview2");
    match std::fs::create_dir_all(&webview2_dir) {
        Ok(()) => {
            std::env::set_var(
                "WEBVIEW2_USER_DATA_FOLDER",
                webview2_dir.to_string_lossy().to_string(),
            );
            diag_log(&format!(
                "[cleanup] WEBVIEW2_USER_DATA_FOLDER 设为: {}",
                webview2_dir.display()
            ));
        }
        Err(e) => {
            diag_log(&format!(
                "[cleanup] 创建临时 WebView2 目录失败: {e}，使用默认路径"
            ));
        }
    }
}

#[cfg(not(target_os = "windows"))]
fn cleanup_orphaned_webview2() {
    // 非 Windows 平台无需清理 WebView2
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    match env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info"))
        .try_init()
    {
        Ok(()) => {}
        Err(e) => eprintln!("[WARN] 日志初始化失败 (可能已有其他初始化器): {e}"),
    }

    // 清理 WebView2 状态
    // 1. 精准终止孤儿 WebView2 进程（WMI 匹配 com.lingjing.manufacturing）
    // 2. 设置 WEBVIEW2_USER_DATA_FOLDER 到可写的临时目录
    //
    // 关键：app_data_dir 可能权限不足（NSIS perMachine 安装），
    // 无法删除/创建子目录。通过设置环境变量让 WebView2 使用 temp 目录，
    // 完全绕过权限和锁冲突问题。
    cleanup_orphaned_webview2();
    diag_log("[启动] 进入 run()，开始构建 Tauri Builder");

    let manager = Arc::new(match SidecarManager::new(DEFAULT_BACKEND_PORT) {
        Ok(m) => m,
        Err(e) => {
            log::error!("创建 SidecarManager 失败: {e}");
            return;
        }
    });
    let app_state = AppState {
        sidecar: manager.clone(),
    };

    let manager_for_setup = manager.clone();

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(app_state)
        .invoke_handler(tauri::generate_handler![
            get_backend_state,
            start_backend,
            stop_backend,
            restart_backend,
            ping_backend,
            get_version_info,
            get_backend_port,
            close_splashscreen,
            // 健康检查与日志导出（前端 HealthCheck.vue + useSettings.ts 调用）
            run_health_check,
            run_single_health_check,
            auto_fix_health,
            get_diagnostics_text,
            export_logs_cmd,
            // 外部链接打开（关于页「前往下载」，自动更新过渡方案）
            open_external_url,
        ])
        .setup(move |app| {
            let app_handle = app.handle().clone();
            diag_log("[setup] 进入 setup 回调");

            // 启动 Sidecar（仅在 desktop 平台执行；移动端暂不打包后端）
            #[cfg(desktop)]
            {
                let manager_for_start = manager_for_setup.clone();
                let app_for_start = app_handle.clone();
                tauri::async_runtime::spawn(async move {
                    // 应用启动后稍等片刻，让 UI 有时间显示"启动中"状态
                    tokio::time::sleep(std::time::Duration::from_millis(300)).await;
                    diag_log("[sidecar] 开始启动后端 sidecar");
                    match manager_for_start.start(&app_for_start).await {
                        Ok(_) => diag_log("[sidecar] 后端启动成功"),
                        Err(e) => {
                            diag_log(&format!("[sidecar] 后端启动失败: {e}"));
                        }
                    }
                });
            }

            // 延迟创建主窗口：避免与 splashscreen 同时创建 WebView2
            // 导致 HRESULT(0x800700AA) "请求的资源在使用中" 错误。
            // splashscreen 的 WebView2 先独占 EBWebView 目录完成初始化，
            // 之后再创建 main 窗口的 WebView2，避免两个窗口争抢同一用户数据目录。
            #[cfg(desktop)]
            {
                let manager_for_close = manager_for_setup.clone();
                let app_for_close = app_handle.clone();
                tauri::async_runtime::spawn(async move {
                    // 短暂延迟让 splashscreen 先完成初始渲染。
                    // Tauri 本身能正确处理多窗口 WebView2 的并发创建，
                    // 不需要长延迟；500ms 足够让 splashscreen 显示出来。
                    tokio::time::sleep(std::time::Duration::from_millis(500)).await;

                    diag_log("[main] 开始创建主窗口 main (延迟500ms后)");
                    let main_window = match WebviewWindowBuilder::new(
                        &app_for_close,
                        "main",
                        tauri::WebviewUrl::App("index.html".into()),
                    )
                    .title("灵境制造 V4")
                    .inner_size(1440.0, 900.0)
                    .min_inner_size(1024.0, 700.0)
                    .resizable(true)
                    .fullscreen(false)
                    .center()
                    .decorations(true)
                    // .transparent(false) 已移除（2026-09-10）：false 本就是默认值，
                    // 且该方法在 macOS 目标被 macos-private-api feature 门控，
                    // 未启用 feature 时 macOS 编译直接 E0599（CI 实测）
                    .visible(false)
                    .build()
                    {
                        Ok(w) => {
                            diag_log("[main] 主窗口创建成功");
                            w
                        }
                        Err(e) => {
                            diag_log(&format!("[main] 主窗口创建失败: {e}"));
                            // 即使 main 窗口创建失败，也要关闭 splashscreen，
                            // 否则用户会卡在"启动中"画面永远进不去。
                            // 关闭后用户至少看到桌面，可以查看日志或重新启动。
                            if let Some(splash) = app_for_close.get_webview_window("splashscreen") {
                                let _ = splash.close();
                            }
                            return;
                        }
                    };

                    // 诊断：立即检查 main 窗口 URL，确认 WebView2 加载方向是否正确
                    if let Some(win) = app_for_close.get_webview_window("main") {
                        match win.url() {
                            Ok(url) => log::info!("[诊断] main 窗口 URL (创建后): {url}"),
                            Err(e) => log::error!("[诊断] 获取 main 窗口 URL 失败: {e}"),
                        }
                        // 立即 eval：设置 title 确认 WebView2 JS 引擎是否工作
                        match win.eval(r#"try{document.title='WEBVIEW_CREATED';}catch(e){}"#) {
                            Ok(_) => log::info!("[诊断] 立即 eval 注入成功 (设置 title)"),
                            Err(e) => log::error!("[诊断] 立即 eval 注入失败: {e}"),
                        }
                    }

                    // 诊断：2 秒后 eval 注入完整诊断代码（不等 5 秒，加快反馈）
                    // 取 sidecar 实际解析到的端口而非编译期常量：sidecar.rs 在首选端口被
                    // 占用时会退化到空闲端口，此处必须跟随，否则诊断通道静默失效。
                    let api_base = format!("http://127.0.0.1:{}", manager_for_close.state().port);
                    let app_for_eval = app_for_close.clone();
                    tauri::async_runtime::spawn(async move {
                        tokio::time::sleep(std::time::Duration::from_secs(2)).await;
                        if let Some(win) = app_for_eval.get_webview_window("main") {
                            log::info!("[诊断] 2秒后 eval 注入诊断代码");
                            match win.url() {
                                Ok(url) => log::info!("[诊断] main 窗口 URL (2s): {url}"),
                                Err(e) => log::error!("[诊断] 获取 main 窗口 URL 失败 (2s): {e}"),
                            }
                            match win.title() {
                                Ok(title) => log::info!("[诊断] main 窗口标题 (2s): {title}"),
                                Err(e) => log::error!("[诊断] 获取 main 窗口标题失败 (2s): {e}"),
                            }
                            let js = r#"
                                (function() {
                                    // __API_BASE__ 由 Rust 侧替换为 sidecar 实际解析到的端口。
                                    // 首选端口被占用时 sidecar 会退化到空闲端口（sidecar.rs
                                    // find_available_port），硬编码 8765 会让诊断通道在端口漂移时静默失效。
                                    var API = '__API_BASE__';
                                    try {
                                        var mounted = window.__VUE_MOUNTED__ || false;
                                        var diagInstalled = window.__DIAG_INSTALLED__ || false;
                                        var appEl = document.getElementById('app');
                                        var appHtml = appEl ? appEl.innerHTML.substring(0, 500) : 'NO_APP_ELEMENT';
                                        var appLen = appEl ? appEl.innerHTML.length : 0;
                                        var scripts = document.querySelectorAll('script').length;
                                        var readyState = document.readyState;
                                        var title = document.title;
                                        var href = location.href;
                                        var msg = 'title=' + title + ' mounted=' + mounted + ' diagInstalled=' + diagInstalled + ' appLen=' + appLen + ' scripts=' + scripts + ' readyState=' + readyState + ' url=' + href + ' appHtml=' + appHtml;
                                        var fullHtml = appEl ? appEl.innerHTML : '';
                                        var bodyText = document.body ? document.body.innerText.substring(0, 300) : '';
                                        var errMsgs = window.__ERR_MSGS__ || [];
                                        var fatalLog = 'FULLHTML=[' + fullHtml + '] BODYTEXT=[' + bodyText + '] ERRS=' + JSON.stringify(errMsgs);
                                        try { fetch(API + '/api/health/ping?__full__=' + encodeURIComponent(fatalLog)).catch(function(){}); } catch(e2) {}
                                        var diagDiv = document.createElement('div');
                                        diagDiv.id = '__RUST_DIAG__';
                                        diagDiv.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#1e1e1e;color:#0f0;font-family:Consolas,monospace;font-size:12px;padding:8px;z-index:999999;white-space:pre-wrap;max-height:300px;overflow:auto;';
                                        diagDiv.textContent = '[RUST DIAG 2s] ' + msg + '

[FULL] ' + (appEl ? appEl.innerHTML : 'NO_APP');
                                        if (document.body) document.body.appendChild(diagDiv);
                                        // P0-修复：fetch 必须挂 .catch()，否则端口漂移（8765 被占用）时
                                        // promise rejection 是异步的，外层 try/catch 捕获不到，
                                        // 会触发 main.ts 全局 unhandledrejection 监听，渲染致命错误页覆盖整个 UI。
                                        try {
                                            fetch(API + '/api/health/ping?__diag__=' + encodeURIComponent(msg)).catch(function(){});
                                        } catch(e) {
                                            if (diagDiv) diagDiv.textContent += '\n[FETCH_FAIL] ' + e.message;
                                            try {
                                                fetch(API + '/api/health/ping?__diag__=FETCH_FAIL=' + encodeURIComponent(e.message)).catch(function(){});
                                            } catch(e2) {}
                                        }
                                    } catch(e) {
                                        try {
                                            fetch(API + '/api/health/ping?__diag__=EVAL_ERROR=' + encodeURIComponent(e.message)).catch(function(){});
                                        } catch(e2) {}
                                    }
                                })();
                            "#.replace("__API_BASE__", &api_base);
                            match win.eval(js.as_str()) {
                                Ok(_) => log::info!("[诊断] eval 注入成功 (2s)"),
                                Err(e) => log::error!("[诊断] eval 注入失败 (2s): {e}"),
                            }
                        } else {
                            log::warn!("[诊断] main 窗口不存在 (2s)");
                        }
                    });

                    // 诊断模式：仅在 debug 构建下打开 DevTools
                    // release 构建不再打开 DevTools，避免暴露 __TAURI__ 全局对象与开发工具入口
                    // （devtools feature 仍保留在 Cargo.toml，是为了 release 出包后通过环境变量
                    //   或快捷键按需打开；这里仅控制默认行为）
                    #[cfg(debug_assertions)]
                    {
                        if let Some(win) = app_for_close.get_webview_window("main") {
                            let _ = win.open_devtools();
                        }
                    }

                    // 为主窗口注册关闭事件：主窗口关闭 -> 优雅停止后端
                    let mgr = manager_for_close.clone();
                    let app_h = app_for_close.clone();
                    main_window.on_window_event(move |event| {
                        if let WindowEvent::CloseRequested { .. } = event {
                            let mgr = mgr.clone();
                            let app_h = app_h.clone();
                            // H1 bug 修复：原代码使用 block_on 在 UI 线程上同步等待
                            // stop() 完成（内部含 5 秒 sleep），导致窗口关闭时 UI 卡死。
                            // 改用 spawn 在后台执行，UI 线程立即返回。
                            tauri::async_runtime::spawn(async move {
                                if let Err(e) = mgr.stop(&app_h).await {
                                    log::warn!("停止 sidecar 时出现错误: {e}");
                                }
                            });
                        }
                    });
                });

                // 超时兜底：独立于 main 窗口创建逻辑。
                // 无论 main 窗口是否创建成功，3 秒后都强制 show main + close splashscreen。
                // 原为 10 秒，但实测前端 close_splashscreen 在 Vue 挂载失败时不会被调用，
                // 导致用户等 10 秒才看到主窗口。改为 3 秒：足够 Vue 挂载（正常 ~1.5s），
                // 失败时也只等 3 秒，main 窗口的诊断占位符会显示启动状态而非白屏。
                let app_for_timeout = app_handle.clone();
                tauri::async_runtime::spawn(async move {
                    tokio::time::sleep(std::time::Duration::from_secs(3)).await;
                    log::warn!("[兜底] 3 秒到达，开始强制切换窗口");

                    // 先尝试 show main 窗口（如果存在且未显示）
                    if let Some(main_win) = app_for_timeout.get_webview_window("main") {
                        let already_visible = main_win.is_visible().unwrap_or(false);
                        if !already_visible {
                            log::warn!("[兜底] 强制显示 main 窗口");
                            if let Err(e) = main_win.show() {
                                log::error!("[兜底] 强制 show main 窗口失败: {e}");
                            }
                            let _ = main_win.set_focus();
                        } else {
                            log::info!("[兜底] main 窗口已可见，无需强制显示");
                        }
                    } else {
                        log::warn!("[兜底] main 窗口不存在（创建失败或仍在创建中）");
                    }

                    // 无论 main 窗口状态如何，都关闭 splashscreen
                    // （如果 main 不存在，用户会回到桌面；如果 main 存在，用户进入主界面）
                    if let Some(splash) = app_for_timeout.get_webview_window("splashscreen") {
                        log::info!("[兜底] 关闭 splashscreen 窗口");
                        let _ = splash.close();
                    }
                });
            }

            Ok(())
        })
        .build(tauri::generate_context!());

    // 安全修复：避免 .expect() 在 build 失败时直接 panic 导致进程异常终止。
    // 改为 match 返回 Result，记录错误日志后以非零码优雅退出。
    let app = match app {
        Ok(a) => a,
        Err(e) => {
            log::error!("启动 Tauri 应用失败: {e}");
            eprintln!("启动 Tauri 应用失败: {e}");
            std::process::exit(1);
        }
    };

    let manager_for_run = manager.clone();
    // 启动时刻：用于 ExitRequested 竞态保护（splash 关闭与 main 窗口
    // 创建存在竞态，启动初期收到的退出请求多为误触发）
    let start_instant = std::time::Instant::now();
    app.run(move |_app_handle, event| match event {
        RunEvent::ExitRequested { api, .. } => {
            log::info!("收到 ExitRequested，进程即将退出");
            // 启动竞态保护：splashscreen 关闭时若 main 窗口尚未可见
            // （visible=false，仍在创建），Tauri 会误判"所有窗口已关闭"
            // 并触发 ExitRequested。启动初期一律阻止，超时后正常放行，
            // 保证用户手动关闭窗口仍可正常退出。
            let elapsed = start_instant.elapsed();
            if elapsed < std::time::Duration::from_secs(20) {
                api.prevent_exit();
                log::info!("启动初期 ExitRequested 被阻止（splash/main 竞态保护）");
            } else {
                log::info!("退出请求已放行（超过启动保护窗口）");
            }
        }
        RunEvent::Exit => {
            // 进程退出阶段：同步强制终止后端子进程，避免残留
            // 注意：此处不能 await，使用 force_kill_sync 非阻塞终止
            log::info!("Tauri RunEvent::Exit 已触发，强制终止后端进程");
            manager_for_run.force_kill_sync();
        }
        _ => {}
    });
}

/// 递归遍历目录，返回所有文件和子目录的路径列表。
/// 这是个最小化的实现，避免引入 `walkdir` crate 依赖。
/// 仅用于启动时清理 WebView2 Default 目录下的 LOCK 文件。
///
/// 安全修复：原实现无深度限制，遇到符号链接环（symlink loop）会无限递归导致栈溢出。
/// 现添加 max_depth 限制（默认 10 层）并跳过符号链接，避免栈溢出风险。
fn walkdir(dir: &std::path::Path) -> Vec<std::path::PathBuf> {
    walkdir_inner(dir, 0, 10)
}

fn walkdir_inner(dir: &std::path::Path, depth: u32, max_depth: u32) -> Vec<std::path::PathBuf> {
    let mut result = Vec::new();
    // 深度超限直接返回，避免恶意构造的深层嵌套目录导致栈溢出
    if depth >= max_depth {
        return result;
    }
    if let Ok(entries) = std::fs::read_dir(dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            // 跳过符号链接，避免符号链接环导致无限递归栈溢出
            // （使用 symlink_metadata 不跟踪符号链接目标）
            let is_symlink = std::fs::symlink_metadata(&path)
                .map(|m| m.file_type().is_symlink())
                .unwrap_or(false);
            if is_symlink {
                continue;
            }
            result.push(path.clone());
            if path.is_dir() {
                result.extend(walkdir_inner(&path, depth + 1, max_depth));
            }
        }
    }
    result
}
