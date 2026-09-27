"""制造状态表示模块。

实现ManufacturingState类，精确捕获制造系统的完整状态：
- 工件状态：JEPA几何嵌入、材料类型、精度参数
- 设备状态：工具磨损度、主轴温度、振动幅度
- 工艺进度：当前操作步骤、已完成操作列表
- 统一状态嵌入：512维综合状态表示向量
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np


@dataclass
class ManufacturingState:
    """制造系统完整状态表示。

    捕获工件、设备和工艺进度的完整状态快照，并生成512维
    统一状态嵌入向量，用于JEPA World Model的状态预测。

    Attributes:
        geometry: JEPA几何嵌入（512维向量）
        material: 材料类型标识
        precision: 当前精度参数（mm）
        tool_wear: 工具磨损度（0.0~1.0，1.0为完全磨损）
        spindle_temp: 主轴温度（℃）
        vibration: 振动幅度（mm/s）
        current_operation: 当前操作步骤索引
        completed_operations: 已完成操作列表
        state_embedding: 512维综合状态嵌入向量
    """

    # 工件状态
    geometry: np.ndarray  # JEPA几何嵌入 (512,)
    material: str
    precision: float

    # 设备状态
    tool_wear: float
    spindle_temp: float
    vibration: float

    # 工艺进度
    current_operation: int
    completed_operations: List[int] = field(default_factory=list)

    # 嵌入表示
    state_embedding: Optional[np.ndarray] = None  # (512,)

    def __post_init__(self):
        """初始化后处理：确保geometry为正确形状，生成状态嵌入。"""
        if isinstance(self.geometry, list):
            self.geometry = np.array(self.geometry, dtype=np.float32)
        if self.geometry.shape != (512,):
            raise ValueError(
                f"几何嵌入维度错误：期望(512,)，实际{self.geometry.shape}"
            )
        if self.state_embedding is None:
            self.state_embedding = self._compute_state_embedding()

    def _compute_state_embedding(self) -> np.ndarray:
        """计算512维综合状态嵌入向量。

        将工件状态、设备状态和工艺进度信息编码为统一的512维向量。
        嵌入空间划分：
            [0:256)   几何嵌入（来自JEPA编码器）
            [256:320) 设备状态编码（tool_wear, spindle_temp, vibration）
            [320:384) 工艺进度编码（current_operation, completed_operations）
            [384:448) 归一化精度与材料特征
            [448:512) 预留扩展

        Returns:
            512维状态嵌入向量
        """
        embedding = np.zeros(512, dtype=np.float32)

        # [0:256): 几何嵌入（取前256维，后256维保留给变换）
        geo = self.geometry[:256].copy()
        embedding[0:256] = geo

        # [256:320): 设备状态编码
        embedding[256:272] = np.clip(self.tool_wear * 2.0 - 1.0, -1.0, 1.0)
        embedding[272:288] = np.clip(self.spindle_temp / 200.0 * 2.0 - 1.0, -1.0, 1.0)
        embedding[288:304] = np.clip(self.vibration / 50.0 * 2.0 - 1.0, -1.0, 1.0)
        # 304:320 保留

        # [320:384): 工艺进度编码
        embedding[320:336] = np.clip(self.current_operation / 50.0 * 2.0 - 1.0, -1.0, 1.0)
        # 编码已完成操作列表
        for i, op_id in enumerate(self.completed_operations[:16]):
            if i < 16:
                embedding[336 + i] = np.clip(op_id / 50.0 * 2.0 - 1.0, -1.0, 1.0)
        # 352:384 保留

        # [384:448): 精度与材料特征
        embedding[384:400] = np.clip(self.precision / 0.5 * 2.0 - 1.0, -1.0, 1.0)
        # 材料类型编码（简单哈希映射）
        material_hash = hash(self.material) % 2**16
        for i in range(16):
            embedding[400 + i] = ((material_hash >> i) & 1) * 2.0 - 1.0
        # 416:448 保留

        # [448:512): 预留扩展

        # L2归一化
        norm = np.linalg.norm(embedding)
        if norm > 1e-10:
            embedding = embedding / norm

        return embedding.astype(np.float32)

    def to_dict(self) -> Dict:
        """转换为字典表示。"""
        return {
            "geometry": self.geometry.tolist(),
            "material": self.material,
            "precision": self.precision,
            "tool_wear": self.tool_wear,
            "spindle_temp": self.spindle_temp,
            "vibration": self.vibration,
            "current_operation": self.current_operation,
            "completed_operations": self.completed_operations,
            "state_embedding": self.state_embedding.tolist() if self.state_embedding is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ManufacturingState":
        """从字典恢复状态。"""
        geometry = np.array(data["geometry"], dtype=np.float32)
        state = cls(
            geometry=geometry,
            material=data["material"],
            precision=data["precision"],
            tool_wear=data["tool_wear"],
            spindle_temp=data["spindle_temp"],
            vibration=data["vibration"],
            current_operation=data["current_operation"],
            completed_operations=data.get("completed_operations", []),
        )
        if data.get("state_embedding") is not None:
            state.state_embedding = np.array(data["state_embedding"], dtype=np.float32)
        return state

    def copy(self) -> "ManufacturingState":
        """深拷贝状态。"""
        return ManufacturingState(
            geometry=self.geometry.copy(),
            material=self.material,
            precision=self.precision,
            tool_wear=self.tool_wear,
            spindle_temp=self.spindle_temp,
            vibration=self.vibration,
            current_operation=self.current_operation,
            completed_operations=list(self.completed_operations),
            state_embedding=self.state_embedding.copy() if self.state_embedding is not None else None,
        )

    def add_noise(self, noise_std: float = 0.05) -> "ManufacturingState":
        """向状态参数添加可控噪声（用于鲁棒性测试）。

        Args:
            noise_std: 噪声标准差（相对于参数范围的5%）

        Returns:
            添加噪声后的新状态
        """
        new_state = self.copy()
        # 对设备状态参数添加噪声（±5%范围）
        new_state.tool_wear = np.clip(
            self.tool_wear + np.random.normal(0, noise_std * 0.05), 0.0, 1.0
        )
        new_state.spindle_temp += np.random.normal(0, noise_std * 10.0)
        new_state.vibration = max(0.0, self.vibration + np.random.normal(0, noise_std * 2.5))
        new_state.precision += np.random.normal(0, noise_std * 0.025)
        # 重新计算嵌入
        new_state.state_embedding = new_state._compute_state_embedding()
        return new_state

    def __repr__(self) -> str:
        return (
            f"ManufacturingState(material={self.material}, "
            f"precision={self.precision:.4f}, "
            f"tool_wear={self.tool_wear:.3f}, "
            f"spindle_temp={self.spindle_temp:.1f}°C, "
            f"vibration={self.vibration:.2f}mm/s, "
            f"op={self.current_operation}, "
            f"completed={len(self.completed_operations)} ops)"
        )
