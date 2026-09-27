"""稀疏点云先验补全（ADR-020 思路 2）。

本文件为 ``PartPriorCompleter`` 的独立模块入口，满足 ADR-020 文件清单
（completer.py 作为独立文件）。

为避免循环导入（Completer 依赖 VAE，VAE 定义在 encoder.py），此处从
``encoder.py`` 重新导出 ``PartPriorCompleter``，保持测试导入路径兼容：

    from app.image_to_3d.part_prior.encoder import PartPriorCompleter
    from app.image_to_3d.part_prior.completer import PartPriorCompleter

工程边界
========
- 推理模式（vae.eval()），冻结权重，不更新参数
- 不做端到端可微重建（用冻结的 COLMAP 点云 + 先验补全）
- 输出体素值域 [0, 1]，需 threshold 后才能 marching cubes
- 所有输出必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床
"""

from __future__ import annotations

from app.image_to_3d.part_prior.encoder import PartPriorCompleter, PartPriorVAE

__all__ = ["PartPriorCompleter", "PartPriorVAE"]
