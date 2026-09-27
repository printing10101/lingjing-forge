"""SVR基准模型实现。"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
from sklearn.svm import SVR


class SVMBaseline:
    def __init__(self, config: dict | None = None) -> None:
        default_config = {
            "kernel": "rbf",
            "C": 1.0,
            "epsilon": 0.1,
            "gamma": "scale",
            "cache_size": 500,
        }
        if config:
            default_config.update(config)
        self.model = SVR(**default_config)
        self._config = default_config
        self._fitted = False

    def fit(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        X_val: np.ndarray | None = None,
        y_val: np.ndarray | None = None,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        self.model.fit(X_train, y_train)
        elapsed = time.perf_counter() - t0
        self._fitted = True
        return {
            "training_time_s": elapsed,
            "n_support_vectors": len(self.model.support_vectors_),
        }

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.model.predict(X)

    def get_params_count(self) -> int:
        if not self._fitted:
            return 0
        n_sv = len(self.model.support_vectors_)
        n_features = self.model.support_vectors_.shape[1]
        return (n_sv + 1) * n_features + 1

    def get_model_size_mb(self) -> float:
        try:
            from app.benchmarks.metrics import measure_model_size_mb

            return measure_model_size_mb(self.model)
        except (ImportError, OSError, AttributeError) as e:
            import logging

            logging.getLogger(__name__).debug("SVM model size measurement failed: %s", e)
            return 0.0
