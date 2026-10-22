"""Pure-numpy PCA frame and coarse-rotation candidates (no CAD dependency)."""

from __future__ import annotations

import numpy as np


def pca_frame(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    centroid = points.mean(axis=0)
    covariance = np.cov((points - centroid).T)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1]
    return centroid, eigenvectors[:, order], eigenvalues[order]


def pca_candidates(source: np.ndarray, target: np.ndarray):
    source_center, source_axes, source_values = pca_frame(source)
    target_center, target_axes, target_values = pca_frame(target)
    # eigh() can return a left-handed frame depending on eigenvector signs.
    # Normalize both frames to right-handed before enumerating proper sign flips.
    source_axes = source_axes.copy()
    target_axes = target_axes.copy()
    if np.linalg.det(source_axes) < 0:
        source_axes[:, -1] *= -1
    if np.linalg.det(target_axes) < 0:
        target_axes[:, -1] *= -1
    for signs in ((1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1)):
        sign_matrix = np.diag(np.asarray(signs, dtype=float))
        rotation = target_axes @ sign_matrix @ source_axes.T
        if np.linalg.det(rotation) < 0:
            continue
        translation = target_center - rotation @ source_center
        yield {
            "signs": signs,
            "rotation": rotation,
            "translation": translation,
            "source_eigenvalues": source_values.tolist(),
            "target_eigenvalues": target_values.tolist(),
        }
