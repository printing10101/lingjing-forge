"""特征提取任务存储：内存 + JSON 文件持久化。

设计权衡
========
灵境制造的特征提取是中等耗时操作（RANSAC 平面拟合 + 圆柱拟合 + 孔检测
对 10 万顶点 mesh 约 5-30 秒），但**关键环节是工程师审核**。

与拍照重建任务的区别：
- 拍照重建任务：纯算法执行，无人工干预
- 特征提取任务：算法提取 → 工程师审核 → 确认/拒绝/编辑 → 导出

工程师审核状态机（项目记忆硬约束：mesh→parametric CAD 自动转换工业上未解决，
生产系统依赖 human-in-the-loop）：

    PENDING → RUNNING → FEATURES_EXTRACTED → REVIEWED → SUCCEEDED
                  ↓             ↓                ↓
                FAILED       CANCELLED        FAILED（审核不通过）

- FEATURES_EXTRACTED: 算法提取完成，等待工程师审核
- REVIEWED: 工程师已审核每个特征（confirmed / rejected / edited）
- SUCCEEDED: 已确认的特征集导出为 JSON，供阶段 3 参数化 STEP 生成使用

不使用 SQLite 的原因：与拍照重建任务存储一致，特征对象是嵌套 dict，
序列化为 JSON 即可，不需要事务等高级特性。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class FeatureExtractionTaskStatus(str, Enum):
    """特征提取任务状态枚举（继承 str 便于 JSON 序列化）。"""

    PENDING = "pending"  # 已创建，等待执行
    RUNNING = "running"  # 执行中
    FEATURES_EXTRACTED = "features_extracted"  # 算法提取完成，等待工程师审核
    REVIEWED = "reviewed"  # 工程师已审核
    SUCCEEDED = "succeeded"  # 已确认特征集已导出
    FAILED = "failed"  # 失败
    CANCELLED = "cancelled"  # 已取消


class FeatureType(str, Enum):
    """几何特征类型枚举。"""

    PLANE = "plane"  # 平面
    CYLINDER = "cylinder"  # 圆柱面
    HOLE = "hole"  # 孔（内圆柱 + 端面）
    BOSS = "boss"  # 凸台（外圆柱 + 端面）
    UNKNOWN = "unknown"  # 未分类区域


class FeatureReviewStatus(str, Enum):
    """单条特征的人工审核状态。"""

    PENDING = "pending"  # 等待工程师审核
    CONFIRMED = "confirmed"  # 工程师确认无误
    REJECTED = "rejected"  # 工程师拒绝（误识别）
    EDITED = "edited"  # 工程师编辑过参数


@dataclass
class ExtractedFeature:
    """单条提取出的几何特征。

    所有几何参数都存于 ``params`` 字典中，键随特征类型而变：
    - plane:    {normal: [nx,ny,nz], offset: float, area_mm2: float}
    - cylinder: {axis: [ax,ay,az], center: [cx,cy,cz], radius_mm: float, height_mm: float}
    - hole:     {normal: [nx,ny,nz], center: [cx,cy,cz], radius_mm: float, depth_mm: float}
    - boss:     {normal: [nx,ny,nz], center: [cx,cy,cz], radius_mm: float, height_mm: float}

    置信度 confidence ∈ [0, 1]，由 RANSAC inlier 比例等指标计算。
    工程师审核字段（review_status / engineer_notes / edited_params）默认空，
    等待工程师在 FEATURES_EXTRACTED 阶段填充。
    """

    feature_id: str
    feature_type: str  # FeatureType 字符串值
    params: dict[str, Any]
    confidence: float
    # 算法给出的原始顶点索引样本（用于前端高亮显示）
    sample_vertex_indices: list[int] = field(default_factory=list)
    # 工程师审核字段
    review_status: str = FeatureReviewStatus.PENDING.value
    engineer_notes: str = ""
    # 工程师编辑后的参数（仅 review_status=edited 时填充）
    edited_params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def effective_params(self) -> dict[str, Any]:
        """返回生效参数：若工程师编辑过则用 edited_params，否则用原始 params。"""
        if self.review_status == FeatureReviewStatus.EDITED.value and self.edited_params:
            return dict(self.edited_params)
        return dict(self.params)


@dataclass
class FeatureExtractionTask:
    """特征提取任务。"""

    task_id: str
    created_at: float
    updated_at: float
    status: str  # FeatureExtractionTaskStatus 字符串值
    # 输入 mesh 路径（来自拍照重建模块的输出）
    input_mesh_path: str
    # 关联的拍照重建任务 ID（可选，便于追溯）
    source_reconstruction_task_id: str = ""
    # 提取出的特征列表
    features: list[ExtractedFeature] = field(default_factory=list)
    # 各阶段耗时
    plane_duration_seconds: float = 0.0
    cylinder_duration_seconds: float = 0.0
    hole_duration_seconds: float = 0.0
    total_duration_seconds: float = 0.0
    # mesh 统计信息
    vertex_count: int = 0
    face_count: int = 0
    # 错误信息（仅 status=failed 时填充）
    error_message: str = ""
    # 审核元信息
    reviewed_by: str = ""
    reviewed_at: float = 0.0
    # 导出文件路径（仅 status=succeeded 时填充）
    exported_features_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "input_mesh_path": self.input_mesh_path,
            "source_reconstruction_task_id": self.source_reconstruction_task_id,
            "features": [f.to_dict() for f in self.features],
            "plane_duration_seconds": self.plane_duration_seconds,
            "cylinder_duration_seconds": self.cylinder_duration_seconds,
            "hole_duration_seconds": self.hole_duration_seconds,
            "total_duration_seconds": self.total_duration_seconds,
            "vertex_count": self.vertex_count,
            "face_count": self.face_count,
            "error_message": self.error_message,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
            "exported_features_path": self.exported_features_path,
        }


class FeatureStore:
    """特征提取任务存储：内存 + 文件持久化。"""

    def __init__(self, persist_dir: Path) -> None:
        self._persist_dir = persist_dir
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, FeatureExtractionTask] = {}
        self._lock = threading.Lock()
        self._load_all()

    def _task_file(self, task_id: str) -> Path:
        return self._persist_dir / f"{task_id}.json"

    def _load_all(self) -> None:
        """启动时加载所有持久化任务。"""
        for f in self._persist_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                # 重建嵌套的 ExtractedFeature 列表
                features_data = data.pop("features", [])
                features = [ExtractedFeature(**fd) for fd in features_data]
                task = FeatureExtractionTask(**data, features=features)
                self._tasks[task.task_id] = task
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.warning("加载特征提取任务文件失败 %s: %s", f, e)

    def create(self, task: FeatureExtractionTask) -> None:
        with self._lock:
            self._tasks[task.task_id] = task
            self._persist(task)

    def get(self, task_id: str) -> FeatureExtractionTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_all(self, limit: int = 100) -> list[FeatureExtractionTask]:
        with self._lock:
            sorted_tasks = sorted(
                self._tasks.values(),
                key=lambda t: t.created_at,
                reverse=True,
            )
            return sorted_tasks[:limit]

    def update(self, task_id: str, **fields: Any) -> FeatureExtractionTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            # features 字段需要特殊处理（list[ExtractedFeature]）
            if "features" in fields and isinstance(fields["features"], list):
                new_features: list[ExtractedFeature] = []
                for f in fields["features"]:
                    if isinstance(f, ExtractedFeature):
                        new_features.append(f)
                    elif isinstance(f, dict):
                        new_features.append(ExtractedFeature(**f))
                    else:
                        logger.warning(
                            "忽略无法识别的特征对象 task_id=%s: %r",
                            task_id,
                            f,
                        )
                fields["features"] = new_features
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
                    logger.warning("删除特征提取任务文件失败 %s: %s", f, e)
            return True

    def cleanup_expired(self, retention_hours: int) -> int:
        """清理超过保留时长的已完成任务。返回清理数量。"""
        if retention_hours <= 0:
            return 0
        cutoff = time.time() - retention_hours * 3600
        cleaned = 0
        with self._lock:
            to_delete = []
            terminal_states = (
                FeatureExtractionTaskStatus.SUCCEEDED.value,
                FeatureExtractionTaskStatus.FAILED.value,
                FeatureExtractionTaskStatus.CANCELLED.value,
            )
            for tid, task in self._tasks.items():
                if task.status in terminal_states and task.updated_at < cutoff:
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

    def _persist(self, task: FeatureExtractionTask) -> None:
        """持久化单个任务到 JSON 文件。"""
        try:
            f = self._task_file(task.task_id)
            f.write_text(
                json.dumps(task.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning(
                "持久化特征提取任务失败 task_id=%s: %s",
                task.task_id,
                e,
            )


# 全局单例（双重检查锁，参考 image_to_3d/task_store.py 的模式）

_feature_store: FeatureStore | None = None
_singleton_lock = threading.Lock()


def get_feature_store() -> FeatureStore:
    """获取全局 FeatureStore 单例。

    延迟导入 config 以避免循环依赖。
    """
    global _feature_store
    if _feature_store is not None:
        return _feature_store
    with _singleton_lock:
        if _feature_store is None:
            from app.config import config

            persist_dir = Path(config.feature_extraction.output_dir) / "tasks"
            _feature_store = FeatureStore(persist_dir=persist_dir)
        return _feature_store
