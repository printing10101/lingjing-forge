"""参数化几何输出精度告知机制：构造每次 API 响应必须携带的 step_disclaimer 字段。

设计原则
========
灵境制造的参数化几何输出模块明确告知用户：

1. **STEP 文件是「算法建议的参数化模型」，不是「权威 CAD 模型」**
   mesh → 参数化 CAD 自动转换在工业上未解决（项目记忆硬约束）。
   本模块把阶段 2 已确认特征（confirmed_features.json）转换为 STEP 文件，
   但特征间的拓扑关系（相切、同心、垂直等）可能不准确，
   工程师必须审核每个特征在 STEP 中的表达是否正确。

2. **STEP 引擎表达精度有差异**
   - pythonOCC（OpenCASCADE）：完整 B-rep 表达，工业可信度最高
   - FreeCAD Python API：基于 OpenCASCADE，但接口层有抽象损失
   - 简易模板：仅支持基础平面/圆柱/孔，无法表达复杂拓扑
   系统会给出 engine_used 字段告知实际使用的引擎。

3. **精度继承自上游 mesh + 阶段 2 特征**
   本模块不引入新的精度档位，全程继承上游告知。
   若上游 mesh 未标定（calibrated=False），STEP 文件中的尺寸是无量纲的。

4. **CAM 二次校验强制**
   生成的 STEP 文件必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床。
   系统定位为「工程师助手」，非「全自动生产线」。

step_disclaimer 字段在以下 API 响应中必须出现：
- GET  /api/v1/parametric_geometry/precision_info
- POST /api/v1/parametric_geometry/tasks
- GET  /api/v1/parametric_geometry/tasks/{task_id}
- POST /api/v1/parametric_geometry/tasks/{task_id}/review
- GET  /api/v1/parametric_geometry/tasks/{task_id}/export
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import ParametricGeometryConfig


@dataclass
class StepDisclaimer:
    """参数化几何输出精度告知字段，所有 API 响应必须携带。"""

    # 上游 mesh 是否已做尺度归一化（影响 STEP 文件中所有尺寸单位）
    mesh_calibrated: bool
    # 阶段 2 特征来源（feature_extraction 任务 ID 或 "external_upload"）
    feature_source: str
    # 精度档位（继承自阶段 1/2）
    precision_tier: str
    # 实际使用的 STEP 写入引擎
    engine_used: str
    # 引擎表达精度等级描述
    engine_precision_note: str
    # 是否需要工程师审核 STEP 中的特征表达（始终 True）
    requires_engineer_review: bool
    # 是否需要 CAM 二次校验（始终 True）
    requires_cam_validation: bool
    # 工业生产硬门槛警告
    industrial_hard_gates: list[str]
    # 总警告消息
    warning_message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# 引擎表达精度等级描述
_ENGINE_PRECISION_NOTES = {
    "pythonocc": ("pythonOCC (OpenCASCADE)：完整 B-rep 表达，支持平面/圆柱/孔/凸台/布尔运算，工业可信度最高"),
    "freecad": ("FreeCAD Python API：基于 OpenCASCADE，支持完整 B-rep 但接口层有抽象损失，部分高级特征可能简化"),
    "template": (
        "简易 STEP 模板：仅支持基础平面/圆柱/孔，"
        "无法表达复杂拓扑（相切/同心/垂直等），STEP 可能被 NX/PowerMill 部分拒绝"
    ),
    "unavailable": ("无可用 STEP 引擎：pythonOCC / FreeCAD / 模板均不可用，本任务无法生成 STEP 文件"),
}


def build_step_disclaimer(
    cfg: "ParametricGeometryConfig",
    mesh_calibrated: bool = False,
    feature_source: str = "external_upload",
    precision_tier: str = "standard",
    engine_used: str = "unavailable",
) -> StepDisclaimer:
    """根据当前配置 + 上游状态构造 step_disclaimer。

    Args:
        cfg: ParametricGeometryConfig
        mesh_calibrated: 上游 mesh 是否已做尺度归一化
        feature_source: 阶段 2 特征来源（feature_extraction 任务 ID 或 "external_upload"）
        precision_tier: 精度档位（继承自阶段 1/2）
        engine_used: 实际使用的 STEP 写入引擎（pythonocc / freecad / template / unavailable）

    Returns:
        StepDisclaimer
    """
    # 工业生产硬门槛（与 project_memory 中记录的硬约束一致）
    industrial_hard_gates = [
        "mesh → 参数化 CAD 自动转换工业上未解决：本模块输出「算法建议 STEP」",
        "工程师必须审核每个特征在 STEP 中的表达（confirmed / rejected / edited）",
        "良品率要求：0 缺陷容忍",
        "配合面公差：0.01mm（手机摄影测量物理上不可达）",
        "CNC 操作资质：需持证操作员",
        "导师签字 + 保险：大一独立项目无法独立完成此环节",
        "STEP 文件必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床",
        "本系统定位为「工程师助手」，非「全自动生产线」",
    ]

    # 引擎表达精度说明
    engine_precision_note = _ENGINE_PRECISION_NOTES.get(
        engine_used,
        f"未知引擎: {engine_used}",
    )

    # 警告消息根据 mesh 标定状态 + 引擎拼接
    if engine_used == "unavailable":
        warning_message = (
            "⚠ 无可用 STEP 写入引擎（pythonOCC / FreeCAD / 模板均不可用）。"
            "本任务无法生成 STEP 文件。"
            "修复建议：pip install pythonocc-core 或安装 FreeCAD 并设置 FREECAD_HOME 环境变量。"
        )
    elif not mesh_calibrated:
        warning_message = (
            f"⚠ 上游 mesh 未做尺度归一化（calibrated=False），"
            "STEP 文件中的尺寸为无量纲值，仅可用于可视化，不可用于工艺仿真。"
            f"当前引擎: {engine_used}。"
            "请在阶段 1 拍照重建时放置标定块并触发尺度归一化。"
            "即便 mesh 已标定，工程师仍必须审核每个特征在 STEP 中的表达后才允许进入阶段 4。"
        )
    else:
        warning_message = (
            f"⚠ 上游 mesh 已标定（feature_source={feature_source}），"
            f"STEP 尺寸单位为 mm，但精度受 SfM 噪声影响。"
            f"当前引擎: {engine_used}（{engine_precision_note}）。"
            "工程师必须审核每个特征在 STEP 中的表达（confirmed / rejected / edited），"
            "审核通过的 STEP 仅供阶段 4 切削参数推荐参考。"
            "STEP / G 代码必须经 CAM 软件二次校验后才允许上机床。"
            "工业级配合面（H7/h6 等，0.01mm 公差）物理上不可达。"
        )

    return StepDisclaimer(
        mesh_calibrated=mesh_calibrated,
        feature_source=feature_source,
        precision_tier=precision_tier,
        engine_used=engine_used,
        engine_precision_note=engine_precision_note,
        requires_engineer_review=True,
        requires_cam_validation=True,
        industrial_hard_gates=industrial_hard_gates,
        warning_message=warning_message,
    )
