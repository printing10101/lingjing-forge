"""
YAML Configuration Management Module

Provides YAML-based configuration loading, validation, access, and persistence
for the LNN workflow system with environment adaptation and runtime updates.

本模块为门面：实现已拆分至 _schemas / _validation_mixin / _persistence_mixin / _models_mixin。
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime
from typing import Any

try:
    import torch

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

from app.ai.lnn.config._models_mixin import _ModelsMixin
from app.ai.lnn.config._persistence_mixin import _PersistenceMixin
from app.ai.lnn.config._schemas import (  # noqa: F401
    AppConfig,
    DatasetCacheConfig,
    EnvironmentConfig,
    LNNConfig,
    ModelConfig,
    ThresholdConfig,
    WorkflowConfig,
)
from app.ai.lnn.config._validation_mixin import _ValidationMixin

logger = logging.getLogger(__name__)


class YAMLConfigManager(_PersistenceMixin, _ValidationMixin, _ModelsMixin):
    """
    YAML配置管理器

    功能：
    - 从YAML文件加载配置
    - 验证配置结构和参数合法性
    - 提供类型安全的配置访问接口
    - 支持运行时动态更新配置
    - 将修改的配置持久化到文件系统
    - 根据运行环境自动调整配置
    """

    DEFAULT_CONFIG = {
        "lnn": {
            "enabled": True,
            "models_dir": "models/lnn",
            "default_device": "cpu",
            "models": {
                "cfc_fast": {
                    "type": "cfc",
                    "path": "cfc_fast_v1.pt",
                    "enabled": True,
                    "input_dim": 128,
                    "output_dim": 10,
                    "hidden_dim": 256,
                    "num_layers": 2,
                    "dropout_rate": 0.1,
                },
                "ltc_timeseries": {
                    "type": "ltc",
                    "path": "ltc_timeseries_v1.pt",
                    "enabled": True,
                    "input_dim": 64,
                    "output_dim": 32,
                    "hidden_dim": 128,
                    "num_layers": 2,
                    "dropout_rate": 0.1,
                    "temporal_horizon": 1000,
                },
                "hybrid_multimodal": {
                    "type": "hybrid_lnn",
                    "path": "hybrid_multimodal_v1.pt",
                    "enabled": True,
                    "input_dim": 256,
                    "output_dim": 10,
                    "hidden_dim": 512,
                    "num_layers": 3,
                    "dropout_rate": 0.1,
                },
                "cutting_force": {
                    "type": "cfc",
                    "path": "cutting_force_v1.pt",
                    "enabled": True,
                },
                "wear_prediction": {
                    "type": "ltc",
                    "path": "wear_prediction_v1.pt",
                    "enabled": True,
                },
            },
            "thresholds": {
                "quick": 0.85,
                "hybrid": 0.60,
                "complexity": 3,
            },
        },
        "workflow": {
            "enabled": True,
            "max_steps": 10,
            "timeout_seconds": 300,
            "enable_fallback": True,
            "fallback_engine": "Rule",
        },
        "dataset_cache": {
            "cache_directory": "~/.lingjing/cache/datasets/",
            "max_cache_size": 5368709120,
            "memory_cache_size": 1073741824,
            "cache_eviction_policy": "lru",
            "enabled": True,
        },
        "environment": {
            "name": "development",
            "debug": True,
        },
    }

    REQUIRED_LNN_KEYS = ["enabled", "models_dir", "default_device"]
    REQUIRED_MODEL_KEYS = ["type", "path"]
    REQUIRED_THRESHOLD_KEYS = ["quick", "hybrid", "complexity"]

    VALID_MODEL_TYPES = ["cfc", "ltc", "hybrid_lnn"]
    VALID_ENVIRONMENTS = ["development", "staging", "production", "testing"]

    def __init__(self, config_path: str | None = None, use_defaults: bool = True):
        """
        初始化配置管理器

        Args:
            config_path: YAML配置文件路径
            use_defaults: 是否使用默认配置作为基础
        """
        self.config_path = config_path
        self._raw_config: dict[str, Any] = {}
        self._config: AppConfig = AppConfig()
        self._last_modified: datetime | None = None
        self._cache_enabled = True
        self._is_dirty = False

        if use_defaults:
            self._raw_config = copy.deepcopy(self.DEFAULT_CONFIG)

        if config_path:
            self.load(config_path)

        self._apply_environment_adaptations()
        self._build_config_object()

    def get(self, section: str, key: str | None = None, default: Any = None) -> Any:
        """
        获取配置值（类型安全）

        Args:
            section: 配置节名称（如 "lnn", "workflow"）
            key: 配置键（可选，为None时返回整个节）
            default: 默认值（当配置不存在时返回）

        Returns:
            配置值

        Examples:
            >>> config.get("lnn", "enabled")
            True
            >>> config.get("lnn", "thresholds", {}).get("quick")
            0.85
        """
        section_data = self._raw_config.get(section)
        if section_data is None:
            return default

        if key is None:
            return section_data

        if isinstance(key, str):
            keys = key.split(".")
        else:
            keys = [key]

        current = section_data
        for k in keys:
            if isinstance(current, dict):
                current = current.get(k)
                if current is None:
                    return default
            else:
                return default

        return current

    def set(self, section: str, key: str, value: Any) -> None:
        """
        动态更新配置参数

        Args:
            section: 配置节名称
            key: 配置键（支持点分隔的嵌套键，如 "thresholds.quick"）
            value: 新值

        Raises:
            ValueError: 配置值验证失败

        Examples:
            >>> config.set("lnn", "enabled", False)
            >>> config.set("lnn", "thresholds.quick", 0.90)
        """
        if section not in self._raw_config:
            self._raw_config[section] = {}

        # 验证配置值
        self._validate_config_value(section, key, value)

        if "." in key:
            keys = key.split(".")
            current = self._raw_config[section]
            for k in keys[:-1]:
                if k not in current or not isinstance(current[k], dict):
                    current[k] = {}
                current = current[k]
            current[keys[-1]] = value
        else:
            self._raw_config[section][key] = value

        self._is_dirty = True
        self._build_config_object()
        logger.debug("Config updated: %s.%s = %s", section, key, value)

    def to_dict(self) -> dict[str, Any]:
        """将配置转换为字典格式"""
        return copy.deepcopy(self._raw_config)

    def to_dataclass(self) -> AppConfig:
        """获取类型安全的配置对象"""
        return self._config

    def is_dirty(self) -> bool:
        """检查配置是否有未保存的修改"""
        return self._is_dirty

    def reset_to_defaults(self) -> None:
        """重置配置为默认值"""
        self._raw_config = copy.deepcopy(self.DEFAULT_CONFIG)
        self._apply_environment_adaptations()
        self._build_config_object()
        self._is_dirty = True
        logger.info("Configuration reset to defaults")

    def _apply_environment_adaptations(self) -> None:
        """根据运行环境自动调整配置"""
        env_name = self._raw_config.get("environment", {}).get("name", "development")
        env_config = self._raw_config.get("environment", {})

        if env_name == "production":
            self._raw_config.setdefault("lnn", {})["enabled"] = True
            self._raw_config.setdefault("environment", {})["debug"] = False
            if "default_device" not in self._raw_config.get("lnn", {}):
                self._raw_config["lnn"]["default_device"] = "cuda" if HAS_TORCH and torch.cuda.is_available() else "cpu"

        elif env_name == "development":
            self._raw_config.setdefault("lnn", {})["enabled"] = True
            self._raw_config.setdefault("environment", {})["debug"] = True

        elif env_name == "testing":
            self._raw_config.setdefault("lnn", {})["enabled"] = True
            self._raw_config.setdefault("workflow", {})["timeout_seconds"] = 60

        device_override = env_config.get("device_override")
        if device_override:
            self._raw_config.setdefault("lnn", {})["default_device"] = device_override

        models_override = env_config.get("models_path_override")
        if models_override and "lnn" in self._raw_config:
            self._raw_config["lnn"]["models_dir"] = models_override

    def _build_config_object(self) -> None:
        """从原始配置字典构建类型安全的AppConfig对象"""
        lnn_data = self._raw_config.get("lnn", {})
        workflow_data = self._raw_config.get("workflow", {})
        env_data = self._raw_config.get("environment", {})

        models = {}
        for name, model_data in lnn_data.get("models", {}).items():
            models[name] = ModelConfig(
                type=model_data.get("type", "cfc"),
                path=model_data.get("path", ""),
                enabled=model_data.get("enabled", True),
                device=model_data.get("device", lnn_data.get("default_device", "cpu")),
                input_dim=model_data.get("input_dim", 128),
                output_dim=model_data.get("output_dim", 10),
                hidden_dim=model_data.get("hidden_dim", 256),
                num_layers=model_data.get("num_layers", 2),
                dropout_rate=model_data.get("dropout_rate", 0.1),
                metadata=model_data.get("metadata"),
            )

        threshold_data = lnn_data.get("thresholds", {})
        thresholds = ThresholdConfig(
            quick=threshold_data.get("quick", 0.85),
            hybrid=threshold_data.get("hybrid", 0.60),
            complexity=threshold_data.get("complexity", 3),
            fallback=threshold_data.get("fallback", 0.50),
            confidence=threshold_data.get("confidence", 0.70),
        )

        lnn_config = LNNConfig(
            enabled=lnn_data.get("enabled", True),
            models_dir=lnn_data.get("models_dir", "models/lnn"),
            default_device=lnn_data.get("default_device", "cpu"),
            models=models,
            thresholds=thresholds,
            cache_size=lnn_data.get("cache_size", 10),
            enable_amp=lnn_data.get("enable_amp", True),
            max_retry_count=lnn_data.get("max_retry_count", 3),
        )

        workflow_config = WorkflowConfig(
            enabled=workflow_data.get("enabled", True),
            max_steps=workflow_data.get("max_steps", 10),
            timeout_seconds=workflow_data.get("timeout_seconds", 300),
            enable_fallback=workflow_data.get("enable_fallback", True),
            fallback_engine=workflow_data.get("fallback_engine", "Rule"),
            log_enabled=workflow_data.get("log_enabled", True),
            log_dir=workflow_data.get("log_dir", "logs/workflows"),
        )

        env_config = EnvironmentConfig(
            name=env_data.get("name", "development"),
            debug=env_data.get("debug", True),
            device_override=env_data.get("device_override"),
            models_path_override=env_data.get("models_path_override"),
        )

        dataset_cache_data = self._raw_config.get("dataset_cache", {})
        dataset_cache_config = DatasetCacheConfig(
            cache_directory=dataset_cache_data.get("cache_directory", "~/.lingjing/cache/datasets/"),
            max_cache_size=dataset_cache_data.get("max_cache_size", 5 * 1024 * 1024 * 1024),
            memory_cache_size=dataset_cache_data.get("memory_cache_size", 1024 * 1024 * 1024),
            cache_eviction_policy=dataset_cache_data.get("cache_eviction_policy", "lru"),
            enabled=dataset_cache_data.get("enabled", True),
        )

        self._config = AppConfig(
            lnn=lnn_config,
            workflow=workflow_config,
            environment=env_config,
            dataset_cache=dataset_cache_config,
        )
