"""思路 3（几何一致性显式约束）单元测试。

对应 ADR-020 第 3.8 节测试方案 /
app/image_to_3d/part_prior/geometry_loss.py + constraints.py。

覆盖（3 用例）：
1. test_symmetry_loss_prefers_symmetric
   - 对称体素 loss < 非对称体素 loss
2. test_flatness_loss_prefers_flat_slab
   - 平坦 slab loss < 起伏 slab loss
3. test_total_loss_returns_dict
   - total_loss 返回 loss_dict 含 6 个固定 key
     (reconstruction / kl / symmetry / flatness / nominal / total)

学术诚信对齐：
- 固定随机种子 torch.manual_seed(42)，保证可复现（D-2 硬约束）
- torch 不可用时通过 pytest.importorskip 自然跳过，不注入桩模块伪装通过
- loss_dict 的 6 个 key 顺序与 D-2 论文表格模板一致
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip_ci


# 固定随机种子（D-2 学术诚信硬约束：torch.manual_seed + cudnn.deterministic）
# 在每个 torch 用例内部独立设置，避免模块加载期依赖 torch。


SEED = 42


def test_symmetry_loss_prefers_symmetric():
    """对称性约束 loss：对称体素 loss < 非对称体素 loss。

    sym_voxel: 8³ 全 1，三轴镜像差为 0
    asym_voxel: 仅前半 D 维填 1，x 轴镜像差 > 0
    """
    torch = pytest.importorskip("torch")
    torch.manual_seed(SEED)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(SEED)
        torch.backends.cudnn.deterministic = True

    from app.image_to_3d.part_prior.geometry_loss import symmetry_loss

    sym_voxel = torch.ones(1, 1, 8, 8, 8)
    asym_voxel = torch.zeros(1, 1, 8, 8, 8)
    asym_voxel[:, :, :4, :, :] = 1.0  # 只填一半（D 轴前 4 片）

    sym_loss = symmetry_loss(sym_voxel, ["x"])
    asym_loss = symmetry_loss(asym_voxel, ["x"])

    assert sym_loss.item() < asym_loss.item(), (
        f"对称体素 loss ({sym_loss.item()}) 应小于非对称体素 loss ({asym_loss.item()})"
    )
    # 对称体素的镜像差应严格为 0
    assert sym_loss.item() == 0.0, f"完全对称体素的 symmetry_loss 应为 0，实际 {sym_loss.item()}"


def test_flatness_loss_prefers_flat_slab():
    """配合面平面度约束 loss：平坦 slab loss < 起伏 slab loss。

    flat_voxel: 8³ 全 0.5，slab 标准差为 0
    rough_voxel: 8³ 随机值 [0,1)，slab 标准差 > 0
    配合面区域 ("x", 4, 1) → D 轴 voxel 3-5 的 slab
    """
    torch = pytest.importorskip("torch")
    torch.manual_seed(SEED)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(SEED)
        torch.backends.cudnn.deterministic = True

    from app.image_to_3d.part_prior.geometry_loss import (
        mating_plane_flatness_loss,
    )

    flat_voxel = torch.ones(1, 1, 8, 8, 8) * 0.5
    rough_voxel = torch.rand(1, 1, 8, 8, 8)

    flat_loss = mating_plane_flatness_loss(flat_voxel, [("x", 4, 1)])
    rough_loss = mating_plane_flatness_loss(rough_voxel, [("x", 4, 1)])

    assert flat_loss.item() < rough_loss.item(), (
        f"平坦 slab loss ({flat_loss.item()}) 应小于起伏 slab loss ({rough_loss.item()})"
    )
    # 平坦 slab 的标准差应严格为 0
    assert flat_loss.item() == 0.0, f"完全平坦 slab 的 flatness_loss 应为 0，实际 {flat_loss.item()}"


def test_total_loss_returns_dict():
    """total_loss 返回 (tensor, dict) 且 loss_dict 含 6 个固定 key。

    验收点：
    - loss 是 torch.Tensor（可 backward）
    - loss_dict 的 key 集合精确等于 {reconstruction, kl, symmetry,
      flatness, nominal, total}（D-2 学术诚信：key 固定不变）
    - 所有 loss 值为 float（避免 tensor 序列化问题）
    """
    torch = pytest.importorskip("torch")
    torch.manual_seed(SEED)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(SEED)
        torch.backends.cudnn.deterministic = True

    from app.image_to_3d.part_prior.constraints import GeometryConstraints
    from app.image_to_3d.part_prior.geometry_loss import total_loss

    recon = torch.sigmoid(torch.randn(2, 1, 64, 64, 64))
    target = torch.ones(2, 1, 64, 64, 64) * 0.5
    mu = torch.randn(2, 256)
    logvar = torch.randn(2, 256)
    constraints = GeometryConstraints(
        symmetry_axes=["x"],
        mating_planes=[("x", 32, 2)],
        nominal_values=[("hole_diameter", 10.0, (100.0, 50.0, 20.0))],
    )

    loss, loss_dict = total_loss(recon, target, mu, logvar, constraints)

    # loss 是标量 tensor
    assert isinstance(loss, torch.Tensor), f"total_loss 返回的 loss 应为 torch.Tensor，实际 {type(loss)}"
    assert loss.dim() == 0, f"total_loss 返回的 loss 应为标量（0 维），实际 {loss.dim()} 维"

    # loss_dict 的 6 个固定 key（D-2 学术诚信硬约束）
    expected_keys = {
        "reconstruction",
        "kl",
        "symmetry",
        "flatness",
        "nominal",
        "total",
    }
    assert set(loss_dict.keys()) == expected_keys, (
        f"loss_dict key 集合不匹配：期望 {expected_keys}，实际 {set(loss_dict.keys())}"
    )

    # 所有值应为 float（MLflow 记录要求）
    for key, value in loss_dict.items():
        assert isinstance(value, float), f"loss_dict['{key}'] 应为 float，实际 {type(value)}"
        assert value == value, f"loss_dict['{key}'] 是 NaN"  # NaN 检查
