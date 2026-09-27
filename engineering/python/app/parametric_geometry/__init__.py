"""参数化几何输出模块（阶段 3）。

将阶段 2 几何特征辅助提取模块输出的 confirmed_features.json 转换为 STEP 文件，
供工程师审核后进入阶段 4 切削参数推荐。

核心 pipeline：
    阶段 2 confirmed_features.json
        → 解析为 ReviewedFeatureRef 列表
        → FeatureToBrepConverter 转换为 B-rep TopoDS_Shape
        → AssemblyBuilder 多特征布尔运算装配成零件
        → StepWriter 输出 STEP 文件（pythonOCC → FreeCAD API → 模板三级降级）
        → 工程师审核 STEP 中每个特征的表达（confirmed / rejected / edited）
        → 导出最终 STEP 文件路径（供阶段 4 使用）

定位声明（项目记忆硬约束）：
    本模块是「工程师助手」，不是「全自动参数化 CAD 生成器」。
    mesh → 参数化 CAD 自动转换在工业上未解决，
    生产系统依赖 human-in-the-loop（工程师审核 STEP 中的特征表达）。
    系统输出的是「建议 STEP」，工程师必须逐条审核后才允许进入阶段 4。

精度继承链（不引入新的精度档位）：
    阶段 1 image_to_3d.precision_tier
    → 阶段 2 feature_extraction.precision_tier
    → 阶段 3 parametric_geometry（本模块全程继承上游告知）

工业级配合面（H7/h6 等，0.01 mm 公差）物理上无法用手机摄影测量达到，
本模块输出的 STEP 文件必须经工程师审核 + CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床。
"""

from __future__ import annotations

# 当前已实现的子模块导出（其余子模块在阶段 3-2/3/4/5 完成后追加）
from app.parametric_geometry.step_store import (
    ParametricGeometryTask,
    ParametricGeometryTaskStatus,
    ReviewedFeatureRef,
    StepReviewStatus,
    TaskStore,
    generate_task_id,
    get_task_store,
)
from app.parametric_geometry.step_disclaimer import (
    StepDisclaimer,
    build_step_disclaimer,
)
from app.parametric_geometry.pipeline import (
    FeaturesLoadError,
    ParametricGeometryError,
    ParametricGeometryPipeline,
    ParametricGeometryResult,
    StepReviewError,
)

__all__ = [
    # 任务存储
    "ParametricGeometryTask",
    "ParametricGeometryTaskStatus",
    "ReviewedFeatureRef",
    "StepReviewStatus",
    "TaskStore",
    "generate_task_id",
    "get_task_store",
    # 精度告知
    "StepDisclaimer",
    "build_step_disclaimer",
    # 流水线
    "ParametricGeometryPipeline",
    "ParametricGeometryResult",
    "ParametricGeometryError",
    "StepReviewError",
    "FeaturesLoadError",
]

from app.parametric_geometry._review_state_machine import (
    ST_PENDING,
    ST_RUNNING,
    ST_STEP_GENERATED,
    ST_REVIEWED,
    ST_SUCCEEDED,
    ST_FAILED,
    ST_CANCELLED,
    can_execute,
    can_review,
    can_finalize,
    all_features_reviewed,
    next_status_after_review,
    is_terminal,
)
