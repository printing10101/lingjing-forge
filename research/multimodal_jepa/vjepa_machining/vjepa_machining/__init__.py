"""V-JEPA Machining Process Anomaly Detection System.

基于V-JEPA架构的端到端加工过程异常检测系统。
实现实时视频流分析，检测并分类加工过程中的异常情况。

主要组件:
    - SpatioTemporalViT: 时空ViT编码器，提取视频序列时空特征嵌入
    - SpatioTemporalMasking: 时空掩码模块（时间+空间维度混合掩码）
    - ActionConditionedPredictor: 动作条件预测器
    - AnomalyDetectionHead: 异常检测头（二分类+多分类+严重程度评估）
    - MachiningFeatureEngineering: 多模态特征工程
    - VJEPALosses: 复合损失函数
    - VJEPAMachiningModel: 完整模型组装
    - MachiningVideoDataset: 加工视频数据集
    - VJEPATrainer: 训练流程管理
    - VJEPAInference: 实时推理引擎
    - AlertModule: 告警与建议模块
"""

from app.ai.vjepa_machining.config import VJEPAMachiningConfig
from app.ai.vjepa_machining.model import VJEPAMachiningModel
from app.ai.vjepa_machining.trainer import VJEPATrainer
from app.ai.vjepa_machining.dataset import MachiningVideoDataset

__all__ = [
    "VJEPAMachiningConfig",
    "VJEPAMachiningModel",
    "VJEPATrainer",
    "MachiningVideoDataset",
]
