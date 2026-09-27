"""任务类型注册表实现.

实现 ITaskRegistry 契约（app/contracts/task.py）。插件通过此注册表声明自己
提供的任务类型。注册表实例由核心层维护，在插件 on_load 时调用 register()。

设计要点：
    1. 线程安全：使用 threading.RLock 保护内部 dict
    2. 重复注册：默认覆盖并告警（便于插件热重载）
    3. 查找：通过 task_type 字符串（必须等于 handler.name() 返回值）
    4. 卸载：插件卸载时调用 unregister_plugin() 清理其注册的所有任务类型
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field


from app.contracts import ITaskRegistry, TaskHandler

logger = logging.getLogger(__name__)


@dataclass
class _RegistryEntry:
    """注册表内部条目."""

    handler: TaskHandler
    plugin_id: str
    description: str = ""
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)


class TaskRegistry(ITaskRegistry):
    """任务类型注册表实现.

    线程安全。重复注册同一 task_type 时默认覆盖并告警，便于插件热重载。
    """

    def __init__(self) -> None:
        self._entries: dict[str, _RegistryEntry] = {}
        self._lock = threading.RLock()

    def register(self, handler: TaskHandler, *, plugin_id: str) -> None:
        if handler is None:
            raise ValueError("handler 不能为空")
        if not plugin_id:
            raise ValueError("plugin_id 不能为空")

        task_type = handler.name()
        if not task_type:
            raise ValueError("handler.name() 返回空字符串，无法注册")

        # 提前抽取元信息，避免后续查询时多次调用 handler
        try:
            description = handler.description() or ""
        except Exception as e:
            logger.warning("handler.description() 抛出异常 (task_type=%s): %s", task_type, e)
            description = ""
        try:
            input_schema = handler.input_schema() or {}
        except Exception as e:
            logger.warning("handler.input_schema() 抛出异常 (task_type=%s): %s", task_type, e)
            input_schema = {}
        try:
            output_schema = handler.output_schema() or {}
        except Exception as e:
            logger.warning("handler.output_schema() 抛出异常 (task_type=%s): %s", task_type, e)
            output_schema = {}

        with self._lock:
            existing = self._entries.get(task_type)
            if existing is not None:
                logger.warning(
                    "任务类型 '%s' 已被插件 '%s' 注册，将被插件 '%s' 覆盖",
                    task_type,
                    existing.plugin_id,
                    plugin_id,
                )

            self._entries[task_type] = _RegistryEntry(
                handler=handler,
                plugin_id=plugin_id,
                description=description,
                input_schema=input_schema,
                output_schema=output_schema,
            )
            logger.info("任务类型 '%s' 由插件 '%s' 注册", task_type, plugin_id)

    def get(self, task_type: str) -> TaskHandler:
        with self._lock:
            entry = self._entries.get(task_type)
        if entry is None:
            raise KeyError(f"任务类型 '{task_type}' 未注册。已注册类型: {list(self._entries.keys())}")
        return entry.handler

    def list(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "name": task_type,
                    "task_type": task_type,
                    "plugin_id": entry.plugin_id,
                    "description": entry.description,
                    "input_schema": entry.input_schema,
                    "output_schema": entry.output_schema,
                }
                for task_type, entry in self._entries.items()
            ]

    def unregister_plugin(self, plugin_id: str) -> int:
        """注销某插件注册的所有任务类型（插件卸载时调用）.

        Returns:
            实际注销的任务类型数量。
        """
        removed = 0
        with self._lock:
            to_remove = [task_type for task_type, entry in self._entries.items() if entry.plugin_id == plugin_id]
            for task_type in to_remove:
                self._entries.pop(task_type, None)
                removed += 1
        if removed:
            logger.info("插件 '%s' 的 %d 个任务类型已注销", plugin_id, removed)
        return removed

    def has(self, task_type: str) -> bool:
        """检查 task_type 是否已注册."""
        with self._lock:
            return task_type in self._entries


# 全局单例
_registry: TaskRegistry | None = None
_registry_init_lock = threading.Lock()


def get_task_registry() -> TaskRegistry:
    """获取全局 TaskRegistry 单例."""
    global _registry
    if _registry is None:
        with _registry_init_lock:
            if _registry is None:
                _registry = TaskRegistry()
    return _registry


def reset_task_registry() -> None:
    """重置全局单例（仅用于测试）."""
    global _registry
    with _registry_init_lock:
        _registry = None
