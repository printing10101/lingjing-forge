"""ResNet-18 CNN骨干网络模块。
基于ResNet-18架构实现特征提取，输出64通道低级视觉特征图。
作为I-JEPA编码器的前置特征提取器，将RGB三视图图像转换为中间特征表示。
Key components:
    - ResNetBackbone: 修改后的ResNet-18骨干网络

Example:
    >>> backbone = ResNetBackbone(output_channels=64)
    >>> features = backbone(images)  # (B, 3, 256, 256) -> (B, 64, 64, 64)
"""

import torch
import torch.nn as nn
from typing import List, Optional


class ResidualBlock(nn.Module):
    """ResNet基础残差块。
    实现标准的两层3x3卷积残差连接。
    Attributes:
        conv1: 第一层3x3卷积
        bn1: 第一个BatchNorm
        conv2: 第二层3x3卷积
        bn2: 第二个BatchNorm
        shortcut: 跳跃连接（维度不匹配时使用1x1卷积）
    """

    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        stride: int = 1,
        downsample: Optional[nn.Module] = None,
    ):
        """初始化残差块。
        Args:
            in_channels: 输入通道数
            out_channels: 输出通道数
            stride: 卷积步长
            downsample: 下采样层（跳跃连接）
        """
        super().__init__()
        self.conv1 = nn.Conv2d(
            in_channels, out_channels, kernel_size=3,
            stride=stride, padding=1, bias=False,
        )
        self.bn1 = nn.BatchNorm2d(out_channels)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = nn.Conv2d(
            out_channels, out_channels, kernel_size=3,
            stride=1, padding=1, bias=False,
        )
        self.bn2 = nn.BatchNorm2d(out_channels)
        self.downsample = downsample

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """前向传播。
        Args:
            x: 输入特征图 (B, C_in, H, W)

        Returns:
            输出特征图 (B, C_out, H', W')
        """
        identity = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)

        if self.downsample is not None:
            identity = self.downsample(x)

        out += identity
        out = self.relu(out)

        return out


class ResNetBackbone(nn.Module):
    """ResNet-18骨干网络，专为三视图特征提取设计。
    修改标准ResNet-18以输出64通道特征图，保持较高空间分辨率（64x64），
    适合后续ViT编码器的patch划分。
    架构：conv1(7x7, 64ch, stride2) -> BN -> ReLU -> MaxPool(3x3, stride2)
         -> layer1(64->64, 2 blocks) -> layer2(64->128, 2 blocks)
         -> layer3(128->256, 2 blocks) -> layer4(256->64, 2 blocks)

    最终输出：(B, 64, 64, 64) - 保持256x256输入下采样4x
    Attributes:
        conv1: 初始7x7卷积层
        bn1: 初始BatchNorm
        maxpool: 初始最大池化
        layer1-4: 四个残差层
        output_channels: 输出通道数
    """

    def __init__(self, output_channels: int = 64):
        """初始化ResNet-18骨干网络。
        Args:
            output_channels: 输出通道数（默认64）
        """
        super().__init__()
        self.output_channels = output_channels

        # 初始卷积层：256x256 -> 128x128 (stride=2)
        self.conv1 = nn.Conv2d(
            3, 64, kernel_size=7, stride=2, padding=3, bias=False,
        )
        self.bn1 = nn.BatchNorm2d(64)
        self.relu = nn.ReLU(inplace=True)
        # MaxPool: 128x128 -> 64x64
        self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)

        # Layer1: 64x64 -> 64x64 (无下采样)
        self.layer1 = self._make_layer(64, 64, blocks=2, stride=1)
        # Layer2: 64x64 -> 32x32 (下采样)
        self.layer2 = self._make_layer(64, 128, blocks=2, stride=2)
        # Layer3: 32x32 -> 16x16 (下采样)
        self.layer3 = self._make_layer(128, 256, blocks=2, stride=2)
        # Layer4: 16x16 -> 16x16 (无下采样，输出调整为64通道)
        self.layer4 = self._make_layer(256, output_channels, blocks=2, stride=1)

        # 上采样恢复至64x64分辨率
        self.upsample = nn.Upsample(
            scale_factor=4, mode="bilinear", align_corners=False,
        )

        self._init_weights()

    def _make_layer(
        self,
        in_channels: int,
        out_channels: int,
        blocks: int,
        stride: int = 1,
    ) -> nn.Sequential:
        """构建残差层。
        Args:
            in_channels: 输入通道数
            out_channels: 输出通道数
            blocks: 残差块数量
            stride: 第一个残差块的步长
        Returns:
            nn.Sequential: 残差层
        """
        downsample = None
        if stride != 1 or in_channels != out_channels:
            downsample = nn.Sequential(
                nn.Conv2d(
                    in_channels, out_channels,
                    kernel_size=1, stride=stride, bias=False,
                ),
                nn.BatchNorm2d(out_channels),
            )

        layers = []
        layers.append(ResidualBlock(in_channels, out_channels, stride, downsample))
        for _ in range(1, blocks):
            layers.append(ResidualBlock(out_channels, out_channels))

        return nn.Sequential(*layers)

    def _init_weights(self) -> None:
        """初始化网络权重（Kaiming初始化）。"""
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(
                    m.weight, mode="fan_out", nonlinearity="relu",
                )
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)

    def extract_intermediate_features(
        self,
        x: torch.Tensor,
    ) -> List[torch.Tensor]:
        """提取中间层特征（用于调试和可视化）。
        Args:
            x: 输入图像 (B, 3, 256, 256)

        Returns:
            各层输出特征图列表
        """
        features = []

        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        features.append(x)

        x = self.maxpool(x)
        features.append(x)

        x = self.layer1(x)
        features.append(x)

        x = self.layer2(x)
        features.append(x)

        x = self.layer3(x)
        features.append(x)

        x = self.layer4(x)
        features.append(x)

        return features

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """前向传播：提取64通道视觉特征图。
        Args:
            x: 输入图像 (B, 3, 256, 256)

        Returns:
            特征图 (B, 64, 64, 64)
        """
        # 初始处理
        x = self.conv1(x)      # (B, 64, 128, 128)
        x = self.bn1(x)
        x = self.relu(x)
        x = self.maxpool(x)    # (B, 64, 64, 64)

        # 残差层
        x = self.layer1(x)     # (B, 64, 64, 64)
        x = self.layer2(x)     # (B, 128, 32, 32)
        x = self.layer3(x)     # (B, 256, 16, 16)
        x = self.layer4(x)     # (B, output_channels, 16, 16)

        # 上采样恢复到64x64
        x = self.upsample(x)   # (B, output_channels, 64, 64)

        return x
