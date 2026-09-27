"""特征提取精度告知机制：构造每次 API 响应必须携带的 feature_disclaimer 字段。

设计原则
========
灵境制造的特征提取模块明确告知用户：

1. **mesh → 参数化 CAD 自动转换在工业上未解决**
   （项目记忆硬约束：生产系统依赖 human-in-the-loop，工程师必须确认特征）
   本模块输出的是「算法建议的特征列表」，不是「权威参数化模型」。
   工程师必须审核每个特征（confirmed / rejected / edited）后才能进入阶段 3。

2. **特征置信度有上限**
   RANSAC 平面拟合对手机摄影测量 mesh 的置信度通常 0.6-0.9，
   圆柱拟合对噪声敏感，孔检测依赖法向估计。
   系统会给出 confidence 字段，但**不可作为最终工艺依据**。

3. **尺度依赖上游 mesh 是否标定**
   若上游 image_to_3d 模块的 mesh 未做尺度归一化（calibrated=False），
   则本模块输出的 radius_mm / height_mm 等参数是无量纲的，仅可用于可视化。

4. **CAM 二次校验强制**
   本模块输出的特征列表经工程师审核后，仅供阶段 3 参数化 STEP 生成参考。
   生成的 STEP / G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床。

feature_disclaimer 字段在以下 API 响应中必须出现：
- POST /api/v1/feature_extraction/tasks（创建任务）
- GET  /api/v1/feature_extraction/tasks/{task_id}（查询状态）
- POST /api/v1/feature_extraction/tasks/{task_id}/review（工程师审核）
- GET  /api/v1/feature_extraction/tasks/{task_id}/export（导出已确认特征）
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import FeatureExtractionConfig


@dataclass
class FeatureDisclaimer:
    """特征提取精度告知字段，所有 API 响应必须携带。"""

    # 上游 mesh 是否已做尺度归一化（影响所有几何参数单位）
    mesh_calibrated: bool
    # 上游 mesh 来源（image_to_3d 任务 ID 或 "external_upload"）
    mesh_source: str
    # 提取方法
    extraction_method: str
    # 预期置信度范围（参考值）
    expected_confidence_range: str
    # 是否需要工程师审核（始终 True，符合项目记忆硬约束）
    requires_engineer_review: bool
    # 是否需要 CAM 二次校验（始终 True）
    requires_cam_validation: bool
    # 工业生产硬门槛警告
    industrial_hard_gates: list[str]
    # 总警告消息
    warning_message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_feature_disclaimer(
    cfg: "FeatureExtractionConfig",
    mesh_calibrated: bool = False,
    mesh_source: str = "external_upload",
) -> FeatureDisclaimer:
    """根据当前配置 + mesh 标定状态构造 feature_disclaimer。

    Args:
        cfg: FeatureExtractionConfig
        mesh_calibrated: 上游 mesh 是否已做尺度归一化
        mesh_source: mesh 来源标识（image_to_3d 任务 ID 或 "external_upload"）

    Returns:
        FeatureDisclaimer
    """
    # 工业生产硬门槛（与 project_memory 中记录的硬约束一致）
    industrial_hard_gates = [
        "mesh → 参数化 CAD 自动转换工业上未解决：本模块输出「算法建议特征」",
        "工程师必须审核每个特征（confirmed / rejected / edited）后才允许进入阶段 3",
        "mesh 尺度归一化依赖标定块：未标定（calibrated=False）时输出无量纲值，不可用于工艺仿真",
        "良品率要求：0 缺陷容忍",
        "配合面公差：0.01mm（手机摄影测量物理上不可达）",
        "CNC 操作资质：需持证操作员",
        "导师签字 + 保险：大一独立项目无法独立完成此环节",
        "G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验",
        "本系统定位为「工程师助手」，非「全自动生产线」",
    ]

    # 提取方法描述
    extraction_method = (
        f"RANSAC 平面拟合 (threshold={cfg.plane_ransac_threshold_mm}mm, "
        f"min_inliers={cfg.plane_min_inliers}) + "
        f"圆柱拟合 (radius_range={cfg.cylinder_min_radius_mm}-"
        f"{cfg.cylinder_max_radius_mm}mm) + "
        f"孔检测 (min_radius={cfg.hole_min_radius_mm}mm)"
    )

    # 预期置信度范围（基于经验值）
    expected_confidence_range = "0.60-0.95（RANSAC inlier 比例，仅供参考）"

    # 警告消息根据 mesh 标定状态拼接
    if not mesh_calibrated:
        warning_message = (
            "⚠ 上游 mesh 未做尺度归一化（calibrated=False），"
            "本模块输出的 radius_mm / height_mm 等参数为无量纲值，"
            "仅可用于可视化，不可用于工艺仿真。"
            "请在阶段 1 拍照重建时放置标定块并触发尺度归一化。"
            "即便 mesh 已标定，工程师仍必须审核每个特征后才能进入阶段 3。"
        )
    else:
        warning_message = (
            f"⚠ 上游 mesh 已标定（mesh_source={mesh_source}），"
            "几何参数单位为 mm，但精度受 SfM 噪声影响。"
            "工程师必须审核每个特征（confirmed / rejected / edited），"
            "审核通过的特征集仅供阶段 3 参数化 STEP 生成参考。"
            "生成的 STEP / G 代码必须经 CAM 软件二次校验后才允许上机床。"
            "工业级配合面（H7/h6 等，0.01mm 公差）物理上不可达。"
        )

    return FeatureDisclaimer(
        mesh_calibrated=mesh_calibrated,
        mesh_source=mesh_source,
        extraction_method=extraction_method,
        expected_confidence_range=expected_confidence_range,
        requires_engineer_review=True,
        requires_cam_validation=True,
        industrial_hard_gates=industrial_hard_gates,
        warning_message=warning_message,
    )
