"""AsyncTaskManager → ITaskExecutor 契约适配器.

将现有 AsyncTaskManager（PostgreSQL+Redis+asyncio 实现）适配为
契约层 :class:`app.contracts.task.ITaskExecutor` 接口。

适配点（参见 ADR-005 第 3.2 节）：

1. **task_type 字符串 ↔ TaskType 枚举**
   契约层 ``submit(task_type: str, ...)`` 接收任意字符串；
   AsyncTaskManager.create_task 需要 :class:`TaskType` 枚举。
   适配器对未知 task_type 使用 ``TaskType.UNKNOWN`` 兜底，
   真正的语义校验由 :class:`TaskRegistry` 在执行时完成。

2. **progress 0..100 ↔ 0..1**
   AsyncTaskManager.progress_updater 接收 0..100 百分比；
   契约层 :class:`TaskProgress.progress` 为 0..1 浮点。
   适配器在转换 SSE 事件时进行 ``/100`` 缩放。

3. **TaskStatus 枚举差异**
   AsyncTaskManager 使用 ``task_manager.TaskStatus``（6 状态，无 SKIPPED）；
   契约层 :class:`app.contracts.task.TaskStatus` 含 SKIPPED。
   SKIPPED 仅在工作流编排器层使用，单任务执行器无需映射。

4. **subscribe 返回类型**
   AsyncTaskManager.subscribe 返回 ``asyncio.Queue``（SSE 字符串事件）；
   契约层 ``subscribe`` 返回 ``AsyncIterator[TaskProgress]``。
   适配器将 Queue 包装为异步生成器，逐条解析 SSE 事件并转换。

5. **executor 回调签名**
   AsyncTaskManager.execute_task 接收 ``executor(cancel_evt, progress_updater)``；
   契约层 :class:`TaskHandler.execute(ctx)` 接收 TaskContext 返回 TaskResult。
   适配器在 ``submit`` 中根据 task_type 从 :class:`TaskRegistry` 取得 handler，
   构造闭包 ``executor`` 内部调用 ``handler.execute(ctx)`` 并把
   TaskResult 的 outputs/metrics 回写。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from collections.abc import AsyncIterator

from app.contracts.task import (
    Artifact,
    ITaskExecutor,
    TaskContext,
    TaskPriority,
    TaskProgress,
    TaskResult,
    TaskStatus as ContractTaskStatus,
)
from app.tasks.task_manager import TaskStatus as InternalTaskStatus, TaskType
from app.tasks.task_system import AsyncTaskManager, TaskRecord
from app.tasks.registry import get_task_registry

logger = logging.getLogger(__name__)


# 内部辅助：状态/进度转换

_INTERNAL_TO_CONTRACT_STATUS: dict[InternalTaskStatus, ContractTaskStatus] = {
    InternalTaskStatus.PENDING: ContractTaskStatus.PENDING,
    InternalTaskStatus.QUEUED: ContractTaskStatus.QUEUED,
    InternalTaskStatus.RUNNING: ContractTaskStatus.RUNNING,
    InternalTaskStatus.COMPLETED: ContractTaskStatus.COMPLETED,
    InternalTaskStatus.FAILED: ContractTaskStatus.FAILED,
    InternalTaskStatus.CANCELLED: ContractTaskStatus.CANCELLED,
}


def _map_status(internal: InternalTaskStatus) -> ContractTaskStatus:
    """内部 TaskStatus → 契约层 TaskStatus。"""
    return _INTERNAL_TO_CONTRACT_STATUS.get(internal, ContractTaskStatus.PENDING)


def _coerce_task_type(task_type: str) -> TaskType:
    """字符串 task_type → TaskType 枚举（未知类型使用 UNKNOWN 兜底）。"""
    try:
        return TaskType(task_type)
    except ValueError:
        # 契约层允许任意字符串 task_type（插件可注册自定义任务），
        # AsyncTaskManager 仅作元数据存储用，UNKNOWN 不影响执行。
        return TaskType.UNKNOWN


def _record_to_result(record: TaskRecord) -> TaskResult:
    """TaskRecord → TaskResult 契约转换。

    AsyncTaskManager 的 result 字段为 ``Dict[str, Any]``，
    约定形如 ``{"outputs": {...}, "metrics": {...}}``（由适配器写入）。
    老任务可能没有此结构，做兼容处理。
    """
    outputs: dict[str, Artifact] = {}
    metrics: dict[str, float] = {}
    error: str | None = record.error
    error_code: str | None = None

    if isinstance(record.result, dict):
        raw_outputs = record.result.get("outputs") or {}
        if isinstance(raw_outputs, dict):
            for name, art in raw_outputs.items():
                try:
                    if isinstance(art, Artifact):
                        outputs[name] = art
                    elif isinstance(art, dict):
                        outputs[name] = Artifact(**art)
                except (TypeError, ValueError) as e:
                    logger.warning(
                        "TaskRecord %s 输出 %s 反序列化 Artifact 失败: %s",
                        record.job_id,
                        name,
                        e,
                    )
        raw_metrics = record.result.get("metrics") or {}
        if isinstance(raw_metrics, dict):
            for k, v in raw_metrics.items():
                try:
                    metrics[k] = float(v)
                except (TypeError, ValueError):
                    logger.debug("忽略非数值 metric %s=%r", k, v)
        error_code = record.result.get("error_code") if isinstance(record.result, dict) else None

    return TaskResult(
        status=_map_status(record.status),
        outputs=outputs,
        metrics=metrics,
        error=error,
        error_code=error_code,
    )


def _parse_sse_event(raw: str) -> tuple[str | None, dict | None]:
    """解析 SSE 文本帧为 (event_type, data_dict)。

    AsyncTaskManager._broadcast_event 输出形如::

        event: progress\\n
        data: {"job_id": "...", "percent": 50.0}\\n\\n

    非法格式返回 (None, None)，由调用方跳过。
    """
    event_type: str | None = None
    data: dict | None = None
    for line in raw.splitlines():
        if not line:
            continue
        if line.startswith("event:"):
            event_type = line[len("event:") :].strip()
        elif line.startswith("data:"):
            payload = line[len("data:") :].strip()
            try:
                parsed = json.loads(payload)
                if isinstance(parsed, dict):
                    data = parsed
                else:
                    data = {"_raw": parsed}
            except (json.JSONDecodeError, ValueError):
                data = {"_raw": payload}
    return event_type, data


# 内部 SSE event_type (契约 TaskStatus, progress_hint)
# progress_hint 为 None 表示保留上一进度（不更新）
_EVENT_TO_STATUS: dict[str, tuple[ContractTaskStatus, float | None]] = {
    "queued": (ContractTaskStatus.QUEUED, 0.0),
    "started": (ContractTaskStatus.RUNNING, 0.0),
    "progress": (ContractTaskStatus.RUNNING, None),  # percent 由 data 提供
    "complete": (ContractTaskStatus.COMPLETED, 1.0),
    "cancelled": (ContractTaskStatus.CANCELLED, None),
    "failed": (ContractTaskStatus.FAILED, None),
}


# ITaskExecutor 实现


class AsyncTaskManagerAdapter(ITaskExecutor):
    """将 AsyncTaskManager 适配为 ITaskExecutor 契约。

    单例：通过 :func:`get_task_executor` 获取。整个应用共享一个适配器实例，
    内部直接复用 AsyncTaskManager 单例与全局 TaskRegistry。
    """

    def __init__(
        self,
        manager: AsyncTaskManager | None = None,
        registry: Any | None = None,
    ) -> None:
        # 显式注入仅用于测试；生产环境通过单例获取
        self._manager: AsyncTaskManager = manager or AsyncTaskManager()
        # 延迟解析 registry：避免在 __init__ 阶段触发单例锁竞争
        self._registry = registry

    @property
    def registry(self):
        if self._registry is None:
            self._registry = get_task_registry()
        return self._registry

    async def submit(
        self,
        task_type: str,
        params: dict[str, Any],
        *,
        owner_id: str | None = None,
        idempotency_key: str | None = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        timeout_seconds: int = 3600,
    ) -> str:
        """提交任务并立即调度执行。

        实现细节：
        1. 通过 ``TaskRegistry.get(task_type)`` 获取已注册的 TaskHandler；
           未注册时直接抛出 KeyError（契约要求 task_type 必须先注册）。
        2. 调用 ``AsyncTaskManager.create_task`` 创建任务记录（持久化到 DB）。
        3. 构造 ``executor`` 闭包：
           - 创建 ``TaskContext``（含 job_id、params、deadline）
           - 调用 ``handler.execute(ctx)`` 获取 ``TaskResult``
           - 通过 ``progress_updater`` 上报进度（0→100 启动 / 100 完成）
           - 将 TaskResult 的 outputs/metrics 序列化到 record.result
        4. 通过 ``asyncio.create_task`` 异步触发 ``execute_task``，不阻塞 submit。
        """
        # 1. 校验 task_type 已注册
        try:
            handler = self.registry.get(task_type)
        except KeyError:
            logger.warning("ITaskExecutor.submit 失败：task_type=%s 未注册", task_type)
            raise

        # 2. 创建任务记录
        record = await self._manager.create_task(
            task_type=_coerce_task_type(task_type),
            params=params,
            owner_id=owner_id,
            idempotency_key=idempotency_key,
        )
        job_id = record.job_id

        # 3. 构造 executor 闭包
        deadline_ts = time.time() + timeout_seconds if timeout_seconds > 0 else None

        async def _executor(cancel_evt: asyncio.Event, progress_updater) -> dict[str, Any]:
            """AsyncTaskManager.execute_task 期望的 executor 签名。

            Returns:
                序列化后的 TaskResult 字典，AsyncTaskManager 会写入 record.result。
            """
            # 上报启动进度（0%）
            try:
                await progress_updater(0.0, "任务启动")
            except Exception as e:
                logger.debug("progress_updater(0) 失败（忽略）: %s", e)

            ctx = TaskContext(
                job_id=job_id,
                workflow_run_id=None,
                config=params,
                retry_count=0,
                deadline_ts=deadline_ts,
            )

            # 监听取消信号：若 cancel_evt 被设置，则抛出 CancelledError
            # 让 AsyncTaskManager 进入 CANCELLED 分支
            if cancel_evt.is_set():
                raise asyncio.CancelledError("任务在启动前已被取消")

            result = await handler.execute(ctx)

            # 上报完成进度（100%）
            if cancel_evt.is_set():
                raise asyncio.CancelledError("任务在完成前被取消")
            try:
                await progress_updater(100.0, "任务完成")
            except Exception as e:
                logger.debug("progress_updater(100) 失败（忽略）: %s", e)

            # 序列化 TaskResult 为 dict（用于 record.result 持久化）
            return {
                "outputs": {
                    name: art.__dict__ if isinstance(art, Artifact) else art for name, art in result.outputs.items()
                },
                "metrics": dict(result.metrics),
                "error_code": result.error_code,
            }

        # 4. 异步触发执行（不阻塞 submit）
        # 注意：priority 目前 AsyncTaskManager 未消费，仅记录在 params 中便于后续扩展
        if priority != TaskPriority.NORMAL:
            params.setdefault("_priority", int(priority))

        # 异步调度，异常仅记录日志（AsyncTaskManager 内部已处理重试/超时）
        async def _schedule():
            try:
                await self._manager.execute_task(job_id, _executor)
            except asyncio.CancelledError:
                logger.info("Task %s 被取消", job_id)
            except Exception as e:
                logger.error("Task %s execute_task 异常: %s", job_id, e, exc_info=True)

        # [H8] 保存任务引用到 set，防止 asyncio.create_task 弱引用被 GC 回收
        # 导致任务在执行中被取消且无异常日志。
        sched_task = asyncio.create_task(_schedule())
        _pending_schedule_tasks.add(sched_task)
        sched_task.add_done_callback(_pending_schedule_tasks.discard)
        logger.info("ITaskExecutor.submit 已调度 task_type=%s job_id=%s", task_type, job_id)
        return job_id

    async def get(self, job_id: str) -> TaskResult:
        """获取任务结果或当前快照。"""
        record = await self._manager.get_task(job_id)
        if record is None:
            # 契约未明确要求抛出何种异常，这里返回 FAILED 状态的空结果
            return TaskResult(
                status=ContractTaskStatus.FAILED,
                error=f"任务不存在: {job_id}",
                error_code="NOT_FOUND",
            )
        return _record_to_result(record)

    async def cancel(self, job_id: str) -> bool:
        """发送取消信号。返回 True 表示已成功发出（不保证立即终止）。"""
        return await self._manager.cancel_task(job_id)

    async def subscribe(self, job_id: str) -> AsyncIterator[TaskProgress]:
        """订阅任务进度事件流。

        将 AsyncTaskManager.subscribe 返回的 ``asyncio.Queue``（SSE 字符串）
        包装为 ``AsyncIterator[TaskProgress]``，逐条解析并转换。

        终止条件：遇到 ``complete`` / ``cancelled`` / ``failed`` 事件后关闭迭代器。
        """
        queue: asyncio.Queue = self._manager.subscribe(job_id)
        last_progress: float = 0.0

        try:
            while True:
                # asyncio.Queue.get 默认无限等待；若任务已终结且 broadcast 完成，
                # 调用方需通过 cancel 或超时退出。这里增加 30s 心跳超时避免永久挂起。
                try:
                    raw = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    # 心跳：发一个保持当前进度的 RUNNING 事件（仅当任务仍在运行）
                    yield TaskProgress(
                        job_id=job_id,
                        status=ContractTaskStatus.RUNNING,
                        progress=last_progress,
                        message="heartbeat",
                        timestamp=time.time(),
                    )
                    continue

                if not isinstance(raw, str):
                    continue

                event_type, data = _parse_sse_event(raw)
                if event_type is None or event_type not in _EVENT_TO_STATUS:
                    continue

                status, progress_hint = _EVENT_TO_STATUS[event_type]
                # progress 事件：从 data["percent"] 提取并 /100
                if event_type == "progress" and data:
                    pct = data.get("percent", 0.0)
                    try:
                        last_progress = max(0.0, min(1.0, float(pct) / 100.0))
                    except (TypeError, ValueError):
                        pass
                elif progress_hint is not None:
                    last_progress = progress_hint

                message = data.get("message") if data else None
                # complete 事件可能携带 error_code（失败时）
                if event_type == "failed" and data:
                    message = data.get("error") or message

                yield TaskProgress(
                    job_id=job_id,
                    status=status,
                    progress=last_progress,
                    message=message,
                    timestamp=time.time(),
                )

                # 终态事件：关闭迭代器
                if event_type in {"complete", "cancelled", "failed"}:
                    return
        except asyncio.CancelledError:
            # 订阅者主动取消迭代：清理订阅
            self._manager.unsubscribe(job_id, queue)
            raise
        finally:
            self._manager.unsubscribe(job_id, queue)


# 单例访问

_adapter: AsyncTaskManagerAdapter | None = None
# [H2] asyncio.Lock 懒初始化：模块级创建会绑定到导入时的事件循环，
# 在多事件循环场景下抛 RuntimeError "bound to a different event loop"。
_adapter_lock: asyncio.Lock | None = None

# [H8] 调度任务引用集合：防止 create_task 弱引用被 GC 回收
_pending_schedule_tasks: set = set()


def _get_adapter_lock() -> asyncio.Lock:
    """懒初始化适配器单例锁，绑定到首次调用的事件循环。"""
    global _adapter_lock
    if _adapter_lock is None:
        _adapter_lock = asyncio.Lock()
    return _adapter_lock


async def get_task_executor() -> AsyncTaskManagerAdapter:
    """获取全局 ITaskExecutor 单例。

    异步函数：单例锁使用 asyncio.Lock 避免事件循环阻塞。
    """
    global _adapter
    if _adapter is None:
        async with _get_adapter_lock():
            if _adapter is None:
                _adapter = AsyncTaskManagerAdapter()
    return _adapter


def reset_task_executor() -> None:
    """重置全局单例（仅用于测试）。

    同步函数：测试中通常在 setup/teardown 调用，无需加锁。
    """
    global _adapter
    _adapter = None
