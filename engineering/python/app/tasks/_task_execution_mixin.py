"""执行方法组：execute_task 主执行器 + 进度/队列/错误建议辅助。"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any
from collections.abc import Callable


from app.core.safe_errors import safe_error_message
from app.services.redis_client import save_task_progress
from app.tasks._task_types import (
    RETRYABLE_EXCEPTIONS,
)
from app.tasks.task_manager import TaskStatus


import logging

logger = logging.getLogger(__name__)


class _TaskExecutionMixin:
    # 宿主契约：由主类 AsyncTaskManager / 兄弟 mixin 提供
    _tasks: dict[str, Any]
    _cancel_events: dict[str, asyncio.Event]
    _max_concurrent: int
    _max_retries: int
    _task_timeout: float
    _get_semaphore: Callable[..., Any]
    _get_task_lock: Callable[..., Any]
    _persist_task_to_db: Callable[..., Any]
    _broadcast_event: Callable[..., Any]
    _cleanup_task: Callable[..., Any]

    async def execute_task(self, job_id: str, executor: Callable):
        async with self._get_semaphore():
            async with self._get_task_lock():
                if job_id not in self._tasks:
                    return
                record = self._tasks[job_id]
                if record.status == TaskStatus.CANCELLED:
                    return
                record.status = TaskStatus.RUNNING
                record.started_at = time.time()

            await self._persist_task_to_db(record)

            await self._broadcast_event(
                job_id,
                "started",
                {
                    "job_id": job_id,
                    "started_at": datetime.fromtimestamp(record.started_at).isoformat(),
                    "resources": {"max_concurrent": self._max_concurrent},
                },
            )

            retry_count = 0
            for attempt in range(self._max_retries + 1):
                try:
                    timeout = self._task_timeout
                    cancel_evt = self._cancel_events.get(job_id)

                    # 执行前检查：若执行期间已被 cancel_task 标记取消，则不再启动 executor，
                    # 保持 CANCELLED 状态并退出（test_execute_task_cancellation 期望确定性取消）。
                    if cancel_evt is not None and cancel_evt.is_set():
                        async with self._get_task_lock():
                            record = self._tasks[job_id]
                            record.status = TaskStatus.CANCELLED
                            record.completed_at = time.time()
                        await self._persist_task_to_db(record)
                        await save_task_progress(
                            job_id,
                            {"progress": record.progress, "status": "cancelled"},
                        )
                        await self._cleanup_task(job_id)
                        return

                    # 使用 wait_for 添加超时控制
                    try:
                        result = await asyncio.wait_for(
                            executor(cancel_evt, self._create_progress_updater(job_id)),
                            timeout=timeout,
                        )
                    except asyncio.TimeoutError:
                        raise TimeoutError(f"Task {job_id} exceeded timeout of {timeout}s")

                    # 执行期间若被取消（executor 返回后 cancel_task 已标记 CANCELLED），
                    # 保持取消状态，不覆盖为 COMPLETED
                    if cancel_evt is not None and cancel_evt.is_set():
                        async with self._get_task_lock():
                            record = self._tasks[job_id]
                            record.status = TaskStatus.CANCELLED
                            record.completed_at = time.time()
                        await self._persist_task_to_db(record)
                        await save_task_progress(
                            job_id,
                            {"progress": record.progress, "status": "cancelled"},
                        )
                        await self._cleanup_task(job_id)
                        return

                    # 成功执行，跳出重试循环
                    async with self._get_task_lock():
                        record = self._tasks[job_id]
                        record.status = TaskStatus.COMPLETED
                        record.progress = 100.0
                        record.result = result
                        record.completed_at = time.time()
                        metrics = result.get("metrics") if result else None
                        if retry_count > 0:
                            if metrics is None:
                                metrics = {}
                            metrics["retry_count"] = retry_count
                        record.metrics = metrics

                    await self._persist_task_to_db(record)
                    await save_task_progress(
                        job_id,
                        {"progress": 100.0, "status": "completed", "message": "Done"},
                    )

                    await self._broadcast_event(
                        job_id,
                        "complete",
                        {
                            "job_id": job_id,
                            "result": result,
                            "completed_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )

                    await self._cleanup_task(job_id)
                    return

                except asyncio.CancelledError:
                    async with self._get_task_lock():
                        record = self._tasks[job_id]
                        record.status = TaskStatus.CANCELLED
                        record.completed_at = time.time()

                    await self._persist_task_to_db(record)
                    await save_task_progress(
                        job_id,
                        {"progress": record.progress, "status": "cancelled"},
                    )

                    await self._broadcast_event(
                        job_id,
                        "cancelled",
                        {
                            "job_id": job_id,
                            "cancelled_at": datetime.now(timezone.utc).isoformat(),
                            "progress": record.progress,
                        },
                    )

                    await self._cleanup_task(job_id)
                    return

                except RETRYABLE_EXCEPTIONS as e:
                    retry_count += 1
                    if attempt < self._max_retries:
                        logger.warning(
                            "Task %s failed (attempt %d/%d), retrying: %s",
                            job_id,
                            attempt + 1,
                            self._max_retries,
                            e,
                        )
                        # 重试时重置进度
                        async with self._get_task_lock():
                            if job_id in self._tasks:
                                self._tasks[job_id].progress = 0.0
                        await save_task_progress(
                            job_id,
                            {
                                "progress": 0.0,
                                "status": "running",
                                "message": f"Retrying (attempt {attempt + 1}/{self._max_retries})",
                            },
                        )
                        await self._broadcast_event(
                            job_id,
                            "progress",
                            {
                                "job_id": job_id,
                                "percent": 0.0,
                                "message": f"Retrying (attempt {attempt + 1}/{self._max_retries})",
                                "retry_count": retry_count,
                            },
                        )
                        # 指数退避
                        await asyncio.sleep(2**attempt)
                        continue
                    # 最后一次重试失败（含超时）：标记 FAILED 并清理，防止任务残留
                    # （测试 test_timeout_marks_failed / test_retryable_exhausts_retries 期望
                    # 重试耗尽后任务从 _tasks 移除，get_task 返回 None）
                    safe = safe_error_message(e, context=f"task_system.run_task[{job_id}]")
                    async with self._get_task_lock():
                        if job_id in self._tasks:
                            record = self._tasks[job_id]
                            record.status = TaskStatus.FAILED
                            record.error = safe["message"]
                            record.completed_at = time.time()
                            if retry_count > 0:
                                if record.metrics is None:
                                    record.metrics = {}
                                record.metrics["retry_count"] = retry_count
                    if job_id in self._tasks:
                        await self._persist_task_to_db(record)
                        await save_task_progress(
                            job_id,
                            {
                                "progress": record.progress,
                                "status": "failed",
                                "error": safe["message"],
                                "error_id": safe.get("error_id"),
                            },
                        )
                        await self._broadcast_event(
                            job_id,
                            "failed",
                            {
                                "job_id": job_id,
                                "error": safe["message"],
                                "error_id": safe.get("error_id"),
                                "failed_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                        await self._cleanup_task(job_id)
                    return

                except (RuntimeError, ValueError, TypeError) as e:
                    # 不可重试的异常，直接失败
                    safe = safe_error_message(e, context=f"task_system.run_task[{job_id}]")
                    async with self._get_task_lock():
                        record = self._tasks[job_id]
                        record.status = TaskStatus.FAILED
                        record.error = safe["message"]
                        record.completed_at = time.time()
                        if retry_count > 0:
                            if record.metrics is None:
                                record.metrics = {}
                            record.metrics["retry_count"] = retry_count

                    await self._persist_task_to_db(record)
                    await save_task_progress(
                        job_id,
                        {
                            "progress": record.progress,
                            "status": "failed",
                            "error": safe["message"],
                            "error_id": safe.get("error_id"),
                        },
                    )

                    await self._broadcast_event(
                        job_id,
                        "failed",
                        {
                            "job_id": job_id,
                            "error": safe["message"],
                            "error_id": safe.get("error_id"),
                            "suggestion": self._get_error_suggestion(e),
                            "failed_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )

                    await self._cleanup_task(job_id)
                    return

            # 所有重试耗尽后仍然失败（RETRYABLE_EXCEPTIONS 最后一次 raise 后不会到这里，
            # 但为安全起见保留此兜底）
            logger.error("Task %s exhausted all %d retries", job_id, self._max_retries)

    def _create_progress_updater(self, job_id: str) -> Callable:
        async def update_progress(percent: float, message: str = "", metrics: dict | None = None):
            async with self._get_task_lock():
                if job_id in self._tasks:
                    self._tasks[job_id].progress = percent
                    if metrics:
                        self._tasks[job_id].metrics = metrics

            await save_task_progress(
                job_id,
                {
                    "progress": round(percent, 1),
                    "message": message,
                    "status": "running",
                    "metrics": json.dumps(metrics) if metrics else "{}",
                },
            )

            await self._broadcast_event(
                job_id,
                "progress",
                {
                    "job_id": job_id,
                    "percent": round(percent, 1),
                    "message": message,
                    "metrics": metrics or {},
                },
            )

        return update_progress

    def _estimate_wait(self) -> float:
        queued_count = sum(1 for t in self._tasks.values() if t.status == TaskStatus.QUEUED)
        return queued_count * 60.0

    def _queue_size(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.QUEUED)

    def _get_error_suggestion(self, error: Exception) -> str:
        err_msg = str(error).lower()
        if "memory" in err_msg:
            return "减小 batch_size 或使用 CPU 模式"
        if "cuda" in err_msg:
            return "检查GPU驱动或切换到CPU模式"
        if "file" in err_msg or "path" in err_msg:
            return "检查文件路径是否正确"
        return "检查输入参数后重试"
