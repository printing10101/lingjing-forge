"""CAD 数据集加载与预处理（ADR-020 思路 2）。

从公开 CAD 数据源（GrabCAD/TraceParts）抓取的 STEP/STL 文件，
经预处理转为 64³ 体素网格，作为 PartPriorVAE 的预训练数据。

预处理流程
==========
1. STEP/STL → mesh（trimesh.load）
2. mesh → 点云采样（trimesh.mesh.sample，固定 N=10000 点）
3. 点云 → 归一化（中心化 + 缩放到包围盒）
4. 点云 → 64³ 体素网格（栅格化）

工程边界
========
- 不依赖商业 CAD 软件（FreeCAD/OpenSCAD 命令行可选）
- v1 用 trimesh + numpy 实现轻量预处理，不引入 Open3D（避免依赖膨胀）
- 体素化阈值可配置（默认 0.5）
- 数据集 manifest 用 JSON 索引，记录每个样本的类别/来源/预处理参数

学术诚信硬约束（D-2）
====================
- 数据集划分（train/val/test）必须固定随机种子
- 每个样本的预处理参数必须记录到 MLflow
- 不允许在 test 集上做模型选择
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# 默认体素网格维度（与 PartPriorVAE.voxel_dim 一致）
DEFAULT_VOXEL_DIM = 64
# 默认点云采样数（mesh 点云）
DEFAULT_NUM_SAMPLES = 10000
# 默认体素化阈值（点云密度 二值体素）
DEFAULT_VOXEL_THRESHOLD = 0.5
# 固定随机种子（D-2 硬约束）
DEFAULT_SEED = 42


@dataclass
class PartPriorSample:
    """单个零件样本的元数据与体素数据。

    Attributes:
        sample_id: 样本唯一 ID（如 "flange_001"）
        category: 零件类别（如 "flange"/"bearing_block"/"bracket"）
        source: 数据来源（如 "GrabCAD"/"TraceParts"）
        source_url: 原始文件 URL（可追溯性）
        step_path: 原始 STEP 文件路径（可选，仅用于追溯）
        voxel: 64³ 体素网格（值域 [0, 1]），由预处理生成
        bbox_dimensions: 包围盒尺寸 (length, width, height) mm
        preprocess_params: 预处理参数（用于 MLflow 记录）
    """

    sample_id: str
    category: str
    source: str
    source_url: str
    step_path: str | None
    voxel: np.ndarray  # (voxel_dim, voxel_dim, voxel_dim)
    bbox_dimensions: tuple[float, float, float]
    preprocess_params: dict[str, Any] = field(default_factory=dict)


@dataclass
class PartPriorDataset:
    """零件先验预训练数据集。

    Attributes:
        samples: 样本列表
        categories: 类别列表（如 ["flange", "bearing_block", ...]）
        voxel_dim: 体素网格维度
        seed: 随机种子（用于划分一致性）
    """

    samples: list[PartPriorSample] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    voxel_dim: int = DEFAULT_VOXEL_DIM
    seed: int = DEFAULT_SEED

    def __len__(self) -> int:
        return len(self.samples)

    def split(
        self,
        train_ratio: float = 0.8,
        val_ratio: float = 0.1,
    ) -> tuple[list[PartPriorSample], list[PartPriorSample], list[PartPriorSample]]:
        """按固定种子划分 train/val/test。

        Args:
            train_ratio: 训练集比例（默认 0.8）
            val_ratio: 验证集比例（默认 0.1，test 自动取剩余）

        Returns:
            (train_samples, val_samples, test_samples)
        """
        rng = random.Random(self.seed)
        indices = list(range(len(self.samples)))
        rng.shuffle(indices)

        n_total = len(indices)
        n_train = int(n_total * train_ratio)
        n_val = int(n_total * val_ratio)

        train_idx = indices[:n_train]
        val_idx = indices[n_train : n_train + n_val]
        test_idx = indices[n_train + n_val :]

        train = [self.samples[i] for i in train_idx]
        val = [self.samples[i] for i in val_idx]
        test = [self.samples[i] for i in test_idx]

        logger.info(
            "数据集划分完成 train=%d val=%d test=%d (seed=%d)",
            len(train),
            len(val),
            len(test),
            self.seed,
        )
        return train, val, test

    def to_voxel_tensor(self, samples: list[PartPriorSample]) -> np.ndarray:
        """把样本列表转为 (N, 1, D, D, D) numpy 数组，供 PyTorch DataLoader 使用。

        Args:
            samples: 样本列表

        Returns:
            (N, 1, voxel_dim, voxel_dim, voxel_dim) float32 数组
        """
        n = len(samples)
        arr = np.zeros((n, 1, self.voxel_dim, self.voxel_dim, self.voxel_dim), dtype=np.float32)
        for i, s in enumerate(samples):
            arr[i, 0] = s.voxel.astype(np.float32)
        return arr


def voxelize_points(
    points: np.ndarray,
    bbox: tuple[float, float, float],
    voxel_dim: int = DEFAULT_VOXEL_DIM,
    threshold: float = DEFAULT_VOXEL_THRESHOLD,
) -> np.ndarray:
    """点云 → 体素网格（栅格化 + 阈值化）。

    Args:
        points: (N, 3) 点云坐标（mm，已中心化到包围盒原点）
        bbox: (length, width, height) mm 包围盒尺寸
        voxel_dim: 体素网格维度（默认 64）
        threshold: 体素化阈值（默认 0.5），点云密度低于此值的体素置 0

    Returns:
        (voxel_dim, voxel_dim, voxel_dim) 二值体素网格（0 或 1）
    """
    voxel = np.zeros((voxel_dim, voxel_dim, voxel_dim), dtype=np.float32)
    # 归一化点到 [0, voxel_dim)
    normalized = np.zeros_like(points)
    normalized[:, 0] = (points[:, 0] / bbox[0]) * voxel_dim
    normalized[:, 1] = (points[:, 1] / bbox[1]) * voxel_dim
    normalized[:, 2] = (points[:, 2] / bbox[2]) * voxel_dim
    # 栅格化（保留落在范围内的点）
    valid = (normalized >= 0).all(axis=1) & (normalized < voxel_dim).all(axis=1)
    indices = normalized[valid].astype(np.int64)
    for idx in indices:
        voxel[idx[0], idx[1], idx[2]] += 1.0
    # 归一化到 [0, 1]（按最大占据数）
    max_count = voxel.max()
    if max_count > 0:
        voxel = voxel / max_count
    # 阈值化
    voxel = (voxel >= threshold).astype(np.float32)
    return voxel


def load_dataset_manifest(manifest_path: Path) -> dict[str, Any]:
    """加载数据集 manifest（JSON 索引）。

    Manifest 格式：
    {
        "categories": ["flange", "bearing_block", ...],
        "samples": [
            {
                "sample_id": "flange_001",
                "category": "flange",
                "source": "GrabCAD",
                "source_url": "https://...",
                "step_path": "/data/cad/flange_001.step",
                "bbox_dimensions": [100.0, 50.0, 20.0]
            },
            ...
        ]
    }

    Args:
        manifest_path: manifest.json 路径

    Returns:
        manifest 字典

    Raises:
        FileNotFoundError: manifest 文件不存在
        ValueError: manifest 文件损坏或格式错误
    """
    # M20 修复：manifest 是外部 JSON 文件，需处理不存在和格式错误两种情况
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except FileNotFoundError:
        logger.error("manifest 文件不存在：%s", manifest_path)
        raise
    except json.JSONDecodeError as e:
        logger.error("manifest 文件损坏（JSON 格式错误）：%s: %s", manifest_path, e)
        raise ValueError(f"manifest 文件损坏：{manifest_path}: {e}") from e
    except OSError as e:
        logger.error("manifest 文件读取失败：%s: %s", manifest_path, e)
        raise

    if not isinstance(manifest, dict):
        raise ValueError(f"manifest 格式错误：期望 dict，实际 {type(manifest).__name__}: {manifest_path}")

    logger.info(
        "加载 manifest: %s（%d 类别, %d 样本）",
        manifest_path,
        len(manifest.get("categories", [])),
        len(manifest.get("samples", [])),
    )
    return manifest


__all__ = [
    "PartPriorSample",
    "PartPriorDataset",
    "voxelize_points",
    "load_dataset_manifest",
    "DEFAULT_VOXEL_DIM",
    "DEFAULT_NUM_SAMPLES",
    "DEFAULT_VOXEL_THRESHOLD",
    "DEFAULT_SEED",
]
