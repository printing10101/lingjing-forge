"""Unit checks for PCA coarse candidates (no CAD dependency)."""

from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation as Rot

from pca_core import pca_candidates


def _anisotropic_cloud(count: int = 5000, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    points = rng.normal(size=(count, 3)) * np.array([3.0, 2.0, 0.7])
    # Break any accidental symmetry so the principal axes are well-defined.
    points[:, 2] += 0.25 * np.sin(points[:, 0])
    return points


def test_candidates_are_proper_rotations_and_include_truth() -> None:
    target = _anisotropic_cloud()
    rotation_true = Rot.from_euler("zyx", [25.0, -8.0, 4.0], degrees=True).as_matrix()
    translation_true = np.array([12.0, -6.0, 3.0])
    source = (rotation_true @ target.T).T + translation_true

    candidates = list(pca_candidates(source, target))
    assert len(candidates) == 4
    assert all(np.isclose(np.linalg.det(item["rotation"]), 1.0, atol=1e-9) for item in candidates)

    errors = []
    for item in candidates:
        residual = item["rotation"] @ rotation_true
        angle = np.degrees(np.arccos(np.clip((np.trace(residual) - 1.0) / 2.0, -1.0, 1.0)))
        translation_error = np.linalg.norm(item["translation"] - (-rotation_true.T @ translation_true))
        errors.append((angle, translation_error))

    best_angle, best_translation = min(errors, key=lambda pair: pair[0])
    assert best_angle < 1e-6, best_angle
    assert best_translation < 1e-6, best_translation
