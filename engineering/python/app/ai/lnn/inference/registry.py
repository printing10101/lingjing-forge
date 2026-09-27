"""
Model Registry

Manages model metadata, version control, and validation.
Provides a centralized registry for all LNN models with predefined model support.

本模块为门面：实现已拆分至 _base_registry / _registry_models / _lnn_registry / _runtime_registry / _torch_map。
"""

from __future__ import annotations

import logging

from app.ai.lnn.inference._base_registry import (  # noqa: F401
    BaseModelRegistry,
    get_base_model_name,
    get_quantized_model_name,
    is_quantized_model,
)
from app.ai.lnn.inference._lnn_registry import LNNModelRegistry  # noqa: F401
from app.ai.lnn.inference._registry_models import (  # noqa: F401
    ModelEntry,
    ModelInfo,
)
from app.ai.lnn.inference._runtime_registry import ModelRegistry  # noqa: F401
from app.ai.lnn.inference._torch_map import (  # noqa: F401
    _init_torch_model_map,
    get_torch_model_class,
)

logger = logging.getLogger(__name__)
