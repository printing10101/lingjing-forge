"""参数化几何输出模块：任务存储 + 状态机 + 审核枚举。

数据流：
    阶段 2 导出的 confirmed_features.json
        → 解析为 ExtractedFeature 列表
        → FeatureToBrepConverter 转换为 B-rep TopoDS_Shape
        → AssemblyBuilder 多特征布尔运算装配成零件
        → StepWriter 输出 STEP 文件
        → 工程师审核 STEP 文件（confirmed / rejected / edited）
        → 导出最终 STEP 文件路径（供阶段 4 切削参数推荐使用）

定位声明（项目记忆硬约束）：
    本模块是「工程师助手」，不是「全自动参数化 CAD 生成器」。
    mesh → 参数化 CAD 自动转换在工业上未解决，
    生产系统依赖 human-in-the-loop（工程师确认 STEP 输出）。
    本模块输出的 STEP 必须经过 CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床。

精度继承链：
    阶段 1 image_to_3d.precision_tier → 阶段 2 feature_extraction.precision_tier → 阶段 3
    本模块不引入新的精度档位，全程继承上游告知，避免精度信息断层。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from app.core.safe_errors import safe_error_message


logger = __import__("logging").getLogger(__name__)


# 枚举：任务状态 / 特征类型 / 审核状态


class ParametricGeometryTaskStatus(str, Enum):
    """参数化几何任务状态机。

    状态转移图：
        PENDING → RUNNING → STEP_GENERATED → REVIEWED → SUCCEEDED
                                                    ↘ FAILED
                                                    ↘ CANCELLED

    - PENDING          : 任务已创建，等待触发执行
    - RUNNING          : 正在执行特征→B-rep 转换 + 装配 + STEP 写入
    - STEP_GENERATED   : STEP 文件已生成，等待工程师审核
    - REVIEWED         : 工程师已审核全部特征（confirmed / rejected / edited）
    - SUCCEEDED        : 最终 STEP 已导出，可供阶段 4 使用
    - FAILED           : 执行失败（pythonOCC 不可用 + 模板降级失败 等）
    - CANCELLED        : 用户主动取消
    """

    PENDING = "pending"
    RUNNING = "running"
    STEP_GENERATED = "step_generated"
    REVIEWED = "reviewed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepReviewStatus(str, Enum):
    """工程师审核单个特征在 STEP 中的表达状态。

    - PENDING   : 待审核
    - CONFIRMED : 工程师确认该特征在 STEP 中表达正确
    - REJECTED  : 工程师拒绝该特征（将从最终 STEP 中移除）
    - EDITED    : 工程师编辑了该特征的参数（如半径、深度、位置）
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EDITED = "edited"


# 数据类：已审核特征引用 + 任务


@dataclass
class ReviewedFeatureRef:
    """阶段 2 已确认特征在阶段 3 的引用 + 审核状态。

    字段：
    - feature_id        : 阶段 2 的特征 ID（如 "feat_plane_001"）
    - feature_type      : 特征类型（plane / cylinder / hole / boss）
    - source_params     : 阶段 2 导出的原始参数（dict）
    - review_status     : 阶段 3 工程师审核状态（pending/confirmed/rejected/edited）
    - edited_params     : 工程师编辑后的参数（仅 review_status=edited 时存在）
    - engineer_notes    : 工程师审核备注
    - reviewed_by       : 审核人
    - reviewed_at       : 审核时间戳
    """

    feature_id: str
    feature_type: str
    source_params: dict[str, Any]
    review_status: str = StepReviewStatus.PENDING.value
    edited_params: dict[str, Any] | None = None
    engineer_notes: str | None = None
    reviewed_by: str | None = None
    reviewed_at: float | None = None

    def effective_params(self) -> dict[str, Any]:
        """返回生效参数（若工程师编辑过则用 edited_params，否则用 source_params）。

        与阶段 2 ExtractedFeature.effective_params() 行为一致，
        保证阶段 3 装配器始终使用工程师确认后的参数生成 STEP。
        """
        if self.review_status == StepReviewStatus.EDITED.value and self.edited_params:
            # 合并 source_params + edited_params（edited_params 优先）
            merged = dict(self.source_params)
            merged.update(self.edited_params)
            return merged
        return dict(self.source_params)


@dataclass
class ParametricGeometryTask:
    """参数化几何任务。

    字段：
    - task_id                       : 本模块任务 ID
    - source_feature_extraction_task_id : 阶段 2 任务 ID（用于追溯 confirmed_features.json 来源）
    - input_features_path           : 阶段 2 导出的 confirmed_features.json 路径
    - input_features                : 解析后的 ReviewedFeatureRef 列表
    - status                        : 任务状态
    - precision_tier                : 精度档位（继承自阶段 2）
    - mesh_calibrated               : 上游 mesh 是否已标定（继承自阶段 1）
    - step_output_path              : STEP 文件输出路径（STEP_GENERATED 后存在）
    - final_step_path               : 最终 STEP 文件路径（SUCCEEDED 后存在）
    - engine_used                   : 实际使用的 STEP 写入引擎（pythonocc / freecad / template）
    - cam_validation_required       : 是否需要 CAM 二次校验（始终为 True）
    - error_message                 : 失败原因（FAILED 状态）
    - created_at / updated_at       : 时间戳
    - workspace_dir                  : 任务工作目录
    """

    task_id: str
    source_feature_extraction_task_id: str
    input_features_path: str
    input_features: list[ReviewedFeatureRef] = field(default_factory=list)
    status: str = ParametricGeometryTaskStatus.PENDING.value
    precision_tier: str = "standard"
    mesh_calibrated: bool = False
    step_output_path: str | None = None
    final_step_path: str | None = None
    engine_used: str | None = None
    cam_validation_required: bool = True
    error_message: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    workspace_dir: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（用于 JSON 持久化与 API 响应）。"""
        return {
            "task_id": self.task_id,
            "source_feature_extraction_task_id": self.source_feature_extraction_task_id,
            "input_features_path": self.input_features_path,
            "input_features": [f.__dict__ for f in self.input_features],
            "status": self.status,
            "precision_tier": self.precision_tier,
            "mesh_calibrated": self.mesh_calibrated,
            "step_output_path": self.step_output_path,
            "final_step_path": self.final_step_path,
            "engine_used": self.engine_used,
            "cam_validation_required": self.cam_validation_required,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "workspace_dir": self.workspace_dir,
            "feature_count": len(self.input_features),
        }


# TaskStore：单例 + 线程锁 + JSON 持久化


class TaskStore:
    """参数化几何任务存储（单例 + 线程锁 + JSON 持久化）。

    与阶段 1/2 的 TaskStore 设计一致：
    - 内存字典 + JSON 文件持久化
    - threading.Lock 保护并发访问
    - 双重检查锁的全局单例
    """

    _instance: "TaskStore | None" = None
    _instance_lock = threading.Lock()

    def __init__(self, persist_path: Path | None = None) -> None:
        self._tasks: dict[str, ParametricGeometryTask] = {}
        self._lock = threading.Lock()
        self._persist_path = persist_path
        if persist_path is not None:
            self._load_from_disk()

    @classmethod
    def get_instance(cls) -> "TaskStore":
        """获取全局单例（双重检查锁）。"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（仅供测试使用）。"""
        with cls._instance_lock:
            cls._instance = None

    def create(self, task: ParametricGeometryTask) -> None:
        """创建任务。"""
        with self._lock:
            self._tasks[task.task_id] = task
            self._persist()

    def get(self, task_id: str) -> ParametricGeometryTask | None:
        """查询任务。"""
        with self._lock:
            return self._tasks.get(task_id)

    def update(self, task_id: str, **kwargs: Any) -> ParametricGeometryTask | None:
        """更新任务字段。"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            for key, value in kwargs.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            task.updated_at = time.time()
            self._persist()
            return task

    def delete(self, task_id: str) -> bool:
        """删除任务。"""
        with self._lock:
            if task_id in self._tasks:
                del self._tasks[task_id]
                self._persist()
                return True
            return False

    def list_tasks(self, limit: int = 50) -> list[ParametricGeometryTask]:
        """列出最近任务（按创建时间倒序）。"""
        with self._lock:
            tasks = sorted(
                self._tasks.values(),
                key=lambda t: t.created_at,
                reverse=True,
            )
            return tasks[:limit]

    def _persist(self) -> None:
        """持久化到 JSON 文件。"""
        if self._persist_path is None:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            data = {task_id: task.to_dict() for task_id, task in self._tasks.items()}
            self._persist_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            safe = safe_error_message(e, context="parametric_geometry.TaskStore._persist")
            logger.error(
                "TaskStore 持久化失败 error_id=%s message=%s",
                safe.get("error_id"),
                safe.get("message"),
            )

    def _load_from_disk(self) -> None:
        """从 JSON 文件加载。"""
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            for task_id, task_dict in data.items():
                # 重建 ReviewedFeatureRef 列表
                features = [ReviewedFeatureRef(**f) for f in task_dict.pop("input_features", [])]
                # to_dict() 输出 feature_count（序列化专用派生字段），
                # ParametricGeometryTask 无此字段，加载时必须剔除，否则持久化后 reload 必崩。
                task_dict.pop("feature_count", None)
                task = ParametricGeometryTask(**{**task_dict, "input_features": features})
                self._tasks[task_id] = task
        except Exception as e:
            safe = safe_error_message(e, context="parametric_geometry.TaskStore._load_from_disk")
            logger.warning(
                "TaskStore 加载历史数据失败 error_id=%s message=%s，将以空 store 启动",
                safe.get("error_id"),
                safe.get("message"),
            )


def get_task_store() -> TaskStore:
    """获取全局 TaskStore 单例。"""
    return TaskStore.get_instance()


def generate_task_id() -> str:
    """生成任务 ID（pg_ 前缀 + 时间戳 + UUID4 短码）。"""
    return f"pg_{int(time.time())}_{uuid.uuid4().hex[:8]}"
