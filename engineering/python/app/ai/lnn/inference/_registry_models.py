"""模型注册表数据类（从 registry 拆出）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.ai.lnn.core import ModelConfig

try:
    from app.ai.lnn.models.base_lnn import BaseLNNModel

    _HAS_TORCH_MODELS = True
except ImportError:
    BaseLNNModel = None
    _HAS_TORCH_MODELS = False


@dataclass
class ModelInfo:
    """Model information dataclass with validation"""

    name: str
    model_type: str
    model_path: str
    input_features: list[str]
    output_features: list[str]
    version: str = "1.0.0"

    def __post_init__(self):
        """Validate required fields"""
        if not self.name:
            raise ValueError(
                "Model registration failed: name cannot be empty. Use a meaningful name (e.g. 'cutting_force_45steel')."
            )
        if not self.model_type:
            raise ValueError(
                "Model registration failed: model_type cannot be empty. "
                "Supported types: LNN, CTC, CFC, LTC. "
                "Call GET /api/v1/lnn/models for the list."
            )
        if not self.model_path:
            raise ValueError(
                "Model registration failed: model_path cannot be empty. "
                "Path must point to a trained weight file (.npz v2 或 .onnx), "
                "e.g. 'models/lnn/cutting_force.npz'."
            )
        if not self.input_features:
            raise ValueError(
                "Model registration failed: Input features cannot be empty. "
                "Input features define model input variables "
                "(e.g. 'cutting_speed', 'feed_rate', 'depth_of_cut')."
            )
        if not self.output_features:
            raise ValueError(
                "Model registration failed: Output features cannot be empty. "
                "Output features define model prediction targets "
                "(e.g. 'cutting_force', 'tool_wear')."
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary"""
        return {
            "name": self.name,
            "model_type": self.model_type,
            "model_path": self.model_path,
            "input_features": self.input_features,
            "output_features": self.output_features,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelInfo":
        """Deserialize from dictionary"""
        return cls(
            name=data["name"],
            model_type=data["model_type"],
            model_path=data["model_path"],
            input_features=data.get("input_features", []),
            output_features=data.get("output_features", []),
            version=data.get("version", "1.0.0"),
        )


@dataclass
class ModelEntry:
    """Model registry entry"""

    config: ModelConfig | None = None
    info: ModelInfo | None = None
    model: BaseLNNModel | None = None
    is_loaded: bool = False
    last_accessed: float = 0.0
    access_count: int = 0
    metadata: dict[str, Any] | None = None
