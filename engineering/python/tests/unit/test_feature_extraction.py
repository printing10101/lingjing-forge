"""feature_extraction 模块单元测试（feature_store / precision_disclaimer）。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.feature_extraction.feature_store import (
    ExtractedFeature,
    FeatureExtractionTask,
    FeatureExtractionTaskStatus,
    FeatureReviewStatus,
    FeatureStore,
    FeatureType,
)
from app.feature_extraction.precision_disclaimer import build_feature_disclaimer

pytestmark = pytest.mark.unit


def _feature(feature_id='f1', **kw) -> ExtractedFeature:
    return ExtractedFeature(
        feature_id=feature_id,
        feature_type=kw.get('feature_type', 'plane'),
        params=kw.get('params', {'radius_mm': 10.0, 'height_mm': 5.0}),
        confidence=kw.get('confidence', 0.85),
    )


def _task(task_id='fe-1', status=FeatureExtractionTaskStatus.PENDING.value, **kw) -> FeatureExtractionTask:
    return FeatureExtractionTask(
        task_id=task_id,
        created_at=kw.get('created_at', 1000.0),
        updated_at=kw.get('updated_at', 1000.0),
        status=status,
        input_mesh_path=kw.get('mesh', '/tmp/mesh.ply'),
    )


def _cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.plane_ransac_threshold_mm = 0.5
    cfg.plane_min_inliers = 100
    cfg.cylinder_min_radius_mm = 5.0
    cfg.cylinder_max_radius_mm = 50.0
    cfg.hole_min_radius_mm = 2.0
    return cfg


class TestEnums:
    def test_status_values(self):
        assert FeatureExtractionTaskStatus.FEATURES_EXTRACTED.value == 'features_extracted'
        assert FeatureExtractionTaskStatus.SUCCEEDED.value == 'succeeded'

    def test_feature_type_values(self):
        assert FeatureType.PLANE.value == 'plane'
        assert FeatureType.CYLINDER.value == 'cylinder'
        assert FeatureType.HOLE.value == 'hole'


class TestExtractedFeature:
    def test_effective_params_not_edited(self):
        f = _feature()
        assert f.effective_params() == {'radius_mm': 10.0, 'height_mm': 5.0}

    def test_effective_params_edited(self):
        f = _feature()
        f.review_status = FeatureReviewStatus.EDITED.value
        f.edited_params = {'radius_mm': 12.0}
        assert f.effective_params() == {'radius_mm': 12.0}

    def test_effective_params_edited_empty_falls_back(self):
        f = _feature()
        f.review_status = FeatureReviewStatus.EDITED.value
        assert f.effective_params() == {'radius_mm': 10.0, 'height_mm': 5.0}

    def test_to_dict(self):
        f = _feature()
        d = f.to_dict()
        assert d['feature_id'] == 'f1'
        assert d['feature_type'] == 'plane'
        assert d['confidence'] == 0.85
        assert d['review_status'] == 'pending'


class TestFeatureExtractionTask:
    def test_to_dict_nested(self):
        t = _task()
        t.features = [_feature()]
        d = t.to_dict()
        assert d['task_id'] == 'fe-1'
        assert len(d['features']) == 1
        assert d['features'][0]['feature_id'] == 'f1'


class TestFeatureStore:
    def test_create_and_get(self, tmp_path):
        store = FeatureStore(persist_dir=tmp_path)
        store.create(_task('fe-1'))
        assert store.get('fe-1').task_id == 'fe-1'
        assert (tmp_path / 'fe-1.json').exists()

    def test_get_missing_returns_none(self, tmp_path):
        store = FeatureStore(persist_dir=tmp_path)
        assert store.get('nope') is None

    def test_update_fields(self, tmp_path):
        store = FeatureStore(persist_dir=tmp_path)
        store.create(_task('fe-1'))
        updated = store.update('fe-1', status=FeatureExtractionTaskStatus.REVIEWED.value)
        assert updated.status == 'reviewed'
        assert store.get('fe-1').status == 'reviewed'

    def test_update_missing_returns_none(self, tmp_path):
        store = FeatureStore(persist_dir=tmp_path)
        assert store.update('nope', status='x') is None

    def test_list_all(self, tmp_path):
        store = FeatureStore(persist_dir=tmp_path)
        store.create(_task('fe-1'))
        store.create(_task('fe-2'))
        assert len(store.list_all()) == 2
        assert len(store.list_all(limit=1)) == 1

    def test_delete(self, tmp_path):
        store = FeatureStore(persist_dir=tmp_path)
        store.create(_task('fe-1'))
        assert store.delete('fe-1') is True
        assert store.get('fe-1') is None
        assert not (tmp_path / 'fe-1.json').exists()
        assert store.delete('fe-1') is False

    def test_load_all_on_init(self, tmp_path):
        store = FeatureStore(persist_dir=tmp_path)
        store.create(_task('fe-1'))
        # 重新实例化，从磁盘加载
        store2 = FeatureStore(persist_dir=tmp_path)
        assert store2.get('fe-1') is not None
        assert store2.get('fe-1').features == []

    def test_cleanup_expired(self, tmp_path):
        import time
        store = FeatureStore(persist_dir=tmp_path)
        # 一个过期 SUCCEEDED + 一个新鲜 SUCCEEDED + 一个 PENDING
        old = _task('fe-old', status=FeatureExtractionTaskStatus.SUCCEEDED.value, updated_at=time.time() - 99999)
        fresh = _task('fe-fresh', status=FeatureExtractionTaskStatus.SUCCEEDED.value, updated_at=time.time())
        pending = _task('fe-pending', status=FeatureExtractionTaskStatus.PENDING.value, updated_at=time.time() - 99999)
        store.create(old)
        store.create(fresh)
        store.create(pending)
        cleaned = store.cleanup_expired(retention_hours=1)
        assert cleaned == 1
        assert store.get('fe-old') is None
        assert store.get('fe-fresh') is not None
        assert store.get('fe-pending') is not None

    def test_cleanup_zero_retention(self, tmp_path):
        store = FeatureStore(persist_dir=tmp_path)
        store.create(_task('fe-1'))
        assert store.cleanup_expired(retention_hours=0) == 0


class TestBuildFeatureDisclaimer:
    def test_hard_constraints_always_true(self):
        d = build_feature_disclaimer(_cfg(), mesh_calibrated=True)
        assert d.requires_cam_validation is True
        assert d.requires_engineer_review is True

    def test_uncalibrated_warning(self):
        d = build_feature_disclaimer(_cfg(), mesh_calibrated=False)
        assert '未做尺度归一化' in d.warning_message or '未' in d.warning_message

    def test_calibrated_warning_contains_source(self):
        d = build_feature_disclaimer(_cfg(), mesh_calibrated=True, mesh_source='recon-1')
        assert 'recon-1' in d.warning_message

    def test_extraction_method_contains_threshold(self):
        d = build_feature_disclaimer(_cfg(), mesh_calibrated=True)
        assert 'RANSAC' in d.extraction_method
        assert '0.5' in d.extraction_method

    def test_to_dict(self):
        d = build_feature_disclaimer(_cfg(), mesh_calibrated=True)
        data = d.to_dict()
        assert data['requires_cam_validation'] is True
        assert data['warning_message']
        assert len(data['industrial_hard_gates']) > 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
