"""LNN NumPy 推理模型包（工程运行时版）。

W 引擎验证修复：V2.7 解耦时这四个纯 NumPy 推理类被一并迁往 research/，
导致 ``LNNModelRegistry.MODEL_CLASS_MAP`` 全为 None——所有 LNN 在线预测
报 "Unsupported model type"。本包只收录零 torch 依赖的 NumPy 推理实现
（桌面运行时契约：运行时不含 torch）；torch 训练变体归 research/models。

来源：research/models/{base_lnn,cfc_model,ltc_model,hybrid_lnn}.py（2026-09 回迁）。
"""

from .base_lnn import BaseLNNModel
from .cfc_model import CFCModel
from .hybrid_lnn import HybridLNNModel
from .ltc_model import LTCModel

__all__ = ["BaseLNNModel", "CFCModel", "HybridLNNModel", "LTCModel"]
