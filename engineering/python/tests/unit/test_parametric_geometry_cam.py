"""parametric_geometry / cam_validation 覆盖率补强测试。

覆盖：
- app/parametric_geometry/step_store.py：TaskStore 单例 CRUD + JSON 持久化
- app/cam_validation/cam_disclaimer.py：CamDisclaimer 构建与序列化
"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from app.parametric_geometry.step_store import (
    ParametricGeometryTask,
    ParametricGeometryTaskStatus,
    ReviewedFeatureRef,
    StepReviewStatus,
    TaskStore,
    generate_task_id,
)
from app.cam_validation.cam_disclaimer import (
    CamDisclaimer,
    build_cam_disclaimer,
)

pytestmark = pytest.mark.unit


# ReviewedFeatureRef / ParametricGeometryTask


class TestReviewedFeatureRef:
    def test_effective_params_uses_source(self):
        ref = ReviewedFeatureRef(
            feature_id="f1",
            feature_type="hole",
            source_params={"diameter": 10.0},
        )
        assert ref.effective_params() == {"diameter": 10.0}

    def test_effective_params_merges_edited(self):
        ref = ReviewedFeatureRef(
            feature_id="f1",
            feature_type="hole",
            source_params={"diameter": 10.0, "depth": 5.0},
            review_status=StepReviewStatus.EDITED.value,
            edited_params={"diameter": 12.0},
        )
        merged = ref.effective_params()
        assert merged["diameter"] == 12.0  # edited 优先
        assert merged["depth"] == 5.0  # source 保留


class TestParametricGeometryTask:
    def test_to_dict(self):
        task = ParametricGeometryTask(
            task_id="pg-001",
            source_feature_extraction_task_id="fe-001",
            input_features_path="/tmp/confirmed_features.json",
            input_features=[
                ReviewedFeatureRef(feature_id="f1", feature_type="hole", source_params={}),
            ],
            status=ParametricGeometryTaskStatus.STEP_GENERATED.value,
            step_output_path="/tmp/out.step",
        )
        d = task.to_dict()
        assert d["task_id"] == "pg-001"
        assert d["status"] == "step_generated"
        assert d["feature_count"] == 1
        assert d["cam_validation_required"] is True

    def test_defaults(self):
        task = ParametricGeometryTask(
            task_id="t",
            source_feature_extraction_task_id="s",
            input_features_path="p",
        )
        assert task.status == ParametricGeometryTaskStatus.PENDING.value
        assert task.precision_tier == "standard"
        assert task.cam_validation_required is True


# TaskStore CRUD


class TestTaskStore:
    def _make_task(self, task_id: str = "pg-1") -> ParametricGeometryTask:
        return ParametricGeometryTask(
            task_id=task_id,
            source_feature_extraction_task_id="fe-1",
            input_features_path="/tmp/f.json",
        )

    def test_create_and_get(self, tmp_path):
        store = TaskStore(persist_path=tmp_path / "store.json")
        task = self._make_task()
        store.create(task)
        got = store.get("pg-1")
        assert got is not None
        assert got.task_id == "pg-1"

    def test_get_missing_returns_none(self, tmp_path):
        store = TaskStore(persist_path=tmp_path / "store.json")
        assert store.get("nope") is None

    def test_update_fields(self, tmp_path):
        store = TaskStore(persist_path=tmp_path / "store.json")
        store.create(self._make_task())
        updated = store.update("pg-1", status="failed", error_message="测试失败")
        assert updated is not None
        assert updated.status == "failed"
        assert updated.error_message == "测试失败"

    def test_update_missing_returns_none(self, tmp_path):
        store = TaskStore(persist_path=tmp_path / "store.json")
        assert store.update("missing", status="x") is None

    def test_delete(self, tmp_path):
        store = TaskStore(persist_path=tmp_path / "store.json")
        store.create(self._make_task())
        assert store.delete("pg-1") is True
        assert store.get("pg-1") is None
        assert store.delete("pg-1") is False  # 二次删除返回 False

    def test_list_tasks_sorted_desc(self, tmp_path):
        store = TaskStore(persist_path=tmp_path / "store.json")
        # 显式时间戳：避免同微秒内创建的两个任务 created_at 相等，
        # 导致稳定排序保留插入序（tasks[0] 变成 pg-1）使测试 flaky。
        t1 = self._make_task("pg-1")
        t1.created_at = 1000.0
        t2 = self._make_task("pg-2")
        t2.created_at = 2000.0
        store.create(t1)
        store.create(t2)
        tasks = store.list_tasks()
        assert len(tasks) == 2
        # 按 created_at 倒序（后创建的在前）
        assert tasks[0].task_id == "pg-2"
        assert tasks[1].task_id == "pg-1"

    def test_list_tasks_limit(self, tmp_path):
        store = TaskStore(persist_path=tmp_path / "store.json")
        for i in range(5):
            store.create(self._make_task(f"pg-{i}"))
        assert len(store.list_tasks(limit=2)) == 2

    def test_persist_roundtrip(self, tmp_path):
        path = tmp_path / "store.json"
        store1 = TaskStore(persist_path=path)
        store1.create(self._make_task("pg-1"))
        store1.create(self._make_task("pg-2"))
        # 新建 store 从磁盘加载
        store2 = TaskStore(persist_path=path)
        assert store2.get("pg-1") is not None
        assert store2.get("pg-2") is not None

    def test_singleton_get_instance(self, tmp_path):
        TaskStore.reset_instance()
        s1 = TaskStore.get_instance()
        s2 = TaskStore.get_instance()
        assert s1 is s2
        TaskStore.reset_instance()

    def test_generate_task_id(self):
        tid = generate_task_id()
        assert isinstance(tid, str)
        assert len(tid) > 0


# CamDisclaimer


class TestCamDisclaimer:
    def _build(self, **kwargs: Any) -> CamDisclaimer:
        params: dict[str, Any] = dict(
            precision_tier="standard",
            controller_type="fanuc_0i",
            material_name="6061-T6",
            material_calibration_status="calibrated",
            gcode_report_source="/tmp/report.json",
            gcode_file_source="/tmp/out.nc",
            prediction_method="mixed",
            total_features=5,
            passed_features=4,
            failed_features=1,
            pending_calibration=False,
            ltc_experiment_used=False,
            cam_backend_used="internal_only",
            cam_backend_fallback_reason="",
        )
        params.update(kwargs)
        return build_cam_disclaimer(**params)

    def test_build_basic(self):
        d = self._build()
        assert d.precision_tier == "standard"
        assert d.total_features == 5
        assert d.passed_features == 4
        assert d.failed_features == 1

    def test_hard_gates_always_true(self):
        d = self._build()
        assert d.requires_cam_validation is True
        assert d.requires_engineer_review is True
        assert len(d.industrial_hard_gates) >= 10

    def test_to_dict(self):
        d = self._build()
        out = d.to_dict()
        assert out["controller_type"] == "fanuc_0i"
        assert out["requires_cam_validation"] is True
        assert out["cam_backend_used"] == "internal_only"
        assert len(out["industrial_hard_gates"]) >= 10

    def test_pending_calibration_flag(self):
        d = self._build(pending_calibration=True, material_calibration_status="pending_calibration")
        assert d.pending_calibration is True
        assert d.material_calibration_status == "pending_calibration"

    def test_cam_report_exported(self):
        d = self._build(cam_report_exported=True)
        assert d.cam_report_exported is True

    def test_warning_message_non_empty_when_failed(self):
        # failed_features > 0 时 warning_message 动态拼接（非空）
        d = self._build()
        assert "未通过" in d.warning_message
