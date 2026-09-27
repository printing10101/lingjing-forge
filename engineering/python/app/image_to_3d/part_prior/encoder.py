"""零件专属先验 VAE 编码器（ADR-020 思路 2）。

借鉴 GUSH3R 用大规模人体先验预训练的思想，用公开 CAD 数据集预训练
零件几何 VAE，学习典型零件特征分布（平面/圆柱/孔/槽/凸台）。

工程边界（与 ADR-020 思路 2 一致）：
- 输入：64³ 体素网格（由 STEP→mesh→体素化得到）
- 输出：latent 向量（用于先验补全）+ 重建体素
- 不直接输出 STEP（mesh→参数化 CAD 仍走 ADR-008 human-in-the-loop）
- 精度仍受手机照片物理极限限制（0.1-1mm，配合面 0.01mm 不可达）

学术诚信硬约束（D-2）：
- 训练时必须固定随机种子（torch.manual_seed + cudnn.deterministic）
- 所有 loss 与超参必须通过 MLflow 记录，保证可复现
"""

from __future__ import annotations

import torch
import torch.nn as nn

from app.image_to_3d.part_prior.constraints import GeometryConstraints
from app.image_to_3d.part_prior.geometry_loss import total_loss


class PartPriorVAE(nn.Module):
    """零件几何变分自编码器。

    编码器：64³ 体素 → latent_dim 维 latent
    解码器：latent_dim 维 latent → 64³ 体素

    架构选择
    --------
    - 3D 卷积下采样（4 层，stride=2）：64³ → 32³ → 16³ → 8³ → 4³
    - 展平后接 fc_mu / fc_logvar 得到 latent
    - 3D 反卷积上采样（4 层，stride=2）恢复 64³
    - Sigmoid 输出保证体素值在 [0, 1]
    """

    def __init__(
        self,
        voxel_dim: int = 64,
        latent_dim: int = 256,
        base_channels: int = 32,
    ) -> None:
        """初始化 VAE。

        Args:
            voxel_dim: 体素网格维度（默认 64，即 64³）
            latent_dim: latent 向量维度（默认 256）
            base_channels: 基础通道数（默认 32，逐层翻倍）
        """
        super().__init__()
        self.voxel_dim = voxel_dim
        self.latent_dim = latent_dim
        self.base_channels = base_channels
        self.encoder = self._build_encoder(voxel_dim, latent_dim, base_channels)
        self.decoder = self._build_decoder(voxel_dim, latent_dim, base_channels)
        # 展平维度：4³（4 层 stride=2 下采样）× (base_channels*8)
        flat_dim = (voxel_dim // 16) ** 3 * (base_channels * 8)
        self.fc_mu = nn.Linear(flat_dim, latent_dim)
        self.fc_logvar = nn.Linear(flat_dim, latent_dim)

    def _build_encoder(
        self,
        voxel_dim: int,
        latent_dim: int,
        base_ch: int,
    ) -> nn.Module:
        """3D 卷积下采样：64³ × 1 → 4³ × (base_ch*8) → flatten。

        4 层 Conv3d(stride=2) 把 64³ 下采样到 4³，通道数翻倍 4 次：
        1 → base_ch → base_ch*2 → base_ch*4 → base_ch*8
        """
        return nn.Sequential(
            nn.Conv3d(1, base_ch, 4, 2, 1),
            nn.ReLU(),  # 64 → 32
            nn.Conv3d(base_ch, base_ch * 2, 4, 2, 1),
            nn.ReLU(),  # 32 → 16
            nn.Conv3d(base_ch * 2, base_ch * 4, 4, 2, 1),
            nn.ReLU(),  # 16 → 8
            nn.Conv3d(base_ch * 4, base_ch * 8, 4, 2, 1),
            nn.ReLU(),  # 8 → 4
            nn.Flatten(),
        )

    def _build_decoder(
        self,
        voxel_dim: int,
        latent_dim: int,
        base_ch: int,
    ) -> nn.Module:
        """3D 反卷积上采样：latent → 1³ × (latent_dim*8) → 64³ × 1。

        5 层 ConvTranspose3d 把 1³ 上采样到 64³：
        1 → 4 → 8 → 16 → 32 → 64
        第一层 stride=2 padding=0（1 → 4），后续 4 层 stride=2 padding=1 标准翻倍。

        注意：ADR-020 原骨架只有 4 层（输出 32³），此处补到 5 层以匹配
        测试方案期望的 recon.shape == (B, 1, 64, 64, 64)。
        """
        return nn.Sequential(
            nn.Linear(latent_dim, latent_dim * 8),
            nn.ReLU(),
            nn.Unflatten(1, (latent_dim * 8, 1, 1, 1)),
            nn.ConvTranspose3d(latent_dim * 8, base_ch * 8, 4, 2, 0),
            nn.ReLU(),  # 1 → 4
            nn.ConvTranspose3d(base_ch * 8, base_ch * 4, 4, 2, 1),
            nn.ReLU(),  # 4 → 8
            nn.ConvTranspose3d(base_ch * 4, base_ch * 2, 4, 2, 1),
            nn.ReLU(),  # 8 → 16
            nn.ConvTranspose3d(base_ch * 2, base_ch, 4, 2, 1),
            nn.ReLU(),  # 16 → 32
            nn.ConvTranspose3d(base_ch, 1, 4, 2, 1),
            nn.Sigmoid(),  # 32 → 64
        )

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """前向传播：编码 → 重参数化采样 → 解码。

        Args:
            x: (batch, 1, 64, 64, 64) 体素网格，值域 [0, 1]

        Returns:
            (recon, mu, logvar)
            - recon: (batch, 1, 64, 64, 64) 重建体素，值域 [0, 1]（Sigmoid）
            - mu: (batch, latent_dim) 后验均值
            - logvar: (batch, latent_dim) 后验对数方差
        """
        h = self.encoder(x)
        mu = self.fc_mu(h)
        logvar = self.fc_logvar(h)
        # 重参数化：z = mu + σ * ε，允许梯度回传到编码器
        z = mu + torch.exp(0.5 * logvar) * torch.randn_like(mu)
        recon = self.decoder(z)
        return recon, mu, logvar

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        """仅编码，返回 latent（用于先验补全）。

        Args:
            x: (batch, 1, 64, 64, 64) 体素网格

        Returns:
            (batch, latent_dim) latent 向量（取 mu，不采样）
        """
        h = self.encoder(x)
        return self.fc_mu(h)

    def compute_loss(
        self,
        target: torch.Tensor,
        recon: torch.Tensor,
        mu: torch.Tensor,
        logvar: torch.Tensor,
        constraints: GeometryConstraints,
        voxel_dim: int | None = None,
    ) -> tuple[torch.Tensor, dict[str, float]]:
        """训练 loss 接入几何一致性约束（ADR-020 思路 3）。

        在原 VAE 的 reconstruction + KL 基础上叠加 3 类几何约束 loss：
            total = recon + β·KL + γ·symmetry + δ·flatness + ε·nominal
        β=1（标准 VAE，不可配置），γ/δ/ε 由 constraints.weights 控制。

        消融实验：把对应权重置 0 即可关闭该项约束，loss_dict 的 6 个 key
        始终返回（即使该项关闭也返回 0.0），保证 MLflow 日志一致性。

        Args:
            target: (B, 1, D, H, W) 目标体素，值域 [0, 1]
            recon: (B, 1, D, H, W) 重建体素（forward 的第一个返回值）
            mu: (B, latent_dim) 后验均值（forward 的第二个返回值）
            logvar: (B, latent_dim) 后验对数方差（forward 的第三个返回值）
            constraints: 几何约束配置（消融实验时逐项清空对应列表）
            voxel_dim: 体素网格维度，None 时取 self.voxel_dim

        Returns:
            (total_loss, loss_dict)
            - total_loss: 标量 tensor，可直接 backward
            - loss_dict: 6 个固定 key 的字典，供 MLflow 记录
              keys: reconstruction / kl / symmetry / flatness / nominal / total

        工程边界：
            - 推理路径（PartPriorCompleter）不调用本方法
            - loss_dict 的 key 顺序与 D-2 论文表格模板一致
        """
        vd = self.voxel_dim if voxel_dim is None else voxel_dim
        return total_loss(
            recon=recon,
            target=target,
            mu=mu,
            logvar=logvar,
            constraints=constraints,
            voxel_dim=vd,
        )


class PartPriorCompleter:
    """稀疏点云先验补全。

    输入：COLMAP 稀疏点云（来自 ADR-006 主 pipeline）
    输出：稠密体素网格（经过先验补全，转 mesh 在 runner.py 中完成）

    流程
    ====
    1. 稀疏点云 → 64³ 体素网格（栅格化）
    2. 体素网格 → VAE latent（编码）
    3. latent → 解码 → 补全后的稠密体素网格
    4. 补全体素 → marching cubes → mesh（在 runner.py 中完成）

    工程边界
    ========
    - 推理模式（vae.eval()），冻结权重，不更新参数
    - 不做端到端可微重建（用冻结的 COLMAP 点云 + 先验补全）
    - 输出体素值域 [0, 1]，需 threshold 后才能 marching cubes
    """

    def __init__(self, vae: PartPriorVAE, voxel_dim: int = 64) -> None:
        """初始化补全器。

        Args:
            vae: 预训练好的 PartPriorVAE 实例
            voxel_dim: 体素网格维度（应与 vae.voxel_dim 一致）
        """
        self.vae = vae
        self.voxel_dim = voxel_dim
        self.vae.eval()  # 推理模式，冻结权重

    def complete(
        self,
        sparse_points: torch.Tensor,
        bbox: tuple[float, float, float],
    ) -> torch.Tensor:
        """稀疏点云 → 补全后的稠密体素网格。

        Args:
            sparse_points: (N, 3) 点云坐标（mm，相对于包围盒原点）
            bbox: (length, width, height) mm 包围盒尺寸

        Returns:
            (voxel_dim, voxel_dim, voxel_dim) 补全后的体素网格，值域 [0, 1]

        注意：
            输出仍为体素，转 mesh 需调用 marching cubes（在 runner.py 中完成）。
        """
        # 1. 点云体素化
        voxel = self._points_to_voxel(sparse_points, bbox)
        # 2. 编码+解码（先验补全），no_grad 避免推理时构建计算图
        with torch.no_grad():
            recon, _, _ = self.vae(voxel.unsqueeze(0).unsqueeze(0))
        return recon.squeeze()

    def _points_to_voxel(
        self,
        points: torch.Tensor,
        bbox: tuple[float, float, float],
    ) -> torch.Tensor:
        """点云 → 64³ 体素网格（栅格化）。

        Args:
            points: (N, 3) 点云坐标（mm）
            bbox: (length, width, height) mm 包围盒尺寸

        Returns:
            (voxel_dim, voxel_dim, voxel_dim) 二值体素网格（0 或 1）

        实现说明：
            - 把点云坐标归一化到 [0, voxel_dim) 体素索引空间
            - 落在范围内的点置 1.0，其余为 0.0
            - 多个点落在同一体素只置一次（覆盖写）
        """
        voxel = torch.zeros(
            self.voxel_dim,
            self.voxel_dim,
            self.voxel_dim,
            dtype=points.dtype,
            device=points.device,
        )
        # 归一化点到 [0, voxel_dim)
        normalized = points.clone()
        normalized[:, 0] = (points[:, 0] / bbox[0]) * self.voxel_dim
        normalized[:, 1] = (points[:, 1] / bbox[1]) * self.voxel_dim
        normalized[:, 2] = (points[:, 2] / bbox[2]) * self.voxel_dim
        # 栅格化（保留落在范围内的点）
        valid = (normalized >= 0).all(dim=1) & (normalized < self.voxel_dim).all(dim=1)
        indices = normalized[valid].long()
        voxel[indices[:, 0], indices[:, 1], indices[:, 2]] = 1.0
        return voxel


__all__ = ["PartPriorVAE", "PartPriorCompleter"]
