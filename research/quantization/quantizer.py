"""
Model Quantization Module

INT8 quantization utilities for LNN models using PyTorch quantization API.
Supports dynamic and static quantization with calibration, performance evaluation,
and model management.

Features:
- Dynamic quantization (quantize_dynamic)
- Static quantization with calibration
- Quantized model save/load
- Performance evaluation (size, speed, memory, accuracy)
"""

import os
import time
import json
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict

import numpy as np

HAS_TORCH_QUANTIZATION = False
TORCH_QUANTIZATION_VERSION = ""
TORCH_QUANTIZATION_ERROR = ""

try:
    import torch
    import torch.nn as nn
    from torch.quantization import quantize_dynamic, get_default_qconfig
    from torch.quantization import prepare, convert

    _torch_version = torch.__version__
    TORCH_QUANTIZATION_VERSION = _torch_version

    _required_functions = [
        ("quantize_dynamic", quantize_dynamic),
        ("get_default_qconfig", get_default_qconfig),
        ("prepare", prepare),
        ("convert", convert),
    ]

    _all_callable = all(callable(fn) for _, fn in _required_functions)

    # Parse semantic version: major.minor.patch
    _version_parts = _torch_version.split(".")
    _major_version = int(_version_parts[0]) if _version_parts[0].isdigit() else 0
    _minor_version = (
        int(_version_parts[1])
        if len(_version_parts) > 1 and _version_parts[1].isdigit()
        else 0
    )

    # torch.quantization introduced in PyTorch 1.3
    # API stable across 1.3-1.10 and backward compatible in 2.x
    _version_supported = (
        _major_version == 1 and _minor_version >= 3
    ) or _major_version >= 2

    if _all_callable and _version_supported:
        HAS_TORCH_QUANTIZATION = True
    else:
        TORCH_QUANTIZATION_ERROR = (
            f"PyTorch {_torch_version} quantization not supported. "
            f"Requires PyTorch >= 1.3.0 (got {_major_version}.{_minor_version}), "
            f"callable={_all_callable}, version_supported={_version_supported}"
        )
except ImportError as e:
    TORCH_QUANTIZATION_ERROR = f"PyTorch quantization import failed: {e}"
except (AttributeError, RuntimeError, ValueError, OSError) as e:
    # 模块顶层初始化可能因 PyTorch API 不兼容、平台不支持等失败
    TORCH_QUANTIZATION_ERROR = f"PyTorch quantization initialization failed: {e}"

logger = logging.getLogger(__name__)

if not HAS_TORCH_QUANTIZATION and TORCH_QUANTIZATION_ERROR:
    logger.warning("PyTorch quantization not available: %s", TORCH_QUANTIZATION_ERROR)
else:
    logger.debug(
        "PyTorch quantization available (version=%s)", TORCH_QUANTIZATION_VERSION
    )


class QuantizationType(str, Enum):
    DYNAMIC = "dynamic"
    STATIC = "static"


@dataclass
class QuantizationConfig:
    """Configuration for model quantization"""

    quantization_type: QuantizationType = QuantizationType.DYNAMIC
    target_dtype: str = "qint8"
    target_layers: List[str] = field(default_factory=lambda: ["Linear"])
    calibration_samples: int = 1000
    calibration_batch_size: int = 32
    output_dir: Optional[str] = None
    preserve_fp32_model: bool = True

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["quantization_type"] = self.quantization_type.value
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QuantizationConfig":
        if "quantization_type" in data:
            data["quantization_type"] = QuantizationType(data["quantization_type"])
        return cls(**data)


@dataclass
class QuantizationResult:
    """Results of model quantization"""

    model_name: str
    quantization_type: QuantizationType
    original_size_bytes: int = 0
    quantized_size_bytes: int = 0
    original_inference_time_ms: float = 0.0
    quantized_inference_time_ms: float = 0.0
    original_accuracy: float = 0.0
    quantized_accuracy: float = 0.0
    compression_ratio: float = 0.0
    speedup_ratio: float = 0.0
    accuracy_drop: float = 0.0
    quantization_time_seconds: float = 0.0
    quantized_model_path: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["quantization_type"] = self.quantization_type.value
        result["size_reduction_percent"] = (
            (1.0 - self.compression_ratio) * 100.0
            if self.compression_ratio > 0
            else 0.0
        )
        result["speedup_percent"] = (
            (self.speedup_ratio - 1.0) * 100.0 if self.speedup_ratio > 0 else 0.0
        )
        return result

    def get_report(self) -> str:
        d = self.to_dict()
        lines = [
            f"Quantization Report: {self.model_name}",
            f"  Type: {self.quantization_type.value}",
            f"  Original size: {self.original_size_bytes / 1024:.2f} KB",
            f"  Quantized size: {self.quantized_size_bytes / 1024:.2f} KB",
            f"  Compression: {d['size_reduction_percent']:.1f}%",
            f"  Original inference: {self.original_inference_time_ms:.2f} ms",
            f"  Quantized inference: {self.quantized_inference_time_ms:.2f} ms",
            f"  Speedup: {d['speedup_percent']:.1f}%",
            f"  Original accuracy: {self.original_accuracy:.4f}",
            f"  Quantized accuracy: {self.quantized_accuracy:.4f}",
            f"  Accuracy drop: {self.accuracy_drop:.4f}",
            f"  Quantization time: {self.quantization_time_seconds:.2f} s",
        ]
        return "\n".join(lines)


class Quantizer:
    """
    INT8 Quantization for LNN models.

    Features:
    - Dynamic quantization via torch.quantization.quantize_dynamic
    - Static quantization with calibration
    - Save/load quantized models
    - Performance evaluation
    """

    def __init__(self, config: Optional[QuantizationConfig] = None):
        self.config = config or QuantizationConfig()
        self.logger = logging.getLogger(__name__)

    def dynamic_quantize(
        self, model: nn.Module, calibration_data: Optional[Any] = None
    ) -> nn.Module:
        if not HAS_TORCH_QUANTIZATION:
            raise RuntimeError(
                "模型量化失败：当前环境中不可用 PyTorch 量化模块。"
                "可能原因：1) PyTorch 未安装或版本不兼容；"
                "2) 使用了不支持量化的 PyTorch 构建版本。"
                "请确认已安装完整 PyTorch（pip install torch），"
                "并检查 PyTorch 版本是否与量化 API 兼容。"
            )

        self.logger.info("Starting dynamic quantization")
        model.eval()

        target_modules = set()
        if "Linear" in self.config.target_layers:
            target_modules.add(nn.Linear)

        if not target_modules:
            target_modules = {nn.Linear}

        quantized_model = quantize_dynamic(
            model,
            target_modules,
            dtype=torch.qint8,
        )

        self.logger.info("Dynamic quantization completed")
        return quantized_model

    def static_quantize(self, model: nn.Module, calibration_data: Any) -> nn.Module:
        if not HAS_TORCH_QUANTIZATION:
            raise RuntimeError(
                "静态量化失败：当前环境中不可用 PyTorch 量化模块。"
                "可能原因：1) PyTorch 未安装或版本不兼容；"
                "2) 使用了不支持量化的 PyTorch 构建版本。"
                "请确认已安装完整 PyTorch（pip install torch），"
                "并检查 PyTorch 版本是否与量化 API 兼容。"
            )

        if calibration_data is None:
            raise ValueError(
                "静态量化失败：缺少校准数据集。"
                "静态量化需要代表性校准数据来估计激活值范围。"
                "请提供至少 100 个样本的校准数据集（numpy array 或 torch tensor），"
                "并通过 'calibration_data' 参数传入。"
            )

        self.logger.info("Starting static quantization with calibration")
        model.eval()

        model.qconfig = torch.quantization.get_default_qconfig("fbgemm")

        prepared_model = prepare(model, inplace=False)

        self._calibrate(prepared_model, calibration_data)

        quantized_model = convert(prepared_model, inplace=False)

        self.logger.info("Static quantization completed")
        return quantized_model

    def _calibrate(self, model: nn.Module, calibration_data: Any) -> None:
        self.logger.info("Calibrating with %s samples", len(calibration_data))
        model.eval()

        samples_processed = 0
        batch_size = self.config.calibration_batch_size

        if isinstance(calibration_data, torch.utils.data.DataLoader):
            for batch in calibration_data:
                if samples_processed >= self.config.calibration_samples:
                    break

                if isinstance(batch, (list, tuple)):
                    inputs = batch[0]
                else:
                    inputs = batch

                if isinstance(inputs, np.ndarray):
                    inputs = torch.from_numpy(inputs.astype(np.float32))

                if inputs.dim() == 1:
                    inputs = inputs.unsqueeze(0)

                # P2-AI-4: 使用 inference_mode 替代 no_grad，校准阶段为纯推理，无需 autograd 图
                with torch.inference_mode():
                    model(inputs)

                samples_processed += inputs.shape[0]
                self.logger.debug("Calibrated %s samples", samples_processed)
        else:
            if isinstance(calibration_data, np.ndarray):
                calibration_data = torch.from_numpy(calibration_data.astype(np.float32))

            total_samples = calibration_data.shape[0]
            num_batches = min(
                self.config.calibration_samples // batch_size,
                (total_samples + batch_size - 1) // batch_size,
            )

            for i in range(num_batches):
                start = i * batch_size
                end = min(start + batch_size, total_samples)
                batch = calibration_data[start:end]

                if batch.dim() == 1:
                    batch = batch.unsqueeze(0)

                # P2-AI-4: 使用 inference_mode 替代 no_grad，校准阶段为纯推理，无需 autograd 图
                with torch.inference_mode():
                    model(batch)

                samples_processed += batch.shape[0]

        self.logger.info(
            f"Calibration completed: {samples_processed} samples processed"
        )

    def save_quantized_model(
        self,
        model: nn.Module,
        save_path: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        save_dir = os.path.dirname(save_path)
        if save_dir and not os.path.exists(save_dir):
            os.makedirs(save_dir, exist_ok=True)

        torch.save(model.state_dict(), save_path)
        self.logger.info("Quantized model saved to %s", save_path)

        if metadata:
            meta_path = save_path + ".meta.json"
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2, default=str)
            self.logger.info("Metadata saved to %s", meta_path)

        return save_path

    def load_quantized_model(
        self,
        model_class: Any,
        model_path: str,
        config: Any,
    ) -> nn.Module:
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"量化模型加载失败：找不到模型文件 '{model_path}'。可能原因：1) 模型尚未量化保存；2) 文件路径错误或文件已被删除/移动。请确认路径是否正确，或先调用量化流程生成模型文件。"
            )

        model = model_class(config)
        model.eval()

        state_dict = torch.load(model_path, map_location="cpu", weights_only=True)
        model.load_state_dict(state_dict)

        self.logger.info("Quantized model loaded from %s", model_path)
        return model

    def evaluate_performance(
        self,
        original_model: nn.Module,
        quantized_model: nn.Module,
        test_data: Any,
        num_samples: int = 100,
    ) -> QuantizationResult:
        original_model.eval()
        quantized_model.eval()

        if isinstance(test_data, np.ndarray):
            test_tensor = torch.from_numpy(test_data.astype(np.float32))
        elif isinstance(test_data, torch.Tensor):
            test_tensor = test_data
        else:
            raise ValueError(
                "测试数据评估失败：'test_data' 参数必须为 numpy 数组（np.ndarray）"
                "或 PyTorch 张量（torch.Tensor）。"
                f"当前类型：{type(test_data).__name__}。"
                "请检查数据类型转换逻辑，确保输入为数值型张量。"
            )

        if test_tensor.dim() == 1:
            test_tensor = test_tensor.unsqueeze(0)

        test_tensor = test_tensor[:num_samples]

        original_times = []
        # P2-AI-4: 使用 inference_mode 替代 no_grad，基准测试为纯推理，无需 autograd 图
        with torch.inference_mode():
            for i in range(len(test_tensor)):
                sample = test_tensor[i : i + 1]
                start = time.perf_counter()
                _ = original_model(sample)
                elapsed = (time.perf_counter() - start) * 1000
                original_times.append(elapsed)

        original_avg_time = np.mean(original_times)

        quantized_times = []
        # P2-AI-4: 使用 inference_mode 替代 no_grad，基准测试为纯推理，无需 autograd 图
        with torch.inference_mode():
            for i in range(len(test_tensor)):
                sample = test_tensor[i : i + 1]
                start = time.perf_counter()
                _ = quantized_model(sample)
                elapsed = (time.perf_counter() - start) * 1000
                quantized_times.append(elapsed)

        quantized_avg_time = np.mean(quantized_times)

        original_params = sum(p.numel() for p in original_model.parameters())
        original_size = original_params * 4

        quantized_size = original_size // 4 if original_size > 0 else 0

        compression_ratio = 0.25 if original_size > 0 else 0.0
        speedup_ratio = (
            original_avg_time / quantized_avg_time if quantized_avg_time > 0 else 1.0
        )

        result = QuantizationResult(
            model_name=getattr(original_model, "model_name", "unknown"),
            quantization_type=self.config.quantization_type,
            original_size_bytes=original_size,
            quantized_size_bytes=quantized_size,
            original_inference_time_ms=original_avg_time,
            quantized_inference_time_ms=quantized_avg_time,
            compression_ratio=compression_ratio,
            speedup_ratio=speedup_ratio,
        )

        return result

    def get_model_size(self, model_path: str) -> int:
        if not os.path.exists(model_path):
            return 0
        return os.path.getsize(model_path)

    def quantize(
        self,
        model: nn.Module,
        calibration_data: Optional[Any] = None,
        save_path: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[nn.Module, QuantizationResult]:
        start_time = time.perf_counter()

        if self.config.quantization_type == QuantizationType.DYNAMIC:
            quantized_model = self.dynamic_quantize(model, calibration_data)
        elif self.config.quantization_type == QuantizationType.STATIC:
            if calibration_data is None:
                raise ValueError(
                    "量化流程执行失败：'STATIC' 量化模式缺少校准数据。"
                    "可能原因：调用量化 API 时未传入 calibration_data 参数。"
                    "解决方案：1) 提供代表性校准数据集；"
                    "或 2) 将 quantization_type 改为 'DYNAMIC' 模式（无需校准数据）。"
                )
            quantized_model = self.static_quantize(model, calibration_data)
        else:
            raise ValueError(
                f"量化流程执行失败：未知的量化类型 '{self.config.quantization_type}'。"
                "支持的量化类型为：DYNAMIC（动态量化，无需校准数据）、"
                "STATIC（静态量化，需要校准数据）。"
                "请检查 QuantizationConfig.quantization_type 配置值。"
            )

        quantization_time = time.perf_counter() - start_time

        original_params = sum(p.numel() for p in model.parameters())
        original_size = original_params * 4

        result = QuantizationResult(
            model_name=getattr(model, "model_name", "unknown"),
            quantization_type=self.config.quantization_type,
            original_size_bytes=original_size,
            quantized_size_bytes=original_size // 4,
            compression_ratio=0.25,
            quantization_time_seconds=quantization_time,
        )

        if save_path:
            meta = metadata or {}
            meta.update(
                {
                    "quantization_type": self.config.quantization_type.value,
                    "quantization_date": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "original_model": getattr(model, "model_name", "unknown"),
                }
            )
            self.save_quantized_model(quantized_model, save_path, meta)
            result.quantized_model_path = save_path
            result.quantized_size_bytes = self.get_model_size(save_path)

        return quantized_model, result
