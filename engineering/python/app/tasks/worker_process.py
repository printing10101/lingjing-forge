"""Standalone worker process for plugin execution.

This script is spawned by PluginWorkerManager._run_worker() as an independent
subprocess. It loads the target plugin, initializes it, and runs an HTTP
health-check endpoint on the port assigned via the PLUGIN_PORT environment
variable.

Environment variables set by the parent:
    PLUGIN_ID     — unique plugin identifier
    PLUGIN_PORT   — port for the health-check HTTP server
    PLUGIN_PATH   — filesystem path to the plugin directory
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import signal
import socketserver
import sys
import threading
import time
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("worker_process")


class _WorkerState:
    """Shared mutable state accessed by the HTTP handler and main loop."""

    plugin_id: str = ""
    plugin_path: str = ""
    plugin_port: int = 0
    plugin_instance: Any | None = None
    started_at: float = 0.0
    running: bool = True


state = _WorkerState()


def _load_plugin() -> Any:
    """Dynamically import the plugin module from PLUGIN_PATH and return its instance."""
    plugin_dir = state.plugin_path
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)

    try:
        from plugin import create_plugin

        instance = create_plugin()
        logger.info("Plugin '%s' loaded from %s", state.plugin_id, plugin_dir)
        return instance
    except ImportError:
        logger.warning("No plugin.py found in %s, using stub", plugin_dir)
        return None


def _start_health_server(port: int) -> socketserver.TCPServer:
    """Start a minimal HTTP server that reports worker health status.

    仅绑定 127.0.0.1：该端口仅供父进程 PluginWorkerManager 通过本机回环探测
    子进程存活状态，无需暴露到外网，避免被远程探测或攻击。
    """
    bind_host = os.environ.get("PLUGIN_HEALTH_HOST", "127.0.0.1")

    class HealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/health":
                payload = {
                    "status": "running",
                    "plugin_id": state.plugin_id,
                    "uptime": time.time() - state.started_at,
                }
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            logger.debug(format, *args)

    server = socketserver.ThreadingTCPServer((bind_host, port), HealthHandler)
    server.daemon_threads = True
    return server


def _signal_handler(signum: int, frame: Any) -> None:
    """Handle SIGTERM / SIGINT for graceful shutdown."""
    logger.info("Received signal %s, shutting down", signum)
    state.running = False


def main() -> None:
    """插件 worker 进程入口（纯同步上下文）。

    .. note::
        仅同步上下文使用：本函数作为独立进程入口，使用 ``time.sleep``
        维持主循环心跳，不存在 async 事件循环，无需改用 ``asyncio.sleep``。
    """
    # Read environment
    state.plugin_id = os.environ.get("PLUGIN_ID", "unknown")
    state.plugin_port = int(os.environ.get("PLUGIN_PORT", "8080"))
    state.plugin_path = os.environ.get("PLUGIN_PATH", ".")
    state.started_at = time.time()

    logger.info(
        "Worker starting: id=%s, port=%s, path=%s",
        state.plugin_id,
        state.plugin_port,
        state.plugin_path,
    )

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    plugin = _load_plugin()
    state.plugin_instance = plugin

    if plugin and hasattr(plugin, "initialize"):
        plugin.initialize({})

    server = _start_health_server(state.plugin_port)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    logger.info("Health server listening on port %s", state.plugin_port)

    try:
        while state.running:
            time.sleep(1)
    except KeyboardInterrupt:
        # 用户主动中断是 worker 正常退出信号，静默处理
        pass
    finally:
        server.shutdown()
        if plugin and hasattr(plugin, "shutdown"):
            plugin.shutdown()
        logger.info("Worker stopped")


if __name__ == "__main__":
    main()
