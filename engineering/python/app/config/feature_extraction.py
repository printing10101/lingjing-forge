"""几何特征提取模块配置（RANSAC 平面/圆柱/孔检测）。

设计依据：项目记忆硬约束——
  - mesh → 参数化 CAD 自动转换工业上未解决，必须 human-in-the-loop
  - 系统定位「工程师助手」，非「全自动生产线」
  - 所有特征参数必须经工程师审核 + CAM 二次校验才能上机床

环境变量命名约定：LNN_FE_*（feature_extraction 缩写）
字段命名与 precision_disclaimer.py 中引用的字段保持一致
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.config._utils import _bool_env, _env, _float_env, _int_env, _path, logger


@dataclass
class FeatureExtractionConfig:
    """几何特征提取模块配置。

    所有配置项支持环境变量覆盖，遵循 12-Factor App 原则。
    """

    # 总开关：桌面轻量档位下可关闭，避免 trimesh/open3d 依赖加载
    enabled: bool = field(default_factory=lambda: _bool_env("LNN_FE_ENABLED", True))

    # 输出目录：存放每次特征提取任务的中间产物和最终 JSON
    output_dir: str = field(
        default_factory=lambda: _path("LNN_FE_OUTPUT_DIR", os.path.join("output", "feature_extraction"))
    )

    # 并发约束：特征提取是 CPU 密集型（RANSAC），桌面模式默认串行
    max_concurrent: int = field(default_factory=lambda: _int_env("LNN_FE_MAX_CONCURRENT", 1))

    # 任务超时（秒）：RANSAC + 圆柱拟合 + 孔检测在 5 万顶点 mesh 上约 30-300 秒
    task_timeout_seconds: int = field(default_factory=lambda: _int_env("LNN_FE_TASK_TIMEOUT", 600))

    # 任务历史保留时长（小时）：比拍照重建长，因为工程师审核需要时间
    task_retention_hours: int = field(default_factory=lambda: _int_env("LNN_FE_TASK_RETENTION_HOURS", 168))

    # 平面提取参数（RANSAC）
    # RANSAC 距离阈值（mm）：顶点到平面的距离小于此值才算内点
    # 越小越严格，但太小会导致噪声干扰；标准档位 0.5mm 较合理
    plane_ransac_threshold_mm: float = field(
        default_factory=lambda: _float_env("LNN_FE_PLANE_RANSAC_THRESHOLD_MM", 0.5)
    )
    # 最小内点数：少于此数的平面被丢弃（避免噪声碎片）
    plane_min_inliers: int = field(default_factory=lambda: _int_env("LNN_FE_PLANE_MIN_INLIERS", 1000))
    # 最多提取多少个平面（避免过拟合噪声）
    plane_max_features: int = field(default_factory=lambda: _int_env("LNN_FE_PLANE_MAX_FEATURES", 20))

    # 圆柱提取参数
    # 圆柱半径范围（mm）：超出此范围的圆柱被丢弃
    cylinder_min_radius_mm: float = field(default_factory=lambda: _float_env("LNN_FE_CYLINDER_MIN_RADIUS_MM", 1.0))
    cylinder_max_radius_mm: float = field(default_factory=lambda: _float_env("LNN_FE_CYLINDER_MAX_RADIUS_MM", 100.0))
    cylinder_min_inliers: int = field(default_factory=lambda: _int_env("LNN_FE_CYLINDER_MIN_INLIERS", 500))
    cylinder_max_features: int = field(default_factory=lambda: _int_env("LNN_FE_CYLINDER_MAX_FEATURES", 10))

    # 孔/凸台检测参数
    # 孔半径范围（mm）：超出此范围的孔被丢弃
    hole_min_radius_mm: float = field(default_factory=lambda: _float_env("LNN_FE_HOLE_MIN_RADIUS_MM", 0.5))
    hole_max_radius_mm: float = field(default_factory=lambda: _float_env("LNN_FE_HOLE_MAX_RADIUS_MM", 50.0))
    hole_max_features: int = field(default_factory=lambda: _int_env("LNN_FE_HOLE_MAX_FEATURES", 30))

    # mesh 预处理参数
    # 大 mesh 降采样目标顶点数：避免 RANSAC 在百万顶点 mesh 上过慢
    mesh_decimation_target_vertices: int = field(
        default_factory=lambda: _int_env("LNN_FE_MESH_DECIMATION_TARGET", 50000)
    )
    # 是否计算法向量（孔检测需要，依赖 trimesh）
    mesh_compute_normals: bool = field(default_factory=lambda: _bool_env("LNN_FE_MESH_COMPUTE_NORMALS", True))

    # 精度档位（仅用于显示告知，实际精度由上游 mesh 决定）
    precision_tier: str = field(default_factory=lambda: _env("LNN_FE_PRECISION_TIER", "standard"))

    def __post_init__(self) -> None:
        """启动时校验配置合法性。"""
        valid_tiers = {"coarse", "standard", "high"}
        if self.precision_tier not in valid_tiers:
            logger.warning(
                "Invalid LNN_FE_PRECISION_TIER='%s', expected one of %s. Falling back to 'standard'.",
                self.precision_tier,
                sorted(valid_tiers),
            )
            self.precision_tier = "standard"

        if self.plane_ransac_threshold_mm <= 0:
            logger.warning(
                "LNN_FE_PLANE_RANSAC_THRESHOLD_MM=%s invalid, must be > 0. Setting to 0.5 (default).",
                self.plane_ransac_threshold_mm,
            )
            self.plane_ransac_threshold_mm = 0.5

        if self.plane_min_inliers < 100:
            logger.warning(
                "LNN_FE_PLANE_MIN_INLIERS=%s too small, must be >= 100. Setting to 100.",
                self.plane_min_inliers,
            )
            self.plane_min_inliers = 100

        if self.cylinder_min_radius_mm >= self.cylinder_max_radius_mm:
            logger.warning(
                "LNN_FE_CYLINDER_MIN_RADIUS_MM=%s >= MAX_RADIUS_MM=%s, adjusting to defaults.",
                self.cylinder_min_radius_mm,
                self.cylinder_max_radius_mm,
            )
            self.cylinder_min_radius_mm = 1.0
            self.cylinder_max_radius_mm = 100.0

        if self.hole_min_radius_mm >= self.hole_max_radius_mm:
            logger.warning(
                "LNN_FE_HOLE_MIN_RADIUS_MM=%s >= MAX_RADIUS_MM=%s, adjusting to defaults.",
                self.hole_min_radius_mm,
                self.hole_max_radius_mm,
            )
            self.hole_min_radius_mm = 0.5
            self.hole_max_radius_mm = 50.0

        if self.mesh_decimation_target_vertices < 1000:
            logger.warning(
                "LNN_FE_MESH_DECIMATION_TARGET=%s too small, must be >= 1000. Setting to 1000.",
                self.mesh_decimation_target_vertices,
            )
            self.mesh_decimation_target_vertices = 1000
