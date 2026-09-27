"""LNNPredictor 加载器（LRU 缓存 + 线程安全）.

从原 ``explainability_service.py`` 拆分。封装 model_uri 解析与 predictor 缓存，
缓存上限 4（与 ``world_model/plugin.py`` 对齐），LRU 淘汰策略。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.contracts.explainability import (
    ExplanationValidationError,
    ProjectionError,
)


logger = logging.getLogger(__name__)


class PredictorLoader:
    """LNNPredictor LRU 缓存加载器.

    Parameters
    ----------
    cache_limit : int
        LRU 缓存上限（默认 4，与 ``world_model/plugin.py`` 对齐）。
    """

    def __init__(self, cache_limit: int = 4) -> None:
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._cache_limit = cache_limit

    @staticmethod
    def parse_model_uri(model_uri: str) -> str:
        """解析 model_uri 为 model_name.

        支持格式：
        - ``model://<model_name>/<version>``
        - ``model://<model_name>``
        - ``<model_name>``（直接使用）

        Returns
        -------
        str
            模型名称（用于 ModelRegistry.get / from_registry）。
        """
        if not model_uri:
            raise ExplanationValidationError("model_uri 不能为空")
        if model_uri.startswith("model://"):
            rest = model_uri[len("model://") :]
            # 去除 version 部分（首个 / 之后）
            if "/" in rest:
                return rest.split("/", 1)[0]
            return rest
        return model_uri

    def get(self, model_uri: str):
        """获取或加载 LNNPredictor（LRU 缓存，limit=4）.

        Returns
        -------
        LNNPredictor
            已加载的预测器实例。

        Raises
        ------
        ProjectionError
            模型加载失败。
        """
        # 快速路径：缓存命中
        predictor = self._cache.get(model_uri)
        if predictor is not None:
            return predictor

        with self._lock:
            predictor = self._cache.get(model_uri)
            if predictor is not None:
                return predictor

            # 加载模型：model_uri → model_name → 注册表加载
            # （加载方式与 api/v1/lnn/routes_prediction._load_model_for_predict 一致）
            model_name = self.parse_model_uri(model_uri)
            try:
                from app.ai.lnn.inference.predictor import LNNPredictor

                from app.services.model_registry_service import get_model_registry_service

                predictor = LNNPredictor.from_registry(
                    registry=get_model_registry_service().model_registry,
                    model_name=model_name,
                    use_amp=True,
                    auto_device=True,
                )
            except ImportError as exc:
                raise ProjectionError(f"无法加载模型: {model_uri}（{exc}）") from exc

            # LRU 淘汰
            if len(self._cache) >= self._cache_limit:
                oldest_uri = next(iter(self._cache))
                del self._cache[oldest_uri]
            self._cache[model_uri] = predictor
            return predictor


__all__ = ["PredictorLoader"]
