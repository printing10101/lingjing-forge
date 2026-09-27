"""制造动作表示模块。

实现ManufacturingAction类，标准化工艺操作表示：
- operation_type：操作类型（rough_milling, finish_milling, drilling等）
- tool_id：工具标识
- parameters：主轴转速、进给率、切削深度、冷却液使用等
- action_embedding：512维动作嵌入向量
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np


# 操作类型到索引的映射
OPERATION_TYPE_MAP: Dict[str, int] = {
    "rough_milling": 0,
    "finish_milling": 1,
    "drilling": 2,
    "reaming": 3,
    "tapping": 4,
    "boring": 5,
    "facing": 6,
    "chamfering": 7,
    "grooving": 8,
    "threading": 9,
}

NUM_OPERATION_TYPES = len(OPERATION_TYPE_MAP)


@dataclass
class ManufacturingAction:
    """标准化工艺操作表示。

    将制造工艺操作编码为统一的512维动作嵌入向量，用于JEPA World Model
    的状态转移预测。

    Attributes:
        operation_type: 操作类型标识
        tool_id: 工具标识
        parameters: 工艺参数字典
        action_embedding: 512维动作嵌入向量
    """

    operation_type: str  # "rough_milling" / "finish_milling" / "drilling" ...
    tool_id: str
    parameters: Dict = field(default_factory=lambda: {
        "spindle_speed": 8000,
        "feed_rate": 500.0,
        "depth_of_cut": 2.0,
        "coolant": True,
    })
    action_embedding: Optional[np.ndarray] = None  # (512,)

    def __post_init__(self):
        """初始化后处理：确保参数完整性，生成动作嵌入。"""
        # 确保必要参数存在
        defaults = {
            "spindle_speed": 8000,
            "feed_rate": 500.0,
            "depth_of_cut": 2.0,
            "coolant": True,
        }
        for key, default_val in defaults.items():
            if key not in self.parameters:
                self.parameters[key] = default_val

        if self.action_embedding is None:
            self.action_embedding = self._compute_action_embedding()

    def _compute_action_embedding(self) -> np.ndarray:
        """计算512维动作嵌入向量。

        嵌入空间划分：
            [0:64)    操作类型one-hot编码（10种操作类型 + 填充）
            [64:128)  工具ID编码（字符串哈希）
            [128:256) 工艺参数编码（spindle_speed, feed_rate, depth_of_cut）
            [256:320) 冷却液与辅助参数
            [320:512) 预留扩展

        Returns:
            512维动作嵌入向量
        """
        embedding = np.zeros(512, dtype=np.float32)

        # [0:64): 操作类型one-hot编码
        op_idx = OPERATION_TYPE_MAP.get(self.operation_type, 0)
        embedding[op_idx] = 1.0
        # 多热编码：对近似操作类型也设置部分值
        related_ops = self._get_related_operations(self.operation_type)
        for rel_op, weight in related_ops:
            rel_idx = OPERATION_TYPE_MAP.get(rel_op, -1)
            if rel_idx >= 0 and rel_idx < 64:
                embedding[rel_idx] = max(embedding[rel_idx], weight)

        # [64:128): 工具ID编码
        tool_hash = hash(self.tool_id) % 2**32
        for i in range(32):
            if (tool_hash >> i) & 1:
                embedding[64 + i * 2] = 1.0
            else:
                embedding[64 + i * 2] = -0.5

        # [128:256): 工艺参数编码
        spindle_speed = self.parameters.get("spindle_speed", 8000)
        feed_rate = self.parameters.get("feed_rate", 500.0)
        depth_of_cut = self.parameters.get("depth_of_cut", 2.0)

        embedding[128:160] = np.clip(spindle_speed / 30000.0 * 2.0 - 1.0, -1.0, 1.0)
        embedding[160:192] = np.clip(feed_rate / 2000.0 * 2.0 - 1.0, -1.0, 1.0)
        embedding[192:224] = np.clip(depth_of_cut / 50.0 * 2.0 - 1.0, -1.0, 1.0)
        # 224:256 保留

        # [256:320): 冷却液与辅助参数
        coolant = self.parameters.get("coolant", True)
        embedding[256:272] = 1.0 if coolant else -1.0
        # 272:320 保留

        # L2归一化
        norm = np.linalg.norm(embedding)
        if norm > 1e-10:
            embedding = embedding / norm

        return embedding.astype(np.float32)

    @staticmethod
    def _get_related_operations(op_type: str) -> List[tuple]:
        """获取与给定操作类型相关的操作及其权重。

        Args:
            op_type: 操作类型字符串

        Returns:
            [(相关操作类型, 权重), ...]
        """
        related_map = {
            "rough_milling": [("finish_milling", 0.5), ("facing", 0.3)],
            "finish_milling": [("rough_milling", 0.5), ("facing", 0.2)],
            "drilling": [("reaming", 0.6), ("tapping", 0.4), ("boring", 0.3)],
            "reaming": [("drilling", 0.6), ("boring", 0.3)],
            "tapping": [("drilling", 0.5), ("threading", 0.4)],
            "boring": [("drilling", 0.4), ("reaming", 0.3)],
            "facing": [("rough_milling", 0.3), ("finish_milling", 0.2)],
            "chamfering": [("facing", 0.3), ("finish_milling", 0.2)],
            "grooving": [("rough_milling", 0.3), ("facing", 0.2)],
            "threading": [("tapping", 0.4), ("drilling", 0.2)],
        }
        return related_map.get(op_type, [])

    def to_dict(self) -> Dict:
        """转换为字典表示。"""
        return {
            "operation_type": self.operation_type,
            "tool_id": self.tool_id,
            "parameters": self.parameters,
            "action_embedding": self.action_embedding.tolist() if self.action_embedding is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ManufacturingAction":
        """从字典恢复动作。"""
        action = cls(
            operation_type=data["operation_type"],
            tool_id=data["tool_id"],
            parameters=data.get("parameters", {}),
        )
        if data.get("action_embedding") is not None:
            action.action_embedding = np.array(data["action_embedding"], dtype=np.float32)
        return action

    def copy(self) -> "ManufacturingAction":
        """深拷贝动作。"""
        return ManufacturingAction(
            operation_type=self.operation_type,
            tool_id=self.tool_id,
            parameters=dict(self.parameters),
            action_embedding=self.action_embedding.copy() if self.action_embedding is not None else None,
        )

    def __repr__(self) -> str:
        return (
            f"ManufacturingAction(type={self.operation_type}, "
            f"tool={self.tool_id}, "
            f"s={self.parameters.get('spindle_speed', '?')}rpm, "
            f"f={self.parameters.get('feed_rate', '?')}mm/min, "
            f"doc={self.parameters.get('depth_of_cut', '?')}mm, "
            f"coolant={'on' if self.parameters.get('coolant', True) else 'off'})"
        )
