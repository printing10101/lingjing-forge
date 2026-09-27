"""模型注册表抽象基类与工具（从 registry 拆出）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseModelRegistry(ABC):
    """Abstract base class for model registries.

    Defines the minimal interface required for model loading.
    All registry implementations should inherit from this class.
    """

    @abstractmethod
    def get(self, model_name: str) -> Any:
        """Get a model instance by name.

        Args:
            model_name: Unique model identifier

        Returns:
            Model instance

        Raises:
            KeyError: If model not found
        """
        ...


def is_quantized_model(model_name: str) -> bool:
    """Check if a model name refers to a quantized model."""
    return model_name.endswith("_int8")


def get_base_model_name(model_name: str) -> str:
    """Get the base model name from a quantized model name."""
    if is_quantized_model(model_name):
        return model_name[:-5]
    return model_name


def get_quantized_model_name(model_name: str) -> str:
    """Get the quantized model name from a base model name."""
    if not is_quantized_model(model_name):
        return f"{model_name}_int8"
    return model_name
