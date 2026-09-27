"""拍照重建模块。

将用户用普通手机拍摄的多角度照片，重建为带真实尺度（mm）的三维网格模型，
作为后续工艺仿真/颤振预测/G 代码生成的几何输入。

核心 pipeline：
    多角度照片 → COLMAP SfM（稀疏点云 + 相机位姿）
              → OpenMVS MVS（稠密点云 → 网格 → 纹理）
              → 尺度归一化（用标定块把无量纲 mesh 转成 mm）
              → 输出 GLB / PLY / STL

精度档位（用户必须知情）：
    coarse  : 0.5-2.0 mm，仅适合工艺理解 / 装夹方向预判
    standard: 0.1-1.0 mm，适合非配合面尺寸复核 / 铸锻毛坯检验
    high    : 0.1-0.5 mm，小零件细节观察，仍达不到工业级配合面公差

工业级配合面（H7/h6 等，0.01 mm 公差）物理上无法用手机摄影测量达到，
本模块输出必须经过 CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床。
"""

from __future__ import annotations

from app.image_to_3d.pipeline import (
    ReconstructionPipeline,
    ReconstructionTask,
    ReconstructionTaskStatus,
    ReconstructionResult,
)
from app.image_to_3d.task_store import TaskStore, get_task_store
from app.image_to_3d.precision_disclaimer import (
    PrecisionDisclaimer,
    build_precision_disclaimer,
)

__all__ = [
    "ReconstructionPipeline",
    "ReconstructionTask",
    "ReconstructionTaskStatus",
    "ReconstructionResult",
    "TaskStore",
    "get_task_store",
    "PrecisionDisclaimer",
    "build_precision_disclaimer",
]
