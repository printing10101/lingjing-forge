"""参数化几何输出模块配置（特征→B-rep→STEP）。

设计依据：项目记忆硬约束——
  - mesh → 参数化 CAD 自动转换工业上未解决，必须 human-in-the-loop
  - 系统定位「工程师助手」，非「全自动生产线」
  - 生成的 STEP 文件必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床
  - 普通手机摄影测量精度 0.1-1mm，配合面公差 0.01mm 物理上不可达

精度继承链：阶段 1 image_to_3d.precision_tier → 阶段 2 feature_extraction.precision_tier
          → 阶段 3（本模块不引入新的精度档位，全程继承上游告知）

环境变量命名约定：LNN_PG_*（parametric_geometry 缩写）
字段命名与 step_disclaimer.py / pipeline.py 中引用的字段保持一致
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.config._utils import _bool_env, _env, _float_env, _int_env, _path, logger


@dataclass
class ParametricGeometryConfig:
    """参数化几何输出模块配置。

    所有配置项支持环境变量覆盖，遵循 12-Factor App 原则。
    """

    # 总开关：桌面轻量档位下可关闭，避免 pythonOCC/FreeCAD 依赖探测开销
    enabled: bool = field(default_factory=lambda: _bool_env("LNN_PG_ENABLED", True))

    # 输出目录：存放每次参数化几何任务的工作目录（含 STEP/assembly_plan/brep_shapes）
    output_dir: str = field(
        default_factory=lambda: _path("LNN_PG_OUTPUT_DIR", os.path.join("output", "parametric_geometry"))
    )

    # 并发约束：STEP 写入是 CPU 密集型（pythonOCC 布尔运算），桌面模式默认串行
    max_concurrent: int = field(default_factory=lambda: _int_env("LNN_PG_MAX_CONCURRENT", 1))

    # 任务超时（秒）：pythonOCC 在 50 个形状的布尔运算下约 10-60 秒；
    # 复杂零件（100+ 形状）可能数分钟，默认 600 秒兜底
    task_timeout_seconds: int = field(default_factory=lambda: _int_env("LNN_PG_TASK_TIMEOUT", 600))

    # 任务历史保留时长（小时）：与阶段 2 一致，工程师审核需要时间
    task_retention_hours: int = field(default_factory=lambda: _int_env("LNN_PG_TASK_RETENTION_HOURS", 168))

    # 毛坯余量（mm）：装配器在 add 形状 bbox 并集外扩此值得到毛坯尺寸
    # 2.0mm 是粗加工常见余量；精加工余量 0.5mm 由阶段 4 切削参数推荐覆盖
    blank_margin_mm: float = field(default_factory=lambda: _float_env("LNN_PG_BLANK_MARGIN_MM", 2.0))

    # 精度档位（仅用于显示告知，实际精度由上游 mesh 决定）
    # 继承自阶段 1/2，本模块不引入新档位
    precision_tier: str = field(default_factory=lambda: _env("LNN_PG_PRECISION_TIER", "standard"))

    # 默认 mesh 标定状态：当任务创建时未显式传入 mesh_calibrated 时使用
    # 保守默认为 False，强制上游显式声明已标定
    default_mesh_calibrated: bool = field(default_factory=lambda: _bool_env("LNN_PG_DEFAULT_MESH_CALIBRATED", False))

    def __post_init__(self) -> None:
        """启动时校验配置合法性。"""
        valid_tiers = {"coarse", "standard", "high"}
        if self.precision_tier not in valid_tiers:
            logger.warning(
                "Invalid LNN_PG_PRECISION_TIER='%s', expected one of %s. Falling back to 'standard'.",
                self.precision_tier,
                sorted(valid_tiers),
            )
            self.precision_tier = "standard"

        if self.blank_margin_mm <= 0:
            logger.warning(
                "LNN_PG_BLANK_MARGIN_MM=%s invalid, must be > 0. Setting to 2.0 (default roughing margin).",
                self.blank_margin_mm,
            )
            self.blank_margin_mm = 2.0

        if self.blank_margin_mm > 20.0:
            logger.warning(
                "LNN_PG_BLANK_MARGIN_MM=%s too large (>20mm), "
                "may produce oversized blank. Verify before production use.",
                self.blank_margin_mm,
            )

        if self.max_concurrent < 1:
            logger.warning(
                "LNN_PG_MAX_CONCURRENT=%s invalid, must be >= 1. Setting to 1 (serial).",
                self.max_concurrent,
            )
            self.max_concurrent = 1

        if self.task_timeout_seconds < 60:
            logger.warning(
                "LNN_PG_TASK_TIMEOUT=%s too small (<60s), pythonOCC 布尔运算可能未完成。Setting to 600.",
                self.task_timeout_seconds,
            )
            self.task_timeout_seconds = 600
