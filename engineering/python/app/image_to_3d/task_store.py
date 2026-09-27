"""任务存储：内存 + JSON 文件持久化。

设计权衡
========
灵境制造的拍照重建任务是长耗时操作（COLMAP 200 张照片 high 档位约 30-60 分钟），
需要异步执行 + 状态轮询。

实现方式：
- 内存字典：快速访问当前任务状态
- JSON 文件：进程重启后能恢复任务历史
- 线程锁：防止并发任务状态竞争

不使用 SQLite 的原因：
- 拍照重建任务是短期产物（默认 72 小时后清理）
- 任务对象是嵌套 dict，序列化为 JSON 即可
- 不需要事务、跨进程并发等高级特性
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ReconstructionTaskStatus(str, Enum):
    """任务状态枚举（继承 str 便于 JSON 序列化）。"""

    PENDING = "pending"  # 已创建，等待执行
    RUNNING = "running"  # 执行中
    COLMAP_DONE = "colmap_done"  # COLMAP 完成，OpenMVS 进行中
    SUCCEEDED = "succeeded"  # 全部完成
    FAILED = "failed"  # 失败
    CANCELLED = "cancelled"  # 已取消
    TIMEOUT = "timeout"  # 超时


@dataclass
class ReconstructionTask:
    """单次重建任务。"""

    task_id: str
    created_at: float
    updated_at: float
    status: str  # ReconstructionTaskStatus 的字符串值
    precision_tier: str
    photo_count: int
    workspace_dir: str
    # 标定块距离（无量纲坐标系下），None=未提供
    calibration_anchor_distance: float | None = None
    # 最终输出 mesh 路径
    output_mesh_path: str = ""
    # COLMAP 输出（稀疏模型）
    sparse_model_dir: str = ""
    sparse_ply_path: str = ""
    num_images_registered: int = 0
    # 缩放信息
    scale_factor: float = 1.0
    calibrated: bool = False
    # 错误信息（仅 status=failed 时填充）
    error_message: str = ""
    # 各阶段耗时
    colmap_duration_seconds: float = 0.0
    openmvs_duration_seconds: float = 0.0
    scale_normalize_duration_seconds: float = 0.0
    total_duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskStore:
    """任务存储：内存 + 文件持久化。"""

    def __init__(self, persist_dir: Path) -> None:
        self._persist_dir = persist_dir
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, ReconstructionTask] = {}
        self._lock = threading.Lock()
        self._load_all()

    def _task_file(self, task_id: str) -> Path:
        return self._persist_dir / f"{task_id}.json"

    def _load_all(self) -> None:
        """启动时加载所有持久化任务。"""
        for f in self._persist_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                task = ReconstructionTask(**data)
                self._tasks[task.task_id] = task
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.warning("加载任务文件失败 %s: %s", f, e)

    def create(self, task: ReconstructionTask) -> None:
        with self._lock:
            self._tasks[task.task_id] = task
            self._persist(task)

    def get(self, task_id: str) -> ReconstructionTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_all(self, limit: int = 100) -> list[ReconstructionTask]:
        with self._lock:
            sorted_tasks = sorted(
                self._tasks.values(),
                key=lambda t: t.created_at,
                reverse=True,
            )
            return sorted_tasks[:limit]

    def update(self, task_id: str, **fields: Any) -> ReconstructionTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            for k, v in fields.items():
                if hasattr(task, k):
                    setattr(task, k, v)
            task.updated_at = time.time()
            self._persist(task)
            return task

    def delete(self, task_id: str) -> bool:
        with self._lock:
            if task_id not in self._tasks:
                return False
            del self._tasks[task_id]
            f = self._task_file(task_id)
            if f.exists():
                try:
                    f.unlink()
                except OSError as e:
                    logger.warning("删除任务文件失败 %s: %s", f, e)
            return True

    def cleanup_expired(self, retention_hours: int) -> int:
        """清理超过保留时长的已完成任务。返回清理数量。"""
        if retention_hours <= 0:
            return 0
        cutoff = time.time() - retention_hours * 3600
        cleaned = 0
        with self._lock:
            to_delete = []
            for tid, task in self._tasks.items():
                if (
                    task.status
                    in (
                        ReconstructionTaskStatus.SUCCEEDED.value,
                        ReconstructionTaskStatus.FAILED.value,
                        ReconstructionTaskStatus.CANCELLED.value,
                        ReconstructionTaskStatus.TIMEOUT.value,
                    )
                    and task.updated_at < cutoff
                ):
                    to_delete.append(tid)
            for tid in to_delete:
                del self._tasks[tid]
                f = self._task_file(tid)
                if f.exists():
                    try:
                        f.unlink()
                    except OSError:
                        pass
                cleaned += 1
        return cleaned

    def _persist(self, task: ReconstructionTask) -> None:
        """持久化单个任务到 JSON 文件。"""
        try:
            f = self._task_file(task.task_id)
            f.write_text(
                json.dumps(task.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("持久化任务失败 task_id=%s: %s", task.task_id, e)


# 全局单例
_task_store: TaskStore | None = None
_singleton_lock = threading.Lock()


def get_task_store() -> TaskStore:
    """获取全局 TaskStore 单例。"""
    global _task_store
    if _task_store is not None:
        return _task_store
    with _singleton_lock:
        if _task_store is None:
            from app.config import config

            persist_dir = Path(config.image_to_3d.output_dir) / "tasks"
            _task_store = TaskStore(persist_dir=persist_dir)
        return _task_store
