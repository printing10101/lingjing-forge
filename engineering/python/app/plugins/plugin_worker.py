from __future__ import annotations

import logging
import multiprocessing
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from collections.abc import Callable

logger = logging.getLogger(__name__)


class WorkerStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    CRASHED = "crashed"
    RESTARTING = "restarting"


@dataclass
class WorkerConfig:
    plugin_id: str
    plugin_path: str
    worker_port: int = 0
    max_restarts: int = 3
    health_check_interval: float = 30.0
    restart_delay: float = 5.0
    resource_limits: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)


@dataclass
class WorkerInfo:
    config: WorkerConfig
    status: WorkerStatus = WorkerStatus.STOPPED
    process: multiprocessing.Process | None = None
    pid: int | None = None
    port: int | None = None
    started_at: float | None = None
    restart_count: int = 0
    last_health_check: float | None = None
    last_error: str | None = None


class PluginWorkerManager:
    _instance: "PluginWorkerManager" | None = None

    def __init__(self):
        self._workers: dict[str, WorkerInfo] = {}
        self._health_check_callbacks: list[Callable] = []
        self._running = False

    @classmethod
    def get_instance(cls) -> "PluginWorkerManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        cls._instance = None

    def start_worker(self, config: WorkerConfig) -> WorkerInfo:
        if config.plugin_id in self._workers:
            existing = self._workers[config.plugin_id]
            if existing.status == WorkerStatus.RUNNING:
                raise ValueError(f"Worker for plugin '{config.plugin_id}' already running")

        port = config.worker_port or self._find_free_port()

        info = WorkerInfo(
            config=config,
            port=port,
            status=WorkerStatus.STARTING,
        )
        self._workers[config.plugin_id] = info

        process = multiprocessing.Process(
            target=self._run_worker,
            args=(config, port),
            name=f"plugin-worker-{config.plugin_id}",
            daemon=True,
        )

        process.start()

        info.process = process
        info.pid = process.pid
        info.started_at = time.time()
        info.status = WorkerStatus.RUNNING

        logger.info(f"Worker started for plugin '{config.plugin_id}' (PID: {process.pid}, Port: {port})")

        return info

    def stop_worker(self, plugin_id: str, timeout: float = 10.0) -> None:
        info = self._workers.get(plugin_id)
        if info is None:
            return

        info.status = WorkerStatus.STOPPING

        if info.process and info.process.is_alive():
            try:
                info.process.terminate()
                info.process.join(timeout=timeout)

                if info.process.is_alive():
                    info.process.kill()
                    info.process.join(timeout=2.0)
            except (OSError, RuntimeError, TimeoutError) as e:
                # 进程 terminate/kill 可能抛出未预期异常（OSError/PermissionError 等）
                logger.error(
                    f"Error stopping worker '{plugin_id}': {e}",
                    exc_info=True,
                )

        info.status = WorkerStatus.STOPPED
        logger.info("Worker stopped for plugin '%s'", plugin_id)

    def restart_worker(self, plugin_id: str) -> WorkerInfo:
        """重启插件 worker。

        .. note::
            仅同步上下文使用：本方法使用 ``time.sleep`` 等待重启延迟，
            不应在 async 上下文中直接调用。如需 async 支持，请用
            ``asyncio.to_thread`` 包装。
        """
        info = self._workers.get(plugin_id)
        if info is None:
            raise KeyError(f"Worker for plugin '{plugin_id}' not found")

        if info.restart_count >= info.config.max_restarts:
            info.status = WorkerStatus.CRASHED
            info.last_error = f"Max restarts ({info.config.max_restarts}) reached"
            raise RuntimeError(f"Cannot restart '{plugin_id}': max restarts exceeded")

        info.status = WorkerStatus.RESTARTING
        info.restart_count += 1

        logger.info(f"Restarting worker for plugin '{plugin_id}' (attempt {info.restart_count})")

        self.stop_worker(plugin_id)

        time.sleep(info.config.restart_delay)

        new_info = self.start_worker(info.config)
        new_info.restart_count = info.restart_count

        return new_info

    def health_check(self, plugin_id: str | None = None) -> dict[str, Any]:
        results: dict[str, Any] = {}

        plugin_ids = [plugin_id] if plugin_id else list(self._workers.keys())

        for pid in plugin_ids:
            info = self._workers.get(pid)
            if info is None:
                results[pid] = {"status": "not_found"}
                continue

            if info.process is None:
                results[pid] = {"status": "no_process"}
                continue

            is_alive = info.process.is_alive()
            info.last_health_check = time.time()

            if not is_alive and info.status == WorkerStatus.RUNNING:
                info.status = WorkerStatus.CRASHED
                info.last_error = "Process died unexpectedly"

                try:
                    self.restart_worker(pid)
                    results[pid] = {
                        "status": "restarted",
                        "restart_count": info.restart_count,
                    }
                except RuntimeError:
                    results[pid] = {"status": "crashed", "error": info.last_error}
            else:
                results[pid] = {
                    "status": "healthy" if is_alive else "unhealthy",
                    "pid": info.pid,
                    "uptime": time.time() - info.started_at if info.started_at else 0,
                    "restart_count": info.restart_count,
                }

        return results

    def get_worker_info(self, plugin_id: str) -> dict[str, Any] | None:
        info = self._workers.get(plugin_id)
        if info is None:
            return None

        return {
            "plugin_id": info.config.plugin_id,
            "status": info.status.value,
            "pid": info.pid,
            "port": info.port,
            "started_at": info.started_at,
            "restart_count": info.restart_count,
            "last_error": info.last_error,
            "uptime": time.time() - info.started_at if info.started_at else 0,
        }

    def list_workers(self) -> list[dict[str, Any]]:
        result = []
        for pid in self._workers:
            info = self.get_worker_info(pid)
            if info is not None:
                result.append(info)
        return result

    def stop_all_workers(self, timeout: float = 10.0) -> None:
        for plugin_id in list(self._workers.keys()):
            try:
                self.stop_worker(plugin_id, timeout)
            except (OSError, RuntimeError, TimeoutError) as e:
                # 批量停止时单个 worker 失败不应阻塞其他 worker
                logger.error(
                    f"Error stopping worker '{plugin_id}': {e}",
                    exc_info=True,
                )

        self._workers.clear()
        logger.info("All workers stopped")

    def _find_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("localhost", 0))
            s.listen(1)
            port = s.getsockname()[1]
        return port

    def _run_worker(self, config: WorkerConfig, port: int) -> None:
        logger.info(f"Worker process starting for plugin '{config.plugin_id}' on port {port}")

        try:
            env = os.environ.copy()
            env.update(config.environment)
            env["PLUGIN_ID"] = config.plugin_id
            env["PLUGIN_PORT"] = str(port)
            env["PLUGIN_PATH"] = config.plugin_path

            worker_script = Path(__file__).parent / "worker_process.py"

            if worker_script.exists():
                try:
                    result = subprocess.run(
                        [sys.executable, str(worker_script)],
                        env=env,
                        cwd=config.plugin_path,
                        timeout=300,  # 5分钟超时，防止僵尸进程
                        capture_output=True,
                        text=True,
                    )
                except subprocess.TimeoutExpired:
                    logger.error("插件执行超时（300s）: %s", config.plugin_path)
                    raise RuntimeError("插件执行超时（300s）")

                if result.returncode != 0:
                    logger.error("Worker process exited with code %s", result.returncode)
            else:
                self._run_worker_inline(config, port)

        except (subprocess.SubprocessError, OSError, RuntimeError, ValueError, TimeoutError) as e:
            # 兜底捕获：worker 进程启动涉及 subprocess + 环境变量 + 端口绑定
            # 异常族多源（OSError/ValueError 等），统一记录后抛出
            logger.error(
                f"Worker process failed for '{config.plugin_id}': {e}",
                exc_info=True,
            )
            raise

    def _run_worker_inline(self, config: WorkerConfig, port: int) -> None:
        """内联运行插件 worker（同步线程上下文）。

        .. note::
            仅同步上下文使用：本方法在独立线程中运行，使用 ``time.sleep``
            维持 worker 心跳循环，不应在 async 上下文中直接调用。
        """
        from app.plugins.plugin_system import PluginLoader, PluginRegistry, PluginMetadata

        registry = PluginRegistry.get_instance()
        loader = PluginLoader(registry)

        metadata = PluginMetadata(
            id=config.plugin_id,
            name=config.plugin_id,
            version="1.0.0",
            plugin_path=config.plugin_path,
        )

        instance = None
        stop_event = threading.Event()

        try:
            instance = loader.load_plugin(metadata)

            if hasattr(instance, "initialize"):
                instance.initialize({})

            # 修复 P1：原 while True 无退出条件，现通过 Event 支持优雅退出
            while not stop_event.is_set():
                time.sleep(1)

        except KeyboardInterrupt:
            # 用户主动中断（Ctrl+C）是 worker 正常退出信号，设置停止标志
            stop_event.set()
        finally:
            # 修复 P1：instance 可能未定义（load_plugin 抛异常时），需判空
            if instance is not None and hasattr(instance, "shutdown"):
                instance.shutdown()
