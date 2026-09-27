"""重建流水线编排器：把 COLMAP / OpenMVS / 尺度归一化串起来。

执行顺序
========
1. 创建任务（PENDING）
2. 异步触发执行：
   a. 调 COLMAP 稀疏重建 → COLMAP_DONE
   b. 调 OpenMVS 稠密化 → 网格生成
   c. 尺度归一化（标定块法）→ 最终 mesh
3. 任务状态置为 SUCCEEDED / FAILED / TIMEOUT

并发控制
========
默认串行（max_concurrent=1），桌面模式硬件资源有限。
如需并发，调高 LNN_I2T3D_MAX_CONCURRENT，但需注意：
- COLMAP 单任务 CPU 占用高（多线程内部并行）
- OpenMVS 显存占用大
- 桌面机磁盘 IO 是主要瓶颈
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import ImageTo3DConfig
from app.image_to_3d.colmap_runner import ColmapError, run_sparse_reconstruction
from app.image_to_3d.openmvs_runner import OpenMvsError, run_dense_reconstruction
from app.image_to_3d.scale_normalizer import (
    ScaleNormalizationError,
    ScaleNormalizationResult,
    normalize_scale,
)
from app.image_to_3d.task_store import (
    ReconstructionTask,
    ReconstructionTaskStatus,
    TaskStore,
)

logger = logging.getLogger(__name__)

# 重导出便于 __init__.py 引用
__all__ = [
    "ReconstructionPipeline",
    "ReconstructionTask",
    "ReconstructionTaskStatus",
    "ReconstructionResult",
]


@dataclass
class ReconstructionResult:
    """重建结果摘要，用于 API 响应。"""

    task_id: str
    status: str
    output_mesh_path: str
    sparse_ply_path: str
    num_images_registered: int
    calibrated: bool
    scale_factor: float
    colmap_duration_seconds: float
    openmvs_duration_seconds: float
    total_duration_seconds: float
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "output_mesh_path": self.output_mesh_path,
            "sparse_ply_path": self.sparse_ply_path,
            "num_images_registered": self.num_images_registered,
            "calibrated": self.calibrated,
            "scale_factor": self.scale_factor,
            "colmap_duration_seconds": self.colmap_duration_seconds,
            "openmvs_duration_seconds": self.openmvs_duration_seconds,
            "total_duration_seconds": self.total_duration_seconds,
            "error_message": self.error_message,
        }


class ReconstructionPipeline:
    """拍照重建流水线编排器。"""

    def __init__(self, task_store: TaskStore, cfg: ImageTo3DConfig) -> None:
        self._store = task_store
        self._cfg = cfg
        self._semaphore = asyncio.Semaphore(cfg.max_concurrent)

    async def create_task(
        self,
        photo_paths: list[Path],
        calibration_anchor_distance: float | None = None,
    ) -> ReconstructionTask:
        """创建重建任务（不立即执行）。

        Args:
            photo_paths: 已上传并保存到本地的照片路径列表
            calibration_anchor_distance: 标定块在无量纲坐标系下的距离。
                None 表示无标定块，输出无量纲 mesh。

        Returns:
            ReconstructionTask
        """
        task_id = uuid.uuid4().hex[:16]
        workspace_dir = Path(self._cfg.output_dir) / task_id
        workspace_dir.mkdir(parents=True, exist_ok=True)

        # 把照片拷贝 / 软链到 workspace/images/
        images_dir = workspace_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        for src in photo_paths:
            dst = images_dir / src.name
            if not dst.exists():
                try:
                    # 优先用硬链接（同盘符快），失败回退拷贝
                    dst.hardlink_to(src)
                except (OSError, AttributeError):
                    try:
                        dst.write_bytes(src.read_bytes())
                    except OSError as e:
                        logger.warning("拷贝照片失败 %s: %s", src, e)

        now = time.time()
        task = ReconstructionTask(
            task_id=task_id,
            created_at=now,
            updated_at=now,
            status=ReconstructionTaskStatus.PENDING.value,
            precision_tier=self._cfg.precision_tier,
            photo_count=len(photo_paths),
            workspace_dir=str(workspace_dir),
            calibration_anchor_distance=calibration_anchor_distance,
        )
        self._store.create(task)
        logger.info(
            "创建重建任务 task_id=%s photos=%d tier=%s",
            task_id,
            len(photo_paths),
            self._cfg.precision_tier,
        )
        return task

    async def run_task(self, task_id: str) -> None:
        """异步执行重建任务。

        注意：本方法不抛异常，所有错误都写入 task.error_message 并把状态置为 FAILED。
        调用方只需轮询任务状态即可。
        """
        async with self._semaphore:
            await self._run_task_impl(task_id)

    async def _run_task_impl(self, task_id: str) -> None:
        task = self._store.get(task_id)
        if task is None:
            logger.error("任务不存在 task_id=%s", task_id)
            return

        workspace_dir = Path(task.workspace_dir)
        images_dir = workspace_dir / "images"
        t_start = time.time()

        # 标记运行中
        self._store.update(
            task_id,
            status=ReconstructionTaskStatus.RUNNING.value,
        )

        try:
            # 阶段 1: COLMAP 稀疏重建
            t_colmap_start = time.time()
            colmap_result = await asyncio.to_thread(
                run_sparse_reconstruction,
                images_dir,
                workspace_dir,
                self._cfg,
            )
            colmap_duration = time.time() - t_colmap_start

            self._store.update(
                task_id,
                status=ReconstructionTaskStatus.COLMAP_DONE.value,
                sparse_model_dir=str(colmap_result["model_dir"]),
                sparse_ply_path=str(colmap_result["sparse_ply"]),
                num_images_registered=colmap_result["num_images_registered"],
                colmap_duration_seconds=colmap_duration,
            )

            # 阈值：成功注册的相机数 < 3 时无法继续
            if colmap_result["num_images_registered"] < 3:
                raise ColmapError(
                    f"COLMAP 注册相机数不足 "
                    f"({colmap_result['num_images_registered']} < 3)。"
                    "可能原因：1) 照片重叠度不够；2) 照片纹理缺失；"
                    "3) 照片过曝或欠曝。建议增加照片数量并确保 70% 以上重叠。"
                )

            # ============= 阶段 2: 稠密化（按 pipeline 分支）=============
            # ADR-020 思路 2：part_prior 路径用 VAE 先验补全替代 OpenMVS 稠密化
            if self._cfg.pipeline == "part_prior":
                mesh_path, openmvs_duration = await self._run_part_prior_path(
                    task_id,
                    Path(colmap_result["sparse_ply"]),
                    workspace_dir,
                )
            else:
                # 默认路径：COLMAP + OpenMVS（Hunyuan3D 走独立 pipeline，此处不涉及）
                t_openmvs_start = time.time()
                openmvs_result = await asyncio.to_thread(
                    run_dense_reconstruction,
                    Path(colmap_result["model_dir"]),
                    workspace_dir,
                    self._cfg,
                )
                openmvs_duration = time.time() - t_openmvs_start
                mesh_path = Path(openmvs_result["mesh_path"])

            # 阶段 3: 尺度归一化
            t_scale_start = time.time()
            output_mesh_path = workspace_dir / "output_normalized.ply"
            scale_result: ScaleNormalizationResult = await asyncio.to_thread(
                normalize_scale,
                mesh_path,
                output_mesh_path,
                self._cfg,
                task.calibration_anchor_distance,
            )
            scale_duration = time.time() - t_scale_start

            total_duration = time.time() - t_start

            self._store.update(
                task_id,
                status=ReconstructionTaskStatus.SUCCEEDED.value,
                output_mesh_path=str(scale_result.mesh_path),
                scale_factor=scale_result.scale_factor,
                calibrated=scale_result.calibrated,
                scale_normalize_duration_seconds=scale_duration,
                openmvs_duration_seconds=openmvs_duration,
                total_duration_seconds=total_duration,
                error_message="",  # 清空之前的错误信息
            )
            logger.info(
                "重建任务成功 task_id=%s total=%.1fs calibrated=%s",
                task_id,
                total_duration,
                scale_result.calibrated,
            )

        except (ColmapError, OpenMvsError, ScaleNormalizationError) as e:
            total_duration = time.time() - t_start
            self._store.update(
                task_id,
                status=ReconstructionTaskStatus.FAILED.value,
                error_message=str(e),
                total_duration_seconds=total_duration,
            )
            logger.error(
                "重建任务失败 task_id=%s err=%s",
                task_id,
                e,
                exc_info=True,
            )
        except asyncio.TimeoutError as e:
            total_duration = time.time() - t_start
            self._store.update(
                task_id,
                status=ReconstructionTaskStatus.TIMEOUT.value,
                error_message=f"任务超时: {e}",
                total_duration_seconds=total_duration,
            )
            logger.error("重建任务超时 task_id=%s", task_id)
        except Exception as e:
            # 兜底：未预期的异常
            total_duration = time.time() - t_start
            self._store.update(
                task_id,
                status=ReconstructionTaskStatus.FAILED.value,
                error_message=f"未预期异常: {type(e).__name__}: {e}",
                total_duration_seconds=total_duration,
            )
            logger.error(
                "重建任务未预期异常 task_id=%s",
                task_id,
                exc_info=True,
            )

    async def _run_part_prior_path(
        self,
        task_id: str,
        sparse_ply_path: Path,
        workspace_dir: Path,
    ) -> tuple[Path, float]:
        """ADR-020 思路 2：零件专属先验补全路径。

        用 COLMAP 稀疏点云 + 预训练 VAE 先验补全替代 OpenMVS 稠密化，
        输出 mesh 后缩放回 SfM 无量纲坐标系，供阶段 3 尺度归一化使用。

        工程边界：
        - 需要预训练 VAE 权重（cfg.part_prior.pretrained_model_path）
        - 需要 PyTorch 环境（torch 不可用时抛 RuntimeError）
        - 输出 mesh 必须经 CAM 软件二次校验后才允许上机床

        Args:
            task_id: 任务 ID（日志追踪）
            sparse_ply_path: COLMAP 稀疏点云 PLY 路径
            workspace_dir: 任务工作目录

        Returns:
            (mesh_path, duration_seconds)
        """
        t_start = time.time()

        def _run_sync() -> Path:
            # 延迟导入 torch 与 part_prior 模块，避免模块加载期依赖 torch
            try:
                import torch
                from app.image_to_3d.part_prior.encoder import PartPriorVAE
                from app.image_to_3d.part_prior.runner import PartPriorRunner
            except ImportError as e:
                raise RuntimeError(
                    "part_prior 路径需要 PyTorch，但当前环境不可用。"
                    f" ImportError: {e}。"
                    " 请安装 PyTorch 或切换到 colmap_openmvs 主 pipeline。"
                ) from e

            prior_cfg = self._cfg.part_prior

            # 1. 实例化 VAE 并加载预训练权重
            vae = PartPriorVAE(
                voxel_dim=prior_cfg.voxel_dim,
                latent_dim=prior_cfg.latent_dim,
                base_channels=prior_cfg.base_channels,
            )
            if prior_cfg.pretrained_model_path:
                if os.path.exists(prior_cfg.pretrained_model_path):
                    # weights_only=True：state_dict 仅含张量/基本容器，拒绝
                    # 序列化进 checkpoint 的任意对象执行（反序列化 RCE 防护）
                    state_dict = torch.load(
                        prior_cfg.pretrained_model_path,
                        map_location="cpu",
                        weights_only=True,
                    )
                    vae.load_state_dict(state_dict)
                    logger.info(
                        "part_prior[%s] 加载预训练权重: %s",
                        task_id,
                        prior_cfg.pretrained_model_path,
                    )
                else:
                    logger.warning(
                        "part_prior[%s] 预训练权重路径不存在: %s，使用随机初始化权重（仅用于链路测试，不可用于生产）",
                        task_id,
                        prior_cfg.pretrained_model_path,
                    )
            else:
                logger.warning(
                    "part_prior[%s] 未配置预训练权重"
                    "（LNN_I2T3D_PART_PRIOR_MODEL_PATH 为空），"
                    "使用随机初始化权重（仅用于链路测试，不可用于生产）",
                    task_id,
                )

            # 2. 从稀疏点云计算 bbox（SfM 无量纲坐标系）
            bbox_extent, bbox_min = self._compute_bbox_from_ply(sparse_ply_path)
            logger.info(
                "part_prior[%s] 稀疏点云 bbox: extent=%s min=%s",
                task_id,
                bbox_extent,
                bbox_min,
            )

            # 3. 运行先验补全（稀疏点云 稠密体素 mesh）
            runner = PartPriorRunner(
                vae=vae,
                voxel_dim=prior_cfg.voxel_dim,
                inference_seed=prior_cfg.inference_seed,
            )
            part_prior_output_dir = workspace_dir / "part_prior"
            runner_result = runner.run(
                sparse_points_path=sparse_ply_path,
                bbox=bbox_extent,
                output_dir=part_prior_output_dir,
                task_id=task_id,
            )

            # 4. 将 mesh 从体素坐标 [0, voxel_dim] 缩放回 SfM 坐标系
            # completer 归一化: normalized = (point - bbox_min) / bbox_extent
            # voxel_idx = normalized * voxel_dim
            # 逆变换: vertex_sfm = vertex_voxel / voxel_dim * bbox_extent + bbox_min
            scaled_mesh_path = workspace_dir / "output_part_prior_scaled.ply"
            self._scale_mesh_to_sfm(
                Path(runner_result.output_mesh_path),
                scaled_mesh_path,
                bbox_extent,
                bbox_min,
                prior_cfg.voxel_dim,
            )

            return scaled_mesh_path

        mesh_path = await asyncio.to_thread(_run_sync)
        duration = time.time() - t_start
        logger.info(
            "part_prior[%s] 先验补全完成 duration=%.1fs mesh=%s",
            task_id,
            duration,
            mesh_path,
        )
        return mesh_path, duration

    def _compute_bbox_from_ply(
        self,
        ply_path: Path,
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """从 PLY 点云计算轴对齐包围盒。

        Args:
            ply_path: PLY 文件路径

        Returns:
            (bbox_extent, bbox_min)
            - bbox_extent: (length, width, height) 包围盒尺寸
            - bbox_min: (x_min, y_min, z_min) 包围盒最小角点
        """
        try:
            import numpy as np
            from plyfile import PlyData
        except ImportError as e:
            raise RuntimeError(f"part_prior 路径需要 numpy + plyfile，但当前环境不可用: {e}") from e

        plydata = PlyData.read(str(ply_path))
        vertex = plydata["vertex"]
        points = np.stack([vertex["x"], vertex["y"], vertex["z"]], axis=-1).astype(np.float64)

        bbox_min = tuple(float(points.min(axis=0)[i]) for i in range(3))
        bbox_max = tuple(float(points.max(axis=0)[i]) for i in range(3))
        bbox_extent = tuple(bbox_max[i] - bbox_min[i] for i in range(3))

        # 防止零尺寸轴导致除零
        bbox_extent_safe = tuple(max(ext, 1e-6) for ext in bbox_extent)
        return bbox_extent_safe, bbox_min

    def _scale_mesh_to_sfm(
        self,
        input_mesh_path: Path,
        output_mesh_path: Path,
        bbox_extent: tuple[float, float, float],
        bbox_min: tuple[float, float, float],
        voxel_dim: int,
    ) -> None:
        """将体素坐标 mesh 缩放回 SfM 坐标系。

        PartPriorCompleter 把点云归一化到 [0, voxel_dim] 体素空间，
        marching cubes 输出的 mesh 顶点也在 [0, voxel_dim] 空间。
        本方法做逆变换：vertex_sfm = vertex_voxel / voxel_dim * extent + min

        Args:
            input_mesh_path: 输入 mesh（体素坐标）
            output_mesh_path: 输出 mesh（SfM 坐标）
            bbox_extent: 包围盒尺寸
            bbox_min: 包围盒最小角点
            voxel_dim: 体素网格维度
        """
        try:
            import numpy as np
            import trimesh
            from trimesh import Trimesh
        except ImportError as e:
            raise RuntimeError(f"part_prior 路径需要 numpy + trimesh，但当前环境不可用: {e}") from e

        # H5 bug 修复：Trimesh 构造函数没有 file_path 参数（这是 AI 臆造的 API）。
        # 原写法 mesh.vertices 永远为空 直接导出空 mesh 缩放逻辑全部跳过。
        # 改用 trimesh.load 正确加载文件。
        mesh = trimesh.load(str(input_mesh_path), force="mesh")
        # Scene 对象（多 mesh 容器）需合并为单个 Trimesh
        if isinstance(mesh, trimesh.Scene):
            mesh = trimesh.util.concatenate([g for g in mesh.geometry.values()])
        if len(mesh.vertices) == 0:
            # 空 mesh 直接保存
            mesh.export(str(output_mesh_path))
            return

        verts = np.asarray(mesh.vertices, dtype=np.float64)
        # 逆归一化：voxel SfM
        verts[:, 0] = verts[:, 0] / voxel_dim * bbox_extent[0] + bbox_min[0]
        verts[:, 1] = verts[:, 1] / voxel_dim * bbox_extent[1] + bbox_min[1]
        verts[:, 2] = verts[:, 2] / voxel_dim * bbox_extent[2] + bbox_min[2]

        scaled_mesh = Trimesh(vertices=verts, faces=mesh.faces)
        scaled_mesh.export(str(output_mesh_path))
        logger.info(
            "part_prior mesh 缩放完成: %s（%d 顶点）",
            output_mesh_path,
            len(verts),
        )

    def get_result(self, task_id: str) -> ReconstructionResult | None:
        """从任务存储构造 API 响应摘要。"""
        task = self._store.get(task_id)
        if task is None:
            return None
        return ReconstructionResult(
            task_id=task.task_id,
            status=task.status,
            output_mesh_path=task.output_mesh_path,
            sparse_ply_path=task.sparse_ply_path,
            num_images_registered=task.num_images_registered,
            calibrated=task.calibrated,
            scale_factor=task.scale_factor,
            colmap_duration_seconds=task.colmap_duration_seconds,
            openmvs_duration_seconds=task.openmvs_duration_seconds,
            total_duration_seconds=task.total_duration_seconds,
            error_message=task.error_message,
        )
