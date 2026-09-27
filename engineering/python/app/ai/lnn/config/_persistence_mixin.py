"""LNN 配置持久化 mixin（从 config_manager 拆出）。"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any
from collections.abc import Callable

import yaml  # type: ignore[import-untyped]

from app.utils.dict_utils import deep_merge

logger = logging.getLogger(__name__)


class _PersistenceMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）
    _apply_environment_adaptations: Callable[..., Any]
    _build_config_object: Callable[..., Any]
    _validate_config: Callable[..., Any]
    _is_dirty: Any
    _last_modified: Any
    _raw_config: Any
    config_path: Any

    def load(self, config_path: str | None = None) -> None:
        """
        从YAML文件加载配置

        Args:
            config_path: YAML配置文件路径（可选，使用初始化时设置的路径）

        Raises:
            FileNotFoundError: 配置文件不存在
            yaml.YAMLError: YAML解析错误
            ValueError: 配置验证失败
        """
        path = config_path or self.config_path
        if not path:
            raise ValueError(
                "配置加载失败：未指定配置文件路径。请通过 config_manager.set_path('/path/to/config.json') 设置配置文件路径，或在初始化时传入 config_path 参数。"
            )

        if not os.path.exists(path):
            raise FileNotFoundError(
                f"配置加载失败：找不到配置文件 '{path}'。可能原因：1) 文件路径错误；2) 配置文件尚未创建。请检查路径是否正确，或调用 config_manager.create_default_config() 创建默认配置文件。"
            )

        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded_config = yaml.safe_load(f) or {}

            self._merge_config(loaded_config)
            self._validate_config()
            self._apply_environment_adaptations()
            self._build_config_object()

            self._last_modified = datetime.fromtimestamp(os.path.getmtime(path))
            self.config_path = path
            self._is_dirty = False

            logger.info("Configuration loaded from %s", path)

        except yaml.YAMLError as e:
            raise yaml.YAMLError(f"Failed to parse YAML config: {e}")
        except (OSError, FileNotFoundError, json.JSONDecodeError, ValueError) as e:
            logger.error("配置加载失败: %s", e, exc_info=True)
            if isinstance(e, (ValueError, FileNotFoundError)):
                raise
            raise RuntimeError(
                f"配置加载失败：解析配置文件时出现异常。错误详情: {e}。可能原因：1) 配置文件格式不正确（非 JSON/YAML 格式）；2) 配置文件内容有误；3) 文件编码不匹配。请检查配置文件语法、内容格式和文件编码。"
            ) from e

    def save(self, output_path: str | None = None) -> None:
        """
        将配置持久化到文件系统

        Args:
            output_path: 输出路径（可选，默认使用加载时的路径）

        Raises:
            ValueError: 没有指定输出路径
            IOError: 写入文件失败
        """
        target_path = output_path or self.config_path
        if not target_path:
            raise ValueError(
                "配置保存失败：未指定输出文件路径。请通过 config_manager.set_path('/path/to/config.json') 设置保存路径，或在调用 save() 时传入 output_path 参数。"
            )

        try:
            output_dir = os.path.dirname(target_path)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)

            with open(target_path, "w", encoding="utf-8") as f:
                yaml.dump(
                    self._raw_config,
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )

            self._is_dirty = False
            self._last_modified = datetime.now(timezone.utc)
            logger.info("Configuration saved to %s", target_path)

        except IOError as e:
            raise IOError(
                f"配置保存失败：无法将配置写入文件。错误详情: {e}。可能原因：1) 磁盘空间不足；2) 目标目录无写入权限；3) 文件被其他进程占用。请检查磁盘状态和目录权限。"
            ) from e

    def _merge_config(self, loaded_config: dict[str, Any]) -> None:
        """合并加载的配置到现有配置"""
        self._raw_config = deep_merge(self._raw_config, loaded_config)

    def _deep_merge(self, base: dict, override: dict) -> dict:
        """深度合并两个字典（兼容委托）。

        YAMLConfigManager 等子类/测试直接调用本方法；实现统一收敛到
        :func:`app.utils.dict_utils.deep_merge`，新代码请直接使用该函数。
        """
        return deep_merge(base, override)
