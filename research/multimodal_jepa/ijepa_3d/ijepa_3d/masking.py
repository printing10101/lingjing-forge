"""多尺度块掩码策略模块�?
实现I-JEPA核心的掩码机制：使用16×16像素块作为基础掩码单元�?
每次训练随机掩码30%区域，预测目标为64×64语义块嵌入�?
支持渐进式掩码增强（�?0%逐步提升�?0%）�?
Key components:
    - MultiScaleMasking: 多尺度块级掩码生成器

Example:
    >>> masker = MultiScaleMasking(image_size=256, block_size=16)
    >>> mask = masker.generate_mask(mask_ratio=0.3)
    >>> target_mask = masker.generate_target_mask()
"""

import torch
import torch.nn as nn
from typing import Tuple, Optional


class MultiScaleMasking(nn.Module):
    """多尺度块级掩码生成器�?
    基于16×16像素块的掩码策略，生成上下文区域和目标区域的掩码�?
支持渐进式掩码增强和多种目标块大小�?
    Attributes:
        image_size: 图像分辨率（正方形）
        block_size: 基础掩码块大小（像素�?        num_blocks_per_side: 每边的块数量
        total_blocks: 总块数量
        target_block_size: 目标语义块大小（像素�?        num_target_blocks_per_side: 每边的目标块数量
    """

    def __init__(
        self,
        image_size: int = 256,
        block_size: int = 16,
        target_block_size: int = 64,
    ):
        """初始化多尺度掩码生成器�?
        Args:
            image_size: 输入图像分辨率（正方形，默认256�?
            block_size: 基础掩码块大小（像素，默�?6�?
            target_block_size: 预测目标块大小（像素，默�?4�?        """
        super().__init__()
        self.image_size = image_size
        self.block_size = block_size
        self.target_block_size = target_block_size

        self.num_blocks_per_side = image_size // block_size
        self.total_blocks = self.num_blocks_per_side ** 2
        self.num_target_blocks_per_side = image_size // target_block_size
        self.total_target_blocks = self.num_target_blocks_per_side ** 2

        # 预计算块索引映射
        self._block_indices = torch.arange(self.total_blocks)

    def generate_mask(
        self,
        mask_ratio: float = 0.30,
        batch_size: int = 1,
        device: torch.device = torch.device("cpu"),
    ) -> torch.Tensor:
        """生成随机块级掩码�?
        以block_size×block_size为单位随机掩码指定比例的图像区域�?
        Args:
            mask_ratio: 掩码比例�?.0~1.0），默认0.30
            batch_size: 批次大小
            device: 计算设备

        Returns:
            mask: 形状�?(B, total_blocks) 的布尔张量，
                  True表示被掩码（遮挡），False表示可见
        """
        num_masked = int(self.total_blocks * mask_ratio)

        masks = []
        for _ in range(batch_size):
            # 随机选择要掩码的块索引（无放回采样）
            masked_indices = torch.randperm(self.total_blocks)[:num_masked]

            # 创建掩码张量 (True=掩码, False=可见)
            mask = torch.zeros(self.total_blocks, dtype=torch.bool)
            mask[masked_indices] = True
            masks.append(mask)

        return torch.stack(masks).to(device)

    def generate_target_mask(
        self,
        batch_size: int = 1,
        device: torch.device = torch.device("cpu"),
        num_targets: Optional[int] = None,
    ) -> torch.Tensor:
        """生成目标语义块掩码�?
        指定哪些64×64语义块作为预测目标�?        通常选择4-8个目标块（对应图像不同区域�
��?
        Args:
            batch_size: 批次大小
            device: 计算设备
            num_targets: 目标块数量，默认选择所有块�?5%

        Returns:
            target_mask: 形状�?(B, total_target_blocks) 的布尔张�?        """
        if num_targets is None:
            num_targets = max(1, self.total_target_blocks // 4)

        target_masks = []
        for _ in range(batch_size):
            target_indices = torch.randperm(self.total_target_blocks)[:num_targets]
            target_mask = torch.zeros(self.total_target_blocks, dtype=torch.bool)
            target_mask[target_indices] = True
            target_masks.append(target_mask)

        return torch.stack(target_masks).to(device)

    def expand_block_mask_to_pixels(
        self,
        block_mask: torch.Tensor,
    ) -> torch.Tensor:
        """将块级掩码扩展为像素级掩码�?
        Args:
            block_mask: 形状 (B, total_blocks) 的块级掩�?
        Returns:
            pixel_mask: 形状 (B, 1, H, W) 的像素级掩码
        """
        B = block_mask.shape[0]
        H = W = self.image_size
        num_blocks = self.num_blocks_per_side

        # 重塑�?(B, num_blocks, num_blocks)
        mask_2d = block_mask.view(B, num_blocks, num_blocks)

        # 扩展每个块到block_size×block_size像素
        pixel_mask = mask_2d.float().unsqueeze(1)  # (B, 1, n, n)
        pixel_mask = torch.nn.functional.interpolate(
            pixel_mask,
            size=(H, W),
            mode="nearest",
        )
        return pixel_mask.bool()

    def apply_mask(
        self,
        images: torch.Tensor,
        block_mask: torch.Tensor,
        fill_value: float = 0.0,
    ) -> torch.Tensor:
        """将掩码应用到图像上�?
        Args:
            images: 形状 (B, C, H, W) 的输入图�?
            block_mask: 形状 (B, total_blocks) 的块级掩�?
            fill_value: 掩码区域的填充�?
        Returns:
            masked_images: 掩码后的图像
        """
        pixel_mask = self.expand_block_mask_to_pixels(block_mask)
        # pixel_mask: (B, 1, H, W), True=掩码区域
        masked_images = images.clone()
        masked_images = torch.where(
            pixel_mask.expand_as(images),
            torch.full_like(images, fill_value),
            masked_images,
        )
        return masked_images

    def get_progressive_mask_ratio(
        self,
        current_epoch: int,
        total_epochs: int,
        start_ratio: float = 0.10,
        end_ratio: float = 0.30,
    ) -> float:
        """计算渐进式掩码增强的当前掩码比例�?
        训练过程中从start_ratio线性增加到end_ratio�?
        Args:
            current_epoch: 当前训练轮次�?-indexed�?
            total_epochs: 总训练轮�?            start_ratio: 起始掩码比例
            end_ratio: 最终掩码比�?
        Returns:
            当前掩码比例
        """
        progress = min(current_epoch / max(total_epochs - 1, 1), 1.0)
        return start_ratio + (end_ratio - start_ratio) * progress

    def forward(
        self,
        images: torch.Tensor,
        mask_ratio: float = 0.30,
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """前向传播：生成掩码并应用到图像�?
        Args:
            images: 形状 (B, C, H, W) 的输入图�?            mask_ratio: 掩码比例

        Returns:
            masked_images: 掩码后的图像
            context_mask: 上下文区域掩码（False=可见区域�?            target_mask: 预测目标区域掩码
        """
        B = images.shape[0]
        device = images.device

        context_mask = self.generate_mask(mask_ratio, B, device)
        target_mask = self.generate_target_mask(B, device)
        masked_images = self.apply_mask(images, context_mask)

        return masked_images, context_mask, target_mask
