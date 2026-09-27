"""Random Forest基准模型实现。"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestRegressor


class RFBaseline:
    def __init__(self, config: dict | None = None) -> None:
        default_config = {
            "n_estimators": 200,
            "max_depth": 15,
            "min_samples_split": 5,
            "min_samples_leaf": 2,
            "random_state": 42,
            "n_jobs": -1,
        }
        if config:
            default_config.update(config)
        self.model = RandomForestRegressor(**default_config)
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
            "n_estimators": self._config["n_estimators"],
        }

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.model.predict(X)

    def get_params_count(self) -> int:
        if not self._fitted:
            return 0
        total = sum(tree.tree_.node_count if hasattr(tree, "tree_") else 0 for tree in self.model.estimators_)
        return total * 3

    def get_model_size_mb(self) -> float:
        try:
            from app.benchmarks.metrics import measure_model_size_mb

            return measure_model_size_mb(self.model)
        except (ImportError, OSError, AttributeError) as e:
            import logging

            logging.getLogger(__name__).debug("RF model size measurement failed: %s", e)
            return 0.0
