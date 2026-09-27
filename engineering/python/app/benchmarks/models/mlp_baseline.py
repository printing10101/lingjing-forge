"""MLP神经网络基准模型实现。"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
from sklearn.neural_network import MLPRegressor


class MLPBaseline:
    def __init__(self, config: dict | None = None) -> None:
        default_config = {
            "hidden_layer_sizes": (128, 64, 32),
            "activation": "relu",
            "solver": "adam",
            "alpha": 0.0001,
            "batch_size": 32,
            "learning_rate": "adaptive",
            "learning_rate_init": 0.001,
            "max_iter": 300,
            "early_stopping": True,
            "validation_fraction": 0.1,
            "n_iter_no_change": 15,
            "random_state": 42,
        }
        if config:
            default_config.update(config)
        self.model = MLPRegressor(**default_config)
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
            "n_iter": self.model.n_iter_,
            "loss": self.model.loss_,
        }

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.model.predict(X)

    def get_params_count(self) -> int:
        if not self._fitted:
            return 0
        total = 0
        coefs = self.model.coefs_
        intercepts = self.model.intercepts_
        for w in coefs:
            total += w.size
        for b in intercepts:
            total += b.size
        return total

    def get_model_size_mb(self) -> float:
        try:
            from app.benchmarks.metrics import measure_model_size_mb

            return measure_model_size_mb(self.model)
        except (ImportError, OSError, AttributeError) as e:
            import logging

            logging.getLogger(__name__).debug("MLP model size measurement failed: %s", e)
            return 0.0
