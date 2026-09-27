"""cutting_parameters 模块单元测试（cutting_store / cutting_disclaimer / material_resolver）。"""

from __future__ import annotations

import json

import pytest

from app.cutting_parameters.cutting_disclaimer import build_cutting_disclaimer
from app.cutting_parameters.cutting_store import (
    CuttingParametersTask,
    CuttingParametersTaskStatus,
    CuttingReviewStatus,
    OperationType,
    RecommendedCuttingParams,
    ReviewError,
    TaskStore,
    generate_task_id,
    get_task_store,
)
from app.cutting_parameters.material_resolver import (
    MaterialParams,
    MaterialResolver,
    MaterialResolverError,
    get_material_resolver,
    reset_material_resolver,
)

pytestmark = pytest.mark.unit


def _params(feature_id='f1', **kw) -> RecommendedCuttingParams:
    return RecommendedCuttingParams(
        feature_id=feature_id,
        feature_type=kw.get('feature_type', 'plane'),
        operation=kw.get('operation', 'roughing'),
        spindle_speed_rpm=kw.get('spindle_speed_rpm', 3000.0),
        feed_rate_mm_per_min=kw.get('feed_rate_mm_per_min', 500.0),
        feed_per_tooth_mm=kw.get('feed_per_tooth_mm', 0.1),
        cutting_speed_m_per_min=kw.get('cutting_speed_m_per_min', 120.0),
        axial_depth_mm=kw.get('axial_depth_mm', 2.0),
        radial_depth_mm=kw.get('radial_depth_mm', 5.0),
    )


def _task(task_id='cp-1', status=CuttingParametersTaskStatus.PENDING.value, **kw) -> CuttingParametersTask:
    return CuttingParametersTask(
        task_id=task_id,
        created_at=kw.get('created_at', 1000.0),
        source_parametric_geometry_task_id=kw.get('src_geo', 'pg-1'),
        step_file_path=kw.get('step', '/tmp/x.step'),
        input_features_path=kw.get('features', '/tmp/f.json'),
        material_id=kw.get('material_id', 'steel'),
        precision_tier=kw.get('precision_tier', 'standard'),
        mesh_calibrated=kw.get('mesh_calibrated', True),
        status=status,
    )


def _material_dict(**kw) -> dict:
    return {
        'id': kw.get('id', 'ti_tc4'),
        'name': kw.get('name', 'TC4钛合金'),
        'category': kw.get('category', 'titanium'),
        'hardness_hb': kw.get('hardness_hb', 330.0),
        'tensile_strength_mpa': kw.get('tensile_strength_mpa', 900.0),
        'thermal_conductivity': kw.get('thermal_conductivity', 7.0),
        'density_gcm3': kw.get('density_gcm3', 4.4),
        'specific_cutting_force': kw.get('specific_cutting_force', 1500.0),
        'cutting_speed_range': kw.get('cutting_speed_range', {'roughing': [40, 80], 'finishing': [80, 120]}),
        'feed_range': kw.get('feed_range', {'roughing': [0.05, 0.15], 'finishing': [0.02, 0.08]}),
        'depth_of_cut_range': kw.get('depth_of_cut_range', {'roughing': [1, 4], 'finishing': [0.1, 0.5]}),
        'taylor_exponent_n': kw.get('taylor_exponent_n', 0.25),
        'taylor_constant_c': kw.get('taylor_constant_c', 200.0),
    }


class TestEnums:
    def test_task_status_values(self):
        assert CuttingParametersTaskStatus.PARAMS_RECOMMENDED.value == 'params_recommended'
        assert CuttingParametersTaskStatus.SUCCEEDED.value == 'succeeded'

    def test_operation_type_values(self):
        assert OperationType.ROUGHING.value == 'roughing'
        assert OperationType.FINISHING.value == 'finishing'


class TestRecommendedCuttingParams:
    def test_effective_params_not_edited(self):
        p = _params(axial_depth_mm=2.0)
        eff = p.effective_params()
        assert eff['axial_depth_mm'] == 2.0
        assert eff['spindle_speed_rpm'] == 3000.0

    def test_effective_params_edited(self):
        p = _params(axial_depth_mm=2.0)
        p.review_status = CuttingReviewStatus.EDITED.value
        p.edited_params = {'axial_depth_mm': 1.0, 'spindle_speed_rpm': 2500.0}
        eff = p.effective_params()
        assert eff['axial_depth_mm'] == 1.0
        assert eff['spindle_speed_rpm'] == 2500.0
        assert eff['feed_rate_mm_per_min'] == 500.0

    def test_effective_params_edited_empty_falls_back(self):
        p = _params(axial_depth_mm=2.0)
        p.review_status = CuttingReviewStatus.EDITED.value
        assert p.effective_params()['axial_depth_mm'] == 2.0

    def test_to_dict(self):
        p = _params()
        d = p.to_dict()
        assert d['feature_id'] == 'f1'
        assert d['spindle_speed_rpm'] == 3000.0
        assert d['review_status'] == 'pending'


class TestCuttingParametersTask:
    def test_to_dict_nested(self):
        t = _task()
        t.recommended_params = [_params()]
        d = t.to_dict()
        assert d['task_id'] == 'cp-1'
        assert d['cam_validation_required'] is True
        assert len(d['recommended_params']) == 1
        assert d['recommended_params'][0]['feature_id'] == 'f1'


class TestGenerateTaskId:
    def test_prefix_and_uniqueness(self):
        tid = generate_task_id()
        assert tid.startswith('cp_')
        ids = {generate_task_id() for _ in range(50)}
        assert len(ids) == 50


class TestTaskStore:
    def test_create_and_get(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        store.create_task(_task('cp-1'))
        assert store.get_task('cp-1').task_id == 'cp-1'
        assert (tmp_path / 'cp-1.json').exists()

    def test_get_missing_returns_none(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        assert store.get_task('nope') is None

    def test_update_and_persist(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        t = _task('cp-1')
        store.create_task(t)
        t.status = CuttingParametersTaskStatus.REVIEWED.value
        store.update_task(t)
        assert store.get_task('cp-1').status == 'reviewed'
        data = json.loads((tmp_path / 'cp-1.json').read_text(encoding='utf-8'))
        assert data['status'] == 'reviewed'

    def test_list_sorted_desc(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        store.create_task(_task('cp-1', created_at=1000.0))
        store.create_task(_task('cp-2', created_at=2000.0))
        assert [t.task_id for t in store.list_tasks()] == ['cp-2', 'cp-1']

    def test_list_limit(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        for i in range(5):
            store.create_task(_task(f'cp-{i}', created_at=float(i)))
        assert len(store.list_tasks(limit=2)) == 2

    def test_delete_normal(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        store.create_task(_task('cp-1'))
        assert store.delete_task('cp-1') is True
        assert store.get_task('cp-1') is None
        assert not (tmp_path / 'cp-1.json').exists()

    def test_delete_missing_returns_false(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        assert store.delete_task('nope') is False

    def test_delete_succeeded_forbidden(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        store.create_task(_task('cp-1', status=CuttingParametersTaskStatus.SUCCEEDED.value))
        with pytest.raises(ReviewError, match='禁止删除'):
            store.delete_task('cp-1')

    def test_singleton(self):
        assert get_task_store() is get_task_store()


def _disclaimer_kwargs(**kw) -> dict:
    return {
        'mesh_calibrated': kw.get('mesh_calibrated', True),
        'feature_source': kw.get('feature_source', '/tmp/f.json'),
        'step_source': kw.get('step_source', '/tmp/x.step'),
        'material_id': kw.get('material_id', 'ti_tc4'),
        'material_calibration_status': kw.get('material_calibration_status', 'calibrated'),
        'precision_tier': kw.get('precision_tier', 'standard'),
        'machine_type': kw.get('machine_type', 'vmc_850'),
        'tool_diameter_mm': kw.get('tool_diameter_mm', 10.0),
        'chatter_params_ready': kw.get('chatter_params_ready', False),
    }


class TestCuttingDisclaimer:
    def test_hard_constraints_always_true(self):
        d = build_cutting_disclaimer(**_disclaimer_kwargs())
        assert d.requires_cam_validation is True
        assert d.requires_engineer_review is True
        assert 'CAM' in d.warning_message

    def test_uncalibrated_mesh_warning(self):
        d = build_cutting_disclaimer(**_disclaimer_kwargs(mesh_calibrated=False))
        assert '未标定' in d.warning_message

    def test_pending_calibration_material_warning(self):
        d = build_cutting_disclaimer(**_disclaimer_kwargs(material_calibration_status='pending_calibration'))
        assert '待自采' in d.warning_message

    def test_chatter_params_ready_warning(self):
        d = build_cutting_disclaimer(**_disclaimer_kwargs(chatter_params_ready=True))
        assert '已输出' in d.warning_message

    def test_to_dict(self):
        d = build_cutting_disclaimer(**_disclaimer_kwargs())
        data = d.to_dict()
        assert data['material_id'] == 'ti_tc4'
        assert data['requires_cam_validation'] is True
        assert data['warning_message']


class TestMaterialParams:
    def test_to_dict(self):
        m = MaterialParams(
            id='ti_tc4', name='TC4', category='titanium', hardness_hb=330.0,
            tensile_strength_mpa=900.0, thermal_conductivity=7.0, density_gcm3=4.4,
            specific_cutting_force=1500.0,
            cutting_speed_range={'roughing': [40, 80]},
            feed_range={'roughing': [0.05, 0.15]},
            depth_of_cut_range={'roughing': [1, 4]},
            taylor_exponent_n=0.25, taylor_constant_c=200.0,
        )
        d = m.to_dict()
        assert d['id'] == 'ti_tc4'
        assert d['specific_cutting_force'] == 1500.0


class TestParseRange:
    def test_full_range(self):
        raw = {'roughing': [1, 2], 'finishing': [3, 4]}
        assert MaterialResolver._parse_range(raw) == {'roughing': [1.0, 2.0], 'finishing': [3.0, 4.0]}

    def test_missing_key_skipped(self):
        raw = {'roughing': [1, 2]}
        assert MaterialResolver._parse_range(raw) == {'roughing': [1.0, 2.0]}

    def test_invalid_values_zeroed(self):
        raw = {'roughing': 'oops'}
        assert MaterialResolver._parse_range(raw) == {'roughing': [0.0, 0.0]}


class TestMaterialResolver:
    def _write_materials(self, tmp_path, materials) -> str:
        p = tmp_path / 'materials.json'
        p.write_text(json.dumps(materials, ensure_ascii=False), encoding='utf-8')
        return str(p)

    def test_get_hrc52_supplement_without_file(self, tmp_path):
        resolver = MaterialResolver(materials_json_path=str(tmp_path / 'nope.json'))
        m = resolver.get_material('steel_hrc52')
        assert m.id == 'steel_hrc52'
        assert m.calibration_status == 'pending_calibration'
        assert m.hardness_hrc == 52.0

    def test_get_database_material(self, tmp_path):
        path = self._write_materials(tmp_path, [_material_dict()])
        resolver = MaterialResolver(materials_json_path=path)
        m = resolver.get_material('ti_tc4')
        assert m.id == 'ti_tc4'
        assert m.name == 'TC4钛合金'
        assert m.cutting_speed_range['roughing'] == [40.0, 80.0]
        assert m.calibration_status == 'calibrated'

    def test_get_missing_raises(self, tmp_path):
        path = self._write_materials(tmp_path, [_material_dict()])
        resolver = MaterialResolver(materials_json_path=path)
        with pytest.raises(MaterialResolverError, match='未找到'):
            resolver.get_material('unknown')

    def test_list_material_ids_includes_hrc52(self, tmp_path):
        path = self._write_materials(tmp_path, [_material_dict()])
        resolver = MaterialResolver(materials_json_path=path)
        ids = resolver.list_material_ids()
        assert 'ti_tc4' in ids
        assert 'steel_hrc52' in ids
        assert ids == sorted(ids)

    def test_list_materials(self, tmp_path):
        path = self._write_materials(tmp_path, [_material_dict()])
        resolver = MaterialResolver(materials_json_path=path)
        mats = resolver.list_materials()
        assert len(mats) == 2

    def test_has_material(self, tmp_path):
        path = self._write_materials(tmp_path, [_material_dict()])
        resolver = MaterialResolver(materials_json_path=path)
        assert resolver.has_material('ti_tc4') is True
        assert resolver.has_material('steel_hrc52') is True
        assert resolver.has_material('unknown') is False

    def test_invalid_entry_skipped(self, tmp_path):
        path = self._write_materials(tmp_path, [{'id': 'bad'}])
        resolver = MaterialResolver(materials_json_path=path)
        assert resolver.has_material('bad') is False
        assert resolver.has_material('steel_hrc52') is True

    def test_singleton_reset(self):
        reset_material_resolver()
        r1 = get_material_resolver()
        reset_material_resolver()
        r2 = get_material_resolver()
        assert r1 is not r2


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
