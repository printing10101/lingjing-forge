"""LNN Models Package

torch 版模型（BaseLNN/TorchCFCModel/TorchLTCModel/TorchHybridLNN）为软依赖：
桌面 MVP 打包时排除 torch 以减小体积，此时仅 numpy 版模型可用。
"""

from .base_lnn import BaseLNNModel
from .cfc_model import CFCModel
from .ltc_model import LTCModel
from .hybrid_lnn import HybridLNNModel

# torch 版模型软依赖：torch 不可用时跳过，不影响 numpy 版模型导入
try:
    from .torch_base_lnn import BaseLNN, LNNConfig
    from .torch_cfc_model import CFCModel as TorchCFCModel, CFCLayer
    from .torch_ltc_model import LTCModel as TorchLTCModel, LTCCell
    from .torch_hybrid_lnn import HybridLNN as TorchHybridLNN

    _HAS_TORCH_MODELS = True
except ImportError:
    BaseLNN = None  # type: ignore[assignment,misc]
    LNNConfig = None  # type: ignore[assignment,misc]
    TorchCFCModel = None  # type: ignore[assignment,misc]
    CFCLayer = None  # type: ignore[assignment,misc]
    TorchLTCModel = None  # type: ignore[assignment,misc]
    LTCCell = None  # type: ignore[assignment,misc]
    TorchHybridLNN = None  # type: ignore[assignment,misc]
    _HAS_TORCH_MODELS = False

__all__ = [
    "BaseLNNModel",
    "CFCModel",
    "LTCModel",
    "HybridLNNModel",
    "BaseLNN",
    "LNNConfig",
    "TorchCFCModel",
    "CFCLayer",
    "TorchLTCModel",
    "LTCCell",
    "TorchHybridLNN",
    "_HAS_TORCH_MODELS",
]
