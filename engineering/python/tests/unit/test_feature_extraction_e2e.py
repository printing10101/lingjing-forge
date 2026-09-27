"""feature_extraction extractor 端到端测试（合成点云 → 特征提取）。"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from app.feature_extraction.feature_store import FeatureType
from app.feature_extraction.hole_detector import HoleDetector
from app.feature_extraction.plane_extractor import PlaneExtractor

pytestmark = pytest.mark.unit


def _plane_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.plane_max_features = 5
    cfg.plane_min_inliers = 20
    cfg.plane_ransac_threshold_mm = 0.1
    return cfg


def _hole_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.plane_ransac_threshold_mm = 0.1
    cfg.hole_min_radius_mm = 0.5
    cfg.hole_max_radius_mm = 20.0
    cfg.plane_min_inliers = 20
    return cfg


def _plane_cloud(n=100):
    rng = np.random.default_rng(42)
    x = rng.uniform(-5.0, 5.0, n)
    y = rng.uniform(-5.0, 5.0, n)
    z = np.zeros(n)
    return np.column_stack([x, y, z])


class TestPlaneExtractorEndToEnd:
    def test_empty_vertices(self):
        ex = PlaneExtractor(_plane_cfg())
        r = ex.extract(np.array([]))
        assert r.success is False
        assert r.method == "fallback_empty"
        assert "不足 3" in r.error_message

    def test_none_vertices(self):
        ex = PlaneExtractor(_plane_cfg())
        r = ex.extract(None)
        assert r.success is False

    def test_wrong_shape(self):
        ex = PlaneExtractor(_plane_cfg())
        # 3 个点但每点 4 维 形状错误（不是 (N,3)）
        r = ex.extract(np.array([[1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0]]))
        assert r.success is False
        assert "形状错误" in r.error_message

    def test_extract_flat_plane(self):
        ex = PlaneExtractor(_plane_cfg())
        r = ex.extract(_plane_cloud())
        assert r.success is True
        assert r.total_vertex_count == 100
        assert r.extracted_count >= 1
        assert r.features[0].feature_type == FeatureType.PLANE.value
        # 法向量应接近 ±z 轴
        normal = np.array(r.features[0].params["normal"])
        assert abs(abs(normal[2]) - 1.0) < 0.1
        assert r.features[0].confidence > 0.9

    def test_extract_result_to_dict(self):
        ex = PlaneExtractor(_plane_cfg())
        r = ex.extract(_plane_cloud())
        d = r.to_dict()
        assert d["success"] is True
        assert d["total_vertex_count"] == 100
        assert isinstance(d["features"], list)


class TestHoleDetectorEndToEnd:
    def test_empty_vertices(self):
        det = HoleDetector(_hole_cfg())
        r = det.detect(np.array([]))
        assert r.success is False

    def test_detect_result_to_dict(self):
        det = HoleDetector(_hole_cfg())
        r = det.detect(np.array([]))
        d = r.to_dict()
        assert d["success"] is False
        assert isinstance(d["features"], list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
