"""颤振预测核心类型契约（V2.7 自 ``shared/lnn/types.py`` 迁移至此）。

原 ``shared/`` 契约层为 16 个模块的集群设计，但仅有本模块的 4 个类型被实际消费。
V2.7 重构将仅用的类型就地扎根于唯一的消费方——``chatter_prediction/``，
原 ``shared/`` 目录已删除，避免僵尸契约层混淆。

字段定义：
- ``FeatureChatterResult``            单个特征的颤振预测结果
- ``PredictionMethod``                预测方法枚举（analytical / neural_network / fallback）
- ``ChatterReviewStatus``             工程师审核状态枚举
- ``ChatterPredictionTaskStatus``     任务状态机枚举

D-2 学术诚信硬约束保护：
- 工程侧与科研侧的输入/输出 schema 必须与 ``FeatureChatterResult`` 对齐
- 任何字段变更需走 ADR 评审
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# 枚举：任务状态 / 审核状态 / 预测方法


class ChatterPredictionTaskStatus(str, Enum):
    """颤振预测任务状态机（单轮审核，与阶段 4 一致）。"""

    PENDING = "pending"
    RUNNING = "running"
    PREDICTED = "predicted"
    REVIEWED = "reviewed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ChatterReviewStatus(str, Enum):
    """工程师审核单个特征颤振预测结果的状态。"""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EDITED = "edited"


class PredictionMethod(str, Enum):
    """颤振预测方法。"""

    ANALYTICAL = "analytical"
    NEURAL_NETWORK = "neural_network"
    FALLBACK = "fallback"


# 单特征预测结果数据类


@dataclass
class FeatureChatterResult:
    """单个特征的颤振预测结果（工程侧与科研侧共享契约）。"""

    feature_id: str
    feature_type: str
    material_id: str
    spindle_rpm: float
    axial_depth_mm: float
    limit_depth_mm: float
    stable: bool
    stability_margin: float
    method: str
    ltc_active: bool
    confidence: float = 0.8
    inference_time_ms: float = 0.0
    warnings: list[str] = field(default_factory=list)
    material_calibration_status: str = "calibrated"
    review_status: str = ChatterReviewStatus.PENDING.value
    edited_params: dict[str, float] = field(default_factory=dict)
    reviewed_by: str = ""
    reviewed_at: float = 0.0
    engineer_notes: str = ""
    source_cutting_params_task_id: str = ""
    machine_id: str = ""
    tool_id: str = ""
    cutting_force_coeff: float = 0.0

    def effective_result(self) -> dict[str, float]:
        base: dict[str, float] = {
            "limit_depth_mm": self.limit_depth_mm,
            "axial_depth_mm": self.axial_depth_mm,
            "stable": 1.0 if self.stable else 0.0,
        }
        if self.review_status == ChatterReviewStatus.EDITED.value and self.edited_params:
            result = dict(base)
            result.update(self.edited_params)
            return result
        return base

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "feature_type": self.feature_type,
            "material_id": self.material_id,
            "spindle_rpm": self.spindle_rpm,
            "axial_depth_mm": self.axial_depth_mm,
            "limit_depth_mm": self.limit_depth_mm,
            "stable": self.stable,
            "stability_margin": self.stability_margin,
            "method": self.method,
            "ltc_active": self.ltc_active,
            "confidence": self.confidence,
            "inference_time_ms": self.inference_time_ms,
            "warnings": list(self.warnings),
            "material_calibration_status": self.material_calibration_status,
            "review_status": self.review_status,
            "edited_params": dict(self.edited_params),
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
            "engineer_notes": self.engineer_notes,
            "source_cutting_params_task_id": self.source_cutting_params_task_id,
            "machine_id": self.machine_id,
            "tool_id": self.tool_id,
            "cutting_force_coeff": self.cutting_force_coeff,
        }


__all__ = [
    "ChatterPredictionTaskStatus",
    "ChatterReviewStatus",
    "PredictionMethod",
    "FeatureChatterResult",
]
