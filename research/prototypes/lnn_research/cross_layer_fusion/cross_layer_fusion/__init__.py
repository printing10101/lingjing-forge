"""
跨层注意力融合模块 (Cross-Layer Attention Fusion)

实现三层（认知层、感知层、执行层）之间的高效注意力融合机制，
确保各层信息能够有效交互与整合，提升系统整体性能。

模块结构:
    - attention: CrossLayerAttention 基础注意力类
    - fusion: 三层融合机制 (Cog2Per, Per2Exec, Exec2Cog)
    - alignment: alignment_loss 对齐训练函数
"""

from app.ai.cross_layer_fusion.attention import CrossLayerAttention, reshape_attention_weights
from app.ai.cross_layer_fusion.fusion import (
    CognitiveToPerceptionFusion,
    PerceptionToExecutionFusion,
    ExecutionToCognitiveFusion,
    CrossLayerFusionSystem,
)
from app.ai.cross_layer_fusion.alignment import (
    alignment_loss,
    AlignmentLossTracker,
)

__all__ = [
    "CrossLayerAttention",
    "reshape_attention_weights",
    "CognitiveToPerceptionFusion",
    "PerceptionToExecutionFusion",
    "ExecutionToCognitiveFusion",
    "CrossLayerFusionSystem",
    "alignment_loss",
    "AlignmentLossTracker",
]
