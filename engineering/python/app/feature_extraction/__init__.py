"""几何特征辅助提取模块。

将阶段 1 拍照重建输出的 mesh（PLY/STL/GLB）转换为「算法建议的几何特征列表」，
供工程师审核后进入阶段 3 参数化 STEP 生成。

核心 pipeline：
    mesh (PLY/STL) → RANSAC 平面拟合 → 候选平面集
                  → 圆柱拟合 → 候选圆柱集
                  → 孔/凸台检测 → 候选孔/凸台集
                  → 工程师审核（confirmed / rejected / edited）
                  → 导出已确认特征列表（JSON）

定位声明（项目记忆硬约束）：
    本模块是「工程师助手」，不是「全自动参数化 CAD 生成器」。
    mesh → 参数化 CAD 自动转换在工业上未解决，
    生产系统依赖 human-in-the-loop（工程师确认特征）。
    系统输出的是「建议」，工程师必须逐条审核。

精度档位（继承自上游 image_to_3d 模块）：
    coarse  : 0.5-2.0 mm，特征参数误差较大，仅可用于工艺理解
    standard: 0.1-1.0 mm，非配合面特征可用，配合面仍不可达
    high    : 0.1-0.5 mm，小零件细节特征可用，仍达不到工业级配合面公差

工业级配合面（H7/h6 等，0.01 mm 公差）物理上无法用手机摄影测量达到，
本模块输出的特征参数必须经工程师审核 + CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床。
"""

from __future__ import annotations

# 当前已实现的子模块导出（其余子模块在阶段 2-2/3/4/5 完成后追加）
from app.feature_extraction.feature_store import (
    ExtractedFeature,
    FeatureExtractionTask,
    FeatureExtractionTaskStatus,
    FeatureReviewStatus,
    FeatureStore,
    FeatureType,
    get_feature_store,
)
from app.feature_extraction.precision_disclaimer import (
    FeatureDisclaimer,
    build_feature_disclaimer,
)
from app.feature_extraction.plane_extractor import PlaneExtractor, PlaneExtractionResult
from app.feature_extraction.cylinder_extractor import (
    CylinderExtractor,
    CylinderExtractionResult,
)
from app.feature_extraction.hole_detector import HoleDetector, HoleDetectionResult
from app.feature_extraction._feature_classifier import (
    FEATURE_PLANE,
    FEATURE_CYLINDER,
    FEATURE_HOLE,
    FEATURE_BOSS,
    FEATURE_UNKNOWN,
    ACTION_CONFIRMED,
    ACTION_REJECTED,
    ACTION_EDITED,
    FeatureClassificationError,
    classify_hole_or_boss,
    classify_hole_or_boss_deep,
    is_known_feature_type,
    is_valid_review_action,
    validate_feature_params,
    validate_offset,
    validate_threshold,
)
from app.feature_extraction._review_state_machine import (
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_FEATURES_EXTRACTED,
    STATUS_REVIEWED,
    STATUS_SUCCEEDED,
    STATUS_FAILED,
    STATUS_CANCELLED,
    FeatureReviewStateMachine,
    ReviewStateMachineError,
)
from app.feature_extraction.pipeline import (
    FeatureExtractionError,
    FeatureExtractionPipeline,
    FeatureExtractionResult,
    FeatureReviewError,
    MeshLoadError,
)

__all__ = [
    # 特征存储
    "ExtractedFeature",
    "FeatureExtractionTask",
    "FeatureExtractionTaskStatus",
    "FeatureReviewStatus",
    "FeatureStore",
    "FeatureType",
    "get_feature_store",
    # 精度告知
    "FeatureDisclaimer",
    "build_feature_disclaimer",
    # 提取器
    "PlaneExtractor",
    "PlaneExtractionResult",
    "CylinderExtractor",
    "CylinderExtractionResult",
    "HoleDetector",
    "HoleDetectionResult",
    # 特征分类判定（纯 Python 白盒逻辑）
    "FEATURE_PLANE",
    "FEATURE_CYLINDER",
    "FEATURE_HOLE",
    "FEATURE_BOSS",
    "FEATURE_UNKNOWN",
    "ACTION_CONFIRMED",
    "ACTION_REJECTED",
    "ACTION_EDITED",
    "FeatureClassificationError",
    "classify_hole_or_boss",
    "classify_hole_or_boss_deep",
    "is_known_feature_type",
    "is_valid_review_action",
    "validate_feature_params",
    "validate_offset",
    "validate_threshold",
    # 审核状态机（纯 Python 白盒逻辑）
    "STATUS_PENDING",
    "STATUS_RUNNING",
    "STATUS_FEATURES_EXTRACTED",
    "STATUS_REVIEWED",
    "STATUS_SUCCEEDED",
    "STATUS_FAILED",
    "STATUS_CANCELLED",
    "FeatureReviewStateMachine",
    "ReviewStateMachineError",
    # 编排器
    "FeatureExtractionPipeline",
    "FeatureExtractionResult",
    "FeatureExtractionError",
    "FeatureReviewError",
    "MeshLoadError",
]
