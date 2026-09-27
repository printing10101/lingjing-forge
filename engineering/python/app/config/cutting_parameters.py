"""切削参数推荐模块配置（材料→切削参数→ChatterParams）。

环境变量前缀：LNN_CP_*
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.config._utils import _bool_env, _env, _float_env, _int_env, _path, logger


@dataclass
class CuttingParametersConfig:
    """切削参数推荐模块配置（阶段 4）。

    所有配置项支持环境变量覆盖，遵循 12-Factor App 原则。
    环境变量前缀：LNN_CP_*
    """

    # 总开关：桌面轻量档位下可关闭，避免材料数据库加载开销
    enabled: bool = field(default_factory=lambda: _bool_env("LNN_CP_ENABLED", True))

    # 输出目录：存放每次切削参数任务的工作目录（含 ChatterParams JSON）
    output_dir: str = field(
        default_factory=lambda: _path("LNN_CP_OUTPUT_DIR", os.path.join("output", "cutting_parameters"))
    )

    # 并发约束：切削参数推荐为 CPU 密集型（Taylor 估算 + 数学计算），桌面模式默认串行
    max_concurrent: int = field(default_factory=lambda: _int_env("LNN_CP_MAX_CONCURRENT", 1))

    # 任务超时（秒）：单任务推荐 < 1 秒，但工程师审核可能耗时数小时
    # 此 timeout 仅覆盖 run_pipeline 阶段，审核等待不计入
    task_timeout_seconds: int = field(default_factory=lambda: _int_env("LNN_CP_TASK_TIMEOUT", 60))

    # 任务历史保留时长（小时）：与阶段 2/3 一致，工程师审核需要时间
    task_retention_hours: int = field(default_factory=lambda: _int_env("LNN_CP_TASK_RETENTION_HOURS", 168))

    # 默认刀具直径（mm）：用户未指定时使用
    default_tool_diameter_mm: float = field(default_factory=lambda: _float_env("LNN_CP_DEFAULT_TOOL_DIAMETER_MM", 10.0))

    # 默认齿数：用户未指定时使用
    default_num_flutes: int = field(default_factory=lambda: _int_env("LNN_CP_DEFAULT_NUM_FLUTES", 4))

    # 默认机床类型（仅供追溯，实际机床动态参数由阶段 5 查询）
    default_machine_type: str = field(default_factory=lambda: _env("LNN_CP_DEFAULT_MACHINE_TYPE", "vmc_850"))

    # 精度档位（仅用于显示告知，实际精度由上游 mesh 决定）
    # 继承自阶段 1/2/3，本模块不引入新档位
    precision_tier: str = field(default_factory=lambda: _env("LNN_CP_PRECISION_TIER", "standard"))

    # 默认 mesh 标定状态：保守默认为 False，强制上游显式声明已标定
    default_mesh_calibrated: bool = field(default_factory=lambda: _bool_env("LNN_CP_DEFAULT_MESH_CALIBRATED", False))

    # 是否允许 SUCCEEDED 状态任务删除（项目记忆硬约束：始终 False）
    # SUCCEEDED 任务可能已被阶段 5 引用，删除会破坏追溯链
    allow_delete_succeeded: bool = field(default_factory=lambda: _bool_env("LNN_CP_ALLOW_DELETE_SUCCEEDED", False))

    def __post_init__(self) -> None:
        """启动时校验配置合法性。"""
        valid_tiers = {"coarse", "standard", "high"}
        if self.precision_tier not in valid_tiers:
            logger.warning(
                "Invalid LNN_CP_PRECISION_TIER='%s', expected one of %s. Falling back to 'standard'.",
                self.precision_tier,
                sorted(valid_tiers),
            )
            self.precision_tier = "standard"

        if self.default_tool_diameter_mm <= 0:
            logger.warning(
                "LNN_CP_DEFAULT_TOOL_DIAMETER_MM=%s invalid, must be > 0. Setting to 10.0 (default endmill).",
                self.default_tool_diameter_mm,
            )
            self.default_tool_diameter_mm = 10.0

        if self.default_num_flutes < 1:
            logger.warning(
                "LNN_CP_DEFAULT_NUM_FLUTES=%s invalid, must be >= 1. Setting to 4 (default endmill).",
                self.default_num_flutes,
            )
            self.default_num_flutes = 4

        if self.max_concurrent < 1:
            logger.warning(
                "LNN_CP_MAX_CONCURRENT=%s invalid, must be >= 1. Setting to 1 (serial).",
                self.max_concurrent,
            )
            self.max_concurrent = 1

        if self.task_timeout_seconds < 10:
            logger.warning(
                "LNN_CP_TASK_TIMEOUT=%s too small (<10s), 材料查询可能未完成。Setting to 60.",
                self.task_timeout_seconds,
            )
            self.task_timeout_seconds = 60

        if self.allow_delete_succeeded:
            logger.warning(
                "LNN_CP_ALLOW_DELETE_SUCCEEDED=true 违反项目记忆硬约束"
                "（SUCCEEDED 任务可能被阶段 5 引用），强制重置为 false。"
            )
            self.allow_delete_succeeded = False
