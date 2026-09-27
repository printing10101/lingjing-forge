"""演化循环的调度注册（自进化 M1）。

接入面：
- **workflow DAG**：task_type=``evolution_loop``（处理器见 workflow_adapter.py）
- **心跳 cron**：``register_evolution_cron_task`` 按 ``LNN_EVOLUTION_CRON``
  （默认每周一 03:00）注册定时任务，由心跳调度器到期触发
- **REST**：``app/api/v1/evolution.py`` 直接调引擎（人工触发/审核）

安全纪律：注册失败只告警不阻断启动；心跳路径的执行入口全捕获异常
（heartbeat._trigger_task 的异常过滤只认连接类错误，演化回调必须
自行兜底，否则会击穿心跳循环）。
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "EVOLUTION_TASK_TYPE",
    "EVOLUTION_CRON_TASK_ID",
    "EVOLUTION_LOOP_TASK_TYPE",
    "register_evolution_task_handler",
    "register_evolution_cron_task",
]

EVOLUTION_TASK_TYPE = "evolution_loop"
EVOLUTION_CRON_TASK_ID = "evolution_weekly_loop"
_EVOLUTION_AGENT_ID = "core:evolution"

#: 心跳定时任务的 task_type（heartbeat 侧以常量对齐，避免魔法字符串漂移）
EVOLUTION_LOOP_TASK_TYPE = "evolution_loop"


def latest_report() -> dict[str, Any]:
    """读取最近一份进化报告（无报告返回空摘要）。"""
    from app.evolution.loop import _report_dir

    try:
        reports = sorted(_report_dir().glob("evolution_*.json"))
        if not reports:
            return {"report": None, "report_path": ""}
        data = json.loads(reports[-1].read_text(encoding="utf-8"))
        return {"report": data, "report_path": str(reports[-1])}
    except (OSError, json.JSONDecodeError) as e:
        return {"report": None, "report_path": "", "error": str(e)}


def register_evolution_task_handler(handler: Any) -> bool:
    """把 evolution_loop 任务类型注册进全局任务注册表（幂等）。

    handler 由调用方传入（workflow_adapter.EvolutionLoopHandler 实例），
    本模块不 import 处理器类，避免注册失败影响心跳注册路径。

    Returns:
        是否注册成功（重复注册视为成功——注册表语义为覆盖+告警）。
    """
    try:
        from app.tasks.registry import get_task_registry

        get_task_registry().register(handler, plugin_id=_EVOLUTION_AGENT_ID)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("evolution_loop 任务类型注册失败（workflow 模板将不可用）: %s", e)
        return False


def register_evolution_cron_task() -> bool:
    """向心跳调度器注册演化周期任务（env ``LNN_EVOLUTION_CRON`` 控制）。

    仅在心跳调度器已启动（``LNN_HEARTBEAT_ENABLED``）时调用。
    cron 默认 ``0 3 * * 1``（每周一 03:00）。任务参数走完整循环
    （提案制：只提案+报告，发布仍需人工 promote）。

    幂等性：WakeupQueue.add_task 为替换式写入，重复注册无害。

    Returns:
        是否注册成功（失败返回 False 不抛出）。
    """
    try:
        cron_expr = os.getenv("LNN_EVOLUTION_CRON", "0 3 * * 1").strip()
        from app.heartbeat.heartbeat import ScheduledTask, ScheduleStatus, get_scheduler

        get_scheduler().schedule_task(
            ScheduledTask(
                task_id=EVOLUTION_CRON_TASK_ID,
                agent_id=_EVOLUTION_AGENT_ID,
                schedule=cron_expr,
                task_type=EVOLUTION_LOOP_TASK_TYPE,
                params={"action": "run_loop"},
                status=ScheduleStatus.PENDING,
                metadata={"origin": "evolution_startup", "created_at_epoch": time.time()},
            )
        )
        logger.info("演化周期任务已注册: cron=%s task_id=%s", cron_expr, EVOLUTION_CRON_TASK_ID)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("演化周期任务注册失败（不影响启动）: %s", e)
        return False


async def heartbeat_trigger_callback(task: Any) -> None:
    """心跳触发分发器：演化任务走演化执行，其余回落 TaskExecutionEngine。

    用作 ``HeartbeatScheduler.set_task_trigger_callback`` 的回调。心跳
    循环的异常过滤只认连接类错误（见 heartbeat._trigger_task），本回调
    必须自行全捕获并回写任务状态，否则任务会永远停在 RUNNING。
    """
    from app.heartbeat.heartbeat import ScheduleStatus

    task_id = str(getattr(task, "task_id", "") or "unknown")
    task_type = str(getattr(task, "task_type", "") or "")
    params = getattr(task, "params", None)
    queue = get_scheduler().wakeup_queue

    if task_type != EVOLUTION_LOOP_TASK_TYPE:
        # 非演化任务：交给既有执行引擎（检出→预算→技能→执行→状态回写）
        from app.tasks._engine import ExecutionEngine

        await ExecutionEngine().execute_task(task)
        return

    try:
        from types import SimpleNamespace

        from app.evolution.workflow_adapter import run_evolution_action

        ctx = SimpleNamespace(config=dict(params) if isinstance(params, dict) else {})
        result = await run_evolution_action(ctx)
        completed = getattr(getattr(result, "status", None), "value", "") == "completed"
        queue.update_task_status(
            task_id,
            ScheduleStatus.COMPLETED if completed else ScheduleStatus.FAILED,
            last_run=time.time(),
        )
        queue.log_execution(
            task_id,
            "completed" if completed else "failed",
            error_message=str(getattr(result, "error", "") or "") or None,
        )
    except Exception as e:  # noqa: BLE001 - 演化失败不允许击穿心跳循环
        logger.error("演化定时任务执行失败 task_id=%s: %s", task_id, e, exc_info=True)
        try:
            queue.update_task_status(task_id, ScheduleStatus.FAILED)
            queue.log_execution(task_id, "failed", error_message=type(e).__name__)
        except Exception:  # noqa: BLE001
            logger.error("演化定时任务失败状态回写失败 task_id=%s", task_id)


def get_scheduler() -> Any:
    """心跳调度器单例（延迟导入，便于测试桩替换）。"""
    from app.heartbeat.heartbeat import get_scheduler as _get

    return _get()
