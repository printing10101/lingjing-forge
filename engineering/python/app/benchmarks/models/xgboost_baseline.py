"""XGBoost基准模型实现。

实现统一的Baseline接口标准。
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import xgboost as xgb


class XGBoostBaseline:
    def __init__(self, config: dict | None = None) -> None:
        default_config = {
            "n_estimators": 200,
            "max_depth": 6,
            "learning_rate": 0.1,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "random_state": 42,
            "verbosity": 0,
        }
        if config:
            default_config.update(config)
        self.model = xgb.XGBRegressor(**default_config)
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
        eval_set = [(X_train, y_train)]
        if X_val is not None and y_val is not None:
            eval_set.append((X_val, y_val))
        self.model.fit(
            X_train,
            y_train,
            eval_set=eval_set if len(eval_set) > 1 else None,
            verbose=False,
        )
        elapsed = time.perf_counter() - t0
        self._fitted = True
        return {
            "training_time_s": elapsed,
            "n_estimators": len(self.model.get_booster().get_dump()),
        }

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.model.predict(X)

    def get_params_count(self) -> int:
        if not self._fitted:
            return 0
        try:
            booster = self.model.get_booster()
            dump = booster.get_dump()
            total_nodes = sum(d.count("leaf=") + d.count("yes=") + d.count("no=") for d in dump)
            return total_nodes
        except (AttributeError, ValueError) as e:
            import logging

            logging.getLogger(__name__).debug("XGBoost params count fallback: %s", e)
            return len(self.model.get_booster().get_dump()) * 10

    def get_model_size_mb(self) -> float:
        if not self._fitted:
            return 0.0
        try:
            from app.benchmarks.metrics import measure_model_size_mb

            return measure_model_size_mb(self.model)
        except (ImportError, OSError, AttributeError) as e:
            import logging

            logging.getLogger(__name__).debug("XGBoost model size measurement failed: %s", e)
            return 0.0
