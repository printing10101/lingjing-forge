"""Research 桥接模块：统一管理对 research/ 包的延迟导入。

P0#3 解耦策略:
  所有对 ``research.*`` 的直接导入必须通过本模块的工厂函数进行。
  - 开发环境: 返回真实的 research 对象
  - 生产环境 (无 torch): 返回 None 并记录 WARNING

使用方式::

    from app.ai.lnn._research_bridge import get_lnn_config_factory, get_trainer_factory
    LNNConfig = get_lnn_config_factory()
    if LNNConfig is not None:
        cfg = LNNConfig(hidden_size=64)

本模块的导入延迟到工厂函数首次调用时，不会在模块加载时触发 ImportError。
"""

from __future__ import annotations

import logging
from typing import Any
from collections.abc import Callable

logger = logging.getLogger(__name__)

# 所有 research 对象在此缓存（首次成功导入后复用）
_cache: dict[str, Any] = {}
_import_attempted: dict[str, bool] = {}


def _inject_research_syspath() -> None:
    """定位仓库根的 research/ 目录并注入 sys.path（幂等）。

    阶段2 解耦后工程侧位于 engineering/python/，运行时 sys.path 不含仓库根，
    而科研侧代码以 ``research.training.xxx`` 绝对导入。此处沿文件路径向上
    查找含 research/ 的仓库根，避免硬编码路径；找不到时保持原状（降级）。
    """
    import sys as _sys
    from pathlib import Path as _Path

    if "research" in _sys.modules:
        return
    p = _Path(__file__).resolve().parent
    for _ in range(6):
        p = p.parent
        if (p / "research").is_dir():
            _root = str(p)
            if _root not in _sys.path:
                _sys.path.insert(0, _root)
            return


def _lazy_import(module_path: str, attr_name: str) -> Any | None:
    """延迟导入 research 模块的单个属性。

    返回 None 表示导入失败（research 包不可用或 torch 缺失）。
    结果会被缓存，后续调用直接返回缓存值。
    """
    cache_key = f"{module_path}:{attr_name}"
    if cache_key in _cache:
        return _cache[cache_key]
    if _import_attempted.get(cache_key, False):
        return _cache.get(cache_key)

    _import_attempted[cache_key] = True
    try:
        import importlib as _importlib

        # 阶段2 解耦：工程侧运行时（cwd=engineering/python）的 sys.path 不含仓库根，
        # 直接 importlib.import_module("research.xxx") 永远失败。动态定位 research/
        # 目录并注入 sys.path（仅当 module_path 以 research. 开头）。
        if module_path.startswith("research."):
            _inject_research_syspath()

        mod = _importlib.import_module(module_path)
        obj = getattr(mod, attr_name)
        _cache[cache_key] = obj
        logger.debug("research bridge: loaded %s from %s", attr_name, module_path)
        return obj
    except ImportError as e:
        logger.warning(
            "research bridge: cannot import %s from %s (%s). Training functionality will be unavailable.",
            attr_name,
            module_path,
            e,
        )
        _cache[cache_key] = None
        return None
    except AttributeError as e:
        logger.error(
            "research bridge: %s not found in %s (%s)",
            attr_name,
            module_path,
            e,
        )
        _cache[cache_key] = None
        return None
    except Exception as e:  # noqa: BLE001
        # 兜底：任何异常（OSError/WinError 6714、torch 初始化 RuntimeError 等）
        # 都应降级为不可用，而非让桥接层把异常抛给调用方（可选功能）。
        logger.warning(
            "research bridge: unexpected error importing %s from %s (%s: %s). "
            "Training functionality will be unavailable.",
            attr_name,
            module_path,
            type(e).__name__,
            e,
        )
        _cache[cache_key] = None
        return None


def get_torch() -> Any | None:
    """获取 torch 模块（延迟导入）。"""
    return _lazy_import("torch", "__version__")  # trigger import


def get_nn() -> Any | None:
    """获取 torch.nn 模块。"""
    torch = get_torch()
    return torch.nn if torch is not None else None


def get_lnn_config_factory() -> type | None:
    """获取 LNNConfig 类型。

    Returns:
        ``research.models.torch_base_lnn.LNNConfig`` 或 ``None``。
    """
    return _lazy_import("research.models.torch_base_lnn", "LNNConfig")


def get_cfc_model_factory() -> type | None:
    """获取 CFCModel 类型。"""
    return _lazy_import("research.models.torch_cfc_model", "CFCModel")


def get_ltc_model_factory() -> type | None:
    """获取 LTCModel 类型。"""
    return _lazy_import("research.models.torch_ltc_model", "LTCModel")


def get_hybrid_lnn_factory() -> type | None:
    """获取 HybridLNNModel 类型。"""
    return _lazy_import("research.models.hybrid_lnn", "HybridLNNModel")


def get_trainer_factory() -> type | None:
    """获取 LNNTrainer 类型。"""
    return _lazy_import("research.training.trainer", "LNNTrainer")


def get_device_detect() -> Callable | None:
    """获取 detect_device 函数。"""
    return _lazy_import("research.training.device_manager", "detect_device")


def get_device_optimal_batch_size() -> Callable | None:
    """获取 get_optimal_batch_size 函数。"""
    return _lazy_import("research.training.device_manager", "get_optimal_batch_size")


def get_device_optimal_num_workers() -> Callable | None:
    """获取 get_optimal_num_workers 函数。"""
    return _lazy_import("research.training.device_manager", "get_optimal_num_workers")


def get_device_status_func() -> Callable | None:
    """获取 get_device_status 函数。"""
    return _lazy_import("research.training.device_manager", "get_device_status")


def get_available_devices_func() -> Callable | None:
    """获取 get_available_devices 函数。"""
    return _lazy_import("research.training.device_manager", "get_available_devices")


def get_clear_gpu_memory_func() -> Callable | None:
    """获取 clear_gpu_memory 函数。"""
    return _lazy_import("research.training.device_manager", "clear_gpu_memory")


def get_set_global_seed() -> Callable | None:
    """获取 set_global_seed 函数。"""
    return _lazy_import("research.training.reproducibility", "set_global_seed")


def get_mlflow_start_run() -> Callable | None:
    """获取 mlflow.start_run。"""
    return _lazy_import("research.training.experiment_tracker", "mlflow_start_run")


def get_mlflow_log_params() -> Callable | None:
    """获取 mlflow.log_params。"""
    return _lazy_import("research.training.experiment_tracker", "mlflow_log_params")


def get_mlflow_log_metrics() -> Callable | None:
    """获取 mlflow.log_metrics。"""
    return _lazy_import("research.training.experiment_tracker", "mlflow_log_metrics")


def get_mlflow_log_model() -> Callable | None:
    """获取 mlflow.log_model。"""
    return _lazy_import("research.training.experiment_tracker", "mlflow_log_model")


def get_has_mlflow() -> bool:
    """检查 MLflow 是否可用。"""
    obj = _lazy_import("research.training.experiment_tracker", "HAS_MLFLOW")
    return bool(obj)


def get_quantizer_factory() -> type | None:
    """获取量化器类型（Quantizer 类）。"""
    return _lazy_import("research.quantization.quantizer", "Quantizer")


def get_quantization_config() -> type | None:
    """获取 QuantizationConfig 类型。"""
    return _lazy_import("research.quantization.quantizer", "QuantizationConfig")


def get_quantization_type_enum() -> type | None:
    """获取 QuantizationType 枚举。"""
    return _lazy_import("research.quantization.quantizer", "QuantizationType")


def get_multimodal_jepa_chamfer() -> Callable | None:
    """获取 multimodal_jepa 的 chamfer heuristic。"""
    return _lazy_import(
        "research.multimodal_jepa.ijepa_3d.chamfer_heuristic",
        "detect_all_extended",
    )


def get_torch_mamba_lnn_factory() -> type | None:
    """获取 TorchMambaLNN 类型（Phase 3a：④ SSM 预测 backbone）。

    生产容器无 research/ 时返回 None（解耦契约，与其它 research 桥一致）。
    """
    return _lazy_import("research.models.torch_mamba_lnn", "TorchMambaLNN")


def get_synthetic_chatter_generator() -> Callable | None:
    """获取合成颤振数据生成器（Phase 3a：数据管线原型）。"""
    return _lazy_import("research.datasets.synthetic_chatter", "generate_chatter_dataset")


def is_research_available() -> bool:
    """检查 research 包是否可用（torch 已安装 + research 路径可访问）。"""
    return get_torch() is not None and get_lnn_config_factory() is not None
