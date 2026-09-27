"""颤振预测接入模块（阶段 5）。

将阶段 4 输出的 ChatterParams JSON + 材料 ID 转换为带工程师审核状态的
颤振稳定性预测报告，导出 ChatterReport JSON 供阶段 6 G 代码生成使用。

核心 pipeline：
    阶段 4 ChatterParams JSON（含每条特征的 spindle_rpm / machine / tool / axial_depth）
        → ChatterPredictorAdapter 双路径预测：
            路径 A: Tlusty 解析法（compute_stability_limit，工程可用，默认路径）
            路径 B: LTC 神经网络（实验性，chatter_model.pt 不存在时自动回退到路径 A）
            路径 C: 兜底默认值（保守 limit_depth=1.0mm，confidence=0.3）
        → HRC52 材料 pending_calibration 时强制降低置信度（0.8 → 0.5）
        → 工程师审核每个特征的稳定性预测结果（confirmed / rejected / edited）
        → 导出 ChatterReport JSON（供阶段 6 G 代码生成使用）

定位声明（项目记忆硬约束）：
    本模块是「工程师助手」，非「全自动颤振预测器」。
    颤振预测基于 Tlusty 解析法 + LTC 神经网络（实验性），最终稳定性判断必须经工程师审核。
    本模块输出的 ChatterReport 仅供阶段 6 G 代码生成参考，不可直接用于机床。
    实际加工必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验 + 持证操作员 + 导师签字。

精度继承链（不引入新的精度档位）：
    阶段 1 image_to_3d.precision_tier
    → 阶段 2 feature_extraction.precision_tier
    → 阶段 3 parametric_geometry.precision_tier
    → 阶段 4 cutting_parameters.precision_tier
    → 阶段 5 chatter_prediction（本模块全程继承上游告知）
    精度档位影响置信度标注：HRC52 + pending_calibration 时强制降低置信度。

K_s 传递策略（项目记忆硬约束）：
    K_s（cutting_force_coeff）直接取自阶段 4 ChatterParams，不进行二次拟合。
"""

from __future__ import annotations

# 任务存储 + 状态机 + 审核枚举
from app.chatter_prediction.chatter_store import (
    ChatterParamsLoadError,
    ChatterPredictionError,
    ChatterPredictionTask,
    ChatterPredictionTaskStatus,
    ChatterReviewStatus,
    FeatureChatterResult,
    PredictionMethod,
    ReviewError,
    TaskStore,
    generate_task_id,
    get_task_store,
)

# 精度告知
from app.chatter_prediction.chatter_disclaimer import (
    ChatterDisclaimer,
    INDUSTRIAL_HARD_GATES,
    build_chatter_disclaimer,
)

# 双路径预测适配器
from app.chatter_prediction.predictor_adapter import (
    ChatterPredictorAdapter,
    PredictorAdapterError,
    check_ltc_model_available,
)

# 流水线编排器
from app.chatter_prediction.pipeline import (
    ChatterPredictionPipeline,
    ChatterPredictionPipelineError,
    ChatterPredictionResult,
    ChatterReviewError,
)

__all__ = [
    # 任务存储 + 状态机
    "ChatterPredictionTask",
    "ChatterPredictionTaskStatus",
    "ChatterReviewStatus",
    "PredictionMethod",
    "FeatureChatterResult",
    "ChatterPredictionError",
    "ChatterParamsLoadError",
    "ReviewError",
    "TaskStore",
    "generate_task_id",
    "get_task_store",
    # 精度告知
    "ChatterDisclaimer",
    "INDUSTRIAL_HARD_GATES",
    "build_chatter_disclaimer",
    # 双路径预测适配器
    "ChatterPredictorAdapter",
    "PredictorAdapterError",
    "check_ltc_model_available",
    # 流水线
    "ChatterPredictionPipeline",
    "ChatterPredictionResult",
    "ChatterPredictionPipelineError",
    "ChatterReviewError",
]
