"""Dreaming 与 HeartbeatScheduler 的集成适配器。

异步定时反思任务（Asynchronous Dream Jobs）：
    - 在 Session 间隙（凌晨低负载时段）自动触发反思
    - 基于 HeartbeatScheduler + CronParser 实现定时调度
    - 任务合并（coalescing）防止并发反思
    - 失败重试（HeartbeatScheduler 内建 max_retries）

设计原则：
    - 默认 cron: "0 3 * * *"（每天 03:00 执行，凌晨低负载）
    - 反思任务独立 task_id，便于追踪与手动触发
    - 回调内调用 DreamingCLI().run()，复用 cli.py 的完整流程
    - 反思失败不阻塞 HeartbeatScheduler 主循环（异常被捕获并记录）

硬约束对齐：
    - 反思任务不直接执行生产操作，仅更新 Memory Store
    - 失败时不触发任何 CAM 验证跳过或 SUCCEEDED 解锁
    - 审计日志由 cli.py 触发的反思流程内部写入（audit_integration.py）

用法：
    # 注册定时反思任务
    adapter = DreamingSchedulerAdapter()
    adapter.register()

    # 手动触发一次反思（不等待 cron）
    adapter.trigger_now()

    # 查询上次执行结果
    history = adapter.get_execution_history()
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 默认 cron 表达式：每天 03:00 执行
# 选择 03:00 的原因：
# 1. 凌晨低负载时段，不影响白天 LNN 推理/CAM 验证
# 2. 跨日总结前一天的所有 Session（lookback_days=1 等价）
# 3. 与项目 nightly 实验调度不冲突（实验通常 22:00-02:00）
DEFAULT_DREAMING_CRON = "0 3 * * *"

# 反思任务 ID（固定值，便于追踪与手动触发）
DREAMING_TASK_ID = "dreaming_daily_reflection"

# 反思任务 agent_id
DREAMING_AGENT_ID = "dreaming_agent"

# 反思任务类型
DREAMING_TASK_TYPE = "dreaming_reflect"


class DreamingSchedulerAdapter:
    """Dreaming 定时反思任务的 HeartbeatScheduler 适配器。

    封装 ScheduledTask 的创建、注册、触发、查询、注销操作，
    将 DreamingCLI 接入 HeartbeatScheduler 的调度循环。

    Note:
        - register() 是幂等的：重复调用不会创建多个任务
        - trigger_now() 不影响 cron 调度，仅触发一次即时执行
        - unregister() 会删除任务，HeartbeatScheduler 不再调度
    """

    def __init__(
        self,
        scheduler: Any | None = None,
        cron_expression: str = DEFAULT_DREAMING_CRON,
        lookback_days: int = 1,
        max_sessions: int = 100,
        include_ar_02_pre_fix: bool = False,
        enable_llm: bool = True,
    ) -> None:
        """初始化调度适配器。

        Args:
            scheduler: HeartbeatScheduler 实例。None 表示使用 get_scheduler() 单例。
            cron_expression: cron 表达式（5 字段：分 时 日 月 星期）。
                默认 "0 3 * * *"（每天 03:00）。
            lookback_days: 反思回溯天数。默认 1（只看前一天）。
            max_sessions: 最大 Session 数（上限 100）。
            include_ar_02_pre_fix: 是否包含 AR-02 修复前数据。默认 False（排除）。
            enable_llm: 是否启用 LLM 反思。True 优先用 LLM，不可用时降级。
        """
        if scheduler is None:
            from app.dependencies import get_scheduler

            scheduler = get_scheduler()
        self._scheduler = scheduler
        self._cron = cron_expression
        self._lookback_days = lookback_days
        self._max_sessions = max_sessions
        self._include_ar_02 = include_ar_02_pre_fix
        self._enable_llm = enable_llm
        self._registered = False

    def register(self) -> str:
        """注册 Dreaming 定时反思任务到 HeartbeatScheduler。

        幂等操作：若任务已存在则更新参数，不重复创建。

        Returns:
            任务 ID（DREAMING_TASK_ID）。
        """
        from app.heartbeat.heartbeat import ScheduledTask, ScheduleStatus

        # 检查是否已存在
        existing = self._scheduler.wakeup_queue.get_task(DREAMING_TASK_ID)
        if existing is not None:
            logger.info(
                "Dreaming 任务已存在，更新参数：cron=%s, lookback=%d",
                self._cron,
                self._lookback_days,
            )
            # 删除旧任务后重新创建（HeartbeatScheduler 无 update API）
            self._scheduler.wakeup_queue.delete_task(DREAMING_TASK_ID)

        task = ScheduledTask(
            task_id=DREAMING_TASK_ID,
            agent_id=DREAMING_AGENT_ID,
            schedule=self._cron,
            task_type=DREAMING_TASK_TYPE,
            params={
                "lookback_days": self._lookback_days,
                "max_sessions": self._max_sessions,
                "include_ar_02_pre_fix": self._include_ar_02,
                "enable_llm": self._enable_llm,
            },
            status=ScheduleStatus.PENDING,
            max_retries=3,
            metadata={
                "description": "ADR-021 Dreaming 离线反思",
                "cron": self._cron,
                "adr": "ADR-021",
            },
        )

        # 设置回调（HeartbeatScheduler 通过 _on_task_trigger 触发）
        self._scheduler.set_task_trigger_callback(self._dreaming_callback)

        self._scheduler.schedule_task(task)
        self._registered = True
        logger.info(
            "Dreaming 定时反思任务已注册：task_id=%s, cron=%s",
            DREAMING_TASK_ID,
            self._cron,
        )
        return DREAMING_TASK_ID

    def unregister(self) -> bool:
        """注销 Dreaming 定时反思任务。

        Returns:
            True 若任务已删除，False 若任务不存在。
        """
        deleted = self._scheduler.wakeup_queue.delete_task(DREAMING_TASK_ID)
        if deleted:
            self._registered = False
            logger.info("Dreaming 定时反思任务已注销：task_id=%s", DREAMING_TASK_ID)
        return deleted

    def trigger_now(self) -> None:
        """立即触发一次反思（不等待 cron 调度）。

        适用于：
            - 首次部署后立即执行一次反思验证
            - 人工触发紧急反思（如发现批量 CAM 验证失败）
            - CI/CD 流水线中的反思验证
        """
        existing = self._scheduler.wakeup_queue.get_task(DREAMING_TASK_ID)
        if existing is None:
            logger.warning("Dreaming 任务未注册，先执行 register() 再 trigger_now()")
            self.register()

        self._scheduler.trigger_now(DREAMING_TASK_ID)
        logger.info("Dreaming 反思已触发立即执行：task_id=%s", DREAMING_TASK_ID)

    async def _dreaming_callback(self, task: Any) -> None:
        """HeartbeatScheduler 触发回调。

        被 HeartbeatScheduler._trigger_task 调用，接收 ScheduledTask。
        内部调用 DreamingCLI().run() 执行完整反思流程。

        异常处理：
            - 所有异常被捕获并记录到 execution_log
            - 不向 HeartbeatScheduler 抛出异常（防止阻塞主循环）
            - 失败时更新任务状态为 FAILED（由 HeartbeatScheduler 处理）

        Args:
            task: ScheduledTask 实例。
        """
        params = task.params or {}
        lookback = params.get("lookback_days", self._lookback_days)
        max_sessions = params.get("max_sessions", self._max_sessions)
        include_ar_02 = params.get("include_ar_02_pre_fix", self._include_ar_02)
        enable_llm = params.get("enable_llm", self._enable_llm)

        # 构建 CLI 参数
        cli_args = [
            "reflect",
            "--lookback-days",
            str(lookback),
            "--max-sessions",
            str(max_sessions),
        ]
        if include_ar_02:
            cli_args.append("--include-ar-02")
        if not enable_llm:
            cli_args.append("--no-llm")

        logger.info(
            "HeartbeatScheduler 触发 Dreaming 反思：task_id=%s, args=%s",
            task.task_id,
            cli_args,
        )

        try:
            from app.dreaming.cli import DreamingCLI

            cli = DreamingCLI()
            # [A-H5] DreamingCLI.run() 是同步长时操作（反思+LLM 调用），
            # 用 asyncio.to_thread 包装避免阻塞事件循环
            exit_code = await asyncio.to_thread(cli.run, cli_args)

            if exit_code != 0:
                logger.error("Dreaming 反思执行失败：exit_code=%d", exit_code)
                # 记录到 execution_log
                self._scheduler.wakeup_queue.log_execution(
                    task.task_id,
                    "failed",
                    error_message=f"DreamingCLI exit code {exit_code}",
                    result_summary=f"args={cli_args}",
                )
            else:
                logger.info("Dreaming 反思执行成功")
                self._scheduler.wakeup_queue.log_execution(
                    task.task_id,
                    "completed",
                    result_summary=f"args={cli_args}, exit_code=0",
                )
        except Exception as e:
            logger.error("Dreaming 反思回调异常：%s", e, exc_info=True)
            self._scheduler.wakeup_queue.log_execution(
                task.task_id,
                "failed",
                error_message=f"{type(e).__name__}: {e}",
                result_summary=f"args={cli_args}",
            )

    def get_execution_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """查询 Dreaming 反思任务的执行历史。

        Args:
            limit: 最多返回的记录数。

        Returns:
            执行历史记录列表（按时间倒序）。
        """
        return self._scheduler.wakeup_queue.get_task_history(DREAMING_TASK_ID, limit=limit)

    def get_task_info(self) -> dict[str, Any] | None:
        """查询 Dreaming 任务的当前状态。

        Returns:
            任务信息字典，若任务不存在则 None。
        """
        task = self._scheduler.wakeup_queue.get_task(DREAMING_TASK_ID)
        if task is None:
            return None
        return task.to_dict()

    @property
    def is_registered(self) -> bool:
        """任务是否已注册。"""
        return self._registered


def register_default_dreaming_task() -> str:
    """注册默认的 Dreaming 定时反思任务。

    便捷函数，使用默认参数注册到 HeartbeatScheduler 单例。
    适用于应用启动时自动注册。

    Returns:
        任务 ID。
    """
    adapter = DreamingSchedulerAdapter()
    return adapter.register()


def unregister_dreaming_task() -> bool:
    """注销 Dreaming 定时反思任务。

    便捷函数，适用于应用关闭或测试清理。

    Returns:
        True 若任务已删除。
    """
    adapter = DreamingSchedulerAdapter()
    return adapter.unregister()
