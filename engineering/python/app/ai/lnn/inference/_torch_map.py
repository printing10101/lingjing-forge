"""PyTorch 模型类映射（从 registry 拆出）。"""

from __future__ import annotations

import logging


logger = logging.getLogger(__name__)


def get_torch_model_class(model_type_str: str) -> type | None:
    """Get PyTorch model class by type string.

    Args:
        model_type_str: Model type string (e.g. "CFC", "LTC", "HybridLNN")

    Returns:
        PyTorch model class or None if not supported
    """
    from app.ai.lnn.inference._runtime_registry import ModelRegistry

    if not ModelRegistry._torch_model_class_map:
        _init_torch_model_map()
    return ModelRegistry._torch_model_class_map.get(model_type_str)


def _init_torch_model_map() -> None:
    """Initialize the PyTorch model class map lazily."""
    from app.ai.lnn.inference._runtime_registry import ModelRegistry

    try:
        from app.ai.lnn.models.torch_cfc_model import CFCModel as TorchCFCModel
        from app.ai.lnn.models.torch_ltc_model import LTCModel as TorchLTCModel
        from app.ai.lnn.models.torch_hybrid_lnn import HybridLNN as TorchHybridLNNModel

        ModelRegistry._torch_model_class_map.update(
            {
                "CFC": TorchCFCModel,
                "LTC": TorchLTCModel,
                "HybridLNN": TorchHybridLNNModel,
            }
        )
    except ImportError as e:
        # PyTorch 可选依赖未安装时静默跳过（仅影响 PyTorch 后端注册）
        logger.debug(
            f"PyTorch backend not available, skipping model class registration: {e}",
            exc_info=True,
        )
