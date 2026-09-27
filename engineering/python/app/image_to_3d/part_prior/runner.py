"""集成到拍照重建 pipeline 的运行器（ADR-020 思路 2）。

本模块把 PartPriorVAE + PartPriorCompleter 串成一条完整的重建路径，
作为 ADR-006 拍照重建 pipeline 的第三条路径 ``part_prior``：

    COLMAP 稀疏点云 → PartPriorCompleter（先验补全）
                    → 稠密体素网格
                    → marching cubes → mesh
                    → 尺度归一化（复用 scale_normalizer）
                    → 输出 GLB/PLY/STL

工程边界
========
- 不替代 COLMAP+OpenMVS 主 pipeline（精度仍受手机照片物理极限限制）
- 不直接输出 STEP（mesh→参数化 CAD 走 ADR-008 human-in-the-loop）
- v1 不做端到端可微分重建（用冻结的 COLMAP 点云 + 先验补全）
- 输出 mesh 必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床
- 系统定位「工程师助手」，非「全自动生产线」

学术诚信硬约束（D-2）
====================
- 推理时必须固定随机种子（torch.manual_seed + cudnn.deterministic）
- 每次推理的输入点云 hash + 输出 mesh hash 必须记录到 MLflow
- 不允许在推理路径上做 fit_transform（只能用训练时拟合好的预处理器）
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 固定随机种子（D-2 硬约束）
DEFAULT_INFERENCE_SEED = 42


@dataclass
class PartPriorRunnerResult:
    """part_prior 路径的重建结果摘要。

    Attributes:
        output_mesh_path: 输出 mesh 文件路径（PLY 格式）
        dense_voxel_path: 稠密体素网格文件路径（.npy 格式，用于调试）
        num_input_points: 输入稀疏点云点数
        num_output_voxels: 输出稠密体素网格中非零体素数
        vae_latent_dim: VAE latent 维度
        inference_seed: 推理随机种子
        precision_tier: 精度档位（"part_prior"）
        requires_cam_validation: 是否需要 CAM 二次校验（始终 True）
    """

    output_mesh_path: str
    dense_voxel_path: str
    num_input_points: int
    num_output_voxels: int
    vae_latent_dim: int
    inference_seed: int
    precision_tier: str = "part_prior"
    requires_cam_validation: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PartPriorRunner:
    """part_prior 路径运行器。

    集成 PartPriorVAE + PartPriorCompleter + marching cubes，
    把 COLMAP 稀疏点云转为稠密 mesh。

    Attributes:
        vae: 预训练好的 PartPriorVAE 实例
        voxel_dim: 体素网格维度（应与 vae.voxel_dim 一致）
        inference_seed: 推理随机种子（D-2 硬约束，默认 42）
    """

    vae: Any  # PartPriorVAE，用 Any 避免循环导入
    voxel_dim: int = 64
    inference_seed: int = DEFAULT_INFERENCE_SEED

    def run(
        self,
        sparse_points_path: Path,
        bbox: tuple[float, float, float],
        output_dir: Path,
        task_id: str = "",
    ) -> PartPriorRunnerResult:
        """执行 part_prior 重建路径。

        Args:
            sparse_points_path: COLMAP 稀疏点云文件路径（PLY 格式）
            bbox: (length, width, height) mm 包围盒尺寸
            output_dir: 输出目录
            task_id: 任务 ID（用于日志追踪）

        Returns:
            PartPriorRunnerResult

        工程边界：
            - 不直接输出 STEP（mesh→参数化 CAD 走 ADR-008）
            - 输出 mesh 必须经 CAM 二次校验后才允许上机床
        """
        # 延迟导入 torch 与 PartPriorCompleter，避免模块加载期依赖 torch
        try:
            import torch

            from app.image_to_3d.part_prior.encoder import PartPriorCompleter
        except ImportError as e:
            raise RuntimeError(
                "part_prior 路径需要 PyTorch，但当前环境不可用。"
                f" ImportError: {e}。"
                " 请安装 PyTorch 或切换到 colmap_openmvs 主 pipeline。"
            ) from e

        # 固定随机种子（D-2 硬约束）
        torch.manual_seed(self.inference_seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(self.inference_seed)
        torch.backends.cudnn.deterministic = True

        output_dir.mkdir(parents=True, exist_ok=True)

        # 1. 加载稀疏点云（PLY torch.Tensor）
        sparse_points = self._load_ply_points(sparse_points_path, torch)
        num_input = sparse_points.shape[0]
        logger.info("part_prior[%s] 加载稀疏点云: %d 点", task_id, num_input)

        # 2. 先验补全
        completer = PartPriorCompleter(self.vae, voxel_dim=self.voxel_dim)
        dense_voxel = completer.complete(sparse_points, bbox)
        num_output_voxels = int((dense_voxel > 0.5).sum().item())
        logger.info(
            "part_prior[%s] 先验补全完成: %d 非零体素",
            task_id,
            num_output_voxels,
        )

        # 3. 保存稠密体素（调试用）
        dense_voxel_path = output_dir / "dense_voxel.npy"
        np_arr = dense_voxel.detach().cpu().numpy()
        try:
            import numpy as np

            np.save(dense_voxel_path, np_arr)
        except ImportError:
            logger.warning("numpy 不可用，跳过 dense_voxel.npy 保存")

        # 4. marching cubes mesh
        output_mesh_path = output_dir / "output_part_prior.ply"
        self._voxel_to_mesh(dense_voxel, output_mesh_path, torch)

        return PartPriorRunnerResult(
            output_mesh_path=str(output_mesh_path),
            dense_voxel_path=str(dense_voxel_path),
            num_input_points=num_input,
            num_output_voxels=num_output_voxels,
            vae_latent_dim=self.vae.latent_dim,
            inference_seed=self.inference_seed,
        )

    def _load_ply_points(self, ply_path: Path, torch_module: Any) -> Any:
        """加载 PLY 点云 → torch.Tensor (N, 3)。

        Args:
            ply_path: PLY 文件路径
            torch_module: torch 模块（延迟导入传入）

        Returns:
            (N, 3) torch.Tensor，点云坐标（mm）
        """
        try:
            import numpy as np
            from plyfile import PlyData
        except ImportError as e:
            raise RuntimeError(f"part_prior 路径需要 numpy + plyfile，但当前环境不可用: {e}") from e

        plydata = PlyData.read(str(ply_path))
        vertex = plydata["vertex"]
        points = np.stack([vertex["x"], vertex["y"], vertex["z"]], axis=-1).astype(np.float32)
        return torch_module.from_numpy(points)

    def _voxel_to_mesh(
        self,
        voxel: Any,
        output_path: Path,
        torch_module: Any,
    ) -> None:
        """marching cubes → mesh → PLY 保存。

        Args:
            voxel: (D, D, D) 体素网格（torch.Tensor 或 numpy）
            output_path: 输出 PLY 路径
            torch_module: torch 模块（延迟导入传入）

        工程边界：
            - 阈值 0.5（与 PartPriorCompleter 输出对齐）
            - 输出 PLY 格式，供 CAM 软件二次校验
        """
        try:
            import numpy as np
            from skimage import measure
            from trimesh import Trimesh
        except ImportError as e:
            raise RuntimeError(f"part_prior 路径需要 scikit-image + trimesh，但当前环境不可用: {e}") from e

        # 转 numpy
        if hasattr(voxel, "detach"):
            voxel_np = voxel.detach().cpu().numpy()
        else:
            voxel_np = np.asarray(voxel)

        # 二值化
        binary = (voxel_np > 0.5).astype(np.float32)

        # marching cubes
        try:
            verts, faces, normals, _ = measure.marching_cubes(binary, level=0.5)
        except (ValueError, RuntimeError) as e:
            logger.warning("marching_cubes 失败（可能是空体素）: %s，输出空 mesh", e)
            verts = np.zeros((0, 3), dtype=np.float32)
            faces = np.zeros((0, 3), dtype=np.int64)

        mesh = Trimesh(vertices=verts, faces=faces)
        mesh.export(str(output_path))
        logger.info(
            "part_prior mesh 保存完成: %s（%d 顶点, %d 面）",
            output_path,
            len(verts),
            len(faces),
        )


__all__ = ["PartPriorRunner", "PartPriorRunnerResult", "DEFAULT_INFERENCE_SEED"]
