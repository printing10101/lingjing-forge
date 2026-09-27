"""CAM 校验告知 + 工业硬门槛（阶段 7）。

设计原则
========
与阶段 2/3/4/5/6 一致，所有 API 响应必须携带 cam_disclaimer 字段，
强制前端展示工业硬门槛。阶段 7 的告知文本需额外强调：
- 内部预校验（CollisionDetector）是 AABB 快速预筛，**不可替代** CAM 软件二次校验
  （无法检测刀轨几何精度 / 切削力 / 机床运动学 / 后处理器语法兼容性）
- G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后方可上机床
- 系统绝不直接接口 CNC 控制器，阶段 7 产物终止于「CAM 校验报告 JSON」
- CAM 软件不可用时自动降级到「手动校验流程」模式（系统定位「工程师助手」）
- cam_validation_required 始终 True，不可由环境变量关闭
- SUCCEEDED 状态禁止删除（CAM 校验报告是链路最终产物）
- HRC52 材料 pending_calibration 时置信度已降至 0.5（继承阶段 5）
- 复用现有 app.simulation.collision_detector.CollisionDetector + toolpath_parser.ToolpathParser

工业硬约束（项目记忆）：
- 阶段 7 产物终止于「CAM 校验报告 JSON」，不触及物理机床
- 系统定位「工程师助手」，非「全自动 CAM 仿真器」
- 0 缺陷良品率 / 0.01mm 配合面公差 / CNC 持证操作员 / 导师签字 + 保险
- 大一独立项目不可独立完成机床执行环节
- SUCCEEDED 状态禁止删除（阶段 7 CAM 校验报告是链路最终产物，供审计追溯）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# 10 条工业硬门槛（阶段 7 版本）


INDUSTRIAL_HARD_GATES: list[str] = [
    "复用现有 app.simulation.collision_detector.CollisionDetector + toolpath_parser.ToolpathParser，不重写",
    "内部预校验（CollisionDetector）是 AABB 包围盒级别快速预筛，不可替代 CAM 软件二次校验"
    "（无法检测刀轨几何精度 / 切削力 / 机床运动学 / 后处理器语法兼容性）",
    "G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后方可上机床，系统绝不直接接口 CNC 控制器",
    "阶段 7 产物终止于「CAM 校验报告 JSON」，物理机床执行由人工 + CAM 软件 + 持证操作员完成",
    "cam_validation_required 始终 True，不可由环境变量关闭（项目记忆硬约束）",
    "CAM 软件不可用时自动降级到「手动校验流程」模式（生成校验清单 + 工程师回填）",
    "HRC52 材料 pending_calibration 时置信度强制降至 0.5（继承阶段 5，阶段 7 仅体现在告知文本）",
    "良品率要求 0 缺陷容忍，工业级配合面公差 0.01mm，需精加工工序",
    "CNC 机床操作需持证操作员 + 导师签字 + 保险，大一独立项目不可独立完成机床执行",
    "SUCCEEDED 状态禁止删除，CAM 校验报告 JSON 是阶段 7 链路最终产物，需保留供审计追溯",
]


# CamDisclaimer 数据类


@dataclass
class CamDisclaimer:
    """CAM 校验精度告知。

    所有 API 响应必须携带此字段（通过 build_cam_disclaimer() 构造）。
    告知文本明确标注：
    - 内部预校验局限（CollisionDetector 不可替代 CAM 软件二次校验）
    - CAM 校验强制（G 代码必须经 NX/PowerMill/PyCAM 二次校验）
    - CAM 后端策略（实际使用的后端 + 降级原因）
    - 物理机床执行限制（阶段 7 产物终止于 JSON）

    Attributes:
        precision_tier: 精度档位（继承自阶段 1 image_to_3d）
        controller_type: 目标控制器类型（fanuc_0i / siemens_840d / heidenhain_tnc / xmachine_xm100）
        material_name: 材料名（继承自阶段 4/5/6）
        material_calibration_status: 材料校准状态（calibrated / pending_calibration）
        gcode_report_source: 阶段 6 G 代码审核记录 JSON 路径（追溯上游）
        gcode_file_source: 阶段 6 G 代码文件路径
        prediction_method: 阶段 5 预测方法（analytical / neural_network / mixed）
        total_features: 总特征数
        passed_features: 双层校验均通过的特征数
        failed_features: 任一层校验失败的特征数
        pending_calibration: 是否含 HRC52 待校准材料（继承阶段 5/6）
        ltc_experiment_used: 阶段 5 是否使用了 LTC 实验性路径
        cam_backend_used: 实际使用的 CAM 后端（可能因降级与 requested 不同）
        cam_backend_fallback_reason: 降级原因（如 "NX Open executable not configured"）
        cam_backend_requested: 请求的 CAM 后端（来自 CamValidationConfig.default_cam_backend）
        requires_engineer_review: 始终 True（项目记忆硬约束）
        requires_cam_validation: 始终 True（项目记忆硬约束，不可关闭）
        cam_report_exported: 是否已导出 CAM 校验报告 JSON
        industrial_hard_gates: 10 条工业硬门槛列表
        warning_message: 动态拼接的警告信息（永远非空）
    """

    precision_tier: str
    controller_type: str
    material_name: str
    material_calibration_status: str  # calibrated / pending_calibration
    gcode_report_source: str  # 阶段 6 G 代码审核记录 JSON 路径
    gcode_file_source: str  # 阶段 6 G 代码文件路径
    prediction_method: str  # 阶段 5 预测方法（analytical / neural_network / mixed）
    total_features: int
    passed_features: int
    failed_features: int  # 任一层校验失败的特征数
    pending_calibration: bool  # 是否含 HRC52 待校准材料
    ltc_experiment_used: bool  # 阶段 5 是否使用了 LTC 实验性路径
    cam_backend_used: str  # 实际使用的 CAM 后端
    cam_backend_fallback_reason: str  # 降级原因
    cam_backend_requested: str = "internal_only"  # 请求的 CAM 后端
    requires_engineer_review: bool = True
    requires_cam_validation: bool = True  # 始终 True（项目记忆硬约束）
    cam_report_exported: bool = False  # 是否已导出 CAM 校验报告 JSON
    industrial_hard_gates: list[str] = field(default_factory=lambda: list(INDUSTRIAL_HARD_GATES))
    warning_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "precision_tier": self.precision_tier,
            "controller_type": self.controller_type,
            "material_name": self.material_name,
            "material_calibration_status": self.material_calibration_status,
            "gcode_report_source": self.gcode_report_source,
            "gcode_file_source": self.gcode_file_source,
            "prediction_method": self.prediction_method,
            "total_features": self.total_features,
            "passed_features": self.passed_features,
            "failed_features": self.failed_features,
            "pending_calibration": self.pending_calibration,
            "ltc_experiment_used": self.ltc_experiment_used,
            "cam_backend_used": self.cam_backend_used,
            "cam_backend_fallback_reason": self.cam_backend_fallback_reason,
            "cam_backend_requested": self.cam_backend_requested,
            "requires_engineer_review": self.requires_engineer_review,
            "requires_cam_validation": self.requires_cam_validation,
            "cam_report_exported": self.cam_report_exported,
            "industrial_hard_gates": self.industrial_hard_gates,
            "warning_message": self.warning_message,
        }


def build_cam_disclaimer(
    precision_tier: str,
    controller_type: str,
    material_name: str,
    material_calibration_status: str,
    gcode_report_source: str,
    gcode_file_source: str,
    prediction_method: str,
    total_features: int,
    passed_features: int,
    failed_features: int,
    pending_calibration: bool,
    ltc_experiment_used: bool,
    cam_backend_used: str,
    cam_backend_fallback_reason: str,
    cam_backend_requested: str = "internal_only",
    cam_report_exported: bool = False,
) -> CamDisclaimer:
    """构建 CAM 校验精度告知。

    项目记忆硬约束：
    - requires_cam_validation 始终 True，不可由参数关闭
    - requires_engineer_review 始终 True
    - warning_message 永远非空（末尾兜底追加 CAM 校验强制硬门槛）
    """
    warnings: list[str] = []

    # HRC52 待校准材料告知（继承阶段 5）
    if pending_calibration:
        warnings.append("含 HRC52 待校准材料，阶段 5 颤振预测置信度已强制降至 0.5，阶段 7 仅继承告知不二次拟合")

    # 失败特征告知
    if failed_features > 0:
        warnings.append(
            f"含 {failed_features} 个未通过双层校验的特征，需工程师审核（确认 / 拒绝 / 编辑）后才能确认 CAM 校验报告"
        )

    # LTC 实验性路径告知（继承阶段 5）
    if ltc_experiment_used:
        warnings.append("阶段 5 使用了 LTC 神经网络实验性路径，稳定性判断需特别关注（chatter_model.pt 可能未充分训练）")

    # CAM 后端降级告知
    if cam_backend_fallback_reason:
        warnings.append(
            f"CAM 后端已降级：请求 {cam_backend_requested}，"
            f"实际使用 {cam_backend_used}，原因：{cam_backend_fallback_reason}"
        )

    # internal_only 后端告知（仅内部预校验，未执行 CAM 软件二次校验）
    if cam_backend_used == "internal_only":
        warnings.append("本次仅执行内部预校验（CollisionDetector），未执行 CAM 软件二次校验，G 代码不可直接上机床")

    # manual 后端告知（手动校验流程）
    if cam_backend_used == "manual":
        warnings.append(
            "已降级到「手动校验流程」模式：系统生成校验清单，工程师需手动加载 G 代码到 CAM 软件并回填校验结果"
        )

    # 工业硬门槛兜底（项目记忆硬约束）：即使上述特定警告均未触发，
    # CAM 校验强制 + 不直接接口 CNC 控制器 仍然是不可绕过的工业硬门槛——
    # 系统定位「工程师助手」，非「全自动 CAM 仿真器」，所有 G 代码必须经
    # NX/PowerMill/PyCAM 二次校验后方可上机床，阶段 7 产物终止于「CAM 校验报告 JSON」。
    # 这保证 warning_message 永远非空，符合 disclaimer「强制前端展示工业硬门槛」的设计原则。
    warnings.append(
        "G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后方可上机床，"
        "系统绝不直接接口 CNC 控制器，阶段 7 产物终止于 CAM 校验报告 JSON"
    )
    warning_message = "; ".join(warnings)

    return CamDisclaimer(
        precision_tier=precision_tier,
        controller_type=controller_type,
        material_name=material_name,
        material_calibration_status=material_calibration_status,
        gcode_report_source=gcode_report_source,
        gcode_file_source=gcode_file_source,
        prediction_method=prediction_method,
        total_features=total_features,
        passed_features=passed_features,
        failed_features=failed_features,
        pending_calibration=pending_calibration,
        ltc_experiment_used=ltc_experiment_used,
        cam_backend_used=cam_backend_used,
        cam_backend_fallback_reason=cam_backend_fallback_reason,
        cam_backend_requested=cam_backend_requested,
        requires_engineer_review=True,
        requires_cam_validation=True,  # 项目记忆硬约束：始终 True
        cam_report_exported=cam_report_exported,
        warning_message=warning_message,
    )


__all__ = [
    "INDUSTRIAL_HARD_GATES",
    "CamDisclaimer",
    "build_cam_disclaimer",
]
