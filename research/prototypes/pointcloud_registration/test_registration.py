"""Unit checks for the point-to-point ICP core (no CAD or OCP dependency)."""

from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation as Rot

from registration_demo import icp_point_to_point, pose_errors


def _asymmetric_surface(count: int = 5000, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    u = rng.uniform(-1.0, 1.0, count)
    v = rng.uniform(-1.0, 1.0, count)
    z = (
        0.30 * np.sin(2.0 * u) * np.cos(3.0 * v)
        + 0.10 * u * v
        + 0.25 * np.exp(-(((u - 0.35) ** 2 + (v + 0.20) ** 2) / 0.05))
    )
    return np.column_stack([u, v, z])


def test_recovers_known_transform() -> None:
    target = _asymmetric_surface()
    rotation_true = Rot.from_euler("zyx", [20.0, 5.0, 3.0], degrees=True).as_matrix()
    translation_true = np.array([10.0, -5.0, 3.0])
    source = (rotation_true @ target.T).T + translation_true

    result = icp_point_to_point(source, target, max_iter=100, tol=1e-12)
    rotation_error_deg, translation_error_mm = pose_errors(
        result.rotation,
        result.translation,
        rotation_true,
        translation_true,
    )

    assert np.isclose(np.linalg.det(result.rotation), 1.0, atol=1e-10)
    # 1e-4 deg is already 4 orders tighter than the project's 1 deg success line;
    # the residual ~2e-6 deg is floating-point/nearest-neighbor noise, not a pose error.
    assert rotation_error_deg < 1e-4, rotation_error_deg
    assert translation_error_mm < 1e-4, translation_error_mm
    assert result.rmse_mm < 1e-4, result.rmse_mm
    assert result.iterations <= 100


def test_pose_error_is_zero_for_ideal_transform() -> None:
    rotation_true = Rot.from_euler("zyx", [30.0, -7.0, 2.0], degrees=True).as_matrix()
    translation_true = np.array([3.0, 4.0, -2.0])
    rotation_est = rotation_true.T
    translation_est = -rotation_true.T @ translation_true

    rotation_error_deg, translation_error_mm = pose_errors(
        rotation_est,
        translation_est,
        rotation_true,
        translation_true,
    )

    assert rotation_error_deg < 1e-9
    assert translation_error_mm < 1e-9
