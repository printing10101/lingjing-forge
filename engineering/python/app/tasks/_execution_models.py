"""任务执行模型与结构化日志（从 execution.py 拆分，D5）。

只包含数据模型与日志工具；执行逻辑见 execution.ExecutionEngine。
"""

from __future__ import annotations

import json
import logging
import traceback
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.utils.utils import get_output_dir

logger = logging.getLogger(__name__)


class ExecutionStatus(str, Enum):
    """执行状态 - 用于详细执行流程控制"""

    PENDING = "pending"
    PREPARING = "preparing"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERING = "recovering"
    CANCELLED = "cancelled"


class TaskCategory(str, Enum):
    """任务类别"""

    INFERENCE = "inference"
    TRAINING = "training"
    ANALYSIS = "analysis"


@dataclass
class ExecutionResult:
    """执行结果"""

    task_id: str
    status: ExecutionStatus
    start_time: float
    end_time: float
    duration_ms: float
    result_data: dict[str, Any] | None = None
    error_message: str | None = None
    error_traceback: str | None = None
    resource_usage: dict[str, Any] = field(default_factory=dict)
    cost_events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "result_data": self.result_data,
            "error_message": self.error_message,
            "error_traceback": self.error_traceback,
            "resource_usage": self.resource_usage,
            "cost_events": self.cost_events,
        }


@dataclass
class ExecutionSession:
    """执行会话"""

    session_id: str
    task_id: str
    status: ExecutionStatus
    checkpoint_data: dict[str, Any] | None = None
    started_at: float | None = None
    last_updated: float | None = None
    retry_count: int = 0
    max_retries: int = 3

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "status": self.status.value,
            "checkpoint_data": self.checkpoint_data,
            "started_at": self.started_at,
            "last_updated": self.last_updated,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
        }


class StructuredLogger:
    """结构化日志生成系统"""

    def __init__(self, log_dir: str | None = None):
        """
        初始化结构化日志器

        Args:
            log_dir: 日志目录
        """
        if log_dir is None:
            log_dir = str(get_output_dir("logs") / "execution")

        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        logger.info("StructuredLogger initialized at %s", log_dir)

    def log_execution_start(self, task_id: str, task_type: str, metadata: dict[str, Any] | None = None) -> None:
        """记录执行开始"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "execution_start",
            "task_id": task_id,
            "task_type": task_type,
            "metadata": metadata or {},
        }
        self._write_log(task_id, entry)
        logger.info("[EXECUTION_START] task=%s type=%s", task_id, task_type)

    def log_execution_end(
        self,
        task_id: str,
        status: ExecutionStatus,
        duration_ms: float,
        result_summary: str | None = None,
    ) -> None:
        """记录执行结束"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "execution_end",
            "task_id": task_id,
            "status": status.value,
            "duration_ms": duration_ms,
            "result_summary": result_summary,
        }
        self._write_log(task_id, entry)
        logger.info(
            "[EXECUTION_END] task=%s status=%s duration=%.2fms",
            task_id,
            status.value,
            duration_ms,
        )

    def log_resource_usage(self, task_id: str, resource_usage: dict[str, Any]) -> None:
        """记录资源使用情况"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "resource_usage",
            "task_id": task_id,
            "resources": resource_usage,
        }
        self._write_log(task_id, entry)
        logger.debug("[RESOURCE_USAGE] task=%s %s", task_id, resource_usage)

    def log_error(self, task_id: str, error: Exception, traceback_str: str | None = None) -> None:
        """记录错误信息"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "error",
            "task_id": task_id,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback_str or traceback.format_exc(),
        }
        self._write_log(task_id, entry)
        logger.error("[ERROR] task=%s type=%s msg=%s", task_id, type(error).__name__, error)

    def log_cost_event(self, task_id: str, cost_event: dict[str, Any]) -> None:
        """记录成本事件"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "cost",
            "task_id": task_id,
            "cost": cost_event,
        }
        self._write_log(task_id, entry)

    def _write_log(self, task_id: str, entry: dict[str, Any]) -> None:
        """写入日志条目"""
        log_file = os.path.join(self.log_dir, f"{task_id}.jsonl")

        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("Failed to write log entry: %s", e)

    def get_task_logs(self, task_id: str) -> list[dict[str, Any]]:
        """获取任务日志"""
        log_file = os.path.join(self.log_dir, f"{task_id}.jsonl")

        if not os.path.exists(log_file):
            return []

        logs = []
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        logs.append(json.loads(line))
        except OSError as e:
            logger.warning("Failed to read task logs: %s", e)

        return logs
