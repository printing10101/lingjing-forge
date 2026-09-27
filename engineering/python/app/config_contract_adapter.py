"""配置源适配器：将现有环境变量适配为 ``IConfigSource``。

对应 core-contracts-design.md 第 6.4 节。

设计目标：
- 将 ``LNN_<SECTION>__<KEY>`` 环境变量适配为 ``IConfigSource``
- 支持多源合并（env / yaml / db / user_input），按 priority 升序合并
- 类型推断：环境变量值（str）→ bool/int/float/str

命名约定（双下划线段分隔符，避免与字段名中的单下划线冲突）::

    点分路径                  环境变量名
    -----------------------   -------------------------------
    model.hidden_size      →  LNN_MODEL__HIDDEN_SIZE
    training.epochs        →  LNN_TRAINING__EPOCHS
    data.dataset_id        →  LNN_DATA__DATASET_ID

    环境变量名                点分路径
    -----------------------   -------------------------------
    LNN_MODEL__HIDDEN_SIZE →  model.hidden_size
    LNN_TRAINING__EPOCHS   →  training.epochs

用法::

    from app.config_contract_adapter import EnvConfigSource, merge_sources

    env_source = EnvConfigSource(prefix="LNN_")
    config = merge_sources([yaml_source, env_source])
    # env_source 优先级高于 yaml_source（priority 更大）

稳定性承诺：本文件为 Stable 契约 v1.0.0 实现，向后兼容扩展，breaking change 需新开 ADR。
"""

from __future__ import annotations

import os
from typing import Any

from app.contracts.config import IConfigSource


# EnvConfigSource


class EnvConfigSource(IConfigSource):
    """环境变量配置源。

    将 ``PREFIX<SECTION>__<KEY>`` 格式的环境变量适配为 ``IConfigSource``。

    双下划线 ``__`` 作为段分隔符，避免与字段名中的单下划线冲突。
    例如 ``model.hidden_size`` → ``LNN_MODEL__HIDDEN_SIZE``。

    类型推断：
    - ``"true"`` / ``"false"`` → ``bool``
    - ``"123"`` → ``int``
    - ``"0.001"`` → ``float``
    - 其他 → ``str``

    Attributes:
        prefix: 环境变量前缀（默认 ``LNN_``）
        priority: 优先级（默认 100，高于 yaml/db，低于 user_input）
    """

    DEFAULT_PRIORITY: int = 100

    def __init__(
        self,
        prefix: str = "LNN_",
        priority: int = DEFAULT_PRIORITY,
    ) -> None:
        if not prefix:
            raise ValueError("EnvConfigSource.prefix must be a non-empty string")
        self._prefix = prefix
        self._priority = priority

    def priority(self) -> int:
        """返回优先级（数字越大优先级越高）。"""
        return self._priority

    def get(self, key: str) -> Any:
        """取值，不存在抛 ``KeyError``。

        Args:
            key: 点分路径（如 ``model.hidden_size``）

        Returns:
            类型推断后的值（bool/int/float/str）

        Raises:
            KeyError: 环境变量未设置
        """
        env_key = self._key_to_env(key)
        if env_key not in os.environ:
            raise KeyError(key)
        return self._infer_type(os.environ[env_key])

    def keys(self) -> list[str]:
        """返回此配置源所有可用的 key（点分路径）。"""
        result: list[str] = []
        prefix_len = len(self._prefix)
        for env_key in os.environ:
            if not env_key.startswith(self._prefix):
                continue
            stripped = env_key[prefix_len:]
            if not stripped:
                continue
            # 双下划线切分，各段转小写
            parts = stripped.split("__")
            dot_key = ".".join(p.lower() for p in parts if p)
            if dot_key:
                result.append(dot_key)
        return result

    def contains(self, key: str) -> bool:
        """检查 key 是否存在（便捷方法，不在 IConfigSource 契约中）。"""
        return self._key_to_env(key) in os.environ

    def _key_to_env(self, key: str) -> str:
        """点分路径 → 环境变量名。

        ``model.hidden_size`` → ``LNN_MODEL__HIDDEN_SIZE``
        """
        parts = key.split(".")
        return self._prefix + "__".join(p.upper() for p in parts if p)

    @staticmethod
    def _infer_type(raw: str) -> Any:
        """类型推断：str → bool/int/float/str。"""
        if raw.lower() in ("true", "false"):
            return raw.lower() == "true"
        try:
            return int(raw)
        except ValueError:
            pass
        try:
            return float(raw)
        except ValueError:
            pass
        return raw


# DictConfigSource


class DictConfigSource(IConfigSource):
    """字典配置源：将扁平化字典（点分路径 key）适配为 ``IConfigSource``。

    用于将 YAML 加载结果、用户输入等字典数据接入多源合并。

    用法::

        yaml_flat = yaml_loader.load("exp.yaml")  # {"model.hidden_size": 64, ...}
        yaml_source = DictConfigSource(yaml_flat, priority=50)
    """

    def __init__(self, data: dict[str, Any], priority: int = 50) -> None:
        self._data: dict[str, Any] = dict(data)
        self._priority = priority

    def priority(self) -> int:
        return self._priority

    def get(self, key: str) -> Any:
        if key not in self._data:
            raise KeyError(key)
        return self._data[key]

    def keys(self) -> list[str]:
        return list(self._data.keys())


# 多源合并工具


def merge_sources(sources: list[IConfigSource]) -> dict[str, Any]:
    """按 priority 升序合并多个配置源，返回扁平化字典。

    priority 越大的源优先级越高，会覆盖低优先级源的值。

    Args:
        sources: 配置源列表（无需预排序）

    Returns:
        扁平化字典（点分路径 key）
    """
    sorted_sources = sorted(sources, key=lambda s: s.priority())
    merged: dict[str, Any] = {}
    for src in sorted_sources:
        for key in src.keys():
            try:
                merged[key] = src.get(key)
            except KeyError:
                continue
    return merged


__all__ = [
    "EnvConfigSource",
    "DictConfigSource",
    "merge_sources",
]
