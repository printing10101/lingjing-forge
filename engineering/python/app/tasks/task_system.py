"""
Persistent Async Task System

PostgreSQL for task metadata + Redis for real-time progress + asyncio execution.
Supports task lifecycle management, SSE broadcasting, idempotency, and cancellation.
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from threading import Lock
from typing import Any
from collections.abc import Callable

from sqlalchemy import select

from app.tasks.task_manager import TaskStatus, TaskType
from app.database.models import TrainingTask
from app.database.connection import get_sessionmaker
from app.services.redis_client import (
    set_cancel_flag,
    clear_cancel_flag,
)

from app.tasks._task_events_mixin import _TaskEventsMixin
from app.tasks._task_execution_mixin import _TaskExecutionMixin
from app.tasks._task_persistence_mixin import _TaskPersistenceMixin
from app.tasks._task_recovery_mixin import _TaskRecoveryMixin
from app.tasks._task_stats_mixin import _TaskStatsMixin

# 类型/常量经本模块再导出（contract_adapter / 测试 / 外部导入方依赖），
# 故显式保留全部导入（F401 为有意再导出）。
from app.tasks._task_types import (  # noqa: F401
    DEFAULT_MAX_RETRIES,
    DEFAULT_TASK_TIMEOUT_SECONDS,
    RETRYABLE_EXCEPTIONS,
    TaskRecord,
    VALID_STATUS_TRANSITIONS,
)

logger = logging.getLogger(__name__)

# 任务系统默认配置（命名常量，便于统一调整与运维排查）
# 默认单任务最大执行时长（秒）：训练任务通常 5-30 分钟，1 小时作为兜底上限
# 默认最大重试次数：工业任务重试过多会放大副作用（如刀具磨损），3 次为安全上限


# 可重试的异常类型


class AsyncTaskManager(
    _TaskExecutionMixin, _TaskPersistenceMixin, _TaskRecoveryMixin, _TaskEventsMixin, _TaskStatsMixin
):
    """
    Persistent singleton async task manager.

    Storage Architecture:
        PostgreSQL: task metadata (status, params, result, timestamps)
        Redis: real-time progress (Hash: task:{id}:progress), cancel flags

    State Machine: PENDING -> QUEUED -> RUNNING -> COMPLETED/FAILED/CANCELLED
    """

    _instance = None
    _lock = Lock()
    # 宿主契约 / 动态属性（由 LNN 训练子路由挂载，mypy 需要显式声明）
    _training_queues: dict = {}
    _training_queues_lock: Any = None

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True

        self._tasks: dict[str, TaskRecord] = {}
        self._idempotency_map: dict[str, str] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}
        # [A-H15] 懒初始化 asyncio.Lock/Semaphore，避免在 __init__（模块导入时）
        # 绑定到错误的事件循环。所有使用处都在 async 上下文中，第一次调用时
        # get_running_loop() 才会真正绑定。
        self._task_lock: asyncio.Lock | None = None
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._cancel_hooks: dict[str, Callable] = {}

        self._max_concurrent = 3
        self._semaphore: asyncio.Semaphore | None = None
        self._started = False
        # P2-2 修复：shutdown 标志位，防止 shutdown 后再创建新任务
        self._shutdown = False

        # 任务超时和重试配置（命名常量提取，便于运维统一调整）
        self._task_timeout = DEFAULT_TASK_TIMEOUT_SECONDS
        self._max_retries = DEFAULT_MAX_RETRIES

    def _get_task_lock(self) -> asyncio.Lock:
        """[A-H15] 懒初始化 asyncio.Lock，绑定到当前运行的事件循环。"""
        if self._task_lock is None:
            self._task_lock = asyncio.Lock()
        return self._task_lock

    def _get_semaphore(self) -> asyncio.Semaphore:
        """[A-H15] 懒初始化 asyncio.Semaphore，绑定到当前运行的事件循环。"""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrent)
        return self._semaphore

    async def initialize(self, max_concurrent: int = 3):
        self._max_concurrent = max_concurrent
        # [A-H15] 在事件循环内重建 Semaphore，确保绑定到正确的循环
        self._semaphore = asyncio.Semaphore(max_concurrent)
        # 若 _task_lock 已被懒初始化到其他循环，也重建以对齐当前循环
        self._task_lock = asyncio.Lock()

        await self._recover_running_tasks()

        self._started = True
        logger.info("AsyncTaskManager initialized: max_concurrent=%d", max_concurrent)

    async def shutdown(self):
        """关闭任务管理器，取消所有正在运行的任务。

        .. note::
            P3-5 修复：原实现仅标记 ``_started = False``，未通知正在运行的
            任务退出，导致 shutdown 后异步任务仍可能在后台执行，造成资源
            泄漏与状态不一致。本修复通过设置所有 cancel event 通知任务退出，
            并清空订阅者队列避免 shutdown 后残留回调。

            P2-2 修复：补充 ``_shutdown`` 标志位防止 shutdown 后再创建新任务，
            并清理 ``_cancel_events`` / ``_cancel_hooks`` 字典与 ``_subscribers``
            保持一致，避免内存泄漏。
        """
        self._started = False
        self._shutdown = True

        # 通知所有正在运行的任务取消：set cancel event 让任务协程在下一个
        # await 点退出。任务协程应通过 ``is_cancelled(task_id)`` 检查并优雅退出。
        cancel_count = 0
        for task_id, event in self._cancel_events.items():
            if not event.is_set():
                event.set()
                cancel_count += 1

        # 清空所有任务相关字典，避免 shutdown 后残留引用导致内存泄漏
        # （订阅者通常是 SSE/WS 连接，连接断开后 Queue 不再被消费）
        self._subscribers.clear()
        self._cancel_events.clear()
        self._cancel_hooks.clear()

        logger.info(
            "AsyncTaskManager shut down: %d task(s) signalled to cancel, %d task(s) were running",
            cancel_count,
            len(self._tasks),
        )

    async def create_task(
        self,
        task_type: TaskType,
        params: dict[str, Any],
        owner_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> TaskRecord:
        # P2-2 修复：shutdown 后拒绝创建新任务，避免在关闭流程中产生
        # 无法被调度执行的新任务（semaphore 已可能失效、事件循环即将关闭）
        if self._shutdown:
            raise RuntimeError("AsyncTaskManager has been shut down; cannot create new tasks")
        async with self._get_task_lock():
            if idempotency_key and idempotency_key in self._idempotency_map:
                return self._tasks[self._idempotency_map[idempotency_key]]

            if idempotency_key:
                sessionmaker = get_sessionmaker()
                if sessionmaker:
                    try:
                        async with sessionmaker() as session:
                            result = await session.execute(
                                select(TrainingTask).where(TrainingTask.idempotency_key == idempotency_key)
                            )
                            existing_db = result.scalar_one_or_none()
                            if existing_db:
                                record = TaskRecord.from_db_model(existing_db)
                                self._tasks[record.job_id] = record
                                self._idempotency_map[idempotency_key] = record.job_id
                                return record
                    except (RuntimeError, OSError) as e:
                        logger.warning("Idempotency check failed: %s", e)

            job_id = f"{task_type.value}-{uuid.uuid4().hex[:12]}"

            record = TaskRecord(
                job_id=job_id,
                task_type=task_type,
                status=TaskStatus.PENDING,
                params=params,
                owner_id=owner_id,
                idempotency_key=idempotency_key,
            )

            self._tasks[job_id] = record
            self._cancel_events[job_id] = asyncio.Event()
            self._subscribers[job_id] = []

            if idempotency_key:
                self._idempotency_map[idempotency_key] = job_id

            record.status = TaskStatus.QUEUED
            await self._persist_task_to_db(record)

            await self._broadcast_event(
                job_id,
                "queued",
                {
                    "job_id": job_id,
                    "task_type": task_type.value,
                    "estimated_wait": self._estimate_wait(),
                    "queue_position": self._queue_size() + 1,
                },
            )

            logger.info("Task %s created and queued", job_id)
            return record

    async def cancel_task(self, job_id: str) -> bool:
        async with self._get_task_lock():
            if job_id not in self._tasks:
                return False
            record = self._tasks[job_id]
            if record.status in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                return False

            record.status = TaskStatus.CANCELLED
            record.completed_at = time.time()

        if job_id in self._cancel_events:
            self._cancel_events[job_id].set()

        await set_cancel_flag(job_id)

        if job_id in self._cancel_hooks:
            try:
                hook = self._cancel_hooks.pop(job_id)
                if callable(hook):
                    hook()
            except (RuntimeError, ValueError) as e:
                logger.warning("Cancel hook for task %s failed: %s", job_id, e)

        await self._persist_task_to_db(record)

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
        return True

    async def _cleanup_task(self, job_id: str):
        async with self._get_task_lock():
            self._subscribers.pop(job_id, None)
            self._cancel_events.pop(job_id, None)
            self._cancel_hooks.pop(job_id, None)

            record = self._tasks.get(job_id)
            if record and record.idempotency_key:
                self._idempotency_map.pop(record.idempotency_key, None)

            self._tasks.pop(job_id, None)
            await clear_cancel_flag(job_id)

        logger.debug("Task %s resources cleaned up", job_id)

    def register_cancel_hook(self, job_id: str, hook: Callable):
        self._cancel_hooks[job_id] = hook
