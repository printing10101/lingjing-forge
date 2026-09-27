import os
import time
import signal
import asyncio
import logging
import atexit
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)


class IdleAutoShutdownMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        idle_timeout: int = 1800,
        state_file_path: str | None = None,
        check_interval: int = 60,
    ):
        super().__init__(app)
        self.idle_timeout = idle_timeout
        self.last_activity_time = time.time()
        self.state_file_path = state_file_path
        self._shutdown_initiated = False
        self._checker_task: asyncio.Task | None = None
        self._check_interval = check_interval

    async def start_idle_checker(self):
        """Start a background task to periodically check idle timeout."""
        if self._checker_task is not None:
            return
        self._checker_task = asyncio.create_task(self._idle_check_loop())
        logger.info(f"Idle checker started (interval={self._check_interval}s, timeout={self.idle_timeout}s)")

    async def _idle_check_loop(self):
        while not self._shutdown_initiated:
            try:
                self.check_idle_and_shutdown()
            except (RuntimeError, OSError) as e:
                logger.error("Error in idle checker: %s", e)
            await asyncio.sleep(self._check_interval)

    async def dispatch(self, request: Request, call_next):
        # Auto-start idle checker on first request if not already started
        if self._checker_task is None:
            await self.start_idle_checker()

        # P1-10 修复：原代码仅排除 /health，但实际健康探针路径为
        # /api/health 和 /api/health/ping。K8s 探针每 10-30s 访问探针端点，
        # 被误计为"用户活动"导致空闲关停永不触发。此处覆盖所有探针路径，
        # 确保仅真实业务请求才重置空闲计时器。
        _HEALTH_PROBE_PATHS = frozenset(
            {
                "/health",
                "/api/health",
                "/api/health/ping",
                "/api/v1/health/quick",
            }
        )
        if request.url.path not in _HEALTH_PROBE_PATHS:
            self.last_activity_time = time.time()
        else:
            if self._shutdown_initiated:
                from starlette.responses import JSONResponse

                return JSONResponse(
                    content={
                        "status": "shutting_down",
                        "message": "Server is shutting down",
                    },
                    status_code=503,
                )

        response = await call_next(request)
        return response

    def check_idle_and_shutdown(self):
        if self._shutdown_initiated:
            return

        idle_duration = time.time() - self.last_activity_time

        if idle_duration >= self.idle_timeout:
            self._shutdown_initiated = True
            logger.warning(f"Idle timeout reached ({self.idle_timeout}s). Initiating auto shutdown...")
            self._cleanup_resources()
            self._remove_state_file()
            self._trigger_shutdown()

    def _cleanup_resources(self):
        logger.info("Cleaning up resources before shutdown...")

        try:
            from app.ai.lnn.inference.model_cache import ModelCache

            cache = ModelCache()
            cache.clear()
            logger.info("Model cache cleared")
        except (ImportError, OSError, RuntimeError) as e:
            logger.warning("Failed to clear model cache: %s", e)

        try:
            import gc

            gc.collect()
            logger.info("Garbage collection completed")
        except (RuntimeError, OSError) as e:
            logger.warning("Garbage collection failed: %s", e)

        logger.info("Resource cleanup completed")

    def _remove_state_file(self):
        if not self.state_file_path:
            return

        try:
            state_path = Path(self.state_file_path)
            if state_path.exists():
                state_path.unlink()
                logger.info("State file removed: %s", state_path)
        except (OSError, PermissionError) as e:
            logger.warning("Failed to remove state file: %s", e)

    def _trigger_shutdown(self):
        logger.info("Triggering application shutdown...")

        try:
            loop = asyncio.get_running_loop()
            loop.call_later(0.5, self._send_shutdown_signal)
        except RuntimeError:
            try:
                import threading

                t = threading.Thread(target=self._send_shutdown_signal, daemon=True)
                t.start()
            except (RuntimeError, OSError) as e:
                logger.error("Failed to trigger shutdown: %s", e)

    def _send_shutdown_signal(self):
        try:
            if os.name == "nt":
                os.kill(os.getpid(), signal.SIGTERM)
            else:
                os.kill(os.getpid(), signal.SIGTERM)
        except (OSError, ProcessLookupError, PermissionError) as e:
            logger.error("Failed to send shutdown signal: %s", e)


class GracefulShutdownHandler:
    def __init__(self, app=None, state_file_path: str | None = None):
        self.app = app
        self.state_file_path = state_file_path
        self._shutting_down = False
        # [H6] 保存 create_task 引用防止 GC 回收
        self._shutdown_task: asyncio.Task | None = None

    def setup(self):
        # signal.signal() 仅在主线程中可用；在测试环境（TestClient 运行于
        # 子线程）或非主解释器中会抛 ValueError，需优雅降级。
        try:
            signal.signal(signal.SIGTERM, self._handle_shutdown_signal)
            signal.signal(signal.SIGINT, self._handle_shutdown_signal)
        except (ValueError, OSError) as e:
            logger.warning(
                "Signal handlers not registered (non-main thread or unsupported platform): %s",
                e,
            )

        atexit.register(self._handle_atexit)

        logger.info("Graceful shutdown handler registered")

    def _handle_shutdown_signal(self, signum, frame):
        if self._shutting_down:
            logger.warning("Shutdown already in progress, ignoring signal")
            return

        self._shutting_down = True
        signal_name = signal.Signals(signum).name
        logger.info("Received %s signal, initiating graceful shutdown...", signal_name)

        self._update_status_file("shutting_down")

        # 修复：避免在已有事件循环的线程中调用 asyncio.run()
        try:
            loop = asyncio.get_running_loop()
            # [H6] 保存任务引用防止 GC 回收导致 shutdown 流程被中断。
            # 信号处理器中 create_task 的弱引用可能在 shutdown 完成前被回收。
            self._shutdown_task = loop.create_task(self._perform_graceful_shutdown())
        except RuntimeError:
            # 没有运行中的事件循环，安全地使用 asyncio.run()
            asyncio.run(self._perform_graceful_shutdown())

    async def _perform_graceful_shutdown(self):
        logger.info("Performing graceful shutdown...")

        logger.info("Stopping accepting new requests...")

        await asyncio.sleep(0.5)

        logger.info("Completing in-flight requests...")
        await asyncio.sleep(1.0)

        # P0-7 修复：先调用 FastAPI shutdown_event 中的清理逻辑，
        # 确保 ring_log / sse_manager / AsyncTaskManager / Redis / DB / ChromaDB
        # 等资源按正常 shutdown 流程释放，避免 os._exit(0) 绕过清理导致
        # Windows 文件句柄锁定、SQLite WAL 未 checkpoint 等问题。
        try:
            from app.main import shutdown_event

            await shutdown_event()
            logger.info("FastAPI shutdown_event completed")
        except Exception as e:
            logger.error("shutdown_event failed: %s", e, exc_info=True)

        self._cleanup_all_resources()

        self._update_status_file("stopped")
        self._remove_state_file()

        logger.info("Graceful shutdown completed")
        # 保留 os._exit(0) 确保 sidecar 立即退出，避免 sidecar.json 残留；
        # 此时 shutdown_event 已执行，资源已释放。
        os._exit(0)

    def _cleanup_all_resources(self):
        logger.info("Cleaning up all resources...")

        try:
            from app.ai.lnn.inference.model_cache import ModelCache

            cache = ModelCache()
            cache.clear()
            logger.info("Model cache cleared")
        except (ImportError, OSError, RuntimeError) as e:
            logger.warning("Failed to clear model cache: %s", e)

        try:
            import gc

            gc.collect()
            logger.info("Garbage collection completed")
        except (RuntimeError, OSError) as e:
            logger.warning("Garbage collection failed: %s", e)

        logger.info("All resources cleaned up")

    def _update_status_file(self, status: str):
        if not self.state_file_path:
            return

        try:
            import json
            from datetime import datetime, timezone

            state_path = Path(self.state_file_path)
            if state_path.exists():
                with open(state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
                state["status"] = status
                state["updated_at"] = datetime.now(timezone.utc).isoformat()
                with open(state_path, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2)
                logger.info("State file updated: status=%s", status)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.warning("Failed to update status file: %s", e)

    def _remove_state_file(self):
        if not self.state_file_path:
            return

        try:
            state_path = Path(self.state_file_path)
            if state_path.exists():
                state_path.unlink()
                logger.info("State file removed: %s", state_path)
        except (OSError, PermissionError) as e:
            logger.warning("Failed to remove state file: %s", e)

    def _handle_atexit(self):
        if not self._shutting_down:
            logger.info("Application exiting, performing cleanup...")
            self._cleanup_all_resources()
