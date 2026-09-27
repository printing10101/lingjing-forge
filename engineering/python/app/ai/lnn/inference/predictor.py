"""
Predictor Module

Implements single-sample, batch, and streaming inference interfaces for LNN models.
Supports automatic device selection and AMP (Automatic Mixed Precision) acceleration.

本模块为门面：实现已拆分至 _batch_mixin / _stats_mixin / _registry_mixin / predictor_types。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Union

import numpy as np

try:
    import torch

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

try:
    from torch.cuda.amp import autocast

    HAS_AMP = True
except ImportError:
    HAS_AMP = False

from app.ai.lnn.core import EngineType
from app.ai.lnn.preprocessing import DataPreprocessor
from app.ai.lnn.postprocessing import ResultPostprocessor
from app.ai.lnn.inference._batch_mixin import _BatchMixin
from app.ai.lnn.inference._intermediates import _IntermediatesMixin
from app.ai.lnn.inference._mc_dropout import _MCDropoutMixin
from app.ai.lnn.inference._registry_mixin import _RegistryMixin
from app.ai.lnn.inference._stats_mixin import _StatsMixin
from app.ai.lnn.inference.predictor_types import PredictionResult  # noqa: F401

# 阶段2 解耦改造：models/ 已迁移到 research/models/。
# 工程侧推理路径应改为加载 ONNX（见 onnx_predictor.py）。
try:
    from app.ai.lnn.models.base_lnn import BaseLNNModel

    _HAS_TORCH_MODELS = True
except ImportError:
    BaseLNNModel = None
    _HAS_TORCH_MODELS = False

try:
    from app.database.constraints import CuttingConstraintValidator

    _HAS_CONSTRAINT_VALIDATOR = True
except ImportError:
    _HAS_CONSTRAINT_VALIDATOR = False

logger = logging.getLogger(__name__)


class LNNPredictor(
    _MCDropoutMixin,
    _IntermediatesMixin,
    _BatchMixin,
    _StatsMixin,
    _RegistryMixin,
):
    """
    LNN Predictor with single, batch, and streaming inference support.

    Features:
    - Single sample prediction
    - Batch prediction with configurable batch size
    - Streaming prediction for continuous data
    - Automatic device selection (CPU/GPU/MPS)
    - AMP (Automatic Mixed Precision) acceleration
    - Inference statistics tracking
    """

    def __init__(
        self,
        model,
        preprocessor: DataPreprocessor | None = None,
        postprocessor: ResultPostprocessor | None = None,
        model_name: str | None = None,
        engine_type: EngineType = EngineType.LNN,
        use_amp: bool = True,
        auto_device: bool = True,
        material_id: str | None = None,
        tool_id: str | None = None,
        machine_id: str | None = None,
    ):
        """
        Initialize LNN Predictor

        Args:
            model: LNN model instance
            preprocessor: Optional data preprocessor
            postprocessor: Optional result postprocessor
            model_name: Model name identifier
            engine_type: Inference engine type
            use_amp: Enable automatic mixed precision
            auto_device: Enable automatic device selection
        """
        self.model = model
        self.preprocessor = preprocessor or DataPreprocessor()
        self.postprocessor = postprocessor or ResultPostprocessor()
        self.model_name = model_name or getattr(model, "model_name", "unknown")
        self.engine_type = engine_type
        self.use_amp = use_amp and HAS_AMP
        self.auto_device = auto_device

        # MC Dropout 并发保护锁：predict_mc_dropout 在切换 model.train(True)/eval()
        # 期间必须独占访问模型状态，否则并发请求的 eval() 会关闭其他请求的 dropout。
        # 使用 RLock（可重入锁）：predict_mc_dropout 内部可能间接调用其他也需要此锁的方法。
        self._mc_lock = threading.RLock()

        # 统计数据并发保护锁：_update_stats (写) 与 get_statistics/get_performance (读)
        # 在多线程并发推理时会同时操作 self._stats 字典，缺少锁保护会导致计数丢失、
        # 极值错乱、inference_times 列表竞争。使用独立 Lock 避免与 MC Dropout 锁耦合。
        self._stats_lock = threading.Lock()

        self.device = self._select_device()
        if HAS_TORCH and hasattr(self.model, "to"):
            self.model.to(self.device)

        self._material_id = material_id
        self._tool_id = tool_id
        self._machine_id = machine_id
        self._constraint_validator: CuttingConstraintValidator | None = None
        if _HAS_CONSTRAINT_VALIDATOR and material_id and tool_id:
            self._constraint_validator = CuttingConstraintValidator()
            logger.info(
                "物理约束校验已启用 material=%s tool=%s machine=%s",
                material_id,
                tool_id,
                machine_id or "none",
            )

        self._stats = {
            "total_inferences": 0,
            "total_inference_time_ms": 0.0,
            "max_inference_time_ms": 0.0,
            "min_inference_time_ms": float("inf"),
            "peak_memory_mb": 0.0,
            "inference_times": [],
            "window_start": time.perf_counter(),
            "window_inferences": 0,
        }
        self._max_recent_times = 10_000

        # Trace log persistence
        self._trace_log_path = os.path.join(os.getcwd(), "data", "traces", "trace_log.jsonl")
        self._trace_log_enabled = True
        try:
            Path(self._trace_log_path).parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("无法创建 trace log 目录: %s", exc)
            self._trace_log_enabled = False

    def _select_device(self) -> Any:
        """Automatically select best available device"""
        if not self.auto_device or not HAS_TORCH:
            # 修复 P1: HAS_TORCH=False 时 torch 为 None，torch.device("cpu") 会抛 TypeError。
            # 此时返回字符串 "cpu" 作为降级设备标识，下游无需构造 torch.device 对象即可工作。
            if not HAS_TORCH:
                return "cpu"
            return torch.device("cpu")
        if torch.cuda.is_available():
            return torch.device("cuda")
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")

    def _preprocess(self, data: Any) -> tuple[np.ndarray, dict[str, Any] | None]:
        """
        Preprocess input data

        Args:
            data: Raw input data

        Returns:
            Tuple of (processed features, metadata dict)

        Note:
            若预处理器未拟合，``DataPreprocessor.transform`` 会在输入数据上自动
            ``fit``。生产环境应通过 ``preprocessor`` 参数传入已拟合的预处理器，
            以避免在单样本上拟合导致的统计量偏差。
        """
        input_array = self._standardize_input(data)
        preprocessed = self.preprocessor.transform(input_array)
        return preprocessed.features, {"input_shape": input_array.shape}

    def _postprocess(self, output: Any, hidden: dict[str, Any] | None = None) -> Any:
        """
        Postprocess model output

        Args:
            output: Raw model output
            hidden: Intermediate computation results

        Returns:
            Processed output
        """
        if HAS_TORCH and isinstance(output, torch.Tensor):
            output = output.detach().cpu().numpy()
        return output

    def _maybe_inverse_transform(self, predictions: np.ndarray) -> np.ndarray:
        if self.preprocessor.is_fitted and hasattr(self.preprocessor, "mean_") and self.preprocessor.mean_ is not None:
            if predictions.shape[-1] == self.preprocessor.mean_.shape[0]:
                return self.preprocessor.inverse_transform(predictions)
        return predictions

    def predict(
        self,
        input_data: Any,
        return_confidence: bool = False,
    ) -> Union[PredictionResult, Any]:
        """
        优化的单次预测接口

        性能优化点：
        - 减少不必要的类型转换
        - 优化设备同步
        - 减少内存拷贝
        """
        start_time = time.perf_counter()

        try:
            features, hidden = self._preprocess(input_data)

            if _HAS_TORCH_MODELS and isinstance(self.model, BaseLNNModel):
                output = self.model.predict(features)
            else:
                features_tensor = self._to_tensor(features)

                if HAS_TORCH:
                    # 优化：使用 torch.inference_mode 替代 torch.no_grad 以获得更好性能
                    with torch.inference_mode():
                        if self.use_amp and self.device.type == "cuda" and HAS_AMP:
                            with autocast():
                                output = self.model(features_tensor)
                        else:
                            output = self.model(features_tensor)
                else:
                    # torch 不可用时降级为直接调用（测试环境 / ONNX 推理路径）
                    output = self.model(features_tensor)

            processed_output = self._postprocess(output, hidden)
            if isinstance(processed_output, np.ndarray):
                processed_output = self._maybe_inverse_transform(processed_output)

            inference_time = (time.perf_counter() - start_time) * 1000
            mem_after = self._get_memory_usage_mb()
            self._update_stats(inference_time, mem_after)

            # 持久化真实推理性能数据
            input_shape = features.shape if hasattr(features, "shape") else (1,)
            self._write_trace(inference_time, input_shape, success=True)

            try:
                from app.utils.utils import get_metrics_collector

                m = get_metrics_collector()
                m.record_lnn_inference(self.model_name, inference_time / 1000.0)
                m.record_lnn_prediction(self.model_name, "success")
            except (ImportError, AttributeError, RuntimeError, ValueError) as inner_e:
                logger.debug(
                    f"Failed to record inference metrics for {self.model_name}: {inner_e}",
                    exc_info=True,
                )

            confidence = self._compute_confidence(output) if return_confidence else 0.0

            constraint_result = None
            if self._constraint_validator and isinstance(processed_output, dict):
                try:
                    constraint_result = self._constraint_validator.validate(
                        material_id=self._material_id or "",
                        tool_id=self._tool_id or "",
                        params=processed_output,
                        machine_id=self._machine_id,
                    )
                    if constraint_result.adjusted_params:
                        for k, v in constraint_result.adjusted_params.items():
                            if k in processed_output:
                                processed_output[k] = v
                    if constraint_result.warnings:
                        logger.warning(
                            "物理约束校验警告: %s",
                            "; ".join(constraint_result.warnings),
                        )
                except (ValueError, TypeError, AttributeError, KeyError, RuntimeError) as exc:
                    logger.warning("物理约束校验失败: %s", exc, exc_info=True)

            result = PredictionResult(
                value=processed_output,
                confidence=confidence,
                inference_time=inference_time,
                model_info={
                    "name": self.model_name,
                    "device": str(self.device),
                    "constraint_result": constraint_result.to_dict() if constraint_result else None,
                },
            )

            if return_confidence:
                return result
            return result.value

        except (ValueError, TypeError, RuntimeError, OSError) as e:
            logger.error("模型预测失败: %s", e, exc_info=True)
            inference_time = (time.perf_counter() - start_time) * 1000
            self._update_stats(inference_time, self._get_memory_usage_mb())
            try:
                from app.utils.utils import get_metrics_collector

                m = get_metrics_collector()
                m.record_lnn_prediction(self.model_name, "error")
            except (ImportError, AttributeError, RuntimeError, ValueError) as e2:
                logger.debug(
                    f"Failed to record error metrics for {self.model_name}: {e2}",
                    exc_info=True,
                )
            raise RuntimeError(
                "Prediction failed: inference encountered an exception. "
                "Possible causes: 1) input data format mismatch; "
                "2) model weights loading error; 3) GPU OOM. "
                "Please check input data format and model loading."
            ) from e


Predictor = LNNPredictor
