"""切削参数推荐精度告知机制。

设计原则
========
与阶段 2/3 一致，所有 API 响应必须携带 cutting_disclaimer 字段，强制前端展示工业硬门槛。
阶段 4 的精度告知需额外强调：
- 材料数据可信度（HRC52 为 pending_calibration，需自采数据校准）
- 切削参数为「算法推荐」，非「最优解」，工程师必须审核
- 输出的 ChatterParams 仅供阶段 5 颤振预测参考，不可直接用于机床
- K_s（specific_cutting_force）按材料校准，影响颤振预测精度

工业硬约束（项目记忆）：
- 切削参数必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床
- 系统定位「工程师助手」，非「全自动切削参数生成器」
- 0 缺陷良品率 / 0.01mm 配合面公差 / CNC 持证操作员 / 导师签字 + 保险
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# 8 条工业硬门槛


INDUSTRIAL_HARD_GATES: list[str] = [
    "mesh → 参数化 CAD 自动转换工业上未解决，切削参数基于特征参数推导，工程师必须审核",
    "良品率要求 0 缺陷容忍，切削参数试切前必须经 CAM 软件模拟验证",
    "工业级配合面公差 0.01mm，手机摄影测量 + 切削参数推荐无法直接达到，需精加工工序",
    "CNC 机床操作需持证操作员，本系统输出的参数仅供工艺参考",
    "实际加工需导师签字 + 保险，大一独立项目不可独立完成机床执行环节",
    "CAM 二次校验强制：生成的切削参数必须经 NX/PowerMill/PyCAM 校验后才允许上机床",
    "系统定位「工程师助手」，非「全自动切削参数生成器」，最终决策权在工程师",
    "材料 K_s（specific_cutting_force）影响颤振预测精度，HRC52 数据待自采校准",
]


# CuttingDisclaimer 数据类


@dataclass
class CuttingDisclaimer:
    """切削参数推荐精度告知。

    所有 API 响应必须携带此字段（通过 build_cutting_disclaimer() 构造）。
    """

    mesh_calibrated: bool
    feature_source: str
    step_source: str
    material_id: str
    material_calibration_status: str  # calibrated / pending_calibration
    precision_tier: str
    machine_type: str
    tool_diameter_mm: float
    requires_engineer_review: bool = True
    requires_cam_validation: bool = True
    chatter_params_ready: bool = False  # 是否已输出 ChatterParams 给阶段 5
    industrial_hard_gates: list[str] = field(default_factory=lambda: list(INDUSTRIAL_HARD_GATES))
    warning_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mesh_calibrated": self.mesh_calibrated,
            "feature_source": self.feature_source,
            "step_source": self.step_source,
            "material_id": self.material_id,
            "material_calibration_status": self.material_calibration_status,
            "precision_tier": self.precision_tier,
            "machine_type": self.machine_type,
            "tool_diameter_mm": self.tool_diameter_mm,
            "requires_engineer_review": self.requires_engineer_review,
            "requires_cam_validation": self.requires_cam_validation,
            "chatter_params_ready": self.chatter_params_ready,
            "industrial_hard_gates": list(self.industrial_hard_gates),
            "warning_message": self.warning_message,
        }


# 工厂函数


def build_cutting_disclaimer(
    mesh_calibrated: bool,
    feature_source: str,
    step_source: str,
    material_id: str,
    material_calibration_status: str,
    precision_tier: str,
    machine_type: str,
    tool_diameter_mm: float,
    chatter_params_ready: bool = False,
) -> CuttingDisclaimer:
    """构造 CuttingDisclaimer。

    根据 mesh 标定状态 + 材料校准状态动态拼接 warning_message。
    """
    parts: list[str] = []

    if not mesh_calibrated:
        parts.append("上游 mesh 未标定，特征参数无量纲，切削参数仅供参考")
    else:
        parts.append("上游 mesh 已标定，但切削参数仍受 SfM 噪声影响")

    if material_calibration_status == "pending_calibration":
        parts.append(f"材料 {material_id} 数据待自采工业数据校准，参数为工程估算值")

    if not chatter_params_ready:
        parts.append("ChatterParams 尚未输出，阶段 5 颤振预测不可用")
    else:
        parts.append("ChatterParams 已输出，供阶段 5 颤振预测参考")

    parts.append("切削参数必须经工程师审核 + CAM 软件二次校验后才允许上机床")

    warning_message = "；".join(parts) + "。"

    return CuttingDisclaimer(
        mesh_calibrated=mesh_calibrated,
        feature_source=feature_source,
        step_source=step_source,
        material_id=material_id,
        material_calibration_status=material_calibration_status,
        precision_tier=precision_tier,
        machine_type=machine_type,
        tool_diameter_mm=tool_diameter_mm,
        requires_engineer_review=True,
        requires_cam_validation=True,
        chatter_params_ready=chatter_params_ready,
        industrial_hard_gates=list(INDUSTRIAL_HARD_GATES),
        warning_message=warning_message,
    )
