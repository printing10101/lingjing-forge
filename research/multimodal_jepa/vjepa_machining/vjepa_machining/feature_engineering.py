"""异常类型特征工程模块。

为每种异常类型构建多模态特征库，包含视觉特征和传感器特征。

视觉特征提取：
- 断刀：刀具轮廓检测、碎屑特征识别
- 振动异常：图像清晰度评估、画面抖动量化
- 过切：切屑形态分析、加工表面质量评估
- 撞刀：快速位移检测、接触瞬间帧捕捉

传感器特征融合：
- 振动信号：RMS值、峰值因子、峭度
- 声发射：信号能量、峰值捕捉
- 切削力：均值、方差、趋势检测
- 加速度：突变检测、冲击力监控
"""

import torch
import torch.nn as nn
from typing import Dict, Optional


class VisualFeatureExtractor(nn.Module):
    """加工异常视觉特征提取器。

    为每种异常类型提取专用视觉特征。
    """

    def __init__(self, embed_dim: int = 512, num_anomaly_types: int = 4):
        super().__init__()
        self.embed_dim = embed_dim
        self.num_anomaly_types = num_anomaly_types

        # 刀具轮廓检测头
        self.tool_contour_head = nn.Sequential(
            nn.Conv2d(3, 32, 3, padding=1),
            nn.ReLU(),
            nn.Conv2d(32, 64, 3, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d(1),
        )

        # 图像清晰度评估头（振动检测用）
        self.sharpness_head = nn.Sequential(
            nn.Conv2d(3, 16, 3, padding=1),
            nn.ReLU(),
            nn.Conv2d(16, 32, 3, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d(1),
        )

        # 切屑形态分析头
        self.chip_morphology_head = nn.Sequential(
            nn.Conv2d(3, 32, 3, padding=1),
            nn.ReLU(),
            nn.Conv2d(32, 64, 3, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d(1),
        )

        # 快速位移检测头（撞刀检测用）
        self.displacement_head = nn.Sequential(
            nn.Conv2d(3, 32, 3, padding=1),
            nn.ReLU(),
            nn.Conv2d(32, 64, 3, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d(1),
        )

        # 投影到统一维度
        self.fusion_proj = nn.Linear(64 * 4, embed_dim)

    def extract_tool_features(self, frame: torch.Tensor) -> torch.Tensor:
        """提取刀具完整性特征。

        Args:
            frame: (B, 3, H, W)

        Returns:
            (B, 64)
        """
        return self.tool_contour_head(frame).squeeze(-1).squeeze(-1)

    def extract_sharpness(self, frame: torch.Tensor) -> torch.Tensor:
        """评估图像清晰度（振动异常指标）。

        通过分析高频成分比例评估图像清晰度。

        Args:
            frame: (B, 3, H, W)

        Returns:
            (B, 32)
        """
        return self.sharpness_head(frame).squeeze(-1).squeeze(-1)

    def extract_chip_features(self, frame: torch.Tensor) -> torch.Tensor:
        """分析切屑形态与堆积量。

        Args:
            frame: (B, 3, H, W)

        Returns:
            (B, 64)
        """
        return self.chip_morphology_head(frame).squeeze(-1).squeeze(-1)

    def extract_displacement(self, frame: torch.Tensor) -> torch.Tensor:
        """提取快速位移特征（撞刀检测）。

        Args:
            frame: (B, 3, H, W)

        Returns:
            (B, 64)
        """
        return self.displacement_head(frame).squeeze(-1).squeeze(-1)

    def forward(self, frames: torch.Tensor) -> torch.Tensor:
        """提取多维度视觉特征。

        Args:
            frames: (B, T, 3, H, W)

        Returns:
            (B, T, embed_dim)
        """
        B, T, C, H, W = frames.shape
        frames_flat = frames.view(B * T, C, H, W)

        tool_feat = self.extract_tool_features(frames_flat)
        sharp_feat = self.extract_sharpness(frames_flat)
        chip_feat = self.extract_chip_features(frames_flat)
        disp_feat = self.extract_displacement(frames_flat)

        combined = torch.cat([tool_feat, sharp_feat, chip_feat, disp_feat], dim=-1)
        fused = self.fusion_proj(combined)

        return fused.view(B, T, self.embed_dim)


class SensorFeatureProcessor(nn.Module):
    """传感器信号特征处理器。

    处理多通道传感器数据：
    - 振动信号（加速度计）
    - 声发射信号
    - 切削力信号
    - 扭矩信号
    """

    def __init__(
        self,
        num_channels: int = 6,
        feature_dim: int = 128,
        window_size: int = 16,
    ):
        super().__init__()
        self.num_channels = num_channels
        self.feature_dim = feature_dim

        # 时域特征提取（1D卷积）
        self.time_conv = nn.Sequential(
            nn.Conv1d(num_channels, 64, kernel_size=5, padding=2),
            nn.ReLU(),
            nn.Conv1d(64, 128, kernel_size=5, padding=2),
            nn.ReLU(),
            nn.Conv1d(128, feature_dim, kernel_size=5, padding=2),
            nn.ReLU(),
        )

        # 统计特征提取
        self.stat_proj = nn.Linear(num_channels * 6, feature_dim)

        # 融合
        self.fusion = nn.Sequential(
            nn.Linear(feature_dim * 2, feature_dim),
            nn.LayerNorm(feature_dim),
        )

    def extract_statistical_features(self, signals: torch.Tensor) -> torch.Tensor:
        """提取传感器信号的统计特征。

        提取均值、标准差、峰值、RMS、偏度、峰度。

        Args:
            signals: (B, num_channels, T_window)

        Returns:
            (B, num_channels * 6)
        """
        mean = signals.mean(dim=-1)
        std = signals.std(dim=-1)
        peak = signals.max(dim=-1).values
        rms = torch.sqrt((signals ** 2).mean(dim=-1))
        skew = ((signals - signals.mean(dim=-1, keepdim=True)) ** 3).mean(dim=-1) / (
            (signals.std(dim=-1, keepdim=True) + 1e-8) ** 3
        )
        kurtosis = ((signals - signals.mean(dim=-1, keepdim=True)) ** 4).mean(dim=-1) / (
            (signals.std(dim=-1, keepdim=True) + 1e-8) ** 4
        ) - 3

        return torch.cat([mean, std, peak, rms, skew, kurtosis], dim=-1)

    def forward(self, signals: torch.Tensor) -> torch.Tensor:
        """处理传感器信号。

        Args:
            signals: (B, num_channels, T_window)

        Returns:
            (B, feature_dim)
        """
        # 时域卷积特征
        time_feat = self.time_conv(signals).mean(dim=-1)  # (B, feature_dim)

        # 统计特征
        stat_feat_raw = self.extract_statistical_features(signals)
        stat_feat = self.stat_proj(stat_feat_raw)

        # 融合
        return self.fusion(torch.cat([time_feat, stat_feat], dim=-1))


class MachiningFeatureEngineering(nn.Module):
    """加工过程多模态特征工程模块。

    整合视觉特征和传感器特征，为每种异常类型构建专用特征。

    Attributes:
        visual_extractor: 视觉特征提取器
        sensor_processor: 传感器特征处理器
        anomaly_features: 每种异常类型的特征投影
    """

    ANOMALY_TYPES = ["tool_breakage", "vibration_anomaly", "overcut", "collision"]

    def __init__(
        self,
        embed_dim: int = 512,
        sensor_input_channels: int = 6,
        sensor_feature_dim: int = 128,
    ):
        super().__init__()
        self.visual_extractor = VisualFeatureExtractor(embed_dim)
        self.sensor_processor = SensorFeatureProcessor(
            sensor_input_channels, sensor_feature_dim,
        )

        # 多模态融合
        self.multimodal_fusion = nn.Sequential(
            nn.Linear(embed_dim + sensor_feature_dim, embed_dim),
            nn.LayerNorm(embed_dim),
            nn.ReLU(),
            nn.Linear(embed_dim, embed_dim),
        )

        # 异常类型专用特征投影
        self.anomaly_proj = nn.ModuleDict({
            name: nn.Sequential(
                nn.Linear(embed_dim, embed_dim // 2),
                nn.ReLU(),
                nn.Linear(embed_dim // 2, embed_dim),
            )
            for name in self.ANOMALY_TYPES
        })

    def forward(
        self,
        frames: torch.Tensor,
        sensor_data: Optional[torch.Tensor] = None,
    ) -> Dict[str, torch.Tensor]:
        """提取并融合多模态特征。

        Args:
            frames: (B, T, 3, H, W)
            sensor_data: (B, num_channels, T_window) 可选

        Returns:
            特征字典：
            - visual_features: (B, T, D)
            - sensor_features: (B, D) 或 None
            - fused_features: (B, D)
            - anomaly_specific: dict of (B, D)
        """
        visual_feat = self.visual_extractor(frames)
        visual_global = visual_feat.mean(dim=1)  # (B, D)

        if sensor_data is not None:
            sensor_feat = self.sensor_processor(sensor_data)  # (B, D_sensor)
            fused = self.multimodal_fusion(
                torch.cat([visual_global, sensor_feat], dim=-1),
            )
        else:
            sensor_feat = None
            fused = visual_global

        anomaly_specific = {
            name: self.anomaly_proj[name](fused)
            for name in self.ANOMALY_TYPES
        }

        return {
            "visual_features": visual_feat,
            "sensor_features": sensor_feat,
            "fused_features": fused,
            "anomaly_specific": anomaly_specific,
        }

    def compute_anomaly_score(
        self,
        features: Dict[str, torch.Tensor],
        anomaly_type: str,
    ) -> torch.Tensor:
        """计算特定异常类型的异常分数。

        Args:
            features: 特征字典
            anomaly_type: 异常类型名称

        Returns:
            (B,) 异常分数
        """
        spec_feat = features["anomaly_specific"][anomaly_type]
        return torch.norm(spec_feat, dim=-1)
