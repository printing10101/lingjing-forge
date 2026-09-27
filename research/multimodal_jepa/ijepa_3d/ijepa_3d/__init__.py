"""I-JEPA 3D Geometry Extraction System.

基于I-JEPA架构从机械零件三视图到3D几何参数的精确提取系统。

主要组件:
    - ResNetBackbone: CNN骨干网络（ResNet-18），提取64通道低级视觉特征
    - IJEPAEncoder: I-JEPA ViT-Small编码器（12层Transformer, 8头注意力, dim=512）
    - Predictor: 3层MLP预测器网络，预测被遮挡区域嵌入
    - ViewFusion: 交叉注意力三视图融合模块
    - GeometryHead: 3D几何参数回归头（边界框+特征点）
    - MultiScaleMasking: 多尺度块掩码策略
    - IJPELosses: 组合损失函数（VICReg + L1 + Smooth L1）
    - IJEPA3DModel: 完整模型组装
    - IJEPA3DDataset: 数据加载与增强
    - IJEPA3DTrainer: 训练流程管理
"""

# Q2 修复：原 `from app.ai.ijepa_3d.xxx` 为悬空 import（app/ai/ijepa_3d/ 不存在）。
# 改为同包内相对导入，使 research.multimodal_jepa.ijepa_3d.ijepa_3d 可独立加载。
from .config import IJEPA3DConfig
from .model import IJEPA3DModel
from .trainer import IJEPA3DTrainer
from .dataset import IJEPA3DDataset

__all__ = [
    "IJEPA3DConfig",
    "IJEPA3DModel",
    "IJEPA3DTrainer",
    "IJEPA3DDataset",
]
