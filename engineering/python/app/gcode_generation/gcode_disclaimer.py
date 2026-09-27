"""G 代码生成精度告知机制（阶段 6）。

设计原则
========
与阶段 2/3/4/5 一致，所有 API 响应必须携带 gcode_disclaimer 字段，
强制前端展示工业硬门槛。阶段 6 的精度告知需额外强调：
- 生成的 G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后方可上机
- 系统绝不直接接口 CNC 控制器
- stable == False 的特征禁止生成 G 代码
- SAFETY_MARGIN_RATIO=0.8，实际切深超过极限切深 80% 时发出警告
- HRC52 材料 pending_calibration 时置信度强制降至 0.5（继承阶段 5）
- 复用现有 app.postprocessor 包 + GCodeGenerator，不重写

工业硬约束（项目记忆）：
- G 代码生成必须经工程师审核 + CAM 软件二次校验后才允许上机床
- 系统定位「工程师助手」，非「全自动 G 代码生成器」
- 0 缺陷良品率 / 0.01mm 配合面公差 / CNC 持证操作员 / 导师签字 + 保险
- 大一独立项目不可独立完成机床执行环节
- SUCCEEDED 状态禁止删除（阶段 7 CAM 校验可能已引用 G 代码产物）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# 9 条工业硬门槛


INDUSTRIAL_HARD_GATES: list[str] = [
    "G 代码基于现有 app.postprocessor 包 + GCodeGenerator 生成，复用 212 个测试用例",
    "生成的 G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床",
    "系统绝不直接接口 CNC 控制器，G 代码文件需手动加载到 CAM 软件",
    "stable == False 的特征禁止生成 G 代码，强制工程师审核降低切深或主轴转速",
    "SAFETY_MARGIN_RATIO=0.8，实际切深超过极限切深 80% 时在 warnings 中标注",
    "HRC52 材料 pending_calibration 时置信度强制降至 0.5（继承阶段 5）",
    "良品率要求 0 缺陷容忍，工业级配合面公差 0.01mm，需精加工工序",
    "CNC 机床操作需持证操作员 + 导师签字 + 保险，大一独立项目不可独立完成机床执行",
    "SUCCEEDED 状态禁止删除，阶段 7 CAM 校验可能已引用 G 代码产物",
]


# GCodeDisclaimer 数据类


@dataclass
class GCodeDisclaimer:
    """G 代码生成精度告知。

    所有 API 响应必须携带此字段（通过 build_gcode_disclaimer() 构造）。
    """

    precision_tier: str
    controller_type: str
    material_name: str
    material_calibration_status: str  # calibrated / pending_calibration
    chatter_report_source: str  # 阶段 5 ChatterReport JSON 路径
    operation_plan_source: str  # 阶段 3 OperationPlan JSON 路径
    prediction_method: str  # 阶段 5 预测方法（analytical / neural_network / mixed）
    total_features: int
    stable_features: int
    unstable_features: int  # stable == False 的特征数
    pending_calibration: bool  # 是否含 HRC52 待校准材料
    ltc_experiment_used: bool  # 阶段 5 是否使用了 LTC 实验性路径
    requires_engineer_review: bool = True
    requires_cam_validation: bool = True  # 始终 True（项目记忆硬约束）
    gcode_file_exported: bool = False  # 是否已导出 G 代码文件
    industrial_hard_gates: list[str] = field(default_factory=lambda: list(INDUSTRIAL_HARD_GATES))
    warning_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "precision_tier": self.precision_tier,
            "controller_type": self.controller_type,
            "material_name": self.material_name,
            "material_calibration_status": self.material_calibration_status,
            "chatter_report_source": self.chatter_report_source,
            "operation_plan_source": self.operation_plan_source,
            "prediction_method": self.prediction_method,
            "total_features": self.total_features,
            "stable_features": self.stable_features,
            "unstable_features": self.unstable_features,
            "pending_calibration": self.pending_calibration,
            "ltc_experiment_used": self.ltc_experiment_used,
            "requires_engineer_review": self.requires_engineer_review,
            "requires_cam_validation": self.requires_cam_validation,
            "gcode_file_exported": self.gcode_file_exported,
            "industrial_hard_gates": self.industrial_hard_gates,
            "warning_message": self.warning_message,
        }


def build_gcode_disclaimer(
    precision_tier: str,
    controller_type: str,
    material_name: str,
    material_calibration_status: str,
    chatter_report_source: str,
    operation_plan_source: str,
    prediction_method: str,
    total_features: int,
    stable_features: int,
    unstable_features: int,
    pending_calibration: bool,
    ltc_experiment_used: bool,
    gcode_file_exported: bool = False,
) -> GCodeDisclaimer:
    """构建 G 代码生成精度告知。

    项目记忆硬约束：requires_cam_validation 始终 True，不可由参数关闭。
    """
    # 构建警告信息
    warnings: list[str] = []
    if pending_calibration:
        warnings.append("含 HRC52 待校准材料，置信度已强制降至 0.5（继承阶段 5）")
    if unstable_features > 0:
        warnings.append(
            f"含 {unstable_features} 个不稳定特征，已禁止生成 G 代码，需工程师审核降低切深或主轴转速后重新生成"
        )
    if ltc_experiment_used:
        warnings.append("阶段 5 使用了 LTC 神经网络实验性路径，稳定性判断需特别关注（chatter_model.pt 可能未充分训练）")
    # 工业硬门槛兜底（项目记忆硬约束）：即使上述特定警告均未触发，
    # CAM 二次校验仍然是不可绕过的工业硬门槛——系统定位「工程师助手」，
    # 非「全自动 G 代码生成器」，所有产物必须经 NX/PowerMill/PyCAM 二次校验后方可上机床。
    # 这保证 warning_message 永远非空，符合 disclaimer「强制前端展示工业硬门槛」的设计原则。
    warnings.append("G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后方可上机床，系统绝不直接接口 CNC 控制器")
    warning_message = "; ".join(warnings)

    return GCodeDisclaimer(
        precision_tier=precision_tier,
        controller_type=controller_type,
        material_name=material_name,
        material_calibration_status=material_calibration_status,
        chatter_report_source=chatter_report_source,
        operation_plan_source=operation_plan_source,
        prediction_method=prediction_method,
        total_features=total_features,
        stable_features=stable_features,
        unstable_features=unstable_features,
        pending_calibration=pending_calibration,
        ltc_experiment_used=ltc_experiment_used,
        requires_engineer_review=True,
        requires_cam_validation=True,  # 项目记忆硬约束：始终 True
        gcode_file_exported=gcode_file_exported,
        warning_message=warning_message,
    )


__all__ = [
    "INDUSTRIAL_HARD_GATES",
    "GCodeDisclaimer",
    "build_gcode_disclaimer",
]
