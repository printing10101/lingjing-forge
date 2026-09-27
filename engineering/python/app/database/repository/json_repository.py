"""统一数据仓储基类（精简泛型版，生产用）。

提供JSON文件加载、内存缓存、查询和筛选功能。
MachineDatabase、ToolDatabase、MaterialDatabase均从此基类继承，
消除"拷贝-修改"式重复代码。

.. note::
    本模块为 ``app.database.repository`` 包下的**精简泛型版** JSON Repository，
    基于 ``Generic[T]``，仅提供加载+缓存+查询，供生产模块使用。

    与 ``app/repository/json_repository.py`` 的**完整版**不同：后者提供文件锁
    （fcntl）、版本控制（jsonl 日志）、事务支持，继承自 ``Repository`` 基类，
    但当前仅由测试覆盖。两者 API 不同，非简单替换关系。

设计原则：
- 单一职责：仅负责JSON文件加载和基础CRUD
- 开放封闭：子类通过泛型参数T自定义条目类型
- 类型安全：使用Generic[T]确保编译时类型检查
"""

from __future__ import annotations

import json
import logging
from typing import Any, Generic, TypeVar
from collections.abc import Callable

logger = logging.getLogger(__name__)

T = TypeVar("T")


class JsonRepository(Generic[T]):
    """基于JSON文件的泛型数据仓储。

    所有领域实体数据库的公共基类。
    自动化加载、缓存和基础查询功能。

    子类只需：
    1. 指定JSON文件路径
    2. 提供from_dict类方法用于反序列化
    3. 提供get_key方法用于确定主键

    Example:
        >>> class ToolRepo(JsonRepository[ToolEntry]):
        ...     def __init__(self, data_path=None):
        ...         super().__init__(data_path, ToolEntry.from_dict, lambda e: e.id)
    """

    def __init__(
        self,
        data_path: str,
        from_dict: Callable[[dict[str, Any]], T],
        key_fn: Callable[[T], str],
    ) -> None:
        self._data_path: str = data_path
        self._from_dict: Callable[[dict[str, Any]], T] = from_dict
        self._key_fn: Callable[[T], str] = key_fn
        self._entries: dict[str, T] = {}
        self._load()

    def _load(self) -> None:
        # M15 修复：json.load 包裹 try/except，数据文件可能损坏
        try:
            with open(self._data_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"数据文件损坏（JSON 格式错误）: {self._data_path}: {e}") from e
        except FileNotFoundError:
            logger.warning("数据文件不存在，初始化为空: %s", self._data_path)
            raw = []
        for item in raw:
            try:
                entry = self._from_dict(item)
                self._entries[self._key_fn(entry)] = entry
            except ValueError as exc:
                item_id = item.get("id", item.get("name", "unknown"))
                raise ValueError(f"加载条目'{item_id}'失败: {exc}") from exc

    def get(self, key: str) -> T:
        if key not in self._entries:
            available = ", ".join(sorted(self._entries.keys()))
            raise KeyError(f"条目 '{key}' 不在数据库中。可用: {available}")
        return self._entries[key]

    def list_all(self) -> list[T]:
        return list(self._entries.values())

    def list_keys(self) -> list[str]:
        return sorted(self._entries.keys())

    def count(self) -> int:
        return len(self._entries)

    def filter(self, predicate: Callable[[T], bool]) -> list[T]:
        return [e for e in self._entries.values() if predicate(e)]

    def __contains__(self, key: str) -> bool:
        return key in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries.values())
