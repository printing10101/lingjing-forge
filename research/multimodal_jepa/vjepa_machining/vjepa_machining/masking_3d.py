"""时空掩码模块。

实现V-JEPA核心的时空混合掩码策略：
- 时间维度掩码：随机掩盖10-30%的帧patch
- 空间维度掩码：随机掩盖15-40%的图像区域
- 以空间块为单位进行掩码，mask_token填充

Key components:
    - SpatioTemporalMasking: 时空掩码生成器
"""

import torch
import torch.nn as nn
from typing import Tuple


class SpatioTemporalMasking(nn.Module):
    """时空混合掩码生成器。

    对视频序列同时应用时间维度和空间维度的掩码。
    时间掩码：掩盖整个时间patch（T×H×W块）
    空间掩码：掩盖指定比例的空间区域块

    Attributes:
        num_frames: 视频帧数
        frame_size: 帧尺寸
        temporal_patch_size: 时间patch大小
        spatial_patch_size: 空间patch大小
        spatial_block_size: 空间掩码块大小
        num_temporal_patches: 时间维度patch数
        num_spatial_patches: 空间维度总patch数
        total_patches: 总时空patch数
    """

    def __init__(
        self,
        num_frames: int = 16,
        frame_size: int = 224,
        temporal_patch_size: int = 2,
        spatial_patch_size: int = 16,
        spatial_mask_block_size: int = 32,
    ):
        super().__init__()
        self.num_frames = num_frames
        self.frame_size = frame_size
        self.temporal_patch_size = temporal_patch_size
        self.spatial_patch_size = spatial_patch_size
        self.spatial_mask_block_size = spatial_mask_block_size

        self.num_temporal_patches = num_frames // temporal_patch_size
        self.spatial_per_side = frame_size // spatial_patch_size
        self.num_spatial_patches = self.spatial_per_side ** 2
        self.total_patches = self.num_temporal_patches * self.num_spatial_patches

        self.mask_blocks_per_side = frame_size // spatial_mask_block_size
        self.total_mask_blocks = self.mask_blocks_per_side ** 2

    def generate_temporal_mask(
        self,
        mask_ratio: float,
        batch_size: int,
        device: torch.device,
    ) -> torch.Tensor:
        """生成时间维度掩码。

        以时间patch为单位随机掩码，被掩码的时间patch中的所有空间patch均被掩码。

        Args:
            mask_ratio: 时间维度掩码比例
            batch_size: 批次大小
            device: 计算设备

        Returns:
            temporal_mask: (B, num_temporal_patches), True=被掩码
        """
        num_masked = max(1, int(self.num_temporal_patches * mask_ratio))
        masks = []
        for _ in range(batch_size):
            mask = torch.zeros(self.num_temporal_patches, dtype=torch.bool, device=device)
            indices = torch.randperm(self.num_temporal_patches, device=device)[:num_masked]
            mask[indices] = True
            masks.append(mask)
        return torch.stack(masks)

    def generate_spatial_mask(
        self,
        mask_ratio: float,
        batch_size: int,
        device: torch.device,
    ) -> torch.Tensor:
        """生成空间维度掩码。

        以空间块为单位随机掩码。

        Args:
            mask_ratio: 空间维度掩码比例
            batch_size: 批次大小
            device: 计算设备

        Returns:
            spatial_mask: (B, num_spatial_patches), True=被掩码
        """
        num_masked = max(1, int(self.num_spatial_patches * mask_ratio))
        masks = []
        for _ in range(batch_size):
            mask = torch.zeros(self.num_spatial_patches, dtype=torch.bool, device=device)
            indices = torch.randperm(self.num_spatial_patches, device=device)[:num_masked]
            mask[indices] = True
            masks.append(mask)
        return torch.stack(masks)

    def combine_masks(
        self,
        temporal_mask: torch.Tensor,
        spatial_mask: torch.Tensor,
    ) -> torch.Tensor:
        """将时间和空间掩码组合成最终时空掩码。

        逻辑：时间掩码中某帧被掩码 -> 该帧所有空间patch被掩码
              空间掩码中某区域被掩码 -> 所有帧中该区域被掩码
        组合 = temporal_mask OR spatial_mask

        Args:
            temporal_mask: (B, T_patches)
            spatial_mask: (B, S_patches)

        Returns:
            combined_mask: (B, total_patches), True=被掩码
        """
        B = temporal_mask.shape[0]
        T = self.num_temporal_patches
        S = self.num_spatial_patches

        # 将temporal_mask扩展到全空间维度
        t_mask = temporal_mask.unsqueeze(-1).expand(B, T, S).reshape(B, T * S)

        # 将spatial_mask扩展到全时间维度
        s_mask = spatial_mask.unsqueeze(1).expand(B, T, S).reshape(B, T * S)

        return t_mask | s_mask

    def get_progressive_mask_ratio(
        self,
        current_epoch: int,
        total_epochs: int,
        start_ratio: float,
        end_ratio: float,
    ) -> float:
        """计算渐进式掩码比例。

        Args:
            current_epoch: 当前轮次
            total_epochs: 总轮次
            start_ratio: 起始比例
            end_ratio: 结束比例

        Returns:
            当前掩码比例
        """
        progress = min(current_epoch / max(total_epochs - 1, 1), 1.0)
        return start_ratio + (end_ratio - start_ratio) * progress

    def apply_mask_to_video(
        self,
        videos: torch.Tensor,
        combined_mask: torch.Tensor,
        fill_value: float = 0.0,
    ) -> torch.Tensor:
        """将掩码应用到视频上。

        将combined_mask映射回视频像素空间并应用掩码。

        Args:
            videos: (B, C, T, H, W)
            combined_mask: (B, total_patches), True=被掩码
            fill_value: 填充值

        Returns:
            masked_videos: (B, C, T, H, W)
        """
        B, C, T, H, W = videos.shape
        device = videos.device
        masked = videos.clone()

        # 将patch级掩码映射回像素
        combined_3d = combined_mask.view(
            B, self.num_temporal_patches,
            self.spatial_per_side, self.spatial_per_side,
        )

        # 上采样到像素分辨率
        combined_3d_up = torch.nn.functional.interpolate(
            combined_3d.float().unsqueeze(1),
            size=(T, H, W),
            mode="trilinear",
        ).squeeze(1).bool()

        # 扩展到C维度
        combined_expanded = combined_3d_up.unsqueeze(1).expand_as(masked)

        masked = torch.where(combined_expanded, torch.tensor(fill_value, device=device), masked)
        return masked

    def forward(
        self,
        videos: torch.Tensor,
        temporal_mask_ratio: float = 0.20,
        spatial_mask_ratio: float = 0.25,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """前向传播：生成并应用时空掩码。

        Args:
            videos: (B, C, T, H, W)
            temporal_mask_ratio: 时间掩码比例
            spatial_mask_ratio: 空间掩码比例

        Returns:
            masked_videos: 掩码后的视频 (B, C, T, H, W)
            combined_mask: 组合掩码 (B, total_patches)
        """
        B = videos.shape[0]
        device = videos.device

        temporal_mask = self.generate_temporal_mask(temporal_mask_ratio, B, device)
        spatial_mask = self.generate_spatial_mask(spatial_mask_ratio, B, device)
        combined_mask = self.combine_masks(temporal_mask, spatial_mask)
        masked_videos = self.apply_mask_to_video(videos, combined_mask)

        return masked_videos, combined_mask

    def generate_target_mask(
        self,
        batch_size: int,
        device: torch.device,
        num_targets: int = None,
    ) -> torch.Tensor:
        """生成预测目标位置掩码。

        Args:
            batch_size: 批次大小
            device: 计算设备
            num_targets: 目标patch数量

        Returns:
            target_mask: (B, num_targets)
        """
        if num_targets is None:
            num_targets = max(1, self.total_patches // 4)

        masks = []
        for _ in range(batch_size):
            indices = torch.randperm(self.total_patches, device=device)[:num_targets]
            masks.append(indices)
        return torch.stack(masks)
