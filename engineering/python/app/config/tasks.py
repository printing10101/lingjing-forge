"""异步任务系统配置。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config._utils import _env, _int_env


@dataclass
class TaskSystemConfig:
    max_concurrent: int = field(default_factory=lambda: _int_env("TASK_MAX_CONCURRENT", 3))
    recovery_strategy: str = field(default_factory=lambda: _env("TASK_RECOVERY_STRATEGY", "mark_failed"))
    max_task_history: int = field(default_factory=lambda: _int_env("TASK_MAX_HISTORY", 10000))
