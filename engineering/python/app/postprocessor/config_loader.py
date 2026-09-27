"""CNC后处理器配置加载与验证系统。

提供YAML配置文件解析、基础配置与控制器特定配置的深度合并、
配置完整性校验、参数类型及数值范围验证，以及配置缓存管理。
"""

from __future__ import annotations

import logging


logger = logging.getLogger(__name__)

# 子模块导入（常量/类经本模块再导出，__all__ 声明防 ruff F401/F822）
from app.postprocessor._validator import (  # noqa: E402
    COORD_SYSTEMS,
    REQUIRED_BASE_KEYS,
    REQUIRED_BORING_CYCLES,
    REQUIRED_DRILLING_CYCLES,
    REQUIRED_FEED_KEYS,
    REQUIRED_FIXED_CYCLE_GROUPS,
    REQUIRED_SPINDLE_KEYS,
    REQUIRED_SUBPROGRAM_KEYS,
    REQUIRED_TAPPING_CYCLES,
    REQUIRED_THREADING_CYCLES,
    REQUIRED_TOOL_OFFSET_KEYS,
    REQUIRED_TOP_KEYS,
    REQUIRED_WORK_COORD_KEYS,
    VALID_DECREMENT_TYPES,
    VALID_INFEED_METHODS,
    VALID_RETRACT_MODES,
    VALID_RETRACT_TYPES,
    VALID_SHIFT_AXES,
    VALID_SPINDLE_DIRECTIONS,
    ConfigValidationError,
    ConfigValidator,
)
from app.postprocessor._limiter import ConfigLimiter  # noqa: E402
from app.postprocessor._loader import (  # noqa: E402
    CONTROLLER_FULL_ID_MAP,
    CONTROLLER_ID_TO_FULL,
    VALID_CONTROLLER_IDS,
    ConfigLoadError,
    ConfigLoader,
    _deep_merge,
    create_limiter,
)

__all__ = [
    "CONTROLLER_FULL_ID_MAP",
    "CONTROLLER_ID_TO_FULL",
    "COORD_SYSTEMS",
    "REQUIRED_BASE_KEYS",
    "REQUIRED_BORING_CYCLES",
    "REQUIRED_DRILLING_CYCLES",
    "REQUIRED_FEED_KEYS",
    "REQUIRED_FIXED_CYCLE_GROUPS",
    "REQUIRED_SPINDLE_KEYS",
    "REQUIRED_SUBPROGRAM_KEYS",
    "REQUIRED_TAPPING_CYCLES",
    "REQUIRED_THREADING_CYCLES",
    "REQUIRED_TOOL_OFFSET_KEYS",
    "REQUIRED_TOP_KEYS",
    "REQUIRED_WORK_COORD_KEYS",
    "VALID_DECREMENT_TYPES",
    "VALID_INFEED_METHODS",
    "VALID_RETRACT_MODES",
    "VALID_RETRACT_TYPES",
    "VALID_SHIFT_AXES",
    "VALID_SPINDLE_DIRECTIONS",
    "ConfigLoadError",
    "ConfigValidationError",
    "ConfigValidator",
    "ConfigLimiter",
    "ConfigLoader",
    "create_limiter",
    "_deep_merge",
    "VALID_CONTROLLER_IDS",
]

# （控制器映射常量已迁至 _loader.py，经 __all__ 再导出）
