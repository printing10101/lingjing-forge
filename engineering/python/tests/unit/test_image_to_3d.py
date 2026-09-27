"""image_to_3d 纯逻辑单元测试（task_store / precision_disclaimer）。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.image_to_3d.task_store import (
    ReconstructionTask,
    ReconstructionTaskStatus,
    TaskStore,
)
from app.image_to_3d.precision_disclaimer import build_precision_disclaimer

pytestmark = pytest.mark.unit


def _task(task_id='r1', status=ReconstructionTaskStatus.PENDING.value, **kw) -> ReconstructionTask:
    return ReconstructionTask(
        task_id=task_id,
        created_at=kw.get('created_at', 1000.0),
        updated_at=kw.get('updated_at', 1000.0),
        status=status,
        precision_tier=kw.get('precision_tier', 'standard'),
        photo_count=kw.get('photo_count', 50),
        workspace_dir=kw.get('workspace_dir', '/tmp/ws'),
    )


def _cfg(tier='standard', **kw) -> MagicMock:
    cfg = MagicMock()
    cfg.precision_tier = tier
    cfg.precision_specs = {
        'expected_accuracy_mm': kw.get('expected_accuracy_mm', '0.1-1'),
        'suitable_for': kw.get('suitable_for', ['可视化', '粗测']),
        'not_suitable_for': kw.get('not_suitable_for', ['精密配合']),
    }
    return cfg


class TestTaskStatus:
    def test_values(self):
        assert ReconstructionTaskStatus.COLMAP_DONE.value == 'colmap_done'
        assert ReconstructionTaskStatus.SUCCEEDED.value == 'succeeded'


class TestReconstructionTask:
    def test_to_dict(self):
        t = _task()
        d = t.to_dict()
        assert d['task_id'] == 'r1'
        assert d['photo_count'] == 50
        assert d['precision_tier'] == 'standard'
        assert d['calibrated'] is False


class TestTaskStore:
    def test_create_and_get(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        store.create(_task('r1'))
        assert store.get('r1').task_id == 'r1'
        assert (tmp_path / 'r1.json').exists()

    def test_get_missing(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        assert store.get('nope') is None

    def test_update(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        store.create(_task('r1'))
        updated = store.update('r1', status=ReconstructionTaskStatus.SUCCEEDED.value)
        assert updated.status == 'succeeded'
        assert store.get('r1').status == 'succeeded'

    def test_list_all(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        store.create(_task('r1'))
        store.create(_task('r2'))
        assert len(store.list_all()) == 2
        assert len(store.list_all(limit=1)) == 1

    def test_delete(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        store.create(_task('r1'))
        assert store.delete('r1') is True
        assert store.get('r1') is None
        assert store.delete('r1') is False

    def test_load_all_on_init(self, tmp_path):
        store = TaskStore(persist_dir=tmp_path)
        store.create(_task('r1'))
        store2 = TaskStore(persist_dir=tmp_path)
        assert store2.get('r1') is not None

    def test_cleanup_expired(self, tmp_path):
        import time
        store = TaskStore(persist_dir=tmp_path)
        old = _task('r-old', status=ReconstructionTaskStatus.SUCCEEDED.value, updated_at=time.time() - 99999)
        fresh = _task('r-fresh', status=ReconstructionTaskStatus.SUCCEEDED.value, updated_at=time.time())
        pending = _task('r-pending', status=ReconstructionTaskStatus.PENDING.value, updated_at=time.time() - 99999)
        store.create(old)
        store.create(fresh)
        store.create(pending)
        cleaned = store.cleanup_expired(retention_hours=1)
        assert cleaned == 1
        assert store.get('r-old') is None
        assert store.get('r-fresh') is not None
        assert store.get('r-pending') is not None


class TestBuildPrecisionDisclaimer:
    def test_uncalibrated_warning(self):
        d = build_precision_disclaimer(_cfg(), calibrated=False)
        assert '无量纲' in d.warning_message or '未做尺度归一化' in d.warning_message
        assert d.calibrated is False
        assert d.requires_cam_validation is True

    def test_calibrated_warning(self):
        d = build_precision_disclaimer(_cfg(), calibrated=True, scale_factor=2.5)
        assert '缩放因子' in d.warning_message
        assert d.calibrated is True
        assert d.scale_factor == 2.5

    def test_part_prior_notice(self):
        d = build_precision_disclaimer(_cfg(tier='part_prior'), calibrated=False)
        assert 'VAE' in d.warning_message

    def test_to_dict(self):
        d = build_precision_disclaimer(_cfg(), calibrated=False)
        data = d.to_dict()
        assert data['precision_tier'] == 'standard'
        assert data['requires_cam_validation'] is True
        assert len(data['industrial_hard_gates']) > 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
