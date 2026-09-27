"""任务存储公共基类。

收敛四种同构的任务存储实现为两个基类：

- :class:`PerTaskJsonStore` —— 内存字典 + 每任务一个 JSON 文件持久化
  （双重检查锁单例），供 chatter / cutting 使用；
- :class:`InMemoryTaskStore` —— 纯内存 ``__new__`` 单例 + 任务/审核/导出
  三把锁，供 gcode / cam 使用。

子类只需声明差异：持久化目录名、异常构造钩子、删除保护文案。
parametric_geometry（整文件持久化）与 image_to_3d（含过期清理、kwargs
更新语义）与上述两簇差异大于重复，保持独立实现。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, ClassVar, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class PerTaskJsonStore(Generic[T]):
    """内存字典 + 每任务 JSON 文件持久化的线程安全任务存储。

    单例模式（双重检查锁，``_instance`` 按子类隔离）。
    子类声明 ``default_dir_name``（output/ 下的持久化目录名），并可
    覆写 :meth:`_deletable_reason` 实现删除保护（返回非 ``None`` 文案
    表示该任务禁止删除）。
    """

    _instance: "PerTaskJsonStore[Any] | None" = None
    _instance_lock = threading.Lock()

    default_dir_name: ClassVar[str]

    def __init__(self, persist_dir: str | Path | None = None) -> None:
        if persist_dir is None:
            # 与历史实现一致的工程根定位：app/<module>/<store>.py 与
            # app/utils/task_store.py 距工程根层级相同（parents[3]）
            project_root = Path(__file__).resolve().parents[3]
            persist_dir = project_root / "output" / self.default_dir_name
        self._persist_dir = Path(persist_dir)
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, T] = {}
        self._data_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "PerTaskJsonStore[T]":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（供测试使用）。"""
        if cls._instance is not None:
            with cls._instance_lock:
                if cls._instance is not None:
                    cls._instance = None

    # ------------------------------------------------------------------
    # 钩子（子类按需覆写）
    # ------------------------------------------------------------------

    def _review_error(self, message: str) -> Exception:
        """构造审核/删除保护类异常（子类绑定各自的 ReviewError）。"""
        raise NotImplementedError

    def _deletable_reason(self, task: T) -> str | None:
        """返回禁止删除的文案；None 表示允许删除。"""
        return None

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_task(self, task: T) -> None:
        with self._data_lock:
            self._tasks[task.task_id] = task  # type: ignore[index]
            self._persist_task(task)

    def get_task(self, task_id: str) -> T | None:
        with self._data_lock:
            return self._tasks.get(task_id)

    def update_task(self, task: T) -> None:
        with self._data_lock:
            self._tasks[task.task_id] = task  # type: ignore[index]
            self._persist_task(task)

    def list_tasks(self, limit: int = 50) -> list[T]:
        with self._data_lock:
            tasks = sorted(
                self._tasks.values(),
                key=lambda t: t.created_at,  # type: ignore[attr-defined,union-attr]
                reverse=True,
            )
            return tasks[:limit]

    def delete_task(self, task_id: str) -> bool:
        with self._data_lock:
            if task_id not in self._tasks:
                return False
            task = self._tasks[task_id]
            reason = self._deletable_reason(task)
            if reason is not None:
                raise self._review_error(reason)
            del self._tasks[task_id]
            # 删除持久化文件
            persist_path = self._persist_dir / f"{task_id}.json"
            if persist_path.exists():
                try:
                    persist_path.unlink()
                except OSError as e:
                    logger.warning("删除任务持久化文件失败 %s: %s", task_id, e)
            return True

    def _persist_task(self, task: T) -> None:
        persist_path = self._persist_dir / f"{task.task_id}.json"  # type: ignore[attr-defined]
        try:
            with open(persist_path, "w", encoding="utf-8") as f:
                json.dump(task.to_dict(), f, ensure_ascii=False, indent=2)  # type: ignore[attr-defined]
        except (OSError, TypeError) as e:
            logger.warning("任务持久化失败 %s: %s", task.task_id, e)  # type: ignore[attr-defined]


class InMemoryTaskStore:
    """纯内存线程安全单例任务存储（``__new__`` 单例）。

    任务字典由 ``_tasks_lock`` 保护；审核操作使用独立的
    ``_review_lock``、导出操作使用 ``_export_lock`` 防止并发冲突
    （经 :attr:`review_lock` / :attr:`export_lock` 属性暴露）。

    子类提供异常构造钩子：

    - :meth:`_task_error` —— 任务不存在 / ID 重复；
    - :meth:`_review_error` —— SUCCEEDED 删除保护；
    - :meth:`_succeeded_delete_message` —— 删除保护文案（含下游影响说明）。
    """

    _instance: "InMemoryTaskStore | None" = None
    _instance_lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls) -> "InMemoryTaskStore":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._tasks: dict[str, Any] = {}
        self._tasks_lock = threading.Lock()
        self._review_lock = threading.Lock()
        self._export_lock = threading.Lock()
        self._initialized = True

    # ------------------------------------------------------------------
    # 钩子（子类必须提供）
    # ------------------------------------------------------------------

    def _task_error(self, message: str) -> Exception:
        raise NotImplementedError

    def _review_error(self, message: str) -> Exception:
        raise NotImplementedError

    def _succeeded_delete_message(self, task_id: str) -> str:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add_task(self, task: Any) -> None:
        """添加任务到存储（ID 重复时抛任务异常）。"""
        with self._tasks_lock:
            if task.task_id in self._tasks:
                raise self._task_error(f"任务 ID 已存在: {task.task_id}")
            self._tasks[task.task_id] = task

    def get_task(self, task_id: str) -> Any:
        """获取任务（不存在时抛任务异常）。"""
        with self._tasks_lock:
            if task_id not in self._tasks:
                raise self._task_error(f"任务不存在: {task_id}")
            return self._tasks[task_id]

    def list_tasks(self, status_filter: str | None = None) -> list[Any]:
        """列出任务（可选状态过滤，按创建时间倒序）。"""
        with self._tasks_lock:
            tasks = list(self._tasks.values())
        if status_filter:
            tasks = [t for t in tasks if t.status == status_filter]
        tasks.sort(key=lambda t: t.started_at, reverse=True)
        return tasks

    def update_task(self, task: Any) -> None:
        """更新任务（不存在时抛任务异常）。"""
        with self._tasks_lock:
            if task.task_id not in self._tasks:
                raise self._task_error(f"任务不存在: {task.task_id}")
            self._tasks[task.task_id] = task

    def delete_task(self, task_id: str, allow_delete_succeeded: bool = False) -> None:
        """删除任务。

        SUCCEEDED 状态禁止删除（下游阶段可能已引用产物，需保留审计
        追溯链）；``allow_delete_succeeded`` 强制 ``False``，不可由
        环境变量开启。
        """
        with self._tasks_lock:
            if task_id not in self._tasks:
                raise self._task_error(f"任务不存在: {task_id}")
            task = self._tasks[task_id]
            if task.status == "succeeded" and not allow_delete_succeeded:
                raise self._review_error(self._succeeded_delete_message(task_id))
            del self._tasks[task_id]

    def clear(self) -> None:
        """清空所有任务（仅用于测试）。"""
        with self._tasks_lock:
            self._tasks.clear()

    @property
    def review_lock(self) -> threading.Lock:
        """审核操作锁。"""
        return self._review_lock

    @property
    def export_lock(self) -> threading.Lock:
        """导出操作锁。"""
        return self._export_lock
