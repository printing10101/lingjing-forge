"""
LNN (Liquid Neural Network) Module

阶段2 解耦改造：models/ 和 training/ 已迁移到 research/。
工程侧仅保留 inference/ 子模块，通过 ONNX Runtime 消费训练好的模型。
模型类（BaseLNNModel/CFCModel/LTCModel/HybridLNNModel）不再在工程侧暴露，
如需训练或加载 torch 模型，请在 research/ 环境中操作。
"""

__all__: list[str] = []

__version__ = "2.7.0"
