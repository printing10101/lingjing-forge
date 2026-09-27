"""切削参数推荐模块（阶段 4）。

将阶段 3 输出的 STEP 文件 + 阶段 2 的 confirmed_features.json + 材料 ID
转换为带工程师审核状态的切削参数集，导出 ChatterParams JSON 供阶段 5 颤振预测使用。

核心 pipeline：
    阶段 3 STEP 文件 + 阶段 2 confirmed_features.json + material_id
        → MaterialResolver 查询材料切削参数基线（含 HRC52 补充数据）
        → CuttingParamRecommender 按特征类型 + 精度档位推荐切削参数
            （cutting_speed / feed / depth_of_cut / spindle_rpm / tool_life）
        → 工程师审核每个特征的切削参数（confirmed / rejected / edited）
        → 导出 ChatterParams JSON（供阶段 5 LTC 颤振预测使用）

定位声明（项目记忆硬约束）：
    本模块是「工程师助手」，不是「全自动切削参数生成器」。
    切削参数推荐基于材料数据库 + 几何特征，但最终参数必须经工程师审核。
    本模块输出的 ChatterParams 仅供阶段 5 颤振预测参考，不可直接用于机床。
    实际加工必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验 + 持证操作员 + 导师签字。

精度继承链（不引入新的精度档位）：
    阶段 1 image_to_3d.precision_tier
    → 阶段 2 feature_extraction.precision_tier
    → 阶段 3 parametric_geometry.precision_tier
    → 阶段 4 cutting_parameters（本模块全程继承上游告知）
    精度档位影响 operation 选择：high → finishing，standard/coarse → roughing
"""

from __future__ import annotations

# 材料解析器
from app.cutting_parameters.material_resolver import (
    MaterialParams,
    MaterialResolver,
    MaterialResolverError,
    get_material_resolver,
    reset_material_resolver,
)

# 任务存储 + 状态机 + 审核枚举
from app.cutting_parameters.cutting_store import (
    CuttingParametersError,
    CuttingParametersTask,
    CuttingParametersTaskStatus,
    CuttingReviewStatus,
    MaterialNotFoundError,
    OperationType,
    RecommendedCuttingParams,
    ReviewError,
    TaskStore,
    generate_task_id,
    get_task_store,
)

# 精度告知
from app.cutting_parameters.cutting_disclaimer import (
    CuttingDisclaimer,
    INDUSTRIAL_HARD_GATES,
    build_cutting_disclaimer,
)

# 推荐引擎
from app.cutting_parameters.recommender import (
    CuttingParamRecommender,
    FeatureNotSupportedError,
    RecommendationError,
    SUPPORTED_FEATURE_TYPES,
    FEATURE_TYPE_RADIAL_DEPTH_RATIO,
    to_chatter_params_dict,
)

# 流水线编排器
from app.cutting_parameters.pipeline import (
    CuttingParametersPipeline,
    CuttingParametersPipelineError,
    CuttingParametersResult,
    CuttingReviewError,
    FeaturesLoadError,
)

__all__ = [
    # 材料解析器
    "MaterialParams",
    "MaterialResolver",
    "MaterialResolverError",
    "MaterialNotFoundError",
    "get_material_resolver",
    "reset_material_resolver",
    # 任务存储 + 状态机
    "CuttingParametersTask",
    "CuttingParametersTaskStatus",
    "CuttingReviewStatus",
    "OperationType",
    "RecommendedCuttingParams",
    "CuttingParametersError",
    "ReviewError",
    "TaskStore",
    "generate_task_id",
    "get_task_store",
    # 精度告知
    "CuttingDisclaimer",
    "INDUSTRIAL_HARD_GATES",
    "build_cutting_disclaimer",
    # 推荐引擎
    "CuttingParamRecommender",
    "RecommendationError",
    "FeatureNotSupportedError",
    "SUPPORTED_FEATURE_TYPES",
    "FEATURE_TYPE_RADIAL_DEPTH_RATIO",
    "to_chatter_params_dict",
    # 流水线
    "CuttingParametersPipeline",
    "CuttingParametersResult",
    "CuttingParametersPipelineError",
    "CuttingReviewError",
    "FeaturesLoadError",
]
