"""特征提取流水线编排器：把 mesh 加载 / 平面 / 圆柱 / 孔检测串起来。

执行顺序
========
1. 创建任务（PENDING）
2. 异步触发执行：
   a. 加载 mesh → 顶点 + 面片
   b. 平面提取（RANSAC）→ plane_features
   c. 圆柱提取（RANSAC）→ cylinder_features
   d. 孔/凸台检测（基于 plane_features）→ hole_features
   e. 合并所有特征 → 状态置为 FEATURES_EXTRACTED（等待工程师审核）
3. 工程师审核：逐条 confirmed / rejected / edited
   - 全部审核完毕 → 状态置为 REVIEWED
4. 导出已确认特征集（confirmed + edited）→ 状态置为 SUCCEEDED

并发控制
========
默认串行（max_concurrent=1），桌面模式硬件资源有限。
单个 mesh 的特征提取通常 5-30 秒（10 万顶点），不需要并发。

mesh 加载
========
优先用 trimesh（成熟稳定，支持 PLY/STL/GLB/OBJ 等格式）。
trimesh 不可用时退化为简易 PLY 解析（仅支持 ASCII PLY）。
简易解析失败时抛出 FeatureExtractionError。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.config import FeatureExtractionConfig
from app.feature_extraction._review_state_machine import (
    STATUS_REVIEWED,
    STATUS_SUCCEEDED,
    FeatureReviewStateMachine,
    ReviewStateMachineError,
)
from app.feature_extraction.cylinder_extractor import CylinderExtractor
from app.feature_extraction.feature_store import (
    ExtractedFeature,
    FeatureExtractionTask,
    FeatureExtractionTaskStatus,
    FeatureReviewStatus,
    FeatureStore,
    get_feature_store,
)
from app.feature_extraction.hole_detector import HoleDetector
from app.feature_extraction.plane_extractor import PlaneExtractor
from app.feature_extraction.precision_disclaimer import (
    FeatureDisclaimer,
    build_feature_disclaimer,
)

logger = logging.getLogger(__name__)

__all__ = [
    "FeatureExtractionPipeline",
    "FeatureExtractionResult",
    "FeatureExtractionError",
    "MeshLoadError",
    "FeatureReviewError",
]


# 异常类


class FeatureExtractionError(Exception):
    """特征提取通用异常。"""


class MeshLoadError(FeatureExtractionError):
    """mesh 加载失败。"""


class FeatureReviewError(FeatureExtractionError):
    """工程师审核操作失败。"""


# 条件导入：trimesh 可选


def _try_import_trimesh() -> Any:
    """尝试导入 trimesh。

    Returns:
        trimesh 模块 或 None
    """
    try:
        import trimesh

        return trimesh
    except ImportError:
        logger.info(
            "trimesh 未安装，mesh 加载退化为简易 PLY 解析。如需支持 STL/GLB/OBJ 等格式，请安装：pip install trimesh"
        )
        return None


# 结果数据类


@dataclass
class FeatureExtractionResult:
    """特征提取结果摘要，用于 API 响应。"""

    task_id: str
    status: str
    vertex_count: int
    face_count: int
    feature_count: int
    plane_count: int
    cylinder_count: int
    hole_count: int
    boss_count: int
    total_duration_seconds: float
    error_message: str = ""
    # 审核元信息
    reviewed_by: str = ""
    reviewed_at: float = 0.0
    # 导出文件路径（仅 status=succeeded 时填充）
    exported_features_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "vertex_count": self.vertex_count,
            "face_count": self.face_count,
            "feature_count": self.feature_count,
            "plane_count": self.plane_count,
            "cylinder_count": self.cylinder_count,
            "hole_count": self.hole_count,
            "boss_count": self.boss_count,
            "total_duration_seconds": self.total_duration_seconds,
            "error_message": self.error_message,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
            "exported_features_path": self.exported_features_path,
        }


# 特征提取流水线


class FeatureExtractionPipeline:
    """几何特征提取流水线编排器。

    生命周期：
        create_task() → PENDING
        run_task()   → RUNNING → FEATURES_EXTRACTED
        review_feature() (多次) → FEATURES_EXTRACTED（保持）
        _check_all_reviewed() → REVIEWED（自动触发）
        export_confirmed_features() → SUCCEEDED
    """

    def __init__(
        self,
        task_store: FeatureStore | None = None,
        cfg: FeatureExtractionConfig | None = None,
    ) -> None:
        # 延迟初始化，便于测试注入
        self._store = task_store if task_store is not None else get_feature_store()
        from app.config import config

        self._cfg = cfg if cfg is not None else config.feature_extraction
        self._semaphore = asyncio.Semaphore(self._cfg.max_concurrent)
        self._trimesh = _try_import_trimesh()

        # 初始化三个提取器
        self._plane_extractor = PlaneExtractor(self._cfg)
        self._cylinder_extractor = CylinderExtractor(self._cfg)
        self._hole_detector = HoleDetector(self._cfg)

    # 任务创建与执行

    async def create_task(
        self,
        mesh_path: str | Path,
        source_reconstruction_task_id: str = "",
        mesh_calibrated: bool = False,
    ) -> FeatureExtractionTask:
        """创建特征提取任务（不立即执行）。

        Args:
            mesh_path: 输入 mesh 文件路径（PLY/STL/GLB）
            source_reconstruction_task_id: 关联的拍照重建任务 ID（可选）
            mesh_calibrated: 上游 mesh 是否已做尺度归一化
                （影响输出的几何参数单位）

        Returns:
            FeatureExtractionTask
        """
        mesh_path = Path(mesh_path)
        if not mesh_path.exists():
            raise MeshLoadError(f"mesh 文件不存在: {mesh_path}")

        task_id = uuid.uuid4().hex[:16]
        now = time.time()
        task = FeatureExtractionTask(
            task_id=task_id,
            created_at=now,
            updated_at=now,
            status=FeatureExtractionTaskStatus.PENDING.value,
            input_mesh_path=str(mesh_path),
            source_reconstruction_task_id=source_reconstruction_task_id,
            # mesh_calibrated 暂存于 task 元信息中（不直接存于 dataclass，
            # 通过 build_feature_disclaimer 在 API 响应时动态构造）
        )
        self._store.create(task)
        logger.info(
            "创建特征提取任务 task_id=%s mesh=%s source=%s calibrated=%s",
            task_id,
            mesh_path.name,
            source_reconstruction_task_id or "external",
            mesh_calibrated,
        )
        # 把 mesh_calibrated 暂存到内存字段（通过 update 写入，
        # 但 FeatureExtractionTask 没有这个字段，所以只在内存字典里记录）
        # 简化方案：调用方在查询时传入 mesh_calibrated 给 build_feature_disclaimer
        return task

    async def run_task(self, task_id: str) -> None:
        """异步执行特征提取任务。

        注意：本方法不抛异常，所有错误都写入 task.error_message 并把状态置为 FAILED。
        """
        async with self._semaphore:
            await self._run_task_impl(task_id)

    async def _run_task_impl(self, task_id: str) -> None:
        task = self._store.get(task_id)
        if task is None:
            logger.error("任务不存在 task_id=%s", task_id)
            return

        mesh_path = Path(task.input_mesh_path)
        t_start = time.time()

        # 标记运行中
        self._store.update(
            task_id,
            status=FeatureExtractionTaskStatus.RUNNING.value,
        )

        try:
            # 阶段 1: 加载 mesh
            vertices, faces = await asyncio.to_thread(_load_mesh, mesh_path, self._trimesh)

            self._store.update(
                task_id,
                vertex_count=int(len(vertices)),
                face_count=int(len(faces)) if faces is not None else 0,
            )

            # 阶段 2: 平面提取
            t_plane_start = time.time()
            plane_result = await asyncio.to_thread(self._plane_extractor.extract, vertices, faces)
            plane_duration = time.time() - t_plane_start

            if not plane_result.success:
                raise FeatureExtractionError(f"平面提取失败: {plane_result.error_message}")

            logger.info(
                "任务 %s 平面提取完成: %d 个平面（method=%s）",
                task_id,
                plane_result.extracted_count,
                plane_result.method,
            )

            # 阶段 3: 圆柱提取
            t_cyl_start = time.time()
            cyl_result = await asyncio.to_thread(self._cylinder_extractor.extract, vertices, faces)
            cyl_duration = time.time() - t_cyl_start

            if not cyl_result.success:
                logger.warning(
                    "任务 %s 圆柱提取失败（继续）: %s",
                    task_id,
                    cyl_result.error_message,
                )

            logger.info(
                "任务 %s 圆柱提取完成: %d 个圆柱（method=%s）",
                task_id,
                cyl_result.extracted_count,
                cyl_result.method,
            )

            # 阶段 4: 孔/凸台检测
            t_hole_start = time.time()
            hole_result = await asyncio.to_thread(
                self._hole_detector.detect,
                vertices,
                plane_result.features,
            )
            hole_duration = time.time() - t_hole_start

            if not hole_result.success:
                logger.warning(
                    "任务 %s 孔/凸台检测失败（继续）: %s",
                    task_id,
                    hole_result.error_message,
                )

            logger.info(
                "任务 %s 孔/凸台检测完成: %d 个（method=%s）",
                task_id,
                hole_result.extracted_count,
                hole_result.method,
            )

            # 阶段 5: 合并所有特征
            all_features: list[ExtractedFeature] = []
            all_features.extend(plane_result.features)
            all_features.extend(cyl_result.features if cyl_result.success else [])
            all_features.extend(hole_result.features if hole_result.success else [])

            total_duration = time.time() - t_start

            self._store.update(
                task_id,
                status=FeatureExtractionTaskStatus.FEATURES_EXTRACTED.value,
                features=all_features,
                plane_duration_seconds=plane_duration,
                cylinder_duration_seconds=cyl_duration,
                hole_duration_seconds=hole_duration,
                total_duration_seconds=total_duration,
                error_message="",
            )
            logger.info(
                "任务 %s 特征提取完成: 共 %d 个特征（平面=%d 圆柱=%d 孔/凸台=%d）总耗时 %.1fs，等待工程师审核",
                task_id,
                len(all_features),
                plane_result.extracted_count,
                cyl_result.extracted_count if cyl_result.success else 0,
                hole_result.extracted_count if hole_result.success else 0,
                total_duration,
            )

        except (MeshLoadError, FeatureExtractionError) as e:
            total_duration = time.time() - t_start
            self._store.update(
                task_id,
                status=FeatureExtractionTaskStatus.FAILED.value,
                error_message=str(e),
                total_duration_seconds=total_duration,
            )
            logger.error(
                "特征提取任务失败 task_id=%s err=%s",
                task_id,
                e,
                exc_info=True,
            )
        except asyncio.TimeoutError as e:
            total_duration = time.time() - t_start
            self._store.update(
                task_id,
                status=FeatureExtractionTaskStatus.FAILED.value,
                error_message=f"任务超时: {e}",
                total_duration_seconds=total_duration,
            )
            logger.error("特征提取任务超时 task_id=%s", task_id)
        except Exception as e:
            # 兜底：未预期的异常
            total_duration = time.time() - t_start
            self._store.update(
                task_id,
                status=FeatureExtractionTaskStatus.FAILED.value,
                error_message=f"未预期异常: {type(e).__name__}: {e}",
                total_duration_seconds=total_duration,
            )
            logger.error(
                "特征提取任务未预期异常 task_id=%s",
                task_id,
                exc_info=True,
            )

    # 工程师审核

    def review_feature(
        self,
        task_id: str,
        feature_id: str,
        action: str,
        edited_params: dict[str, Any] | None = None,
        engineer_notes: str = "",
        reviewed_by: str = "engineer",
    ) -> ExtractedFeature:
        """工程师审核单个特征。

        Args:
            task_id: 任务 ID
            feature_id: 特征 ID
            action: 审核动作（confirmed / rejected / edited）
            edited_params: 编辑后的参数（仅 action=edited 时填充）
            engineer_notes: 工程师备注
            reviewed_by: 审核人标识

        Returns:
            审核后的 ExtractedFeature

        Raises:
            FeatureReviewError: 任务不存在 / 状态不允许审核 / 特征不存在 /
                action 非法
        """
        task = self._store.get(task_id)
        if task is None:
            raise FeatureReviewError(f"任务不存在: {task_id}")

        # 状态机校验：任务是否可审核 + action 是否合法
        # （FeatureReviewStateMachine 白盒逻辑，与既有行为逐字节一致）
        try:
            FeatureReviewStateMachine.assert_reviewable(task.status)
            FeatureReviewStateMachine.assert_valid_action(action)
        except ReviewStateMachineError as e:
            raise FeatureReviewError(str(e)) from e

        # 查找特征
        target_feature: ExtractedFeature | None = None
        for f in task.features:
            if f.feature_id == feature_id:
                target_feature = f
                break
        if target_feature is None:
            raise FeatureReviewError(f"特征不存在: feature_id={feature_id}")

        # 更新特征审核字段
        target_feature.review_status = action
        target_feature.engineer_notes = engineer_notes
        if action == FeatureReviewStatus.EDITED.value:
            if not edited_params:
                raise FeatureReviewError("action=edited 时必须提供 edited_params")
            target_feature.edited_params = dict(edited_params)

        # 持久化整个任务（features 列表已更新）
        self._store.update(
            task_id,
            features=list(task.features),
            reviewed_by=reviewed_by,
            reviewed_at=time.time(),
        )

        # 检查是否所有特征都已审核
        self._check_all_reviewed(task_id)

        logger.info(
            "特征审核 task_id=%s feature_id=%s action=%s reviewer=%s",
            task_id,
            feature_id,
            action,
            reviewed_by,
        )
        return target_feature

    def _check_all_reviewed(self, task_id: str) -> bool:
        """检查任务的所有特征是否都已审核完毕。

        若全部审核完毕，将任务状态从 FEATURES_EXTRACTED 置为 REVIEWED。

        Returns:
            True 如果已全部审核完毕且状态已更新
        """
        task = self._store.get(task_id)
        if task is None:
            return False

        if task.status != FeatureExtractionTaskStatus.FEATURES_EXTRACTED.value:
            return False

        review_statuses = [f.review_status for f in task.features]
        all_reviewed = FeatureReviewStateMachine.all_features_reviewed(review_statuses)
        if all_reviewed:
            # 状态机决定下一状态（空特征 / 有特征统一转移 REVIEWED）
            self._store.update(
                task_id,
                status=STATUS_REVIEWED,
            )
            logger.info(
                "任务 %s 所有特征审核完毕，状态置为 %s",
                task_id,
                STATUS_REVIEWED,
            )
        return all_reviewed

    # 导出已确认特征

    def export_confirmed_features(
        self,
        task_id: str,
        output_path: str | Path | None = None,
    ) -> Path:
        """导出已确认（confirmed + edited）的特征集为 JSON 文件。

        Args:
            task_id: 任务 ID
            output_path: 输出文件路径。None 时自动生成（output_dir/task_id/features.json）

        Returns:
            实际输出的文件路径

        Raises:
            FeatureReviewError: 任务不存在 / 状态不允许导出 / 无已确认特征
        """
        task = self._store.get(task_id)
        if task is None:
            raise FeatureReviewError(f"任务不存在: {task_id}")

        # 状态机校验：是否允许导出（FEATURES_EXTRACTED / REVIEWED）
        # （允许 FEATURES_EXTRACTED 导出，便于增量工作）
        try:
            FeatureReviewStateMachine.assert_exportable(task.status)
        except ReviewStateMachineError as e:
            raise FeatureReviewError(str(e)) from e

        # 筛选已确认特征（confirmed + edited）
        confirmed_features = [
            f
            for f in task.features
            if f.review_status
            in (
                FeatureReviewStatus.CONFIRMED.value,
                FeatureReviewStatus.EDITED.value,
            )
        ]
        if not confirmed_features:
            raise FeatureReviewError(f"任务 {task_id} 无已确认特征（confirmed/edited），无法导出")

        # 构造导出数据（使用 effective_params）
        export_data = {
            "task_id": task_id,
            "exported_at": time.time(),
            "source_mesh_path": task.input_mesh_path,
            "source_reconstruction_task_id": task.source_reconstruction_task_id,
            "feature_count": len(confirmed_features),
            "features": [
                {
                    "feature_id": f.feature_id,
                    "feature_type": f.feature_type,
                    "params": f.effective_params(),
                    "confidence": f.confidence,
                    "review_status": f.review_status,
                    "engineer_notes": f.engineer_notes,
                    "edited": f.review_status == FeatureReviewStatus.EDITED.value,
                }
                for f in confirmed_features
            ],
        }

        # 输出路径
        if output_path is None:
            output_dir = Path(self._cfg.output_dir) / task_id
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / "confirmed_features.json"
        else:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

        output_path.write_text(
            json.dumps(export_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 状态置为 SUCCEEDED（由状态机决定）
        self._store.update(
            task_id,
            status=STATUS_SUCCEEDED,
            exported_features_path=str(output_path),
        )
        logger.info(
            "任务 %s 已导出 %d 个已确认特征到 %s",
            task_id,
            len(confirmed_features),
            output_path,
        )
        return output_path

    # 查询结果摘要

    def get_result(self, task_id: str) -> FeatureExtractionResult | None:
        """从任务存储构造 API 响应摘要。"""
        task = self._store.get(task_id)
        if task is None:
            return None

        # 统计各类特征数量
        plane_count = sum(1 for f in task.features if f.feature_type == "plane")
        cylinder_count = sum(1 for f in task.features if f.feature_type == "cylinder")
        hole_count = sum(1 for f in task.features if f.feature_type == "hole")
        boss_count = sum(1 for f in task.features if f.feature_type == "boss")

        return FeatureExtractionResult(
            task_id=task.task_id,
            status=task.status,
            vertex_count=task.vertex_count,
            face_count=task.face_count,
            feature_count=len(task.features),
            plane_count=plane_count,
            cylinder_count=cylinder_count,
            hole_count=hole_count,
            boss_count=boss_count,
            total_duration_seconds=task.total_duration_seconds,
            error_message=task.error_message,
            reviewed_by=task.reviewed_by,
            reviewed_at=task.reviewed_at,
            exported_features_path=task.exported_features_path,
        )

    def get_disclaimer(
        self,
        task_id: str,
        mesh_calibrated: bool = False,
    ) -> FeatureDisclaimer:
        """构造任务对应的 feature_disclaimer。

        Args:
            task_id: 任务 ID
            mesh_calibrated: 上游 mesh 是否已标定（由调用方从拍照重建任务查询）

        Returns:
            FeatureDisclaimer
        """
        task = self._store.get(task_id)
        if task is None:
            # 任务不存在时返回默认 disclaimer
            return build_feature_disclaimer(
                self._cfg,
                mesh_calibrated=mesh_calibrated,
                mesh_source="unknown",
            )
        return build_feature_disclaimer(
            self._cfg,
            mesh_calibrated=mesh_calibrated,
            mesh_source=task.source_reconstruction_task_id or "external_upload",
        )


# mesh 加载函数


def _load_mesh(
    mesh_path: Path,
    trimesh_module: Any = None,
) -> tuple[np.ndarray, np.ndarray | None]:
    """加载 mesh 文件，返回 (vertices, faces)。

    Args:
        mesh_path: mesh 文件路径（PLY/STL/GLB/OBJ）
        trimesh_module: 已导入的 trimesh 模块（None 时使用简易解析）

    Returns:
        (vertices (N,3), faces (M,3) 或 None)

    Raises:
        MeshLoadError: 文件不存在 / 格式不支持 / 解析失败
    """
    if not mesh_path.exists():
        raise MeshLoadError(f"mesh 文件不存在: {mesh_path}")

    suffix = mesh_path.suffix.lower()

    # 优先使用 trimesh
    if trimesh_module is not None:
        try:
            mesh = trimesh_module.load(str(mesh_path), force="mesh")
            if hasattr(mesh, "vertices") and hasattr(mesh, "faces"):
                vertices = np.asarray(mesh.vertices, dtype=np.float64)
                faces = np.asarray(mesh.faces, dtype=np.int64) if len(mesh.faces) > 0 else None
                if len(vertices) == 0:
                    raise MeshLoadError(f"mesh 顶点数为 0: {mesh_path}")
                # mesh 抽稀（如果顶点数过多）
                # 注意：抽稀由调用方决定，这里只做加载
                return vertices, faces
            raise MeshLoadError(f"trimesh 加载结果不是 mesh 类型: {type(mesh).__name__}")
        except Exception as e:
            # trimesh 失败时退化为简易解析（仅支持 PLY）
            if suffix == ".ply":
                logger.warning(
                    "trimesh 加载失败 %s: %s，退化为简易 PLY 解析",
                    mesh_path,
                    e,
                )
                return _load_ply_simple(mesh_path)
            raise MeshLoadError(f"trimesh 加载 mesh 失败: {e}")

    # 无 trimesh，仅支持 PLY
    if suffix == ".ply":
        return _load_ply_simple(mesh_path)
    if suffix in (".stl", ".glb", ".gltf", ".obj"):
        raise MeshLoadError(f"格式 {suffix} 需要 trimesh 支持，请安装：pip install trimesh")
    raise MeshLoadError(f"不支持的 mesh 格式: {suffix}")


def _load_ply_simple(mesh_path: Path) -> tuple[np.ndarray, np.ndarray | None]:
    """简易 PLY 解析（仅支持 ASCII PLY）。

    Args:
        mesh_path: PLY 文件路径

    Returns:
        (vertices (N,3), faces (M,3) 或 None)
    """
    try:
        with open(mesh_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError) as e:
        raise MeshLoadError(f"PLY 文件读取失败: {e}")

    # 解析 header
    header_end = 0
    vertex_count = 0
    face_count = 0
    in_header = True
    for i, line in enumerate(lines):
        stripped = line.strip()
        if in_header:
            if stripped == "ply":
                continue
            if stripped.startswith("format"):
                fmt = stripped.split()
                if len(fmt) >= 2 and fmt[1] != "ascii":
                    raise MeshLoadError(
                        f"简易 PLY 解析仅支持 ASCII 格式，实际 {fmt[1]}。请安装 trimesh 以支持 binary PLY"
                    )
                continue
            if stripped.startswith("element"):
                parts = stripped.split()
                if len(parts) >= 3:
                    if parts[1] == "vertex":
                        vertex_count = int(parts[2])
                    elif parts[1] == "face":
                        face_count = int(parts[2])
                continue
            if stripped == "end_header":
                header_end = i + 1
                in_header = False
                break

    if in_header:
        raise MeshLoadError(f"PLY header 未结束: {mesh_path}")

    # 解析顶点和面片
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    body_lines = lines[header_end:]

    for line in body_lines:
        if not line.strip():
            continue
        parts = line.strip().split()
        if not parts:
            continue
        # 顶点行：x y z [其他属性]
        if len(vertices) < vertex_count:
            if len(parts) >= 3:
                try:
                    vertices.append((float(parts[0]), float(parts[1]), float(parts[2])))
                except ValueError:
                    continue
        # 面片行：3 idx1 idx2 idx3（首字段是顶点数）
        elif len(faces) < face_count:
            if len(parts) >= 4:
                try:
                    n_verts = int(parts[0])
                    if n_verts == 3:
                        faces.append((int(parts[1]), int(parts[2]), int(parts[3])))
                except ValueError:
                    continue

    if not vertices:
        raise MeshLoadError(f"PLY 解析后无顶点: {mesh_path}")

    vertices_arr = np.array(vertices, dtype=np.float64)
    faces_arr = np.array(faces, dtype=np.int64) if faces else None
    return vertices_arr, faces_arr
