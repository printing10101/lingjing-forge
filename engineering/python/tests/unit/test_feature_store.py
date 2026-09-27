"""feature_extraction 覆盖率补强测试（feature_store / precision_disclaimer）。

覆盖：
- FeatureStore：任务 CRUD、JSON 持久化 roundtrip、过期清理、单例
- precision_disclaimer：PrecisionDisclaimer 数据类
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from app.feature_extraction.feature_store import (
    ExtractedFeature,
    FeatureExtractionTask,
    FeatureExtractionTaskStatus,
    FeatureReviewStatus,
    FeatureStore,
    FeatureType,
    get_feature_store,
)
from app.feature_extraction.precision_disclaimer import (
    FeatureDisclaimer,
    build_feature_disclaimer,
)

pytestmark = pytest.mark.unit


def _make_task(
    task_id: str = "t-1",
    status: str = FeatureExtractionTaskStatus.PENDING.value,
) -> FeatureExtractionTask:
    now = time.time()
    return FeatureExtractionTask(
        task_id=task_id,
        created_at=now,
        updated_at=now,
        status=status,
        input_mesh_path=f"meshes/{task_id}.obj",
        features=[],
    )


def _make_feature() -> ExtractedFeature:
    return ExtractedFeature(
        feature_id="f1",
        feature_type=FeatureType.HOLE.value,
        params={"radius_mm": 5.0, "depth_mm": 10.0},
        confidence=0.95,
        sample_vertex_indices=[1, 2, 3],
        review_status=FeatureReviewStatus.CONFIRMED.value,
    )


class TestFeatureStore:
    def test_create_and_get(self, tmp_path):
        store = FeatureStore(Path(tmp_path))
        store.create(_make_task())
        task = store.get("t-1")
        assert task is not None
        assert task.task_id == "t-1"

    def test_get_missing_returns_none(self, tmp_path):
        store = FeatureStore(Path(tmp_path))
        assert store.get("nope") is None

    def test_list_all_sorted_newest_first(self, tmp_path):
        store = FeatureStore(Path(tmp_path))
        t1 = _make_task("t-old")
        t1.created_at = time.time() - 100
        t2 = _make_task("t-new")
        t2.created_at = time.time()
        store.create(t1)
        store.create(t2)
        tasks = store.list_all()
        assert [t.task_id for t in tasks] == ["t-new", "t-old"]

    def test_update_fields(self, tmp_path):
        store = FeatureStore(Path(tmp_path))
        store.create(_make_task())
        updated = store.update("t-1", status=FeatureExtractionTaskStatus.SUCCEEDED.value)
        assert updated is not None
        assert updated.status == FeatureExtractionTaskStatus.SUCCEEDED.value
        assert store.get("t-1").status == FeatureExtractionTaskStatus.SUCCEEDED.value

    def test_update_features_from_dicts(self, tmp_path):
        store = FeatureStore(Path(tmp_path))
        store.create(_make_task())
        updated = store.update("t-1", features=[_make_feature().to_dict()])
        assert updated is not None
        assert len(updated.features) == 1
        assert isinstance(updated.features[0], ExtractedFeature)

    def test_update_features_ignores_unknown_objects(self, tmp_path):
        store = FeatureStore(Path(tmp_path))
        store.create(_make_task())
        updated = store.update("t-1", features=["not-a-feature"])
        assert updated is not None
        assert updated.features == []

    def test_update_missing_returns_none(self, tmp_path):
        store = FeatureStore(Path(tmp_path))
        assert store.update("nope", status="x") is None

    def test_delete(self, tmp_path):
        store = FeatureStore(Path(tmp_path))
        store.create(_make_task())
        assert store.delete("t-1") is True
        assert store.get("t-1") is None
        assert store.delete("t-1") is False  # 已删除再删返回 False

    def test_persist_roundtrip(self, tmp_path):
        store = FeatureStore(Path(tmp_path))
        task = _make_task()
        task.features = [_make_feature()]
        store.create(task)
        # 新实例从磁盘加载
        store2 = FeatureStore(Path(tmp_path))
        loaded = store2.get("t-1")
        assert loaded is not None
        assert len(loaded.features) == 1
        assert loaded.features[0].feature_id == "f1"
        assert loaded.features[0].params == {"radius_mm": 5.0, "depth_mm": 10.0}

    def test_cleanup_expired(self, tmp_path):
        store = FeatureStore(Path(tmp_path))
        old = _make_task("t-old", FeatureExtractionTaskStatus.SUCCEEDED.value)
        old.updated_at = time.time() - 7200  # 2 小时前
        fresh = _make_task("t-fresh", FeatureExtractionTaskStatus.SUCCEEDED.value)
        fresh.updated_at = time.time()
        running = _make_task("t-run", FeatureExtractionTaskStatus.RUNNING.value)
        running.updated_at = time.time() - 7200
        store.create(old)
        store.create(fresh)
        store.create(running)
        cleaned = store.cleanup_expired(retention_hours=1)
        assert cleaned == 1  # 只有 old 被清（终态 + 超时）
        assert store.get("t-fresh") is not None
        assert store.get("t-run") is not None  # 运行中不清

    def test_cleanup_expired_zero_retention(self, tmp_path):
        store = FeatureStore(Path(tmp_path))
        assert store.cleanup_expired(0) == 0

    def test_corrupt_json_skipped(self, tmp_path):
        (tmp_path / "bad.json").write_text("{corrupt", encoding="utf-8")
        store = FeatureStore(Path(tmp_path))  # 不应抛异常
        assert store.list_all() == []

    def test_effective_params_edited(self):
        f = ExtractedFeature(
            feature_id="f1",
            feature_type=FeatureType.HOLE.value,
            params={"radius_mm": 5.0},
            confidence=0.9,
            review_status=FeatureReviewStatus.EDITED.value,
            edited_params={"radius_mm": 6.0},
        )
        assert f.effective_params() == {"radius_mm": 6.0}

    def test_effective_params_original(self):
        f = ExtractedFeature(
            feature_id="f1",
            feature_type=FeatureType.HOLE.value,
            params={"radius_mm": 5.0},
            confidence=0.9,
            review_status=FeatureReviewStatus.CONFIRMED.value,
        )
        assert f.effective_params() == {"radius_mm": 5.0}


class TestGetFeatureStore:
    def test_singleton(self):
        a = get_feature_store()
        b = get_feature_store()
        assert a is b
        assert isinstance(a, FeatureStore)


class TestPrecisionDisclaimer:
    def test_default_construction(self):
        d = FeatureDisclaimer(
            mesh_calibrated=False,
            mesh_source="external_upload",
            extraction_method="RANSAC",
            expected_confidence_range="0.60-0.95",
            requires_engineer_review=True,
            requires_cam_validation=True,
            industrial_hard_gates=[],
            warning_message="",
        )
        assert d is not None
        assert d.requires_engineer_review is True
        assert d.requires_cam_validation is True

    def test_to_dict_roundtrip(self):
        d = FeatureDisclaimer(
            mesh_calibrated=True,
            mesh_source="task-abc",
            extraction_method="RANSAC",
            expected_confidence_range="0.60-0.95",
            requires_engineer_review=True,
            requires_cam_validation=True,
            industrial_hard_gates=["gate1"],
            warning_message="warn",
        )
        dd = d.to_dict()
        restored = FeatureDisclaimer(**dd)
        assert restored.mesh_calibrated == d.mesh_calibrated
        assert restored.mesh_source == d.mesh_source
        assert restored.industrial_hard_gates == ["gate1"]

    def test_build_feature_disclaimer(self):
        from app.config import FeatureExtractionConfig

        cfg = FeatureExtractionConfig()
        d = build_feature_disclaimer(cfg, mesh_calibrated=True, mesh_source="task-abc")
        assert isinstance(d, FeatureDisclaimer)
        assert d.mesh_calibrated is True
        assert "标定" in d.warning_message
        assert len(d.industrial_hard_gates) >= 8  # 工业硬门槛完整

    def test_build_feature_disclaimer_uncalibrated(self):
        from app.config import FeatureExtractionConfig

        cfg = FeatureExtractionConfig()
        d = build_feature_disclaimer(cfg, mesh_calibrated=False)
        assert d.mesh_calibrated is False
        assert "未做尺度归一化" in d.warning_message
