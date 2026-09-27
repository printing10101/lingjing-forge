"""颤振预测精度告知机制（阶段 5）。

设计原则
========
与阶段 2/3/4 一致，所有 API 响应必须携带 chatter_disclaimer 字段，
强制前端展示工业硬门槛。阶段 5 的精度告知需额外强调：
- 预测方法可信度（解析法工程可用，LTC 神经网络为实验性）
- HRC52 材料 pending_calibration 时强制降低置信度
- 极限切深为「理论值」，实际加工必须留安全裕度
- 输出的 ChatterReport 仅供阶段 6 G 代码生成参考，不可直接用于机床
- K_s（cutting_force_coeff）直接来自阶段 4，不二次拟合（项目记忆硬约束）

工业硬约束（项目记忆）：
- 颤振预测必须经工程师审核 + CAM 软件二次校验后才允许上机床
- 系统定位「工程师助手」，非「全自动颤振预测器」
- 0 缺陷良品率 / 0.01mm 配合面公差 / CNC 持证操作员 / 导师签字 + 保险
- chatter_model.pt 不存在时自动回退到 Tlusty 解析法
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# 8 条工业硬门槛


INDUSTRIAL_HARD_GATES: list[str] = [
    "颤振预测基于 Tlusty 解析法 + LTC 神经网络（实验性），稳定性判断必须经工程师审核",
    "良品率要求 0 缺陷容忍，极限切深为理论值，实际加工必须留 20% 安全裕度",
    "工业级配合面公差 0.01mm，颤振预测无法直接达到，需精加工工序",
    "CNC 机床操作需持证操作员，本系统输出的预测结果仅供工艺参考",
    "实际加工需导师签字 + 保险，大一独立项目不可独立完成机床执行环节",
    "CAM 二次校验强制：生成的切削参数必须经 NX/PowerMill/PyCAM 校验后才允许上机床",
    "系统定位「工程师助手」，非「全自动颤振预测器」，最终决策权在工程师",
    "LTC 神经网络路径为实验性，chatter_model.pt 不存在时自动回退到 Tlusty 解析法",
]


# ChatterDisclaimer 数据类


@dataclass
class ChatterDisclaimer:
    """颤振预测精度告知。

    所有 API 响应必须携带此字段（通过 build_chatter_disclaimer() 构造）。
    """

    mesh_calibrated: bool
    chatter_params_source: str  # 阶段 4 ChatterParams JSON 路径
    material_id: str
    material_calibration_status: str  # calibrated / pending_calibration
    precision_tier: str
    machine_type: str
    prediction_method: str  # analytical / neural_network / mixed
    ltc_model_available: bool  # chatter_model.pt 是否存在
    ltc_active_ratio: float  # LTC 实际参与预测的特征比例 [0, 1]
    requires_engineer_review: bool = True
    requires_cam_validation: bool = True
    chatter_report_ready: bool = False  # 是否已输出 ChatterReport 给阶段 6
    industrial_hard_gates: list[str] = field(default_factory=lambda: list(INDUSTRIAL_HARD_GATES))
    warning_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mesh_calibrated": self.mesh_calibrated,
            "chatter_params_source": self.chatter_params_source,
            "material_id": self.material_id,
            "material_calibration_status": self.material_calibration_status,
            "precision_tier": self.precision_tier,
            "machine_type": self.machine_type,
            "prediction_method": self.prediction_method,
            "ltc_model_available": self.ltc_model_available,
            "ltc_active_ratio": self.ltc_active_ratio,
            "requires_engineer_review": self.requires_engineer_review,
            "requires_cam_validation": self.requires_cam_validation,
            "chatter_report_ready": self.chatter_report_ready,
            "industrial_hard_gates": list(self.industrial_hard_gates),
            "warning_message": self.warning_message,
        }


# 工厂函数


def build_chatter_disclaimer(
    mesh_calibrated: bool,
    chatter_params_source: str,
    material_id: str,
    material_calibration_status: str,
    precision_tier: str,
    machine_type: str,
    prediction_method: str,
    ltc_model_available: bool,
    ltc_active_ratio: float,
    chatter_report_ready: bool = False,
) -> ChatterDisclaimer:
    """构造 ChatterDisclaimer。

    根据 mesh 标定状态 + 材料校准状态 + LTC 可用性动态拼接 warning_message。
    """
    parts: list[str] = []

    if not mesh_calibrated:
        parts.append("上游 mesh 未标定，特征参数无量纲，颤振预测结果仅供参考")
    else:
        parts.append("上游 mesh 已标定，但颤振预测仍受 SfM 噪声与机床模态参数不确定性影响")

    if material_calibration_status == "pending_calibration":
        parts.append(f"材料 {material_id} 数据待自采工业数据校准，K_s 为工程估算值，极限切深预测置信度已强制降低")

    if not ltc_model_available:
        parts.append("LTC 神经网络模型不可用（chatter_model.pt 不存在），全部走 Tlusty 解析法")
    elif ltc_active_ratio < 1.0:
        parts.append(f"LTC 神经网络仅对 {ltc_active_ratio * 100:.0f}% 特征生效，其余走 Tlusty 解析法")
    else:
        parts.append("LTC 神经网络对全部特征生效（实验性路径）")

    if prediction_method == "analytical":
        parts.append("预测方法为 Tlusty 解析法（工程可用，默认路径）")
    elif prediction_method == "neural_network":
        parts.append("预测方法为 LTC 神经网络（实验性，需工程师重点审核）")
    elif prediction_method == "mixed":
        parts.append("预测方法为解析法 + 神经网络混合（需工程师逐条审核方法标记）")
    elif prediction_method == "fallback":
        parts.append("预测方法为兜底默认值（解析法与神经网络均失败，结果不可信）")

    if not chatter_report_ready:
        parts.append("ChatterReport 尚未输出，阶段 6 G 代码生成不可用")
    else:
        parts.append("ChatterReport 已输出，供阶段 6 G 代码生成参考")

    parts.append("颤振预测必须经工程师审核 + CAM 软件二次校验后才允许上机床")

    warning_message = "；".join(parts) + "。"

    return ChatterDisclaimer(
        mesh_calibrated=mesh_calibrated,
        chatter_params_source=chatter_params_source,
        material_id=material_id,
        material_calibration_status=material_calibration_status,
        precision_tier=precision_tier,
        machine_type=machine_type,
        prediction_method=prediction_method,
        ltc_model_available=ltc_model_available,
        ltc_active_ratio=ltc_active_ratio,
        requires_engineer_review=True,
        requires_cam_validation=True,
        chatter_report_ready=chatter_report_ready,
        industrial_hard_gates=list(INDUSTRIAL_HARD_GATES),
        warning_message=warning_message,
    )
