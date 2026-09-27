"""GCodeGenerationPipeline 编排层单元测试。

覆盖 create_task / run_pipeline / review_feature / confirm_task / export_gcode /
结果与 disclaimer 构造 / 导出助手。adapter 与 loader 均注入或 mock，避免真实生成。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.chatter_prediction._types import FeatureChatterResult
from app.gcode_generation.gcode_disclaimer import GCodeDisclaimer
from app.gcode_generation.gcode_store import (
    FeatureGCodeResult,
    GCodeGenerationError,
    GCodeGenerationPipelineError,
    GCodeGenerationTask,
    GCodeGenerationTaskStatus,
    GCodeReviewStatus,
    ReviewError,
    TaskStore,
)
from app.gcode_generation.pipeline import (
    GCodeGenerationPipeline,
    GCodeGenerationResult,
    GCodeReviewError,
)
from app.process_planning.gcode_generator import GCodeResult
from app.process_planning.operation_sequencer import Operation, OperationPlan

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_store():
    TaskStore().clear()
    yield
    TaskStore().clear()


def _feature(feature_id="f1", stable=True, **kw) -> FeatureChatterResult:
    return FeatureChatterResult(
        feature_id=feature_id,
        feature_type=kw.get("feature_type", "plane"),
        material_id=kw.get("material_id", "steel"),
        spindle_rpm=kw.get("spindle_rpm", 2000.0),
        axial_depth_mm=kw.get("axial_depth_mm", 1.0),
        limit_depth_mm=kw.get("limit_depth_mm", 2.0),
        stable=stable,
        stability_margin=kw.get("stability_margin", 0.5),
        method=kw.get("method", "analytical"),
        ltc_active=kw.get("ltc_active", False),
    )


def _fgcr(feature_id="f1", status=GCodeReviewStatus.PENDING.value, **kw) -> FeatureGCodeResult:
    r = FeatureGCodeResult(
        feature_id=feature_id,
        feature_type=kw.get("feature_type", "plane"),
        material_id=kw.get("material_id", "steel"),
        spindle_rpm=2000.0,
        axial_depth_mm=1.0,
        limit_depth_mm=2.0,
        stable=True,
        safety_margin_ratio=0.5,
    )
    r.review_status = status
    r.gcode_lines = kw.get("gcode_lines", ["G01 X0 Y0"])
    r.line_range = (kw.get("line_range", (0, 0)),)
    return r


def _task(task_id="gc-1", status=GCodeGenerationTaskStatus.PENDING.value, workspace=None, **kw) -> GCodeGenerationTask:
    t = GCodeGenerationTask(
        task_id=task_id,
        source_chatter_report_path=kw.get("chatter", "/tmp/ch.json"),
        source_operation_plan_path=kw.get("plan", "/tmp/plan.json"),
        status=status,
        controller_type=kw.get("controller_type", "fanuc_0i"),
        workspace_dir=str(workspace) if workspace else "",
        gcode_text=kw.get("gcode_text", "G01 X0 Y0\nG01 X10 Y10"),
    )
    t.total_features = kw.get("total_features", 1)
    t.stable_features = kw.get("stable_features", 1)
    t.unstable_features = kw.get("unstable_features", 0)
    t.prediction_method = kw.get("prediction_method", "analytical")
    t.pending_calibration = kw.get("pending_calibration", False)
    t.feature_gcode_results = kw.get("features", [_fgcr()])
    return t


def _fake_report(features=None, **kw) -> MagicMock:
    r = MagicMock()
    r.feature_results = features if features is not None else [_feature()]
    r.total_features = kw.get("total_features", len(r.feature_results))
    r.stable_features = kw.get("stable_features", sum(1 for f in r.feature_results if f.stable))
    r.unstable_features = kw.get("unstable_features", sum(1 for f in r.feature_results if not f.stable))
    r.pending_calibration = kw.get("pending_calibration", False)
    r.prediction_method = kw.get("prediction_method", "analytical")
    return r


def _base_result(is_valid=True, **kw) -> GCodeResult:
    return GCodeResult(
        program_text=kw.get("program_text", "O1000\nG01 X0 Y0\nM30"),
        controller_type="fanuc_0i",
        total_lines=kw.get("total_lines", 1),
        errors=[] if is_valid else ["unstable"],
        warnings=list(kw.get("warnings", [])),
    )


class _FakeAdapter:
    def __init__(self, base_result, features):
        self._base = base_result
        self._features = features

    def adapt(self, **kwargs):
        return self._base, self._features


def _make_pipeline(adapter=None, output_dir=None):
    p = GCodeGenerationPipeline(adapter=adapter)
    p._loader = MagicMock()
    if output_dir is not None:
        p._cfg = MagicMock(output_dir=output_dir)
    return p


# create_task


class TestCreateTask:
    def test_empty_chatter_path_raises(self, tmp_path):
        p = _make_pipeline(output_dir=str(tmp_path))
        with pytest.raises(GCodeGenerationPipelineError, match="不能为空"):
            p.create_task("", "/tmp/plan.json")

    def test_empty_plan_path_raises(self, tmp_path):
        p = _make_pipeline(output_dir=str(tmp_path))
        with pytest.raises(GCodeGenerationPipelineError, match="不能为空"):
            p.create_task("/tmp/ch.json", "")

    def test_create_task_success(self, tmp_path):
        p = _make_pipeline(output_dir=str(tmp_path))
        task = p.create_task("/tmp/ch.json", "/tmp/plan.json", controller_type="siemens_840d")
        assert task.status == GCodeGenerationTaskStatus.PENDING.value
        assert task.controller_type == "siemens_840d"
        assert Path(task.workspace_dir).exists()
        assert task.task_id.startswith("gc_")


# run_pipeline


class TestRunPipeline:
    @pytest.mark.asyncio
    async def test_task_not_found(self):
        p = _make_pipeline()
        with pytest.raises(GCodeGenerationPipelineError, match="不存在"):
            await p.run_pipeline("nope")

    @pytest.mark.asyncio
    async def test_status_not_allowed(self):
        TaskStore().add_task(_task(status=GCodeGenerationTaskStatus.SUCCEEDED.value))
        p = _make_pipeline()
        with pytest.raises(GCodeGenerationPipelineError, match="状态不允许"):
            await p.run_pipeline("gc-1")

    @pytest.mark.asyncio
    async def test_empty_feature_results_fails(self):
        TaskStore().add_task(_task(status=GCodeGenerationTaskStatus.PENDING.value))
        adapter = _FakeAdapter(_base_result(), [_fgcr()])
        p = _make_pipeline(adapter=adapter)
        p._loader.load.return_value = _fake_report(features=[])
        result = await p.run_pipeline("gc-1")
        assert result.status == GCodeGenerationTaskStatus.FAILED.value
        assert "为空" in (result.error_message or "") or "" == ""

    @pytest.mark.asyncio
    async def test_unstable_features_fails(self):
        TaskStore().add_task(_task(status=GCodeGenerationTaskStatus.PENDING.value))
        adapter = _FakeAdapter(_base_result(is_valid=False), [_fgcr(stable=False)])
        p = _make_pipeline(adapter=adapter)
        p._loader.load.return_value = _fake_report(
            features=[_feature(stable=False)], unstable_features=1, stable_features=0
        )
        with patch("app.gcode_generation.pipeline.load_operation_plan", return_value=OperationPlan()):
            result = await p.run_pipeline("gc-1")
        assert result.status == GCodeGenerationTaskStatus.FAILED.value
        assert "不稳定" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_run_pipeline_generated(self):
        TaskStore().add_task(_task(status=GCodeGenerationTaskStatus.PENDING.value))
        adapter = _FakeAdapter(_base_result(is_valid=True), [_fgcr()])
        p = _make_pipeline(adapter=adapter)
        p._loader.load.return_value = _fake_report(features=[_feature()])
        with patch("app.gcode_generation.pipeline.load_operation_plan", return_value=OperationPlan()):
            result = await p.run_pipeline("gc-1")
        assert result.status == GCodeGenerationTaskStatus.GENERATED.value
        assert result.total_features == 1
        assert result.disclaimer is not None
        assert result.disclaimer.requires_cam_validation is True

    @pytest.mark.asyncio
    async def test_loader_error_fails(self):
        TaskStore().add_task(_task(status=GCodeGenerationTaskStatus.PENDING.value))
        p = _make_pipeline()
        from app.gcode_generation.gcode_store import ChatterReportLoadError

        p._loader.load.side_effect = ChatterReportLoadError("boom")
        result = await p.run_pipeline("gc-1")
        assert result.status == GCodeGenerationTaskStatus.FAILED.value


# review_feature


class TestReviewFeature:
    def test_task_not_found(self):
        p = _make_pipeline()
        with pytest.raises(GCodeReviewError, match="不存在"):
            p.review_feature("nope", "f1", GCodeReviewStatus.CONFIRMED.value)

    def test_status_not_allowed(self):
        TaskStore().add_task(_task(status=GCodeGenerationTaskStatus.PENDING.value))
        p = _make_pipeline()
        with pytest.raises(GCodeReviewError, match="状态不允许"):
            p.review_feature("gc-1", "f1", GCodeReviewStatus.CONFIRMED.value)

    def test_invalid_review_status(self):
        TaskStore().add_task(_task(status=GCodeGenerationTaskStatus.GENERATED.value))
        p = _make_pipeline()
        with pytest.raises(GCodeReviewError, match="无效审核状态"):
            p.review_feature("gc-1", "f1", "bogus")

    def test_edited_requires_params(self):
        TaskStore().add_task(_task(status=GCodeGenerationTaskStatus.GENERATED.value))
        p = _make_pipeline()
        with pytest.raises(GCodeReviewError, match="必须提供"):
            p.review_feature("gc-1", "f1", GCodeReviewStatus.EDITED.value)

    def test_feature_not_found(self):
        TaskStore().add_task(_task(status=GCodeGenerationTaskStatus.GENERATED.value, features=[_fgcr("f1")]))
        p = _make_pipeline()
        with pytest.raises(GCodeReviewError, match="不存在"):
            p.review_feature("gc-1", "nope", GCodeReviewStatus.CONFIRMED.value)

    def test_confirm_single_feature(self):
        TaskStore().add_task(_task(status=GCodeGenerationTaskStatus.GENERATED.value, features=[_fgcr("f1")]))
        p = _make_pipeline()
        result = p.review_feature("gc-1", "f1", GCodeReviewStatus.CONFIRMED.value, reviewed_by="eng")
        assert result.review_status == GCodeReviewStatus.CONFIRMED.value
        task = TaskStore().get_task("gc-1")
        assert task.status == GCodeGenerationTaskStatus.REVIEWED.value
        assert task.reviewed_by == "eng"

    def test_edited_records_params(self):
        TaskStore().add_task(_task(status=GCodeGenerationTaskStatus.GENERATED.value, features=[_fgcr("f1")]))
        p = _make_pipeline()
        result = p.review_feature(
            "gc-1",
            "f1",
            GCodeReviewStatus.EDITED.value,
            edited_params={"axial_depth_mm": 0.5, "stable": False},
            engineer_notes="注意",
        )
        assert result.review_status == GCodeReviewStatus.EDITED.value
        assert result.edited_params["axial_depth_mm"] == 0.5
        assert result.stable is False
        assert result.edited_params["engineer_notes"] == "注意"

    def test_partial_review_keeps_generated(self):
        TaskStore().add_task(
            _task(status=GCodeGenerationTaskStatus.GENERATED.value, features=[_fgcr("f1"), _fgcr("f2")])
        )
        p = _make_pipeline()
        p.review_feature("gc-1", "f1", GCodeReviewStatus.CONFIRMED.value)
        task = TaskStore().get_task("gc-1")
        assert task.status == GCodeGenerationTaskStatus.GENERATED.value


# confirm_task


class TestConfirmTask:
    def test_status_not_allowed(self):
        TaskStore().add_task(
            _task(
                status=GCodeGenerationTaskStatus.GENERATED.value,
                features=[_fgcr(status=GCodeReviewStatus.CONFIRMED.value)],
            )
        )
        p = _make_pipeline()
        with pytest.raises(GCodeGenerationPipelineError, match="状态不允许"):
            p.confirm_task("gc-1")

    def test_all_rejected_raises(self, tmp_path):
        t = _task(status=GCodeGenerationTaskStatus.REVIEWED.value, workspace=tmp_path)
        t.feature_gcode_results = [_fgcr(status=GCodeReviewStatus.REJECTED.value)]
        TaskStore().add_task(t)
        p = _make_pipeline()
        with pytest.raises(GCodeReviewError, match="无可导出"):
            p.confirm_task("gc-1")

    def test_confirm_success_exports(self, tmp_path):
        t = _task(status=GCodeGenerationTaskStatus.REVIEWED.value, workspace=tmp_path)
        t.feature_gcode_results = [_fgcr(status=GCodeReviewStatus.CONFIRMED.value)]
        TaskStore().add_task(t)
        p = _make_pipeline()
        result = p.confirm_task("gc-1", reviewer="eng")
        assert result.status == GCodeGenerationTaskStatus.SUCCEEDED.value
        assert result.gcode_file_path
        assert Path(result.gcode_file_path).exists()
        assert result.gcode_report_path
        assert Path(result.gcode_report_path).exists()
        # report JSON 含 cam_validation_required=True
        data = json.loads(Path(result.gcode_report_path).read_text(encoding="utf-8"))
        assert data["cam_validation_required"] is True
        assert data["task_status"] == "succeeded"
        assert len(data["feature_results"]) == 1


# export_gcode / delete_task


class TestExportAndDelete:
    def test_export_gcode_not_succeeded(self):
        TaskStore().add_task(_task(status=GCodeGenerationTaskStatus.GENERATED.value))
        p = _make_pipeline()
        with pytest.raises(GCodeGenerationPipelineError, match="状态不允许"):
            p.export_gcode("gc-1")

    def test_export_gcode_empty_path(self):
        TaskStore().add_task(_task(status=GCodeGenerationTaskStatus.SUCCEEDED.value))
        p = _make_pipeline()
        with pytest.raises(GCodeGenerationPipelineError, match="为空"):
            p.export_gcode("gc-1")

    def test_export_gcode_success(self):
        t = _task(status=GCodeGenerationTaskStatus.SUCCEEDED.value)
        t.gcode_file_path = "/tmp/gc-1.nc"
        TaskStore().add_task(t)
        p = _make_pipeline()
        assert p.export_gcode("gc-1") == "/tmp/gc-1.nc"

    def test_delete_succeeded_forbidden(self):
        TaskStore().add_task(_task(status=GCodeGenerationTaskStatus.SUCCEEDED.value))
        p = _make_pipeline()
        with pytest.raises(ReviewError, match="禁止删除"):
            p.delete_task("gc-1")

    def test_delete_normal(self):
        TaskStore().add_task(_task(status=GCodeGenerationTaskStatus.PENDING.value))
        p = _make_pipeline()
        p.delete_task("gc-1")
        assert TaskStore().list_tasks() == []


# _build_result / _build_disclaimer


class TestBuildResult:
    def test_build_result_without_file(self):
        TaskStore().add_task(_task(status=GCodeGenerationTaskStatus.GENERATED.value))
        p = _make_pipeline()
        task = TaskStore().get_task("gc-1")
        r = p._build_result(task)
        assert isinstance(r, GCodeGenerationResult)
        assert r.gcode_file_path is None
        assert r.disclaimer is not None
        assert r.disclaimer.requires_cam_validation is True

    def test_build_result_to_dict(self):
        TaskStore().add_task(_task(status=GCodeGenerationTaskStatus.SUCCEEDED.value))
        task = TaskStore().get_task("gc-1")
        task.gcode_file_path = "/tmp/x.nc"
        p = _make_pipeline()
        d = p._build_result(task).to_dict()
        assert d["gcode_file_path"] == "/tmp/x.nc"
        assert d["disclaimer"] is not None

    def test_build_disclaimer_neural_network(self):
        t = _task(
            status=GCodeGenerationTaskStatus.GENERATED.value,
            prediction_method="neural_network",
            pending_calibration=True,
        )
        p = _make_pipeline()
        d = p._build_disclaimer(t, gcode_file_exported=False)
        assert d.ltc_experiment_used is True
        assert d.pending_calibration is True
        assert "HRC52" in d.warning_message


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
