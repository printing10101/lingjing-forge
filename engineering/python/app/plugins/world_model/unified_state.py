"""统一状态表示：零件几何 + 切削动力学的融合 embedding。

借鉴 GUSH3R 用 3DGS 统一异质对象的思想，将灵境制造的「几何」与「动力学」
两类异质状态投影到统一 embedding 空间，供 WorldModelNet 在统一空间中
预测未来轨迹。

工程边界（与 ADR-020 思路 1 一致）：
- 不替换 ADR-017 的 LSTM+LTC 架构，仅在输入层做融合
- 不引入 3DGS（与工业 CAD 不兼容，GUSH3R 评估已否决）
- v1 用冻结的几何特征提取器，避免训练不稳定
- 不修改 ADR-013 的颤振预测输出（保持向后兼容）

对应 ADR：ADR-020 思路 1 / ADR-017 世界模型与 RL 模块
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class GeometryFeatures:
    """几何特征（来自 ADR-007 几何特征提取结果）。

    Attributes:
        bbox_dimensions: 包围盒尺寸 (length, width, height) mm
        feature_vector: 平面/圆柱/孔特征统计向量（来自 ADR-007）
        symmetry_score: 对称性评分 [0, 1]
        complexity_score: 复杂度评分 [0, 1]
    """

    bbox_dimensions: tuple[float, float, float]
    feature_vector: list[float]
    symmetry_score: float
    complexity_score: float

    def to_tensor_input(self) -> list[float]:
        """展平为 GeometryEncoder 输入向量。"""
        return list(self.bbox_dimensions) + list(self.feature_vector) + [self.symmetry_score, self.complexity_score]


@dataclass
class DynamicsState:
    """切削动力学状态（来自 ADR-013 颤振预测输入）。

    Attributes:
        spindle_speed: 主轴转速 rpm
        feed_rate: 进给量 mm/min
        depth_of_cut: 切深 mm
        tool_wear: 刀具磨损 mm
        vibration_rms: 振动 RMS g
        temperature: 温度 °C
    """

    spindle_speed: float
    feed_rate: float
    depth_of_cut: float
    tool_wear: float
    vibration_rms: float
    temperature: float

    def to_tensor_input(self) -> list[float]:
        """展平为 DynamicsEncoder 输入向量。"""
        return [
            self.spindle_speed,
            self.feed_rate,
            self.depth_of_cut,
            self.tool_wear,
            self.vibration_rms,
            self.temperature,
        ]


@dataclass
class UnifiedState:
    """统一状态：几何 + 动力学。

    这是 WorldModelNet 的新输入格式，替代 ADR-017 原版的 current_state
    字段拼接。融合后的 embedding 用于 LSTM+LTC 时序预测。

    Attributes:
        geometry: 几何特征（来自 ADR-007）
        dynamics: 动力学状态（来自 ADR-013）
        fused_embedding: 融合后的 embedding，由 FusionLayer 填充；
            未融合时为 None
    """

    geometry: GeometryFeatures
    dynamics: DynamicsState
    fused_embedding: list[float] | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，供 JSON Schema 校验与 MLflow 记录。"""
        return {
            "geometry": {
                "bbox_dimensions": list(self.geometry.bbox_dimensions),
                "feature_vector": list(self.geometry.feature_vector),
                "symmetry_score": self.geometry.symmetry_score,
                "complexity_score": self.geometry.complexity_score,
            },
            "dynamics": {
                "spindle_speed": self.dynamics.spindle_speed,
                "feed_rate": self.dynamics.feed_rate,
                "depth_of_cut": self.dynamics.depth_of_cut,
                "tool_wear": self.dynamics.tool_wear,
                "vibration_rms": self.dynamics.vibration_rms,
                "temperature": self.dynamics.temperature,
            },
            "fused_embedding": self.fused_embedding,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UnifiedState":
        """从字典反序列化（与 to_dict 互逆）。

        用于 WorldModelPlugin 解析 TaskContext.input["current_state"]。

        Raises:
            ValueError: bbox_dimensions 非 list/tuple（如字符串 "abc" 会被
                tuple() 错误地拆为字符序列），feature_vector 同理。
        """
        geo = data["geometry"]
        dyn = data["dynamics"]
        # 严格类型校验：拒绝字符串类型的 bbox_dimensions/feature_vector
        # 避免 tuple("abc") 产生 ('a','b','c') 长度恰好为 3 的脏数据污染下游
        bbox_raw = geo["bbox_dimensions"]
        if not isinstance(bbox_raw, (list, tuple)):
            raise ValueError(f"bbox_dimensions 必须为 list/tuple，得到 {type(bbox_raw).__name__}")
        feat_raw = geo["feature_vector"]
        if not isinstance(feat_raw, (list, tuple)):
            raise ValueError(f"feature_vector 必须为 list/tuple，得到 {type(feat_raw).__name__}")
        return cls(
            geometry=GeometryFeatures(
                bbox_dimensions=tuple(bbox_raw),
                feature_vector=list(feat_raw),
                symmetry_score=float(geo["symmetry_score"]),
                complexity_score=float(geo["complexity_score"]),
            ),
            dynamics=DynamicsState(
                spindle_speed=float(dyn["spindle_speed"]),
                feed_rate=float(dyn["feed_rate"]),
                depth_of_cut=float(dyn["depth_of_cut"]),
                tool_wear=float(dyn["tool_wear"]),
                vibration_rms=float(dyn["vibration_rms"]),
                temperature=float(dyn["temperature"]),
            ),
            fused_embedding=data.get("fused_embedding"),
        )


# JSON Schema（供 ADR-017 WorldModelPlugin 校验输入）
UNIFIED_STATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["geometry", "dynamics"],
    "properties": {
        "geometry": {
            "type": "object",
            "required": [
                "bbox_dimensions",
                "feature_vector",
                "symmetry_score",
                "complexity_score",
            ],
            "properties": {
                "bbox_dimensions": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                },
                "feature_vector": {
                    "type": "array",
                    "items": {"type": "number"},
                },
                "symmetry_score": {"type": "number", "minimum": 0, "maximum": 1},
                "complexity_score": {"type": "number", "minimum": 0, "maximum": 1},
            },
        },
        "dynamics": {
            "type": "object",
            "required": [
                "spindle_speed",
                "feed_rate",
                "depth_of_cut",
                "tool_wear",
                "vibration_rms",
                "temperature",
            ],
            "properties": {
                "spindle_speed": {"type": "number", "minimum": 0},
                "feed_rate": {"type": "number", "minimum": 0},
                "depth_of_cut": {"type": "number", "minimum": 0},
                "tool_wear": {"type": "number", "minimum": 0},
                "vibration_rms": {"type": "number", "minimum": 0},
                "temperature": {"type": "number"},
            },
        },
        "fused_embedding": {
            "type": ["array", "null"],
            "items": {"type": "number"},
        },
    },
}


__all__ = [
    "GeometryFeatures",
    "DynamicsState",
    "UnifiedState",
    "UNIFIED_STATE_SCHEMA",
]
