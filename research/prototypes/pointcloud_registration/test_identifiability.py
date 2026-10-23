"""Unit checks for the identifiability probe (no CAD or OCP dependency).

The probe's claim is statistical: for a shape invariant under G, the two pose
hypotheses (G⁻¹ vs identity) produce indistinguishable residuals, so the
paired bootstrap must report a difference centred on zero with P ≈ 0.5.
For a shape that G does not preserve, the same test must separate them cleanly.
"""

from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation as Rot

from identifiability_probe import (
    DISTINGUISHABLE_D,
    _binomial_two_sided,
    _paired_bootstrap,
    fixed_pose_test,
)

ROT_G = Rot.from_euler("x", 180.0, degrees=True).as_matrix()
TRANS_G = np.array([0.0, 0.0, 60.0])


def _mirror_invariant_cloud(count: int = 2000, seed: int = 0) -> np.ndarray:
    """Point set closed under G: B = B0 ∪ G(B0)."""
    rng = np.random.default_rng(seed)
    b0 = rng.normal(size=(count, 3)) * np.array([20.0, 5.0, 30.0])
    b1 = (ROT_G @ b0.T).T + TRANS_G
    return np.vstack([b0, b1])


def _asymmetric_cloud(count: int = 2000, seed: int = 0) -> np.ndarray:
    """Same construction, but half the points are displaced so G is broken."""
    rng = np.random.default_rng(seed)
    b0 = rng.normal(size=(count, 3)) * np.array([20.0, 5.0, 30.0])
    b1 = (ROT_G @ b0.T).T + TRANS_G
    b1[:, 1] += 0.8  # camber-like asymmetry: G no longer maps the set onto itself
    return np.vstack([b0, b1])


def test_invariant_shape_gives_indistinguishable_hypotheses() -> None:
    target = _mirror_invariant_cloud()
    rng = np.random.default_rng(1)
    source = (ROT_G @ target.T).T + TRANS_G
    source = source + rng.normal(0.0, 0.02, source.shape)

    result = fixed_pose_test(source, target, ROT_G, TRANS_G, seed=1)

    assert abs(result["delta_mm"]) < 0.02
    # 决策规则与生产代码一致：可辨 = D≥2 且 P≥0.95；自对称形状两者不同时成立。
    assert not (result["D"] >= DISTINGUISHABLE_D and result["p_truth_better"] >= 0.95)


def test_asymmetric_shape_separates_hypotheses() -> None:
    target = _asymmetric_cloud()
    rng = np.random.default_rng(1)
    source = (ROT_G @ target.T).T + TRANS_G
    source = source + rng.normal(0.0, 0.02, source.shape)

    result = fixed_pose_test(source, target, ROT_G, TRANS_G, seed=1)

    assert result["delta_mm"] > 0.05
    assert result["D"] >= DISTINGUISHABLE_D
    assert result["p_truth_better"] == 1.0


def test_paired_bootstrap_is_centred_when_hypotheses_agree() -> None:
    rng = np.random.default_rng(3)
    target = rng.normal(size=(1500, 3))
    same = target + rng.normal(0.0, 0.01, target.shape)
    result = _paired_bootstrap(same, same.copy(), target, seed=3)
    assert abs(result["delta_mm"]) < 1e-9
    assert result["p_truth_better"] == 0.0 or result["p_truth_better"] == 1.0


def test_binomial_test_flags_coinflip() -> None:
    # 8/10 with p=0.5 is not significant at 5% in a two-sided exact test.
    assert _binomial_two_sided(8, 10) > 0.05
    assert _binomial_two_sided(10, 10) < 0.01
    assert _binomial_two_sided(5, 10) == 1.0


def _blade_like_surface(count: int = 2000, seed: int = 5, camber: float = 0.0) -> np.ndarray:
    """薄壁叶片样点集：上下两片曲面，厚度峰值在 30% 弦长（NACA 型）。

    camber=0 时关于 x 轴 180° 翻转自对称（压/吸力面互换）；非零弯度破坏该对称。
    """
    rng = np.random.default_rng(seed)
    u = rng.uniform(0.0, 1.0, count)
    z = rng.uniform(0.0, 60.0, count)
    t = 5 * 0.12 * (0.2969 * np.sqrt(u) - 0.1260 * u - 0.3516 * u**2 + 0.2843 * u**3 - 0.1015 * u**4)
    yc = camber / 0.16 * (2 * 0.4 * u - u**2)
    upper = np.column_stack([40 * u, 40 * (yc + t), z])
    lower = np.column_stack([40 * u, 40 * (yc - t), z])
    return np.vstack([upper, lower])


def test_flip_residual_separates_symmetric_axis_from_others() -> None:
    """real 模式的核心判定：对称轴的翻转残差落在点间距量级，其余轴显著高于阈值 1.25。"""
    from identifiability_probe import cloud_spacing, flip_residual

    points = _blade_like_surface()
    centroid = points.mean(axis=0)
    spacing = cloud_spacing(points)
    assert spacing > 0

    symmetric = flip_residual(points, np.array([1.0, 0.0, 0.0]), centroid) / spacing
    other = flip_residual(points, np.array([0.0, 1.0, 0.0]), centroid) / spacing

    assert symmetric <= 1.25, f"对称轴比值应在阈值内，实际 {symmetric:.2f}"
    assert other > 1.25, f"非对称轴比值应超阈值，实际 {other:.2f}"
    assert other > symmetric + 0.3, f"两类轴需可区分：对称 {symmetric:.2f} vs 其他 {other:.2f}"


def test_cloud_loader_accepts_whitespace_pts(tmp_path=None) -> None:
    import tempfile
    from pathlib import Path

    from identifiability_probe import load_cloud

    points = _blade_like_surface()
    target = Path(tempfile.mkdtemp()) / "cloud.pts"
    np.savetxt(target, points, fmt="%.4f")
    loaded = load_cloud(str(target))
    assert loaded.shape == points.shape
    assert np.allclose(loaded, points, atol=1e-4)
