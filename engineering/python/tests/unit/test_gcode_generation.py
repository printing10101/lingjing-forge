"""gcode_generation 模块单元测试。

覆盖阶段 6 三个纯逻辑子模块：
- gcode_store：任务状态机/审核枚举/FeatureGCodeResult/GCodeGenerationTask/TaskStore/文件扩展名
- gcode_disclaimer：精度告知 + 工业硬门槛 + 警告合成
- chatter_report_loader：阶段 5 ChatterReport JSON 加载与字段校验
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.gcode_generation.chatter_report_loader import (
    ChatterReportLoadError,
    ChatterReportLoader,
    LoadedChatterReport,
)
from app.gcode_generation.gcode_disclaimer import (
    INDUSTRIAL_HARD_GATES,
    GCodeDisclaimer,
    build_gcode_disclaimer,
)
from app.gcode_generation.gcode_store import (
    PENDING_CALIBRATION_MATERIALS,
    SAFETY_MARGIN_RATIO,
    FeatureGCodeResult,
    GCodeGenerationError,
    GCodeGenerationTask,
    GCodeGenerationTaskStatus,
    GCodeReviewStatus,
    ReviewError,
    TaskStore,
    generate_task_id,
    get_file_extension,
    get_task_store,
)

pytestmark = pytest.mark.unit


# gcode_store


@pytest.fixture(autouse=True)
def _clean_task_store():
    TaskStore().clear()
    yield
    TaskStore().clear()


def _make_feature_result(feature_id="f1", **kw) -> FeatureGCodeResult:
    return FeatureGCodeResult(
        feature_id=feature_id,
        feature_type=kw.get("feature_type", "plane"),
        material_id=kw.get("material_id", "steel"),
        spindle_rpm=kw.get("spindle_rpm", 2000.0),
        axial_depth_mm=kw.get("axial_depth_mm", 1.0),
        limit_depth_mm=kw.get("limit_depth_mm", 2.0),
        stable=kw.get("stable", True),
        safety_margin_ratio=kw.get("safety_margin_ratio", 0.5),
    )


def _make_task(task_id="gc-1", status=GCodeGenerationTaskStatus.PENDING.value, **kw) -> GCodeGenerationTask:
    return GCodeGenerationTask(
        task_id=task_id,
        source_chatter_report_path=kw.get("chatter", "/tmp/ch.json"),
        source_operation_plan_path=kw.get("plan", "/tmp/plan.json"),
        status=status,
        started_at=kw.get("started_at", 1000.0),
    )


class TestEnumsAndConstants:
    def test_task_status_values(self):
        assert GCodeGenerationTaskStatus.PENDING.value == "pending"
        assert GCodeGenerationTaskStatus.SUCCEEDED.value == "succeeded"
        assert GCodeGenerationTaskStatus.FAILED.value == "failed"

    def test_review_status_values(self):
        assert GCodeReviewStatus.CONFIRMED.value == "confirmed"
        assert GCodeReviewStatus.REJECTED.value == "rejected"
        assert GCodeReviewStatus.EDITED.value == "edited"

    def test_constants(self):
        assert SAFETY_MARGIN_RATIO == 0.8
        assert "hrc52" in PENDING_CALIBRATION_MATERIALS


class TestFeatureGCodeResult:
    def test_to_dict_rounds_and_converts(self):
        r = _make_feature_result(axial_depth_mm=1.234567, limit_depth_mm=2.345678, safety_margin_ratio=0.5263)
        r.line_range = (10, 20)
        d = r.to_dict()
        assert d["axial_depth_mm"] == 1.2346
        assert d["limit_depth_mm"] == 2.3457
        assert d["safety_margin_ratio"] == 0.5263
        assert d["line_range"] == [10, 20]
        assert d["review_status"] == "pending"

    def test_effective_result_not_edited(self):
        r = _make_feature_result(stable=True)
        eff = r.effective_result
        assert eff["stable"] == 1.0
        assert eff["axial_depth_mm"] == 1.0
        assert eff["limit_depth_mm"] == 2.0

    def test_effective_result_edited(self):
        r = _make_feature_result(stable=False)
        r.review_status = GCodeReviewStatus.EDITED.value
        r.edited_params = {"axial_depth_mm": 0.5, "stable": True}
        eff = r.effective_result
        assert eff["axial_depth_mm"] == 0.5
        assert eff["stable"] == 1.0
        assert eff["limit_depth_mm"] == 2.0

    def test_effective_result_edited_empty_params_falls_back(self):
        r = _make_feature_result(stable=True)
        r.review_status = GCodeReviewStatus.EDITED.value
        r.edited_params = {}
        eff = r.effective_result
        assert eff["stable"] == 1.0
        assert eff["axial_depth_mm"] == 1.0


class TestGCodeGenerationTask:
    def test_to_dict_includes_nested(self):
        t = _make_task()
        t.feature_gcode_results = [_make_feature_result()]
        t.warnings = ["w1"]
        d = t.to_dict()
        assert d["task_id"] == "gc-1"
        assert d["cam_validation_required"] is True
        assert len(d["feature_gcode_results"]) == 1
        assert d["feature_gcode_results"][0]["feature_id"] == "f1"
        assert d["warnings"] == ["w1"]


class TestGenerateTaskId:
    def test_prefix_and_format(self):
        tid = generate_task_id()
        assert tid.startswith("gc_")
        uuid.UUID(tid[3:])  # 不应抛错

    def test_unique(self):
        ids = {generate_task_id() for _ in range(50)}
        assert len(ids) == 50


class TestTaskStore:
    def test_singleton(self):
        assert get_task_store() is TaskStore()

    def test_add_and_get(self):
        store = TaskStore()
        store.add_task(_make_task("gc-1"))
        assert store.get_task("gc-1").task_id == "gc-1"

    def test_add_duplicate_raises(self):
        store = TaskStore()
        store.add_task(_make_task("gc-1"))
        with pytest.raises(GCodeGenerationError, match="已存在"):
            store.add_task(_make_task("gc-1"))

    def test_get_missing_raises(self):
        store = TaskStore()
        with pytest.raises(GCodeGenerationError, match="不存在"):
            store.get_task("nope")

    def test_list_tasks_sorted_and_filtered(self):
        store = TaskStore()
        store.add_task(_make_task("gc-1", started_at=1000.0))
        store.add_task(_make_task("gc-2", started_at=2000.0, status=GCodeGenerationTaskStatus.SUCCEEDED.value))
        all_tasks = store.list_tasks()
        assert [t.task_id for t in all_tasks] == ["gc-2", "gc-1"]
        succeeded = store.list_tasks(status_filter=GCodeGenerationTaskStatus.SUCCEEDED.value)
        assert [t.task_id for t in succeeded] == ["gc-2"]

    def test_update_task(self):
        store = TaskStore()
        t = _make_task("gc-1")
        store.add_task(t)
        t.status = GCodeGenerationTaskStatus.GENERATED.value
        store.update_task(t)
        assert store.get_task("gc-1").status == "generated"

    def test_update_missing_raises(self):
        store = TaskStore()
        with pytest.raises(GCodeGenerationError, match="不存在"):
            store.update_task(_make_task("nope"))

    def test_delete_succeeded_forbidden(self):
        store = TaskStore()
        store.add_task(_make_task("gc-1", status=GCodeGenerationTaskStatus.SUCCEEDED.value))
        with pytest.raises(ReviewError, match="禁止删除"):
            store.delete_task("gc-1")

    def test_delete_succeeded_with_override(self):
        store = TaskStore()
        store.add_task(_make_task("gc-1", status=GCodeGenerationTaskStatus.SUCCEEDED.value))
        store.delete_task("gc-1", allow_delete_succeeded=True)
        with pytest.raises(GCodeGenerationError):
            store.get_task("gc-1")

    def test_delete_normal(self):
        store = TaskStore()
        store.add_task(_make_task("gc-1"))
        store.delete_task("gc-1")
        assert store.list_tasks() == []

    def test_delete_missing_raises(self):
        store = TaskStore()
        with pytest.raises(GCodeGenerationError, match="不存在"):
            store.delete_task("nope")


class TestGetFileExtension:
    @pytest.mark.parametrize(
        "controller,ext",
        [
            ("fanuc_0i", ".nc"),
            ("siemens_840d", ".mpf"),
            ("heidenhain_tnc", ".h"),
            ("xmachine_xm100", ".nc"),
            ("unknown_ctrl", ".nc"),
        ],
    )
    def test_extension(self, controller, ext):
        assert get_file_extension(controller) == ext


# gcode_disclaimer


def _disclaimer_kwargs(**kw) -> dict:
    return {
        "precision_tier": kw.get("precision_tier", "standard"),
        "controller_type": kw.get("controller_type", "fanuc_0i"),
        "material_name": kw.get("material_name", "45#钢"),
        "material_calibration_status": kw.get("material_calibration_status", "calibrated"),
        "chatter_report_source": kw.get("chatter_report_source", "/tmp/ch.json"),
        "operation_plan_source": kw.get("operation_plan_source", "/tmp/plan.json"),
        "prediction_method": kw.get("prediction_method", "analytical"),
        "total_features": kw.get("total_features", 2),
        "stable_features": kw.get("stable_features", 1),
        "unstable_features": kw.get("unstable_features", 1),
        "pending_calibration": kw.get("pending_calibration", False),
        "ltc_experiment_used": kw.get("ltc_experiment_used", False),
        "gcode_file_exported": kw.get("gcode_file_exported", False),
    }


class TestGCodeDisclaimer:
    def test_hard_constraints_always_true(self):
        d = build_gcode_disclaimer(**_disclaimer_kwargs())
        assert d.requires_cam_validation is True
        assert d.requires_engineer_review is True
        # 兜底 CAM 校验警告永远存在 warning_message 永远非空
        assert "CAM" in d.warning_message

    def test_pending_calibration_warning(self):
        d = build_gcode_disclaimer(**_disclaimer_kwargs(pending_calibration=True))
        assert "HRC52" in d.warning_message

    def test_unstable_features_warning(self):
        d = build_gcode_disclaimer(**_disclaimer_kwargs(unstable_features=3))
        assert "3 个不稳定特征" in d.warning_message

    def test_ltc_warning(self):
        d = build_gcode_disclaimer(**_disclaimer_kwargs(ltc_experiment_used=True))
        assert "LTC" in d.warning_message

    def test_to_dict(self):
        d = build_gcode_disclaimer(**_disclaimer_kwargs())
        data = d.to_dict()
        assert data["controller_type"] == "fanuc_0i"
        assert data["requires_cam_validation"] is True
        assert len(data["industrial_hard_gates"]) == len(INDUSTRIAL_HARD_GATES)
        assert data["warning_message"]

    def test_gcode_file_exported_flag(self):
        d = build_gcode_disclaimer(**_disclaimer_kwargs(gcode_file_exported=True))
        assert d.gcode_file_exported is True
        assert d.to_dict()["gcode_file_exported"] is True


# chatter_report_loader


def _feature_dict(feature_id="f1", **kw) -> dict:
    return {
        "feature_id": feature_id,
        "feature_type": kw.get("feature_type", "plane"),
        "material_id": kw.get("material_id", "steel"),
        "spindle_rpm": kw.get("spindle_rpm", 2000.0),
        "axial_depth_mm": kw.get("axial_depth_mm", 1.0),
        "limit_depth_mm": kw.get("limit_depth_mm", 2.0),
        "stable": kw.get("stable", True),
        "stability_margin": kw.get("stability_margin", 0.5),
        "method": kw.get("method", "analytical"),
        "ltc_active": kw.get("ltc_active", False),
        "confidence": kw.get("confidence", 0.8),
    }


def _report_dict(**kw) -> dict:
    return {
        "task_id": kw.get("task_id", "ch-1"),
        "task_status": kw.get("task_status", "succeeded"),
        "feature_results": kw.get("feature_results", [_feature_dict()]),
        "material_id": kw.get("material_id", "steel"),
        "prediction_method": kw.get("prediction_method", "analytical"),
    }


def _write_report(tmp_path, data) -> str:
    p = tmp_path / "chatter_report.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(p)


class TestChatterReportLoader:
    def test_load_success(self, tmp_path):
        path = _write_report(
            tmp_path,
            _report_dict(feature_results=[_feature_dict("f1", stable=True), _feature_dict("f2", stable=False)]),
        )
        report = ChatterReportLoader().load(path)
        assert isinstance(report, LoadedChatterReport)
        assert report.total_features == 2
        assert report.stable_features == 1
        assert report.unstable_features == 1
        assert report.task_status == "succeeded"
        assert report.pending_calibration is False

    def test_load_file_missing(self, tmp_path):
        with pytest.raises(ChatterReportLoadError, match="不存在"):
            ChatterReportLoader().load(str(tmp_path / "nope.json"))

    def test_load_invalid_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ChatterReportLoadError, match="格式错误"):
            ChatterReportLoader().load(str(p))

    def test_load_missing_report_field(self, tmp_path):
        data = _report_dict()
        del data["prediction_method"]
        path = _write_report(tmp_path, data)
        with pytest.raises(ChatterReportLoadError, match="缺少必填字段"):
            ChatterReportLoader().load(path)

    def test_load_not_succeeded(self, tmp_path):
        path = _write_report(tmp_path, _report_dict(task_status="failed"))
        with pytest.raises(ChatterReportLoadError, match="未审核通过"):
            ChatterReportLoader().load(path)

    def test_load_feature_results_not_list(self, tmp_path):
        path = _write_report(tmp_path, _report_dict(feature_results="oops"))
        with pytest.raises(ChatterReportLoadError, match="必须是列表"):
            ChatterReportLoader().load(path)

    def test_load_feature_missing_field(self, tmp_path):
        feat = _feature_dict()
        del feat["stable"]
        path = _write_report(tmp_path, _report_dict(feature_results=[feat]))
        with pytest.raises(ChatterReportLoadError, match="缺少必填字段"):
            ChatterReportLoader().load(path)

    def test_load_feature_null_field(self, tmp_path):
        feat = _feature_dict()
        feat["stable"] = None
        path = _write_report(tmp_path, _report_dict(feature_results=[feat]))
        with pytest.raises(ChatterReportLoadError, match="值为 null"):
            ChatterReportLoader().load(path)

    def test_detect_pending_calibration_via_material_id(self, tmp_path):
        path = _write_report(
            tmp_path, _report_dict(material_id="hrc52", feature_results=[_feature_dict("f1", material_id="steel")])
        )
        report = ChatterReportLoader().load(path)
        assert report.pending_calibration is True

    def test_detect_pending_calibration_via_feature(self, tmp_path):
        path = _write_report(tmp_path, _report_dict(feature_results=[_feature_dict("f1", material_id="steel_hrc52")]))
        report = ChatterReportLoader().load(path)
        assert report.pending_calibration is True

    def test_loaded_report_to_dict(self, tmp_path):
        path = _write_report(tmp_path, _report_dict())
        report = ChatterReportLoader().load(path)
        d = report.to_dict()
        assert d["feature_count"] == 1
        assert d["material_id"] == "steel"
        assert d["prediction_method"] == "analytical"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
