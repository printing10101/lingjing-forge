"""切削参数推荐模块：任务存储 + 状态机 + 审核枚举。

数据流：
    阶段 3 输出的 STEP 文件 + 阶段 2 的 confirmed_features.json
        → MaterialResolver 查询材料切削参数基线
        → CuttingParamRecommender 按特征类型 + 精度档位推荐切削参数
        → 工程师审核每个特征的切削参数（confirmed / rejected / edited）
        → 导出 ChatterParams JSON（供阶段 5 颤振预测使用）

定位声明（项目记忆硬约束）：
    本模块是「工程师助手」，不是「全自动切削参数生成器」。
    切削参数推荐基于材料数据库 + 几何特征，但最终参数必须经工程师审核。
    本模块输出的参数必须经过 CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床。

精度继承链：
    阶段 1 image_to_3d.precision_tier → 阶段 2 → 阶段 3 → 阶段 4
    本模块不引入新的精度档位，全程继承上游告知。
    精度档位影响 operation 选择：high → finishing，standard/coarse → roughing。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.utils.task_store import PerTaskJsonStore


# 枚举：任务状态 / 审核状态 / 操作类型


class CuttingParametersTaskStatus(str, Enum):
    """切削参数推荐任务状态机。

    状态转移图：
        PENDING → RUNNING → PARAMS_RECOMMENDED → REVIEWED → SUCCEEDED
                                                    ↘ FAILED
                                                    ↘ CANCELLED

    - PENDING             : 任务已创建，等待触发执行
    - RUNNING             : 正在执行材料查询 + 参数推荐
    - PARAMS_RECOMMENDED  : 推荐参数已生成，等待工程师审核
    - REVIEWED            : 工程师已审核全部特征参数
    - SUCCEEDED           : ChatterParams JSON 已导出，可供阶段 5 使用
    - FAILED              : 执行失败（材料未找到 / 推荐异常 等）
    - CANCELLED           : 用户主动取消
    """

    PENDING = "pending"
    RUNNING = "running"
    PARAMS_RECOMMENDED = "params_recommended"
    REVIEWED = "reviewed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CuttingReviewStatus(str, Enum):
    """工程师审核单个特征切削参数的状态。

    - PENDING   : 待审核
    - CONFIRMED : 工程师确认推荐参数无误
    - REJECTED  : 工程师拒绝该特征（不进入最终 ChatterParams）
    - EDITED    : 工程师编辑了参数（如切深、进给）
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EDITED = "edited"


class OperationType(str, Enum):
    """切削操作类型（由精度档位 + 特征类型决定）。

    - ROUGHING  : 粗加工（大切深、低精度，coarse/standard 档位默认）
    - FINISHING : 精加工（小切深、高精度，high 档位默认）
    """

    ROUGHING = "roughing"
    FINISHING = "finishing"


# 推荐切削参数数据类


@dataclass
class RecommendedCuttingParams:
    """单个特征的推荐切削参数。

    所有数值单位：
    - spindle_speed_rpm: RPM（主轴转速）
    - feed_rate_mm_per_min: mm/min（进给速度）
    - feed_per_tooth_mm: mm/tooth（每齿进给量）
    - cutting_speed_m_per_min: m/min（切削速度，线速度）
    - axial_depth_mm: mm（轴向切深，ap）
    - radial_depth_mm: mm（径向切深，ae，铣削专用）
    """

    feature_id: str
    feature_type: str  # plane / cylinder / hole / boss
    operation: str  # roughing / finishing
    spindle_speed_rpm: float
    feed_rate_mm_per_min: float
    feed_per_tooth_mm: float
    cutting_speed_m_per_min: float
    axial_depth_mm: float
    radial_depth_mm: float = 0.0
    estimated_cutting_time_s: float = 0.0
    tool_life_estimate_min: float = 0.0
    warnings: list[str] = field(default_factory=list)
    # 工程师审核
    review_status: str = CuttingReviewStatus.PENDING.value
    edited_params: dict[str, float] = field(default_factory=dict)
    reviewed_by: str = ""
    reviewed_at: float = 0.0
    engineer_notes: str = ""
    # 来源追溯
    material_id: str = ""
    tool_diameter_mm: float = 0.0
    num_flutes: int = 0

    def effective_params(self) -> dict[str, float]:
        """获取生效参数（edited 时用 edited_params 覆盖，否则用推荐值）。

        与阶段 2/3 的 effective_params() 契约一致：
        - review_status == edited 且 edited_params 非空 → 用编辑值
        - 否则 → 用推荐值副本
        """
        base = {
            "spindle_speed_rpm": self.spindle_speed_rpm,
            "feed_rate_mm_per_min": self.feed_rate_mm_per_min,
            "feed_per_tooth_mm": self.feed_per_tooth_mm,
            "cutting_speed_m_per_min": self.cutting_speed_m_per_min,
            "axial_depth_mm": self.axial_depth_mm,
            "radial_depth_mm": self.radial_depth_mm,
        }
        if self.review_status == CuttingReviewStatus.EDITED.value and self.edited_params:
            result = dict(base)
            result.update(self.edited_params)
            return result
        return base

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "feature_type": self.feature_type,
            "operation": self.operation,
            "spindle_speed_rpm": self.spindle_speed_rpm,
            "feed_rate_mm_per_min": self.feed_rate_mm_per_min,
            "feed_per_tooth_mm": self.feed_per_tooth_mm,
            "cutting_speed_m_per_min": self.cutting_speed_m_per_min,
            "axial_depth_mm": self.axial_depth_mm,
            "radial_depth_mm": self.radial_depth_mm,
            "estimated_cutting_time_s": self.estimated_cutting_time_s,
            "tool_life_estimate_min": self.tool_life_estimate_min,
            "warnings": list(self.warnings),
            "review_status": self.review_status,
            "edited_params": dict(self.edited_params),
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
            "engineer_notes": self.engineer_notes,
            "material_id": self.material_id,
            "tool_diameter_mm": self.tool_diameter_mm,
            "num_flutes": self.num_flutes,
        }


# 任务数据类


@dataclass
class CuttingParametersTask:
    """切削参数推荐任务。"""

    task_id: str
    created_at: float
    source_parametric_geometry_task_id: str
    step_file_path: str
    input_features_path: str
    material_id: str
    precision_tier: str  # coarse / standard / high
    mesh_calibrated: bool
    machine_type: str = "default"
    tool_diameter_mm: float = 10.0
    num_flutes: int = 4
    status: str = CuttingParametersTaskStatus.PENDING.value
    recommended_params: list[RecommendedCuttingParams] = field(default_factory=list)
    workspace_dir: str = ""
    error_message: str = ""
    cam_validation_required: bool = True  # 项目记忆硬约束：始终 True
    chatter_params_path: str = ""  # 输出给阶段 5 的 JSON 路径
    started_at: float = 0.0
    completed_at: float = 0.0
    # 审核追溯
    reviewed_by: str = ""
    reviewed_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "created_at": self.created_at,
            "source_parametric_geometry_task_id": self.source_parametric_geometry_task_id,
            "step_file_path": self.step_file_path,
            "input_features_path": self.input_features_path,
            "material_id": self.material_id,
            "precision_tier": self.precision_tier,
            "mesh_calibrated": self.mesh_calibrated,
            "machine_type": self.machine_type,
            "tool_diameter_mm": self.tool_diameter_mm,
            "num_flutes": self.num_flutes,
            "status": self.status,
            "recommended_params": [p.to_dict() for p in self.recommended_params],
            "workspace_dir": self.workspace_dir,
            "error_message": self.error_message,
            "cam_validation_required": self.cam_validation_required,
            "chatter_params_path": self.chatter_params_path,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
        }


# 任务存储（单例 + JSON 持久化）


def generate_task_id() -> str:
    """生成任务 ID：cp_{timestamp}_{uuid8}。"""
    return f"cp_{int(time.time())}_{uuid.uuid4().hex[:8]}"


class CuttingParametersError(Exception):
    """切削参数推荐异常基类。"""


class MaterialNotFoundError(CuttingParametersError):
    """材料 ID 未找到。"""


class ReviewError(CuttingParametersError):
    """审核异常。"""


class TaskStore(PerTaskJsonStore[CuttingParametersTask]):
    """切削参数任务存储（持久化目录默认 ``output/cutting_parameters_tasks``）。

    公共实现见 :class:`app.utils.task_store.PerTaskJsonStore`。
    """

    default_dir_name = "cutting_parameters_tasks"

    def _review_error(self, message: str) -> Exception:
        return ReviewError(message)

    def _deletable_reason(self, task: CuttingParametersTask) -> str | None:
        # 项目记忆硬约束：SUCCEEDED 状态禁止删除（避免误删阶段 5 已引用的 ChatterParams）
        if task.status == CuttingParametersTaskStatus.SUCCEEDED.value:
            return f"任务 {task.task_id} 处于 SUCCEEDED 状态，禁止删除（阶段 5 颤振预测可能已引用其 ChatterParams）"
        return None


def get_task_store() -> TaskStore:
    """获取全局 TaskStore 单例。"""
    return TaskStore.get_instance()
