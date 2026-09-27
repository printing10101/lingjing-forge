"""量化任务执行器辅助模块。

将 ``services._run_quantization_task_v2`` 中的模型加载、校准数据加载、
量化器执行与量化模型注册逻辑拆分为独立函数,便于单测与维护。
入口函数签名保持不变,本模块仅承担内部编排细节。
"""

import os
import time
import asyncio
import logging
from typing import Any

import numpy as np

from app.ai.lnn.core import ModelType
from app.ai.lnn.inference.registry import (
    get_torch_model_class,
    get_quantized_model_name,
)

# P0#3 解耦: 通过 research_bridge 延迟导入。
_HAS_LNN_CONFIG = False
LNNConfig: Any = None


def _lazy_init_config() -> bool:
    global _HAS_LNN_CONFIG, LNNConfig
    if _HAS_LNN_CONFIG:
        return True
    try:
        from app.ai.lnn._research_bridge import get_lnn_config_factory

        LNNConfig = get_lnn_config_factory()
        _HAS_LNN_CONFIG = LNNConfig is not None
    except Exception:
        _HAS_LNN_CONFIG = False
    return _HAS_LNN_CONFIG


logger = logging.getLogger(__name__)


def _load_model_for_quantization(model_name: str) -> tuple[Any, Any, str]:
    """根据模型名查找注册表,构造模型并加载权重。

    返回 ``(model, entry, model_path_str)``。
    模型类与配置从注册表 entry 解析;若模型不存在或类型不支持,抛出 ValueError。
    权重加载失败时(除 FileNotFoundError 外)降级为初始化权重并记录告警。
    """
    from app.api.v1.lnn.dependencies import model_registry

    entry = model_registry.registry.get(model_name)
    if not entry or entry.info is None:
        raise ValueError(f"Model '{model_name}' not found or missing info")

    model_class = get_torch_model_class(entry.info.model_type)
    if not model_class:
        raise ValueError(f"Unsupported model type: {entry.info.model_type}")

    config_obj = LNNConfig(
        input_size=len(entry.info.input_features),
        hidden_size=128,
        output_size=len(entry.info.output_features),
        num_layers=2,
        dropout=0.1,
    )
    model = model_class(config_obj)

    try:
        model.load(entry.info.model_path)
        model.build()
    except FileNotFoundError:
        raise FileNotFoundError(f"Model file not found: {entry.info.model_path}")
    except (OSError, RuntimeError, ValueError, TypeError) as e:
        logger.warning(
            f"Failed to load model weights for quantization, falling back to initialized weights: {e}",
            exc_info=True,
        )

    model.eval()
    return model, entry, entry.info.model_path


async def _load_calibration_data_async(calibration_data_path: str) -> np.ndarray:
    """异步加载校准数据,返回特征矩阵(去掉最后一列)。

    使用 ``asyncio.to_thread`` 包装同步 ``np.loadtxt``,避免阻塞事件循环。
    路径不存在时抛出 FileNotFoundError;解析失败时抛出 ValueError。
    """
    if not calibration_data_path or not os.path.exists(calibration_data_path):
        raise FileNotFoundError("Calibration data path required for static quantization")

    try:
        # 修复 P2：用 asyncio.to_thread 包装同步 np.loadtxt，避免阻塞事件循环
        calibration_data = await asyncio.to_thread(np.loadtxt, calibration_data_path, delimiter=",")
        if calibration_data.ndim == 1:
            calibration_data = calibration_data.reshape(-1, 1)
        if calibration_data.shape[1] == 1:
            calibration_data = np.column_stack([calibration_data, calibration_data])
        calibration_data = calibration_data[:, :-1]
        return calibration_data
    except (ValueError, TypeError, OSError, FileNotFoundError) as e:
        logger.exception("Failed to load calibration data: %s", e)
        raise ValueError(f"Failed to load calibration data: {e}") from e


def _run_quantizer(model, model_name: str, quantization_type: str, calibration_data, quantized_model_path: str):
    """构造量化器并执行量化。

    返回 ``(quantized_model, result, quantizer)``。
    量化器实例一并返回,供调用方查询模型大小等元信息。
    """
    # P0#3 解耦: 通过 research_bridge 延迟导入
    from app.ai.lnn._research_bridge import (
        get_quantizer_factory,
        get_quantization_config,
        get_quantization_type_enum,
    )

    Quantizer = get_quantizer_factory()
    QuantizationConfig = get_quantization_config()
    QuantizationType: Any = get_quantization_type_enum()
    if any(x is None for x in (Quantizer, QuantizationConfig, QuantizationType)):
        raise ImportError("Quantization module is not available. Ensure the research package is installed with torch.")
    assert Quantizer is not None and QuantizationConfig is not None and QuantizationType is not None

    quant_type = QuantizationType.DYNAMIC if quantization_type == "dynamic" else QuantizationType.STATIC
    quant_config = QuantizationConfig(quantization_type=quant_type)

    quantizer = Quantizer(quant_config)

    quantized_model, result = quantizer.quantize(
        model=model,
        calibration_data=calibration_data,
        save_path=quantized_model_path,
        metadata={
            "base_model": model_name,
            "quantization_type": quantization_type,
        },
    )
    return quantized_model, result, quantizer


def _register_quantized_model(
    model_name: str,
    entry,
    quantized_model_path: str,
    original_size: int,
    quantized_size: int,
    result,
    quantization_type: str,
) -> None:
    """将量化模型注册到 pytorch_registry。"""
    from app.api.v1.lnn.dependencies import pytorch_registry

    quantized_model_name = get_quantized_model_name(model_name)
    pytorch_registry.register_quantized_model(
        model_name=quantized_model_name,
        model_type=ModelType(entry.config.model_type) if entry.config and entry.config.model_type else ModelType.CFC,
        model_path=quantized_model_path,
        metadata={
            "quantization_type": quantization_type,
            "quantization_date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "original_size_bytes": original_size,
            "quantized_size_bytes": quantized_size,
            "compression_ratio": result.compression_ratio,
            "speedup_ratio": result.speedup_ratio,
        },
    )
