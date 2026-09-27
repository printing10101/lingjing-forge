"""振动/颤振稳定性预测模块。

提供基于解析法（Tlusty公式）和神经网络的颤振稳定性极限预测能力，
用于判断加工过程稳定性状态并给出极限切削深度。

子模块:
    - stability: Tlusty 稳定性叶图解析计算
    - predictor: 神经网络推理接口
"""

from __future__ import annotations

from app.simulation.chatter.stability import (
    ChatterParams,
    MachineParams,
    ToolParams,
    compute_stability_limit,
    compute_stability_lobe,
    get_machine_params,
    get_default_machine_params,
)
from app.simulation.chatter.predictor import (
    predict_stability,
    predict_stability_batch,
)

__all__ = [
    "ChatterParams",
    "MachineParams",
    "ToolParams",
    "compute_stability_limit",
    "compute_stability_lobe",
    "get_machine_params",
    "get_default_machine_params",
    "predict_stability",
    "predict_stability_batch",
]
