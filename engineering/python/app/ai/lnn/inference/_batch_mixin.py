"""LNN 批量/流式推理 mixin（从 predictor 拆出）。"""

from __future__ import annotations

import time
from typing import Any
from collections.abc import Callable

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

from app.ai.lnn.inference.predictor_types import PredictionResult

# 阶段2 解耦改造：models/ 已迁移到 research/models/。
try:
    from app.ai.lnn.models.base_lnn import BaseLNNModel

    _HAS_TORCH_MODELS = True
except ImportError:
    BaseLNNModel = None
    _HAS_TORCH_MODELS = False


class _BatchMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）
    _compute_confidence: Callable[..., Any]
    _get_memory_usage_mb: Callable[..., Any]
    _maybe_inverse_transform: Callable[..., Any]
    _postprocess: Callable[..., Any]
    _preprocess: Callable[..., Any]
    _to_tensor: Callable[..., Any]
    _update_stats: Callable[..., Any]
    model: Callable[..., Any]
    predict: Callable[..., Any]
    device: Any
    model_name: Any
    use_amp: Any

    def predict_batch(
        self,
        batch_data: list[Any],
        batch_size: int = 32,
    ) -> list[PredictionResult]:
        """
        Batch prediction with memory control

        Args:
            batch_data: List of input data
            batch_size: Batch size for memory management

        Returns:
            List of PredictionResult objects
        """
        results = []
        for i in range(0, len(batch_data), batch_size):
            chunk = batch_data[i : i + batch_size]
            batch_results = self._predict_batch_chunk(chunk)
            results.extend(batch_results)
        return results

    def _predict_batch_chunk(self, chunk: list[Any]) -> list[PredictionResult]:
        """
        优化的批量预测分块处理

        性能优化点：
        - 使用 torch.inference_mode 替代 torch.no_grad
        - 减少中间张量拷贝
        - 优化内存分配
        """
        features_list = []
        hidden_list = []
        for data in chunk:
            features, hidden = self._preprocess(data)
            features_list.append(features)
            hidden_list.append(hidden)

        batch_features = np.concatenate(features_list, axis=0)

        start_time = time.perf_counter()

        if _HAS_TORCH_MODELS and isinstance(self.model, BaseLNNModel):
            outputs = self.model.predict(batch_features)
        else:
            batch_tensor = self._to_tensor(batch_features)

            if HAS_TORCH:
                # 优化：使用 torch.inference_mode 获得更好性能
                with torch.inference_mode():
                    if self.use_amp and self.device.type == "cuda" and HAS_AMP:
                        with autocast():
                            outputs = self.model(batch_tensor)
                    else:
                        outputs = self.model(batch_tensor)
            else:
                # torch 不可用时降级为直接调用
                outputs = self.model(batch_tensor)

        inference_time = (time.perf_counter() - start_time) * 1000
        mem_after = self._get_memory_usage_mb()

        if HAS_TORCH and isinstance(outputs, torch.Tensor):
            outputs = outputs.detach().cpu().numpy()

        if isinstance(outputs, np.ndarray):
            outputs = self._maybe_inverse_transform(outputs)

        results = []
        per_sample_time = inference_time / len(chunk)
        for i, output in enumerate(outputs):
            processed = self._postprocess(output, hidden_list[i])
            confidence = self._compute_confidence(output)
            result = PredictionResult(
                value=processed,
                confidence=confidence,
                inference_time=per_sample_time,
                model_info={"name": self.model_name, "device": str(self.device)},
            )
            results.append(result)

        self._update_stats(per_sample_time, mem_after)
        return results

    def predict_streaming(
        self,
        data_stream,
        return_confidence: bool = False,
    ):
        """
        Streaming prediction for continuous data

        Args:
            data_stream: Iterator or generator of input data
            return_confidence: Whether to return confidence scores

        Yields:
            Prediction results one by one
        """
        for item in data_stream:
            yield self.predict(item, return_confidence=return_confidence)
