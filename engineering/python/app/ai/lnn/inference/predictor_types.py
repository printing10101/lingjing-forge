"""LNN 推理结果数据类（从 predictor 拆出）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class PredictionResult:
    """Prediction result dataclass with serialization support"""

    value: Any
    confidence: float = 0.0
    inference_time: float = 0.0
    model_info: dict[str, Any] | None = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary"""
        val = self.value
        if isinstance(val, np.ndarray):
            val = val.tolist()
        return {
            "value": val,
            "confidence": self.confidence,
            "inference_time": self.inference_time,
            "model_info": self.model_info or {},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PredictionResult":
        """Deserialize from dictionary"""
        value = data.get("value")
        if isinstance(value, list):
            # M10 修复：反序列化时指定 float32 dtype，保持与推理输出一致
            value = np.array(value, dtype=np.float32)
        return cls(
            value=value,
            confidence=data.get("confidence", 0.0),
            inference_time=data.get("inference_time", 0.0),
            model_info=data.get("model_info", {}),
        )
