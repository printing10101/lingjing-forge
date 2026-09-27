"""chatter_prediction 模块单元测试（chatter_disclaimer / chatter_store / _types 契约）。"""

from __future__ import annotations

import json

import pytest

from app.chatter_prediction._types import (
    ChatterPredictionTaskStatus,
    ChatterReviewStatus,
    FeatureChatterResult,
    PredictionMethod,
)
from app.chatter_prediction.chatter_disclaimer import build_chatter_disclaimer
from app.chatter_prediction.chatter_store import (
    ChatterPredictionTask,
    ReviewError,
    TaskStore,
    generate_task_id,
    get_task_store,
)

pytestmark = pytest.mark.unit


def _feature(feature_id='f1', stable=True, **kw) -> FeatureChatterResult:
    return FeatureChatterResult(
        feature_id=feature_id,
        feature_type=kw.get('feature_type', 'plane'),
        material_id=kw.get('material_id', 'steel'),
        spindle_rpm=kw.get('spindle_rpm', 2000.0),
        axial_depth_mm=kw.get('axial_depth_mm', 1.0),
        limit_depth_mm=kw.get('limit_depth_mm', 2.0),
        stable=stable,
        stability_margin=kw.get('stability_margin', 0.5),
        method=kw.get('method', 'analytical'),
        ltc_active=kw.get('ltc_active', False),
    )


def _task(task_id='ch-1', status=ChatterPredictionTaskStatus.PENDING.value, **kw) -> ChatterPredictionTask:
    return ChatterPredictionTask(
        task_id=task_id,
        created_at=kw.get('created_at', 1000.0),
        source_cutting_parameters_task_id=kw.get('src_cp', 'cp-1'),
        chatter_params_path=kw.get('params', '/tmp/params.json'),
        material_id=kw.get('material_id', 'steel'),
        precision_tier=kw.get('precision_tier', 'standard'),
        mesh_calibrated=kw.get('mesh_calibrated', True),
        status=status,
    )


def _disclaimer_kwargs(**kw) -> dict:
    return {
        'mesh_calibrated': kw.get('mesh_calibrated', True),
        'chatter_params_source': kw.get('chatter_params_source', '/tmp/params.json'),
        'material_id': kw.get('material_id', 'ti_tc4'),
        'material_calibration_status': kw.get('material_calibration_status', 'calibrated'),
        'precision_tier': kw.get('precision_tier', 'standard'),
        'machine_type': kw.get('machine_type', 'vmc_850'),
        'prediction_method': kw.get('prediction_method', 'analytical'),
        'ltc_model_available': kw.get('ltc_model_available', False),
        'ltc_active_ratio': kw.get('ltc_active_ratio', 0.0),
        'chatter_report_ready': kw.get('chatter_report_ready', False),
    }


class TestEnums:
    def test_task_status_values(self):
        assert ChatterPredictionTaskStatus.PREDICTED.value == 'predicted'
        assert ChatterPredictionTaskStatus.SUCCEEDED.value == 'succeeded'

    def test_prediction_method_values(self):
        assert PredictionMethod.ANALYTICAL.value == 'analytical'
        assert PredictionMethod.NEURAL_NETWORK.value == 'neural_network'
        assert PredictionMethod.FALLBACK.value == 'fallback'


class TestFeatureChatterResult:
    def test_effective_result_not_edited(self):
        f = _feature(stable=True)
        eff = f.effective_result()
        assert eff['stable'] == 1.0
        assert eff['axial_depth_mm'] == 1.0
        assert eff['limit_depth_mm'] == 2.0

    def test_effective_result_edited(self):
        f = _feature(stable=False)
        f.review_status = ChatterReviewStatus.EDITED.value
        f.edited_params = {'axial_depth_mm': 0.5, 'stable': True}
        eff = f.effective_result()
        assert eff['axial_depth_mm'] == 0.5
        assert eff['stable'] == 1.0

    def test_effective_result_edited_empty_falls_back(self):
        f = _feature(stable=True)
        f.review_status = ChatterReviewStatus.EDITED.value
        assert f.effective_result()['stable'] == 1.0

    def test_to_dict(self):
        f = _feature()
        d = f.to_dict()
        assert d['feature_id'] == 'f1'
        assert d['stable'] is True
        assert d['confidence'] == 0.8
        assert d['material_calibration_status'] == 'calibrated'


class TestChatterPredictionTask:
    def test_to_dict_nested(self):
        t = _task()
        t.feature_results = [_feature()]
        d = t.to_dict()
        assert d['task_id'] == 'ch-1'
        assert d['cam_validation_required'] is True
        assert len(d['feature_results']) == 1
        assert d['feature_results'][0]['feature_id'] == 'f1'


class TestGenerateTaskId:
    def test_prefix_and_uniqueness(self):
        tid = generate_task_id()
        assert tid.startswith('ch_')
        ids = {generate_task_id() for _ in range(50)}
        assert len(ids) == 50


class TestTaskStore:
    def test_create_and_get(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        store.create_task(_task('ch-1'))
        assert store.get_task('ch-1').task_id == 'ch-1'
        assert (tmp_path / 'ch-1.json').exists()

    def test_get_missing_returns_none(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        assert store.get_task('nope') is None

    def test_update_and_persist(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        t = _task('ch-1')
        store.create_task(t)
        t.status = ChatterPredictionTaskStatus.REVIEWED.value
        store.update_task(t)
        assert store.get_task('ch-1').status == 'reviewed'
        data = json.loads((tmp_path / 'ch-1.json').read_text(encoding='utf-8'))
        assert data['status'] == 'reviewed'

    def test_list_sorted_desc(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        store.create_task(_task('ch-1', created_at=1000.0))
        store.create_task(_task('ch-2', created_at=2000.0))
        assert [t.task_id for t in store.list_tasks()] == ['ch-2', 'ch-1']

    def test_delete_normal(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        store.create_task(_task('ch-1'))
        assert store.delete_task('ch-1') is True
        assert store.get_task('ch-1') is None
        assert not (tmp_path / 'ch-1.json').exists()

    def test_delete_missing_returns_false(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        assert store.delete_task('nope') is False

    def test_delete_succeeded_forbidden(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        store.create_task(_task('ch-1', status=ChatterPredictionTaskStatus.SUCCEEDED.value))
        with pytest.raises(ReviewError, match='禁止删除'):
            store.delete_task('ch-1')

    def test_singleton(self):
        assert get_task_store() is get_task_store()


class TestChatterDisclaimer:
    def test_hard_constraints_always_true(self):
        d = build_chatter_disclaimer(**_disclaimer_kwargs())
        assert d.requires_cam_validation is True
        assert d.requires_engineer_review is True
        assert 'CAM' in d.warning_message

    def test_uncalibrated_mesh_warning(self):
        d = build_chatter_disclaimer(**_disclaimer_kwargs(mesh_calibrated=False))
        assert '未标定' in d.warning_message

    def test_pending_calibration_material_warning(self):
        d = build_chatter_disclaimer(**_disclaimer_kwargs(material_calibration_status='pending_calibration'))
        assert '待自采' in d.warning_message

    def test_ltc_unavailable_warning(self):
        d = build_chatter_disclaimer(**_disclaimer_kwargs(ltc_model_available=False))
        assert 'Tlusty' in d.warning_message

    def test_ltc_partial_ratio_warning(self):
        d = build_chatter_disclaimer(**_disclaimer_kwargs(ltc_model_available=True, ltc_active_ratio=0.5))
        assert '50%' in d.warning_message

    def test_ltc_full_ratio_warning(self):
        d = build_chatter_disclaimer(**_disclaimer_kwargs(ltc_model_available=True, ltc_active_ratio=1.0))
        assert '全部特征' in d.warning_message

    def test_neural_network_method_warning(self):
        d = build_chatter_disclaimer(**_disclaimer_kwargs(prediction_method='neural_network', ltc_model_available=True, ltc_active_ratio=1.0))
        assert 'LTC' in d.warning_message

    def test_fallback_method_warning(self):
        d = build_chatter_disclaimer(**_disclaimer_kwargs(prediction_method='fallback'))
        assert '兜底' in d.warning_message

    def test_to_dict(self):
        d = build_chatter_disclaimer(**_disclaimer_kwargs())
        data = d.to_dict()
        assert data['material_id'] == 'ti_tc4'
        assert data['requires_cam_validation'] is True
        assert data['warning_message']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
