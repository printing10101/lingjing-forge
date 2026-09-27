"""_CostPriceMixin (split from MultiDimensionCostTracker)."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.budget._cost_models import (  # noqa: F401
    CostDimension,
    CostType,
    ProviderType,
    ModelType,
    CostUnitPrice,
    CostEvent,
    CostSummary,
    BudgetEvent,
)

logger = logging.getLogger(__name__)


class _CostPriceMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _conn: Any
    _unit_prices: Any

    def _load_unit_prices(self) -> None:
        """加载单价配置"""
        defaults = {
            "gpu_time_per_second": 0.0001,
            "gpu_memory_per_gb_second": 0.00005,
            "api_call_per_request": 0.001,
            "data_transfer_per_mb": 0.0001,
        }
        for key, val in defaults.items():
            row = self._conn.execute("SELECT price_value FROM unit_price_config WHERE price_key = ?", (key,)).fetchone()
            if row:
                setattr(self._unit_prices, key, row["price_value"])
            else:
                self._conn.execute(
                    "INSERT OR IGNORE INTO unit_price_config (price_key, price_value, updated_at) VALUES (?, ?, ?)",
                    (key, val, time.time()),
                )

        self._conn.commit()

    def set_unit_price(self, key: str, value: float) -> None:
        """设置单价"""
        self._conn.execute(
            "INSERT OR REPLACE INTO unit_price_config (price_key, price_value, updated_at) VALUES (?, ?, ?)",
            (key, value, time.time()),
        )
        self._conn.commit()
        setattr(self._unit_prices, key, value)
        logger.info("Unit price updated: %s = %f", key, value)

    def get_unit_prices(self) -> dict[str, float]:
        """获取所有单价"""
        return self._unit_prices.to_dict()
