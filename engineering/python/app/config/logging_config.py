"""日志轮转和保留策略配置。

注意：模块名使用 ``logging_config`` 而非 ``logging``，避免与标准库 ``logging``
冲突（``from app.config.logging import X`` 会触发标准库导入而非本模块）。
"""

from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field

from app.config._utils import _env, _int_env
from app.config.limits import LOG_ROTATE_MAX_BYTES


@dataclass
class LoggingConfig:
    log_level: str = field(default_factory=lambda: _env("LNN_LOG_LEVEL", "INFO"))
    log_dir: str = field(
        default_factory=lambda: _env(
            "LNN_LOG_DIR",
            str(Path.home() / ".lingjing" / "logs"),
        )
    )
    max_bytes: int = field(default_factory=lambda: _int_env("LNN_LOG_MAX_BYTES", LOG_ROTATE_MAX_BYTES))
    backup_count: int = field(default_factory=lambda: _int_env("LNN_LOG_BACKUP_COUNT", 5))
    retention_days: int = field(default_factory=lambda: _int_env("LNN_LOG_RETENTION_DAYS", 30))
