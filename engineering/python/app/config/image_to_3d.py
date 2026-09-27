"""拍照重建模块配置（COLMAP+OpenMVS / Hunyuan3D / Part Prior）。

设计目标：用户用普通手机拍摄非标零件多张照片 → 重建粗几何 → 进入 CAM 软件校验
工程现实：手机多视角摄影测量精度 0.1-1mm，配合面公差 0.01mm 物理上够不到。
         因此本模块定位为"工艺感知入口"而非"自动生产建模"，
         输出的粗 mesh 必须经人工确认 + CAM 软件二次校验才能上机床。

支持三条 pipeline：
  - colmap_openmvs：传统多视角摄影测量（推荐，无需 GPU，精度可控）
  - hunyuan3d     ：单图/少图神经生成（需 GPU，作为备选）
  - part_prior    ：零件专属先验补全（ADR-020 思路 2，COLMAP 稀疏点云 + VAE 先验补全）

环境变量命名约定：LNN_I2T3D_*
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.config._utils import _bool_env, _env, _float_env, _int_env, _path, logger


@dataclass
class PartPriorConfig:
    """零件专属先验模型配置（ADR-020 思路 2）。

    作为拍照重建的第三条路径 ``part_prior``，与 COLMAP/Hunyuan3D 并列。
    用公开 CAD 数据集预训练的 VAE 对 COLMAP 稀疏点云做先验补全，输出稠密 mesh。

    工程边界：
    - 不替代 COLMAP+OpenMVS 主 pipeline（精度仍受手机照片物理极限限制）
    - 不直接输出 STEP（mesh→参数化 CAD 仍走 ADR-008 human-in-the-loop）
    - 精度 0.1-1mm，配合面 0.01mm 不可达

    环境变量命名约定：LNN_I2T3D_PART_PRIOR_*
    """

    # 预训练 VAE 权重路径（.pt/.pth）。空字符串表示未配置，part_prior 路径不可用
    pretrained_model_path: str = field(default_factory=lambda: _env("LNN_I2T3D_PART_PRIOR_MODEL_PATH", ""))
    # 体素网格维度（必须与预训练 VAE 一致，默认 64³）
    voxel_dim: int = field(default_factory=lambda: _int_env("LNN_I2T3D_PART_PRIOR_VOXEL_DIM", 64))
    # latent 维度（必须与预训练 VAE 一致）
    latent_dim: int = field(default_factory=lambda: _int_env("LNN_I2T3D_PART_PRIOR_LATENT_DIM", 256))
    # 基础通道数（必须与预训练 VAE 一致）
    base_channels: int = field(default_factory=lambda: _int_env("LNN_I2T3D_PART_PRIOR_BASE_CHANNELS", 32))
    # 推理随机种子（D-2 学术诚信硬约束：固定种子保证可复现）
    inference_seed: int = field(default_factory=lambda: _int_env("LNN_I2T3D_PART_PRIOR_SEED", 42))
    # marching cubes 阈值（体素占据概率，0-1）
    marching_cubes_threshold: float = field(
        default_factory=lambda: _float_env("LNN_I2T3D_PART_PRIOR_MC_THRESHOLD", 0.5)
    )

    def __post_init__(self) -> None:
        """校验 part_prior 配置合法性。"""
        if self.voxel_dim <= 0:
            logger.warning(
                "LNN_I2T3D_PART_PRIOR_VOXEL_DIM=%s invalid, must be > 0. Setting to 64.",
                self.voxel_dim,
            )
            self.voxel_dim = 64
        if self.latent_dim <= 0:
            logger.warning(
                "LNN_I2T3D_PART_PRIOR_LATENT_DIM=%s invalid, must be > 0. Setting to 256.",
                self.latent_dim,
            )
            self.latent_dim = 256
        if self.base_channels <= 0:
            logger.warning(
                "LNN_I2T3D_PART_PRIOR_BASE_CHANNELS=%s invalid, must be > 0. Setting to 32.",
                self.base_channels,
            )
            self.base_channels = 32
        if not 0.0 < self.marching_cubes_threshold < 1.0:
            logger.warning(
                "LNN_I2T3D_PART_PRIOR_MC_THRESHOLD=%s invalid, must be in (0, 1). Setting to 0.5.",
                self.marching_cubes_threshold,
            )
            self.marching_cubes_threshold = 0.5


@dataclass
class ImageTo3DConfig:
    """拍照重建模块配置。

    所有配置项支持环境变量覆盖，遵循 12-Factor App 原则。
    """

    # 总开关：桌面轻量档位下可关闭，避免冷启动延迟
    enabled: bool = field(default_factory=lambda: _bool_env("LNN_I2T3D_ENABLED", True))
    # 默认 pipeline：colmap_openmvs（无需 GPU）或 hunyuan3d（需 GPU）
    pipeline: str = field(default_factory=lambda: _env("LNN_I2T3D_PIPELINE", "colmap_openmvs"))

    # COLMAP 二进制路径：用户需单独安装 COLMAP（https://colmap.github.io/install.html）
    # Windows 默认安装路径示例：C:/Program Files/COLMAP/colmap.exe
    # Linux/macOS：通常在 PATH 中可直接调用 colmap
    colmap_bin: str = field(default_factory=lambda: _env("LNN_I2T3D_COLMAP_BIN", "colmap"))
    # OpenMVS 二进制路径：用户需单独安装 OpenMVS（https://github.com/cdcseacave/openMVS）
    # 默认 DensifyMesh 是 OpenMVS 的网格稠密化命令
    openmvs_bin: str = field(default_factory=lambda: _env("LNN_I2T3D_OPENMVS_BIN", "DensifyMesh"))

    # 输出目录：存放每次重建任务的中间产物和最终 GLB/PLY
    output_dir: str = field(
        default_factory=lambda: _path("LNN_I2T3D_OUTPUT_DIR", os.path.join("output", "image_to_3d"))
    )

    # 照片数量约束：少则重建失败，多则 SfM 慢
    min_photos: int = field(default_factory=lambda: _int_env("LNN_I2T3D_MIN_PHOTOS", 8))
    max_photos: int = field(default_factory=lambda: _int_env("LNN_I2T3D_MAX_PHOTOS", 200))

    # 标定块实际边长（mm）：用户拍照时在场景中放置已知尺寸的标定块（如 30mm 量块），
    # 重建后据此做尺度归一化。无标定块时输出无单位 mesh（仅相对几何，不可生产用）
    calibration_block_mm: float = field(default_factory=lambda: _float_env("LNN_I2T3D_CALIBRATION_BLOCK_MM", 30.0))

    # 精度档位：影响 COLMAP SfM 的特征点数量阈值与 OpenMVS 网格密度
    # coarse : 快，0.5-2mm，适合工艺理解/可视化
    # standard: 默认，0.1-1mm，适合非配合面尺寸复核
    # high : 慢，0.1-0.5mm，小零件细节，仍达不到配合面公差
    precision_tier: str = field(default_factory=lambda: _env("LNN_I2T3D_PRECISION_TIER", "standard"))

    # 并发约束：重建任务重 IO+CPU，桌面模式默认串行
    max_concurrent: int = field(default_factory=lambda: _int_env("LNN_I2T3D_MAX_CONCURRENT", 1))

    # 任务超时（秒）：COLMAP 在 200 张照片 + high 档位下单次约 30-60 分钟
    task_timeout_seconds: int = field(default_factory=lambda: _int_env("LNN_I2T3D_TASK_TIMEOUT", 3600))

    # Hunyuan3D-2 备选 pipeline 配置（需 GPU）
    hunyuan3d_model_dir: str = field(default_factory=lambda: _env("LNN_I2T3D_HUNYUAN3D_MODEL_DIR", ""))
    hunyuan3d_device: str = field(default_factory=lambda: _env("LNN_I2T3D_HUNYUAN3D_DEVICE", "cuda"))
    hunyuan3d_dtype: str = field(default_factory=lambda: _env("LNN_I2T3D_HUNYUAN3D_DTYPE", "float16"))

    # Part Prior 备选 pipeline 配置（ADR-020 思路 2，需预训练 VAE 权重）
    part_prior: PartPriorConfig = field(default_factory=PartPriorConfig)

    # 任务历史保留时长（小时）：超过自动清理
    task_retention_hours: int = field(default_factory=lambda: _int_env("LNN_I2T3D_TASK_RETENTION_HOURS", 72))

    def __post_init__(self) -> None:
        """启动时校验配置合法性。"""
        valid_pipelines = {"colmap_openmvs", "hunyuan3d", "part_prior"}
        if self.pipeline not in valid_pipelines:
            logger.warning(
                "Invalid LNN_I2T3D_PIPELINE='%s', expected one of %s. Falling back to 'colmap_openmvs'.",
                self.pipeline,
                sorted(valid_pipelines),
            )
            self.pipeline = "colmap_openmvs"

        # part_prior 路径需要预训练 VAE 权重，未配置时回退到 colmap_openmvs
        if self.pipeline == "part_prior" and not self.part_prior.pretrained_model_path:
            logger.warning(
                "LNN_I2T3D_PIPELINE=part_prior 但未配置预训练权重 "
                "(LNN_I2T3D_PART_PRIOR_MODEL_PATH 为空)，回退到 colmap_openmvs。"
            )
            self.pipeline = "colmap_openmvs"

        valid_tiers = {"coarse", "standard", "high", "part_prior"}
        if self.precision_tier not in valid_tiers:
            logger.warning(
                "Invalid LNN_I2T3D_PRECISION_TIER='%s', expected one of %s. Falling back to 'standard'.",
                self.precision_tier,
                sorted(valid_tiers),
            )
            self.precision_tier = "standard"

        if self.min_photos < 3:
            logger.warning(
                "LNN_I2T3D_MIN_PHOTOS=%s too small, SfM requires >= 3. Setting to 3.",
                self.min_photos,
            )
            self.min_photos = 3

        if self.max_photos < self.min_photos:
            logger.warning(
                "LNN_I2T3D_MAX_PHOTOS=%s < MIN_PHOTOS=%s, adjusting.",
                self.max_photos,
                self.min_photos,
            )
            self.max_photos = self.min_photos

        if self.calibration_block_mm <= 0:
            logger.warning(
                "LNN_I2T3D_CALIBRATION_BLOCK_MM=%s invalid, must be > 0. Setting to 30.0 (default gauge block).",
                self.calibration_block_mm,
            )
            self.calibration_block_mm = 30.0

    @property
    def precision_specs(self) -> dict:
        """返回当前精度档位对应的工程参数。"""
        specs = {
            "coarse": {
                "expected_accuracy_mm": "0.5-2.0",
                "suitable_for": [
                    "工艺理解卡片",
                    "可视化展示",
                    "与客户沟通形状",
                    "装夹方向预判",
                ],
                "not_suitable_for": [
                    "配合面尺寸",
                    "公差检验",
                    "CAM 加工",
                ],
                "colmap_feature_threshold": 500,
                "openmvs_resolution_level": 1,
            },
            "standard": {
                "expected_accuracy_mm": "0.1-1.0",
                "suitable_for": [
                    "非配合面尺寸复核",
                    "铸锻毛坯检验",
                    "外形轮廓参考",
                    "孔位粗定位（±0.5mm）",
                ],
                "not_suitable_for": [
                    "配合面（H7/h6 等）",
                    "螺纹、退刀槽、盲孔",
                    "CAM 直接加工",
                ],
                "colmap_feature_threshold": 2000,
                "openmvs_resolution_level": 0,
            },
            "high": {
                "expected_accuracy_mm": "0.1-0.5",
                "suitable_for": [
                    "小零件细节观察",
                    "复杂曲面参考",
                    "特征点对齐参考",
                ],
                "not_suitable_for": [
                    "工业级配合面（0.01mm）",
                    "几何公差（GD&T）",
                    "直接上机床",
                ],
                "colmap_feature_threshold": 8000,
                "openmvs_resolution_level": -1,
            },
            # ADR-020 思路 2：零件专属先验补全路径
            # 精度 0.1-1mm（与 standard 同量级，受 VAE 先验质量 + 稀疏点云双重限制）
            "part_prior": {
                "expected_accuracy_mm": "0.1-1.0",
                "suitable_for": [
                    "少纹理零件先验补全",
                    "COLMAP 稀疏点云稠密化",
                    "非配合面尺寸复核",
                    "工艺理解卡片",
                ],
                "not_suitable_for": [
                    "配合面（H7/h6 等，0.01mm）",
                    "几何公差（GD&T）",
                    "CAM 直接加工",
                    "未配置预训练权重的场景",
                ],
                # part_prior 路径仍用 COLMAP 生成稀疏点云，feature_threshold 适用
                "colmap_feature_threshold": 2000,
                # part_prior 路径不走 OpenMVS，此字段保留为 0（占位，runner 不读取）
                "openmvs_resolution_level": 0,
            },
        }
        return specs[self.precision_tier]
