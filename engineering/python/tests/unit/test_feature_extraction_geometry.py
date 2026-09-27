"""feature_extraction 几何算法单元测试。

覆盖三个 extractor 的纯几何/数学函数：三点定圆、Kåsa 圆拟合、平面基构造、
平面面积估计、孔/凸台分类。这些是特征识别的数学内核，用合成数据即可测。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from app.feature_extraction.cylinder_extractor import _fit_circle_2d
from app.feature_extraction.feature_store import FeatureType
from app.feature_extraction.hole_detector import (
    HoleDetector,
    _build_plane_basis,
    _circle_from_three_points,
    _fit_circle_kasa,
)
from app.feature_extraction.plane_extractor import _estimate_plane_area

pytestmark = pytest.mark.unit


def _circle_points(cx, cy, r, n=24):
    theta = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.column_stack([cx + r * np.cos(theta), cy + r * np.sin(theta)])


class TestCircleFromThreePoints:
    def test_unit_circle(self):
        p1 = np.array([0.0, 1.0])
        p2 = np.array([1.0, 0.0])
        p3 = np.array([-1.0, 0.0])
        cx, cy, r = _circle_from_three_points(p1, p2, p3)
        assert abs(cx) < 1e-9
        assert abs(cy) < 1e-9
        assert abs(r - 1.0) < 1e-9

    def test_offset_circle(self):
        # 圆心 (1, 2) 半径 3 的三个点
        p1 = np.array([4.0, 2.0])
        p2 = np.array([1.0, 5.0])
        p3 = np.array([-2.0, 2.0])
        cx, cy, r = _circle_from_three_points(p1, p2, p3)
        assert abs(cx - 1.0) < 1e-6
        assert abs(cy - 2.0) < 1e-6
        assert abs(r - 3.0) < 1e-6

    def test_collinear_returns_none(self):
        p1 = np.array([0.0, 0.0])
        p2 = np.array([1.0, 1.0])
        p3 = np.array([2.0, 2.0])
        assert _circle_from_three_points(p1, p2, p3) is None


class TestFitCircle:
    def test_kasa_recovers_circle(self):
        points = _circle_points(1.0, 2.0, 2.0)
        r = _fit_circle_kasa(points)
        assert r is not None
        cx, cy, radius = r
        assert abs(cx - 1.0) < 1e-6
        assert abs(cy - 2.0) < 1e-6
        assert abs(radius - 2.0) < 1e-6

    def test_fit_circle_2d_recovers_circle(self):
        points = _circle_points(-1.0, 0.5, 1.5)
        r = _fit_circle_2d(points)
        assert r is not None
        cx, cy, radius = r
        assert abs(cx + 1.0) < 1e-6
        assert abs(cy - 0.5) < 1e-6
        assert abs(radius - 1.5) < 1e-6

    def test_fit_circle_too_few_points(self):
        assert _fit_circle_kasa(np.array([[0.0, 0.0], [1.0, 1.0]])) is None
        assert _fit_circle_2d(np.array([[0.0, 0.0]])) is None

    def test_fit_circle_degenerate(self):
        # 所有点在原点 半径平方 <= 0 None
        pts = np.array([[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]])
        assert _fit_circle_kasa(pts) is None


class TestBuildPlaneBasis:
    def test_z_axis_normal(self):
        normal = np.array([0.0, 0.0, 1.0])
        u, v = _build_plane_basis(normal)
        assert abs(np.dot(u, v)) < 1e-9
        assert abs(np.dot(u, normal)) < 1e-9
        assert abs(np.dot(v, normal)) < 1e-9
        assert abs(np.linalg.norm(u) - 1) < 1e-9
        assert abs(np.linalg.norm(v) - 1) < 1e-9

    def test_x_axis_normal(self):
        normal = np.array([1.0, 0.0, 0.0])
        u, v = _build_plane_basis(normal)
        assert abs(np.dot(u, normal)) < 1e-9
        assert abs(np.dot(v, normal)) < 1e-9
        assert abs(np.dot(u, v)) < 1e-9

    def test_right_handed(self):
        normal = np.array([0.0, 0.0, 1.0])
        u, v = _build_plane_basis(normal)
        # u × v ≈ normal（右手系）
        assert np.linalg.norm(np.cross(u, v) - normal) < 1e-9


class TestEstimatePlaneArea:
    def test_unit_square(self):
        pts = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=float)
        area = _estimate_plane_area(pts)
        assert abs(area - 1.0) < 1e-6

    def test_fewer_than_3_points(self):
        assert _estimate_plane_area(np.array([[0, 0, 0], [1, 1, 1]], dtype=float)) == 0.0

    def test_tilted_square(self):
        # 倾斜平面上的正方形，面积仍应约等于 1
        pts = np.array([[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]], dtype=float)
        area = _estimate_plane_area(pts)
        assert abs(area - 1.0) < 1e-6


class TestClassifyHoleOrBoss:
    def _detector(self):
        cfg = MagicMock()
        cfg.plane_ransac_threshold_mm = 0.5
        return HoleDetector(cfg)

    def _make_plane_data(self, center_z_offset):
        normal = np.array([0.0, 0.0, 1.0])
        # 圆心附近点（2D 距离 < 0.6）带 z 偏移；远处点 z=0
        all_plane_2d = np.array(
            [[0, 0], [0.1, 0], [-0.1, 0], [0, 0.1], [0, -0.1], [1.5, 0], [-1.5, 0], [0, 1.5], [0, -1.5]], dtype=float
        )
        all_plane_3d = np.array(
            [
                [0, 0, center_z_offset],
                [0.1, 0, center_z_offset],
                [-0.1, 0, center_z_offset],
                [0, 0.1, center_z_offset],
                [0, -0.1, center_z_offset],
                [1.5, 0, 0],
                [-1.5, 0, 0],
                [0, 1.5, 0],
                [0, -1.5, 0],
            ],
            dtype=float,
        )
        inlier_2d = np.array([[1.9, 0], [-1.9, 0], [0, 1.9], [0, -1.9]], dtype=float)
        inlier_3d = np.array([[1.9, 0, 0], [-1.9, 0, 0], [0, 1.9, 0], [0, -1.9, 0]], dtype=float)
        return all_plane_3d, all_plane_2d, inlier_3d, inlier_2d, normal

    def test_hole_when_depressed(self):
        d = self._detector()
        all_plane_3d, all_plane_2d, inlier_3d, inlier_2d, normal = self._make_plane_data(-2.0)
        ftype, offset = d._classify_hole_or_boss(
            inlier_3d, inlier_2d, all_plane_3d, all_plane_2d, 0.0, 0.0, 2.0, normal
        )
        assert ftype == FeatureType.HOLE
        assert offset < 0

    def test_boss_when_raised(self):
        d = self._detector()
        all_plane_3d, all_plane_2d, inlier_3d, inlier_2d, normal = self._make_plane_data(2.0)
        ftype, offset = d._classify_hole_or_boss(
            inlier_3d, inlier_2d, all_plane_3d, all_plane_2d, 0.0, 0.0, 2.0, normal
        )
        assert ftype == FeatureType.BOSS
        assert offset > 0

    def test_default_hole_when_no_center_points(self):
        d = self._detector()
        normal = np.array([0.0, 0.0, 1.0])
        # 圆心附近无点（所有点距离 > 0.6）
        all_plane_2d = np.array([[1.0, 0], [-1.0, 0], [0, 1.0], [0, -1.0]], dtype=float)
        all_plane_3d = np.array([[1.0, 0, 0], [-1.0, 0, 0], [0, 1.0, 0], [0, -1.0, 0]], dtype=float)
        inlier_3d = all_plane_3d.copy()
        inlier_2d = all_plane_2d.copy()
        ftype, offset = d._classify_hole_or_boss(
            inlier_3d, inlier_2d, all_plane_3d, all_plane_2d, 0.0, 0.0, 0.5, normal
        )
        assert ftype == FeatureType.HOLE
        assert offset == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
