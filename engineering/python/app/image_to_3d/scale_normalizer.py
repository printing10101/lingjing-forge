"""尺度归一化：把无量纲 mesh 转成真实毫米单位。

核心思路
========
COLMAP SfM 输出的稀疏点云 + OpenMVS 网格是无量纲的（任意单位），
必须做尺度归一化才能用于工程用途。

灵境制造采用的归一化方式：**标定块法**

操作流程（用户侧）：
1. 用户在拍摄场景中放置一个已知尺寸的标定块
   （推荐使用 30mm 量块，可在量具店购买，约 50 元）
2. 用手机对零件 + 标定块一起拍多角度照片
3. 重建后，COLMAP 输出的稀疏点云中包含标定块的两个角点

归一化算法：
1. 从稀疏点云中找出标定块对应的两个角点（用户在照片中标注，
   或通过 ICP 与已知标定块 CAD 模型对齐）
2. 计算这两点在 COLMAP 无量纲坐标系下的距离 d_arb
3. 已知真实距离 d_real = calibration_block_mm
4. 缩放因子 scale = d_real / d_arb
5. 把整个 mesh 顶点 × scale

无标定块时：
- 输出无量纲 mesh（仅相对几何），并在 precision_disclaimer 中明确警告
- 无量纲 mesh 不允许进入后续工艺仿真链路
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import ImageTo3DConfig

logger = logging.getLogger(__name__)


class ScaleNormalizationError(RuntimeError):
    """尺度归一化失败。"""


@dataclass
class ScaleNormalizationResult:
    """尺度归一化结果。"""

    success: bool
    scale_factor: float
    calibrated: bool  # True=已用标定块归一化；False=无量纲输出
    mesh_path: Path
    message: str


def _try_import_trimesh() -> Any:
    """条件导入 trimesh，失败则返回 None。"""
    try:
        import trimesh

        return trimesh
    except ImportError:
        return None


def normalize_scale(
    mesh_path: Path,
    output_path: Path,
    cfg: ImageTo3DConfig,
    calibration_anchor_distance: float | None = None,
) -> ScaleNormalizationResult:
    """对 mesh 做尺度归一化。

    Args:
        mesh_path: OpenMVS 输出的无量纲 mesh 文件路径
        output_path: 归一化后输出路径
        cfg: ImageTo3DConfig
        calibration_anchor_distance: 标定块在无量纲坐标系下的距离。
            None 表示未提供标定块距离（输出无量纲 mesh）。

    Returns:
        ScaleNormalizationResult

    Note:
        如果 calibration_anchor_distance 为 None 或 cfg.calibration_block_mm <= 0，
        仍会写出 mesh，但 calibrated=False，调用方必须拒绝进入工艺仿真链路。
    """
    trimesh = _try_import_trimesh()
    if trimesh is None:
        # trimesh 未安装时直接拷贝原文件并标记为未归一化
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(mesh_path.read_bytes())
        except OSError as e:
            raise ScaleNormalizationError(f"trimesh 未安装且文件拷贝失败: {e}") from e
        return ScaleNormalizationResult(
            success=True,
            scale_factor=1.0,
            calibrated=False,
            mesh_path=output_path,
            message=("trimesh 未安装，mesh 已原样拷贝但未做尺度归一化。此输出无量纲，不允许进入工艺仿真链路。"),
        )

    try:
        mesh = trimesh.load(str(mesh_path), force="mesh")
    except Exception as e:
        raise ScaleNormalizationError(f"加载 mesh 失败 path={mesh_path}: {e}") from e

    if calibration_anchor_distance is None or calibration_anchor_distance <= 0:
        # 无标定块距离：无量纲输出
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            mesh.export(str(output_path))
        except Exception as e:
            raise ScaleNormalizationError(f"导出无量纲 mesh 失败: {e}") from e
        return ScaleNormalizationResult(
            success=True,
            scale_factor=1.0,
            calibrated=False,
            mesh_path=output_path,
            message=(
                "未提供标定块距离，mesh 输出为无量纲（任意单位）。"
                "此输出仅用于可视化，不允许进入工艺仿真链路。"
                "请放置已知尺寸的标定块（如 30mm 量块）并重新触发重建。"
            ),
        )

    # 有标定块距离：计算缩放因子
    if cfg.calibration_block_mm <= 0:
        raise ScaleNormalizationError(f"标定块尺寸配置无效 calibration_block_mm={cfg.calibration_block_mm}")

    scale_factor = cfg.calibration_block_mm / calibration_anchor_distance
    if scale_factor <= 0 or scale_factor > 1000.0:
        # 异常缩放因子，可能 anchor 距离估计错误
        raise ScaleNormalizationError(
            f"缩放因子异常 scale={scale_factor:.6f} "
            f"(anchor_dist={calibration_anchor_distance}, "
            f"block_mm={cfg.calibration_block_mm})。"
            "可能原因：标定块在照片中被误识别。"
        )

    # 应用缩放
    try:
        mesh.apply_scale(scale_factor)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        mesh.export(str(output_path))
    except Exception as e:
        raise ScaleNormalizationError(f"应用缩放或导出失败: {e}") from e

    logger.info(
        "尺度归一化完成 scale=%.6f anchor=%.4f block_mm=%.2f output=%s",
        scale_factor,
        calibration_anchor_distance,
        cfg.calibration_block_mm,
        output_path,
    )

    return ScaleNormalizationResult(
        success=True,
        scale_factor=scale_factor,
        calibrated=True,
        mesh_path=output_path,
        message=(
            f"已用标定块（{cfg.calibration_block_mm}mm）归一化，"
            f"缩放因子 {scale_factor:.4f}。"
            "注意：尺度精度受 SfM 噪声影响，仍需 CAM 软件二次校验。"
        ),
    )
