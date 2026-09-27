"""几何约束项数据结构（ADR-020 思路 3）。

借鉴 GUSH3R 在 loss 中加入几何一致性约束减少 hallucination 的思想，
为零件先验 VAE 训练定义 3 类工业几何约束的配置结构：

1. 对称性约束：零件多为三轴对称，体素网格应镜像一致
2. 配合面平面度约束：已知配合面区域体素应平坦
3. 标称值约束：已知特征尺寸应回归到标称值

工程边界：
- 约束项权重可配置，支持消融实验（逐项开关）
- 与思路 2 的 VAE 训练共享 loss 函数（geometry_loss.total_loss）
- v1 用体素空间简单镜像差，不做可微对称性检测

学术诚信对齐（D-2）：
- 约束项权重必须通过 MLflow 记录，保证消融实验可复现
- 默认权重值在此冻结，不允许运行时隐式修改
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 默认约束项权重（消融实验基准值，D-2 冻结）
# 这 3 个权重对应 total_loss 中的 γ / δ / ε 系数：
# total = recon + β·KL + γ·symmetry + δ·flatness + ε·nominal
DEFAULT_SYMMETRY_WEIGHT = 0.1
DEFAULT_FLATNESS_WEIGHT = 0.1
DEFAULT_NOMINAL_WEIGHT = 0.1


@dataclass
class GeometryConstraints:
    """几何约束配置。

    所有约束项均为可选——空列表表示该项不约束（消融实验时逐项清空）。

    Attributes:
        symmetry_axes: 对称轴列表（如 ["x", "y", "z"]），空列表表示不约束。
            取值范围为 {"x", "y", "z"}，分别对应体素网格的 D/H/W 轴。
        mating_planes: 配合面区域列表，每个元素为 (axis, position_voxel, tolerance_voxel)。
            - axis: 平面法向轴（"x"/"y"/"z"）
            - position_voxel: 平面在轴上的体素坐标（0 ~ voxel_dim-1）
            - tolerance_voxel: 平面度容忍范围（体素单位，slab 半宽）
        nominal_values: 标称值约束列表，每个元素为 (feature_name, target_value_mm, bbox_mm)。
            - feature_name: 特征名（如 "hole_diameter"），v1 仅用于日志，不参与特征提取
            - target_value_mm: 标称尺寸 mm
            - bbox_mm: 包围盒尺寸 mm（用于体素→mm 换算）
        weights: 各约束项权重。key 取值为 {"symmetry", "flatness", "nominal"}，
            value 为非负浮点数。消融实验时把对应项置 0 即可关闭该项约束。

    工程边界：
        - weights 中的 key 缺失时，total_loss 会回退到 DEFAULT_*_WEIGHT
        - 不做 weights 合法性校验（负权重会让 loss 变负，但这是实验自由度）
    """

    symmetry_axes: list[str] = field(default_factory=list)
    mating_planes: list[tuple[str, int, int]] = field(default_factory=list)
    nominal_values: list[tuple[str, float, tuple[float, float, float]]] = field(default_factory=list)
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "symmetry": DEFAULT_SYMMETRY_WEIGHT,
            "flatness": DEFAULT_FLATNESS_WEIGHT,
            "nominal": DEFAULT_NOMINAL_WEIGHT,
        }
    )

    def to_dict(self) -> dict[str, object]:
        """序列化为字典（供 MLflow 记录消融实验配置）。"""
        return {
            "symmetry_axes": list(self.symmetry_axes),
            "mating_planes": [list(p) for p in self.mating_planes],
            "nominal_values": [[name, target, list(bbox)] for name, target, bbox in self.nominal_values],
            "weights": dict(self.weights),
        }


__all__ = [
    "GeometryConstraints",
    "DEFAULT_SYMMETRY_WEIGHT",
    "DEFAULT_FLATNESS_WEIGHT",
    "DEFAULT_NOMINAL_WEIGHT",
]
