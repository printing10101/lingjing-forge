"""任务执行引擎与单例（从 execution 拆出）。"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import traceback
from typing import Any

from app.dependencies import get_budget_manager
from app.dependencies import get_cost_tracker
from app.plugins.skill_loader import get_skill_loader
from app.workspace.workspace import get_resolver

from app.tasks._execution_models import ExecutionResult, ExecutionSession, ExecutionStatus, StructuredLogger
from app.tasks._session_manager import SessionManager
from app.tasks._task_executor import TaskExecutor

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """
    执行引擎 - 整合所有模块

    实现原子性操作流程：
    任务检出 → 预算检查 → 环境准备 → 技能加载 → 任务执行 → 结果记录 → 资源释放
    """

    def __init__(self):
        self.structured_logger = StructuredLogger()
        self.session_manager = SessionManager()
        self.executor = TaskExecutor()

        self._retry_delays = [600, 1800, 3600]

    async def execute_task(self, task: Any) -> ExecutionResult:
        """
        执行任务的完整原子性流程

        Args:
            task: ScheduledTask实例

        Returns:
            执行结果
        """
        from app.dependencies import get_scheduler
        from app.heartbeat.heartbeat import ScheduleStatus

        scheduler = get_scheduler()
        budget_manager = get_budget_manager()
        workspace_resolver = get_resolver()
        skill_loader = get_skill_loader()

        task_id = task.task_id
        agent_id = task.agent_id
        task_type = task.task_type

        session_id = f"{task_id}_{int(time.time())}"
        session = ExecutionSession(
            session_id=session_id,
            task_id=task_id,
            status=ExecutionStatus.PENDING,
            started_at=time.time(),
            last_updated=time.time(),
            retry_count=task.retry_count,
            max_retries=task.max_retries,
        )

        try:
            self.structured_logger.log_execution_start(task_id, task_type, task.params)
            self.session_manager.create_session(session)

            self.session_manager.update_session(session_id, ExecutionStatus.PREPARING)

            budget_result = budget_manager.check_budget(agent_id)

            if not budget_result.passed:
                error_msg = f"Budget check failed: {'; '.join(budget_result.blocked_reasons)}"
                logger.error("[%s] %s", task_id, error_msg)

                self.structured_logger.log_error(task_id, RuntimeError(error_msg))
                self.session_manager.update_session(session_id, ExecutionStatus.FAILED)

                scheduler.wakeup_queue.update_task_status(task_id, ScheduleStatus.FAILED)
                scheduler.wakeup_queue.log_execution(task_id, "failed", error_message=error_msg)

                budget_manager.suspend_agent_tasks(agent_id, error_msg)

                return ExecutionResult(
                    task_id=task_id,
                    status=ExecutionStatus.FAILED,
                    start_time=time.time(),
                    end_time=time.time(),
                    duration_ms=0,
                    error_message=error_msg,
                )

            workspace = workspace_resolver.resolve(task_id, task_type, task.metadata)

            project_id = getattr(task, "project_id", None)
            skill_context = await skill_loader.inject_skills(
                task_type=task_type,
                project_id=project_id,
                agent_id=agent_id,
                available_context=set(workspace.to_dict().keys()),
            )
            if skill_context:
                task.params["_skill_context"] = skill_context
            skill_loader.inject_context(workspace.to_dict())

            self.session_manager.update_session(session_id, ExecutionStatus.RUNNING)

            result = await self.executor.execute(task_type, workspace, task.params)

            self._record_resource_usage(task_id, agent_id, result)

            self.structured_logger.log_execution_end(task_id, result.status, result.duration_ms)

            if result.status == ExecutionStatus.COMPLETED:
                scheduler.wakeup_queue.update_task_status(task_id, ScheduleStatus.COMPLETED, last_run=time.time())
                scheduler.wakeup_queue.log_execution(
                    task_id,
                    "completed",
                    duration_ms=result.duration_ms,
                    result_summary=json.dumps(result.result_data)[:500] if result.result_data else None,
                )
                self.session_manager.update_session(session_id, ExecutionStatus.COMPLETED)
            else:
                await self._handle_failure(task, session, result)

            self._cleanup_resources(task_id)

            return result

        except (RuntimeError, ValueError, TypeError, TimeoutError, OSError) as e:
            logger.error("Task execution failed %s: %s", task_id, e, exc_info=True)

            self.structured_logger.log_error(task_id, e)
            self.session_manager.update_session(session_id, ExecutionStatus.FAILED)

            scheduler.wakeup_queue.update_task_status(task_id, ScheduleStatus.FAILED)
            scheduler.wakeup_queue.log_execution(task_id, "failed", error_message=type(e).__name__)

            self._cleanup_resources(task_id)

            return ExecutionResult(
                task_id=task_id,
                status=ExecutionStatus.FAILED,
                start_time=time.time(),
                end_time=time.time(),
                duration_ms=0,
                error_message="任务执行失败: 内部错误，请联系管理员",
                error_traceback=traceback.format_exc(),
            )

    async def _handle_failure(self, task: Any, session: ExecutionSession, result: ExecutionResult) -> None:
        """处理任务失败，实现重试机制"""
        from app.dependencies import get_scheduler
        from app.heartbeat.heartbeat import ScheduleStatus

        scheduler = get_scheduler()

        if task.retry_count < task.max_retries:
            # H14 bug 修复：retry_count 超过 _retry_delays 长度时原代码抛 IndexError，
            # 导致任务直接失败而非降级到最后一个 delay。改用 min 钳位。
            retry_idx = min(task.retry_count, len(self._retry_delays) - 1)
            retry_delay = self._retry_delays[retry_idx]

            logger.info(
                "Task %s failed, scheduling retry %d/%d in %d seconds",
                task.task_id,
                task.retry_count + 1,
                task.max_retries,
                retry_delay,
            )

            task.retry_count += 1
            task.next_run = time.time() + retry_delay
            task.status = ScheduleStatus.PENDING

            scheduler.wakeup_queue.add_task(task)
            scheduler.wakeup_queue.log_execution(
                task.task_id,
                "retry_scheduled",
                error_message=result.error_message,
                result_summary=f"Retry {task.retry_count}/{task.max_retries} in {retry_delay}s",
            )

            self.session_manager.update_session(
                session.session_id,
                ExecutionStatus.PENDING,
                checkpoint_data={
                    "retry_count": task.retry_count,
                    "next_run": task.next_run,
                },
            )
        else:
            logger.error(
                "Task %s failed after %d retries, marking as permanently failed",
                task.task_id,
                task.max_retries,
            )

            scheduler.wakeup_queue.update_task_status(task.task_id, ScheduleStatus.FAILED)
            scheduler.wakeup_queue.log_execution(
                task.task_id,
                "permanently_failed",
                error_message=result.error_message,
                result_summary=f"Failed after {task.max_retries} retries",
            )

            self.session_manager.update_session(session.session_id, ExecutionStatus.FAILED)

    def _record_resource_usage(self, task_id: str, agent_id: str, result: ExecutionResult) -> None:
        """记录资源使用"""
        gpu_hours = result.duration_ms / (1000 * 3600) if result.duration_ms else 0

        if gpu_hours > 0:
            get_cost_tracker().record_gpu_usage(task_id, gpu_hours, agent_id)

        get_cost_tracker().record_memory_usage(task_id, result.resource_usage.get("memory_mb", 0), agent_id)

    def _cleanup_resources(self, task_id: str) -> None:
        """清理任务资源"""
        try:
            from app.workspace.workspace import get_resolver

            resolver = get_resolver()
            resolver.cleanup_workspace(task_id, keep_outputs=True)
            logger.info("Resources cleaned up for task %s", task_id)
        except OSError as e:
            logger.warning("Failed to cleanup resources for task %s: %s", task_id, e)

    async def recover_orphaned_tasks(self) -> int:
        """
        检测并恢复孤立任务

        Returns:
            恢复的任务数量
        """
        from app.dependencies import get_scheduler
        from app.heartbeat.heartbeat import ScheduleStatus

        scheduler = get_scheduler()

        orphaned_sessions = self.session_manager.get_orphaned_sessions(timeout_seconds=3600)

        recovered_count = 0

        for session in orphaned_sessions:
            if session.retry_count < session.max_retries:
                task = scheduler.wakeup_queue.get_task(session.task_id)

                if task:
                    task.retry_count = session.retry_count
                    # H14 bug 修复：同上，retry_count 越界保护
                    retry_idx = min(session.retry_count, len(self._retry_delays) - 1)
                    task.next_run = time.time() + self._retry_delays[retry_idx]
                    task.status = ScheduleStatus.PENDING

                    scheduler.wakeup_queue.add_task(task)

                    self.session_manager.update_session(session.session_id, ExecutionStatus.RECOVERING)

                    recovered_count += 1

                    logger.info(
                        "Recovered orphaned task %s (session=%s)",
                        session.task_id,
                        session.session_id,
                    )

        if recovered_count > 0:
            logger.info("Recovered %d orphaned tasks", recovered_count)

        return recovered_count

    async def start_recovery_loop(self, check_interval: float = 60.0, max_interval: float = 600.0) -> None:
        """启动孤立任务检测循环（含指数退避）

        Args:
            check_interval: 基础检测间隔（秒）
            max_interval: 最大检测间隔（秒）
        """
        backoff = check_interval
        while True:
            try:
                recovered = await self.recover_orphaned_tasks()
                if recovered > 0:
                    logger.info("Recovery loop: recovered %d tasks", recovered)
                    backoff = check_interval
                else:
                    backoff = min(backoff * 2, max_interval)
            except (RuntimeError, OSError) as e:
                logger.error("Recovery loop error: %s", e)
                backoff = min(backoff * 2, max_interval)

            await asyncio.sleep(backoff)

    def close(self) -> None:
        """关闭所有资源"""
        self.session_manager.close()


class _EngineHolder:
    """Thread-safe lazy holder for the :class:`ExecutionEngine` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: ExecutionEngine | None = None

    def get(self) -> ExecutionEngine:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                self._instance = ExecutionEngine()
            return self._instance

    def init(self) -> ExecutionEngine:
        """强制重新创建实例（用于启动时初始化的场景）。"""
        with self._lock:
            self._instance = ExecutionEngine()
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _EngineHolder()


def get_execution_engine() -> ExecutionEngine:
    """获取共享的 :class:`ExecutionEngine` 单例；首次访问时懒初始化。

    Returns:
        :class:`ExecutionEngine` 实例（应用生命周期内同一实例）。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_execution_engine)``。
        实现是线程安全的，行为与重构前完全一致。

        说明：原函数名 ``get_engine`` 与 ``app.database.connection.get_engine``
        （返回 AsyncEngine，语义不同）存在命名冲突，故重命名为
        ``get_execution_engine`` 以提升可读性。旧名仍以别名形式保留，向后兼容。
    """
    return _holder.get()


# 向后兼容别名（已弃用，新代码请使用 get_execution_engine）
get_engine = get_execution_engine


def init_execution_engine() -> ExecutionEngine:
    """初始化执行引擎，行为与重构前完全一致。"""
    return _holder.init()
