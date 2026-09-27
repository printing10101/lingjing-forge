"""精度告知机制：构造每次 API 响应必须携带的 precision_disclaimer 字段。

设计原则
========
灵境制造的拍照重建链路明确告知用户精度边界：

1. **物理极限**：手机摄影测量最佳精度 0.1-1mm，
   工业级配合面（H7/h6）要求 0.01mm，物理上不可达。
2. **场景适用**：每档精度档位明确列出「适用」与「不适用」场景。
3. **CAM 二次校验**：所有输出 mesh 必须经 CAM 软件（NX/PowerMill/PyCAM）
   二次校验后才允许上机床，本模块输出不直接对接 CNC 控制器。
4. **资质门槛**：物理机床执行需要持证操作员 + 导师签字 + 保险，
   灵境制造作为大一独立项目不参与此环节。

precision_disclaimer 字段在以下 API 响应中必须出现：
- POST /api/v1/image_to_3d/tasks（创建任务）
- GET  /api/v1/image_to_3d/tasks/{task_id}（查询状态）
- GET  /api/v1/image_to_3d/tasks/{task_id}/result（下载结果）
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.config import ImageTo3DConfig


@dataclass
class PrecisionDisclaimer:
    """精度告知字段，所有 API 响应必须携带。"""

    # 当前精度档位
    precision_tier: str
    # 预期精度范围（mm）
    expected_accuracy_mm: str
    # 适用的场景列表
    suitable_for: list[str]
    # 不适用的场景列表
    not_suitable_for: list[str]
    # 是否已用标定块归一化（True=有真实 mm 尺度；False=无量纲）
    calibrated: bool
    # 缩放因子（仅 calibrated=True 时有意义）
    scale_factor: float
    # CAM 校验要求
    requires_cam_validation: bool
    # 工业生产硬门槛警告
    industrial_hard_gates: list[str]
    # 总警告消息
    warning_message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_precision_disclaimer(
    cfg: ImageTo3DConfig,
    calibrated: bool = False,
    scale_factor: float = 1.0,
) -> PrecisionDisclaimer:
    """根据当前配置 + 归一化状态构造 precision_disclaimer。

    Args:
        cfg: ImageTo3DConfig
        calibrated: 是否已用标定块归一化
        scale_factor: 缩放因子（calibrated=True 时填充）

    Returns:
        PrecisionDisclaimer
    """
    specs = cfg.precision_specs

    # 工业生产硬门槛（与 project_memory 中记录的硬约束一致）
    industrial_hard_gates = [
        "良品率要求：0 缺陷容忍",
        "配合面公差：0.01mm（手机摄影测量物理上不可达）",
        "CNC 操作资质：需持证操作员",
        "导师签字 + 保险：大一独立项目无法独立完成此环节",
        "G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验",
        "本系统定位为「工程师助手」，非「全自动生产线」",
    ]

    # 警告消息根据归一化状态拼接
    # ADR-020 思路 2：part_prior 档位额外告知 VAE 先验依赖
    part_prior_notice = ""
    if cfg.precision_tier == "part_prior":
        part_prior_notice = (
            "【零件专属先验路径】本 mesh 由 COLMAP 稀疏点云 + 预训练 VAE "
            "先验补全生成，精度受 VAE 先验质量与稀疏点云覆盖度双重限制，"
            "可能存在先验补全幻觉（hallucination）。"
        )

    if not calibrated:
        warning_message = (
            f"⚠ 当前精度档位={cfg.precision_tier}，预期精度 "
            f"{specs['expected_accuracy_mm']}mm。"
            "mesh 未做尺度归一化（无量纲），仅可用于可视化，"
            "不允许进入工艺仿真链路。"
            "请放置已知尺寸标定块（如 30mm 量块）后重新触发重建。" + part_prior_notice
        )
    else:
        warning_message = (
            f"⚠ 当前精度档位={cfg.precision_tier}，预期精度 "
            f"{specs['expected_accuracy_mm']}mm，缩放因子 {scale_factor:.4f}。"
            "已用标定块归一化，但尺度精度仍受 SfM 噪声影响。"
            "本 mesh 必须经 CAM 软件二次校验后才允许进入机床加工。"
            "工业级配合面（H7/h6 等，0.01mm 公差）物理上不可达，"
            "请使用三坐标测量机或激光扫描仪做最终检验。" + part_prior_notice
        )

    return PrecisionDisclaimer(
        precision_tier=cfg.precision_tier,
        expected_accuracy_mm=specs["expected_accuracy_mm"],
        suitable_for=list(specs["suitable_for"]),
        not_suitable_for=list(specs["not_suitable_for"]),
        calibrated=calibrated,
        scale_factor=scale_factor,
        requires_cam_validation=True,
        industrial_hard_gates=industrial_hard_gates,
        warning_message=warning_message,
    )
