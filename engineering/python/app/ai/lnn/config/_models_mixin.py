"""LNN 配置模型管理 mixin（从 config_manager 拆出）。"""

from __future__ import annotations

import logging
from typing import Any
from collections.abc import Callable

from app.ai.lnn.config._schemas import DatasetCacheConfig, ModelConfig

logger = logging.getLogger(__name__)


class _ModelsMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）
    _build_config_object: Callable[..., Any]
    _config: Any
    _is_dirty: Any
    _raw_config: Any

    def get_model_config(self, model_name: str) -> ModelConfig | None:
        """
        获取指定模型的配置

        Args:
            model_name: 模型名称

        Returns:
            ModelConfig对象或None
        """
        models = self._raw_config.get("lnn", {}).get("models", {})
        model_data = models.get(model_name)
        if model_data is None:
            return None

        return ModelConfig(
            type=model_data.get("type", "cfc"),
            path=model_data.get("path", ""),
            enabled=model_data.get("enabled", True),
            device=model_data.get("device", self._config.lnn.default_device),
            input_dim=model_data.get("input_dim", 128),
            output_dim=model_data.get("output_dim", 10),
            hidden_dim=model_data.get("hidden_dim", 256),
            num_layers=model_data.get("num_layers", 2),
            dropout_rate=model_data.get("dropout_rate", 0.1),
            temporal_horizon=model_data.get("temporal_horizon", 1000),
            metadata=model_data.get("metadata"),
        )

    def add_model(self, model_name: str, model_config: dict[str, Any]) -> None:
        """
        添加新模型配置

        Args:
            model_name: 模型名称
            model_config: 模型配置字典
        """
        if "lnn" not in self._raw_config:
            self._raw_config["lnn"] = {"models": {}}
        if "models" not in self._raw_config["lnn"]:
            self._raw_config["lnn"]["models"] = {}

        self._raw_config["lnn"]["models"][model_name] = model_config
        self._is_dirty = True
        self._build_config_object()
        logger.info("Model config added: %s", model_name)

    def remove_model(self, model_name: str) -> bool:
        """
        移除模型配置

        Args:
            model_name: 模型名称

        Returns:
            是否成功移除
        """
        models = self._raw_config.get("lnn", {}).get("models", {})
        if model_name in models:
            del self._raw_config["lnn"]["models"][model_name]
            self._is_dirty = True
            self._build_config_object()
            logger.info("Model config removed: %s", model_name)
            return True
        return False

    def get_dataset_cache_config(self) -> DatasetCacheConfig:
        """
        获取数据集缓存配置

        Returns:
            DatasetCacheConfig对象
        """
        return self._config.dataset_cache

    def set_dataset_cache_config(self, config: DatasetCacheConfig) -> None:
        """
        设置数据集缓存配置

        Args:
            config: DatasetCacheConfig对象
        """
        self._raw_config["dataset_cache"] = {
            "cache_directory": config.cache_directory,
            "max_cache_size": config.max_cache_size,
            "memory_cache_size": config.memory_cache_size,
            "cache_eviction_policy": config.cache_eviction_policy,
            "enabled": config.enabled,
        }
        self._is_dirty = True
        self._build_config_object()
        logger.info("Dataset cache config updated")
