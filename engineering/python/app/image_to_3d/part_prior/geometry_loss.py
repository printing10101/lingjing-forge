"""几何一致性显式约束 loss（ADR-020 思路 3）。

借鉴 GUSH3R 在 loss 中加入几何一致性约束减少 hallucination 的思想，
为零件先验 VAE 训练增加 3 类工业几何约束：

1. 对称性约束（symmetry_loss）：零件多为三轴对称，体素网格应镜像一致
2. 配合面平面度约束（mating_plane_flatness_loss）：已知配合面区域体素应平坦
3. 标称值约束（nominal_value_loss）：已知特征尺寸应回归到标称值

组合 loss（total_loss）：
    total = recon + β·KL + γ·symmetry + δ·flatness + ε·nominal
    其中 β=1（标准 VAE），γ/δ/ε 由 GeometryConstraints.weights 配置

工程边界：
- 不修改 COLMAP 主 pipeline（外部二进制，loss 不可改）
- 不约束 G 代码生成阶段（ADR-014 独立模块）
- v1 用体素空间简单镜像差，不做可微对称性检测
- 所有 loss 值通过 loss_dict 返回，供 MLflow 记录消融实验

学术诚信对齐（D-2）：
- loss_dict 的 6 个 key 固定不变，保证消融实验日志一致性
- 固定随机种子由训练脚本负责（不在 loss 函数内设置）
"""

from __future__ import annotations

import torch
import torch.nn.functional as F

from app.image_to_3d.part_prior.constraints import (
    DEFAULT_FLATNESS_WEIGHT,
    DEFAULT_NOMINAL_WEIGHT,
    DEFAULT_SYMMETRY_WEIGHT,
    GeometryConstraints,
)


def symmetry_loss(
    voxel: torch.Tensor,
    axes: list[str],
) -> torch.Tensor:
    """对称性约束 loss：体素网格三轴镜像差。

    对每个指定轴做镜像翻转，计算原体素与镜像体素的 MSE。
    零件多为三轴对称，此 loss 鼓励模型输出对称的体素网格。

    Args:
        voxel: (B, 1, D, H, W) 体素网格
        axes: 约束轴列表，如 ["x", "y", "z"]
            - "x" 对应 D 轴（dim=2）
            - "y" 对应 H 轴（dim=3）
            - "z" 对应 W 轴（dim=4）

    Returns:
        标量 loss（各轴镜像差 MSE 的均值）

    工程边界：
        - axes 为空时返回 0（该项不约束，消融实验用）
        - 非法轴名静默跳过（不抛异常，保持训练稳定）
    """
    if not axes:
        return torch.tensor(0.0, device=voxel.device)
    loss = torch.tensor(0.0, device=voxel.device)
    valid_count = 0
    for axis in axes:
        if axis == "x":
            # D 轴镜像
            mirrored = torch.flip(voxel, dims=[2])
        elif axis == "y":
            # H 轴镜像
            mirrored = torch.flip(voxel, dims=[3])
        elif axis == "z":
            # W 轴镜像
            mirrored = torch.flip(voxel, dims=[4])
        else:
            continue
        loss = loss + F.mse_loss(voxel, mirrored)
        valid_count += 1
    if valid_count == 0:
        return torch.tensor(0.0, device=voxel.device)
    return loss / valid_count


def mating_plane_flatness_loss(
    voxel: torch.Tensor,
    mating_planes: list[tuple[str, int, int]],
) -> torch.Tensor:
    """配合面平面度约束 loss。

    在已知配合面区域（axis 方向 position_voxel 附近 ±tolerance_voxel），
    体素分布应平坦（标准差小）。工业配合面要求平面度公差，此 loss
    鼓励模型在配合面区域输出平坦的体素分布。

    Args:
        voxel: (B, 1, D, H, W) 体素网格
        mating_planes: [(axis, position_voxel, tolerance_voxel), ...]
            - axis: 法向轴 "x"/"y"/"z"
            - position_voxel: 平面在轴上的体素坐标
            - tolerance_voxel: slab 半宽（体素单位）

    Returns:
        标量 loss（各配合面 slab 标准差的均值）

    工程边界：
        - mating_planes 为空时返回 0
        - slab 范围越界时自动裁剪到 [0, D/H/W]
        - 非法轴名静默跳过
    """
    if not mating_planes:
        return torch.tensor(0.0, device=voxel.device)
    loss = torch.tensor(0.0, device=voxel.device)
    valid_count = 0
    for axis, pos, tol in mating_planes:
        lo = max(0, pos - tol)
        hi = pos + tol + 1  # 切片上界 exclusive
        if axis == "x":
            slab = voxel[:, :, lo:hi, :, :]
        elif axis == "y":
            slab = voxel[:, :, :, lo:hi, :]
        elif axis == "z":
            slab = voxel[:, :, :, :, lo:hi]
        else:
            continue
        # 平面度：slab 沿法向的体素分布标准差应小
        # slab.std() 计算所有元素的总体标准差，越小表示越平坦
        if slab.numel() == 0:
            continue
        loss = loss + slab.std()
        valid_count += 1
    if valid_count == 0:
        return torch.tensor(0.0, device=voxel.device)
    return loss / valid_count


def nominal_value_loss(
    voxel: torch.Tensor,
    nominal_values: list[tuple[str, float, tuple[float, float, float]]],
    voxel_dim: int = 64,
) -> torch.Tensor:
    """标称值约束 loss。

    对已知特征（如孔径），从体素网格中提取该特征尺寸，回归到标称值。
    v1 实现简化版：用体素网格沿 D/H/W 三轴的占据 extent（任意轴存在体素的范围长度）
    的最大值作为特征尺寸估计，与标称值做 MSE。
    v2 需根据 feature_name 做精确特征提取。

    Args:
        voxel: (B, 1, D, H, W) 体素网格
        nominal_values: [(feature_name, target_mm, bbox_mm), ...]
            - feature_name: 特征名（v1 仅用于日志，不参与提取）
            - target_mm: 标称尺寸 mm
            - bbox_mm: 包围盒尺寸 mm（用于体素→mm 换算）
        voxel_dim: 体素网格维度

    Returns:
        标量 loss（各标称值 MSE 的均值）

    工程边界：
        - nominal_values 为空时返回 0
        - bbox_mm 全零（无效包围盒）时跳过该项，不贡献 loss
        - v1 用三轴 extent 最大值近似特征尺寸，精度有限
        - mm_per_voxel = max(bbox_mm) / voxel_dim（取最大维做换算）
    """
    if not nominal_values:
        return torch.tensor(0.0, device=voxel.device)
    loss = torch.tensor(0.0, device=voxel.device)
    valid_count = 0
    for feature_name, target_mm, bbox_mm in nominal_values:
        # bbox_mm 全零（无效包围盒）时跳过，避免 mm_per_voxel=0 导致 loss 退化为常数
        if max(bbox_mm) <= 0:
            continue
        # 简化：用三轴占据 extent 的最大值作为特征尺寸估计
        # 真实实现需根据 feature_name 做特征提取（v2）
        occupancy = (voxel > 0.5).float()  # (B, 1, D, H, W)

        # 沿每个空间轴计算占据 extent：该轴上任意位置存在体素的范围长度
        # dim=2 (D): 在 H、W 维上 any-reduce 后，D 轴上 (max_idx - min_idx + 1)
        def _axis_extent(dim: int) -> torch.Tensor:
            # 在非目标轴上 any-reduce -> (B, 1, L)
            other_dims = [d for d in [2, 3, 4] if d != dim]
            proj = occupancy.any(dim=other_dims)  # (B, 1, L)
            # 每批次沿目标轴的 occupied 区间长度
            L = proj.shape[-1]
            idx = torch.arange(L, device=voxel.device).float()
            # masked 索引：未占据位置用 -inf 以便 max/min 只统计占据区
            occupied_mask = proj.squeeze(1)  # (B, L)
            idx_masked = idx.unsqueeze(0) * occupied_mask + (~occupied_mask).float() * (-1e9)
            max_idx = idx_masked.max(dim=1)[0]  # (B,)
            min_idx = idx_masked.min(dim=1)[0]  # (B,)
            extent = torch.clamp(max_idx - min_idx + 1, min=0.0)  # (B,)
            return extent

        ext_d = _axis_extent(2)
        ext_h = _axis_extent(3)
        ext_w = _axis_extent(4)
        # 三轴 extent 最大值（体素单位）
        max_extent = torch.stack([ext_d, ext_h, ext_w], dim=1).max(dim=1)[0]  # (B,)
        # 换算到 mm
        mm_per_voxel = max(bbox_mm) / voxel_dim
        estimated_mm = max_extent * mm_per_voxel
        target_tensor = torch.full_like(estimated_mm, target_mm, dtype=torch.float32)
        loss = loss + F.mse_loss(estimated_mm.float(), target_tensor)
        valid_count += 1
    if valid_count == 0:
        return torch.tensor(0.0, device=voxel.device)
    return loss / valid_count


def total_loss(
    recon: torch.Tensor,
    target: torch.Tensor,
    mu: torch.Tensor,
    logvar: torch.Tensor,
    constraints: GeometryConstraints,
    voxel_dim: int = 64,
) -> tuple[torch.Tensor, dict[str, float]]:
    """组合 loss：reconstruction + β·KL + γ·symmetry + δ·flatness + ε·nominal。

    β=1（标准 VAE，不可配置），γ/δ/ε 由 constraints.weights 控制。
    消融实验时把对应权重置 0 即可关闭该项约束。

    Args:
        recon: (B, 1, D, H, W) 重建体素，值域 [0, 1]（VAE Sigmoid 输出）
        target: (B, 1, D, H, W) 目标体素，值域 [0, 1]
        mu: (B, latent_dim) 后验均值
        logvar: (B, latent_dim) 后验对数方差
        constraints: 几何约束配置
        voxel_dim: 体素网格维度（传给 nominal_value_loss）

    Returns:
        (total_loss, loss_dict)
        - total_loss: 标量 tensor，可直接 backward
        - loss_dict: 6 个 key 的字典，供 MLflow 记录
            keys: reconstruction / kl / symmetry / flatness / nominal / total

    学术诚信对齐（D-2）：
        - loss_dict 的 6 个 key 固定不变，保证消融实验日志一致性
        - 所有 loss 值取 .item() 转 float，避免 tensor 序列化问题
    """
    # 标准 VAE β=1 语义：recon 与 KL 均 per-sample 求和后对 batch 求 mean，
    # 保证 loss 量级不随 batch_size 放大（原 reduction="sum" 会将所有元素求总
    # 和，导致 batch 越大 loss 越大，与 β=1 权重失衡）。
    batch_size = recon.shape[0]
    recon_loss = F.binary_cross_entropy(recon, target, reduction="none").sum()
    recon_loss = recon_loss / batch_size
    kl_loss = -0.5 * torch.sum(1 + logvar - mu.pow(2) - logvar.exp())
    kl_loss = kl_loss / batch_size
    sym_loss = symmetry_loss(recon, constraints.symmetry_axes)
    flat_loss = mating_plane_flatness_loss(recon, constraints.mating_planes)
    nom_loss = nominal_value_loss(recon, constraints.nominal_values, voxel_dim)

    w = constraints.weights
    total = (
        recon_loss
        + kl_loss
        + w.get("symmetry", DEFAULT_SYMMETRY_WEIGHT) * sym_loss
        + w.get("flatness", DEFAULT_FLATNESS_WEIGHT) * flat_loss
        + w.get("nominal", DEFAULT_NOMINAL_WEIGHT) * nom_loss
    )

    loss_dict: dict[str, float] = {
        "reconstruction": float(recon_loss.item()),
        "kl": float(kl_loss.item()),
        "symmetry": float(sym_loss.item()),
        "flatness": float(flat_loss.item()),
        "nominal": float(nom_loss.item()),
        "total": float(total.item()),
    }
    return total, loss_dict


__all__ = [
    "symmetry_loss",
    "mating_plane_flatness_loss",
    "nominal_value_loss",
    "total_loss",
]
