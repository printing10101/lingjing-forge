"""颤振预测接入模块：任务存储 + 状态机 + 审核枚举（阶段 5）。

数据流：
    阶段 4 输出的 ChatterParams JSON（含每条特征的 spindle_rpm / machine / tool / axial_depth）
        → ChatterPredictorAdapter 双路径预测：
            路径 A: Tlusty 解析法（compute_stability_limit，工程可用，默认路径）
            路径 B: LTC 神经网络（实验性，chatter_model.pt 不存在时自动回退到路径 A）
        → 工程师审核每个特征的稳定性预测结果（confirmed / rejected / edited）
        → 导出 ChatterReport JSON（供阶段 6 G 代码生成使用）

定位声明（项目记忆硬约束）：
    本模块是「工程师助手」，不是「全自动颤振预测器」。
    颤振预测基于 Tlusty 解析法 + LTC 神经网络（实验性），最终稳定性判断必须经工程师审核。
    本模块输出的 ChatterReport 仅供阶段 6 G 代码生成参考，不可直接用于机床。
    实际加工必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验 + 持证操作员 + 导师签字。

精度继承链：
    阶段 1 image_to_3d.precision_tier → 阶段 2 → 阶段 3 → 阶段 4 → 阶段 5（本模块）
    本模块不引入新的精度档位，全程继承上游告知。
    精度档位影响置信度标注：HRC52 + pending_calibration 时强制降低置信度。

工程优先策略（项目记忆硬约束：工程生产优先于学术价值）：
    - 默认走 Tlusty 解析法路径（stability.py 已实现，工程可用）
    - LTC 神经网络路径标记为「实验性」，仅在 chatter_model.pt 存在时尝试
    - 不使用合成数据训练 LTC 模型（合成数据在真实车间无意义）
    - HRC52 材料强制注入 pending_calibration 标注并降低置信度
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.utils.task_store import PerTaskJsonStore


# 枚举：任务状态 / 审核状态 / 预测方法

# 类型契约（V2.7: 自 shared/lnn/types.py 迁移至本地的 _types.py）
from app.chatter_prediction._types import (
    ChatterPredictionTaskStatus,
    ChatterReviewStatus,  # noqa: F401 - re-export（pipeline/__init__ 依赖旧导入路径）
    PredictionMethod,  # noqa: F401 - re-export（__init__ 依赖旧导入路径）
    FeatureChatterResult,  # re-export：同上（predictor_adapter 依赖旧导入路径）
)


# 任务数据类


@dataclass
class ChatterPredictionTask:
    """颤振预测任务。"""

    task_id: str
    created_at: float
    source_cutting_parameters_task_id: str
    chatter_params_path: str  # 阶段 4 输出的 ChatterParams JSON
    material_id: str
    precision_tier: str  # coarse / standard / high
    mesh_calibrated: bool
    machine_type: str = "vmc_850"
    status: str = ChatterPredictionTaskStatus.PENDING.value
    feature_results: list[FeatureChatterResult] = field(default_factory=list)
    workspace_dir: str = ""
    error_message: str = ""
    cam_validation_required: bool = True  # 项目记忆硬约束：始终 True
    chatter_report_path: str = ""  # 输出给阶段 6 的 JSON 路径
    started_at: float = 0.0
    completed_at: float = 0.0
    # 预测方法统计
    analytical_count: int = 0
    neural_network_count: int = 0
    fallback_count: int = 0
    ltc_model_available: bool = False  # chatter_model.pt 是否存在
    # 审核追溯
    reviewed_by: str = ""
    reviewed_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "created_at": self.created_at,
            "source_cutting_parameters_task_id": self.source_cutting_parameters_task_id,
            "chatter_params_path": self.chatter_params_path,
            "material_id": self.material_id,
            "precision_tier": self.precision_tier,
            "mesh_calibrated": self.mesh_calibrated,
            "machine_type": self.machine_type,
            "status": self.status,
            "feature_results": [r.to_dict() for r in self.feature_results],
            "workspace_dir": self.workspace_dir,
            "error_message": self.error_message,
            "cam_validation_required": self.cam_validation_required,
            "chatter_report_path": self.chatter_report_path,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "analytical_count": self.analytical_count,
            "neural_network_count": self.neural_network_count,
            "fallback_count": self.fallback_count,
            "ltc_model_available": self.ltc_model_available,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
        }


# 任务存储（单例 + JSON 持久化）


def generate_task_id() -> str:
    """生成任务 ID：ch_{timestamp}_{uuid8}。"""
    return f"ch_{int(time.time())}_{uuid.uuid4().hex[:8]}"


class ChatterPredictionError(Exception):
    """颤振预测异常基类。"""


class ChatterParamsLoadError(ChatterPredictionError):
    """阶段 4 ChatterParams JSON 加载失败。"""


class ReviewError(ChatterPredictionError):
    """审核异常。"""


class TaskStore(PerTaskJsonStore[ChatterPredictionTask]):
    """颤振预测任务存储（持久化目录默认 ``output/chatter_prediction_tasks``）。

    公共实现见 :class:`app.utils.task_store.PerTaskJsonStore`。
    """

    default_dir_name = "chatter_prediction_tasks"

    def _review_error(self, message: str) -> Exception:
        return ReviewError(message)

    def _deletable_reason(self, task: ChatterPredictionTask) -> str | None:
        # 项目记忆硬约束：SUCCEEDED 状态禁止删除
        # （阶段 6 G 代码生成可能已引用其 ChatterReport）
        if task.status == ChatterPredictionTaskStatus.SUCCEEDED.value:
            return f"任务 {task.task_id} 处于 SUCCEEDED 状态，禁止删除（阶段 6 G 代码生成可能已引用其 ChatterReport）"
        return None


def get_task_store() -> TaskStore:
    """获取全局 TaskStore 单例。"""
    return TaskStore.get_instance()
