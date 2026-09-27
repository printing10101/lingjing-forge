"""切削力 PINN 仿真模块。

提供基于物理约束神经网络 (PINN) 的切削力预测能力，
结合 Kienzle 解析公式与残差学习网络。

torch 为软依赖：桌面 MVP 打包时排除 torch，此时仅 Kienzle 解析公式可用，
PINN 模型与基于 torch 的预测接口不可用。

子模块:
    - kienzle: Kienzle 切削力解析计算（无 torch 依赖）
    - pinn: PINN 模型架构与损失函数（依赖 torch）
    - trainer: 模型训练逻辑（依赖 torch）
    - predictor: 推理接口（依赖 torch）
"""

from __future__ import annotations

from typing import Any

from app.simulation.cutting_force.kienzle import (
    KienzleParams,
    compute_cutting_force_fz,
    compute_cutting_forces,
    compute_specific_cutting_force,
    get_kienzle_coefficients,
    DEFAULT_MATERIAL_COEFFICIENTS,
)
from app.simulation.cutting_force.adaptive_milling import (
    AdaptiveMillingParams,
    AdaptiveMillingResult,
    AdaptiveMillingSolver,
    SegmentSolution,
    DEFAULT_TARGET_FORCE_N,
    DEFAULT_MAX_AXIAL_DEPTH_MM,
    DEFAULT_MIN_AXIAL_DEPTH_MM,
    DEFAULT_MAX_FZ_MM,
    DEFAULT_MIN_FZ_MM,
    DEFAULT_MAX_FEED_MM_PER_MIN,
)

# pinn / predictor / trainer 依赖 torch，桌面 MVP 排除 torch 时软降级
predict_cutting_force: Any
predict_cutting_force_batch: Any
try:
    from app.simulation.cutting_force.pinn import (
        CuttingForcePINN,
        PINNLoss,
        ResidualBlock,
    )
    from app.simulation.cutting_force.predictor import (
        predict_cutting_force as predict_cutting_force,
        predict_cutting_force_batch as predict_cutting_force_batch,
    )

    _HAS_TORCH = True
except ImportError:
    CuttingForcePINN = None  # type: ignore[assignment,misc]
    PINNLoss = None  # type: ignore[assignment,misc]
    ResidualBlock = None  # type: ignore[assignment,misc]
    predict_cutting_force = None
    predict_cutting_force_batch = None
    _HAS_TORCH = False

__all__ = [
    "KienzleParams",
    "compute_cutting_force_fz",
    "compute_cutting_forces",
    "compute_specific_cutting_force",
    "get_kienzle_coefficients",
    "DEFAULT_MATERIAL_COEFFICIENTS",
    "CuttingForcePINN",
    "PINNLoss",
    "ResidualBlock",
    "predict_cutting_force",
    "predict_cutting_force_batch",
    "_HAS_TORCH",
    # Adaptive Milling
    "AdaptiveMillingParams",
    "AdaptiveMillingResult",
    "AdaptiveMillingSolver",
    "SegmentSolution",
    "DEFAULT_TARGET_FORCE_N",
    "DEFAULT_MAX_AXIAL_DEPTH_MM",
    "DEFAULT_MIN_AXIAL_DEPTH_MM",
    "DEFAULT_MAX_FZ_MM",
    "DEFAULT_MIN_FZ_MM",
    "DEFAULT_MAX_FEED_MM_PER_MIN",
]
