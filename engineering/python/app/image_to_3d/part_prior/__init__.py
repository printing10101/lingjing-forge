"""零件专属先验模型模块（ADR-020 思路 2）。

借鉴 GUSH3R 用大规模人体先验预训练 + 前馈推理的范式，用公开 CAD 数据集
预训练零件几何 VAE，学习典型零件特征分布（平面/圆柱/孔/槽/凸台），
作为拍照重建（ADR-006）的第三条路径 ``part_prior``。

三条路径对比
============
- COLMAP+OpenMVS：纯几何重建，无零件先验，薄壁件/反光面/少纹理区域易失败
- Hunyuan3D-2：通用物体先验，对工业零件配合面/装配面语义不理解
- part_prior：零件专属先验，比 COLMAP 鲁棒，比 Hunyuan3D-2 精准

工程边界
========
- 输入：64³ 体素网格（由 STEP→mesh→体素化得到）
- 输出：latent 向量 + 补全后的稠密体素网格
- 不直接输出 STEP（mesh→参数化 CAD 仍走 ADR-008 human-in-the-loop）
- 精度仍受手机照片物理极限限制（0.1-1mm，配合面 0.01mm 不可达）
- 所有输出必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床

对应 ADR：ADR-020 思路 2 / ADR-006 拍照重建模块
"""

from __future__ import annotations

from app.image_to_3d.part_prior.encoder import (
    PartPriorCompleter,
    PartPriorVAE,
)
from app.image_to_3d.part_prior.runner import PartPriorRunner, PartPriorRunnerResult

__all__ = [
    "PartPriorVAE",
    "PartPriorCompleter",
    "PartPriorRunner",
    "PartPriorRunnerResult",
]
