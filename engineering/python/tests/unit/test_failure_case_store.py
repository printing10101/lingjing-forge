"""失败案例库测试（Phase 0 自进化管道 M1-M3）。

覆盖：FailureCase 校验规则、SQLite 存储读写、过滤查询、
M3 基线报表统计（一次通过率 / 按来源 / 按错误码分布）、
pipeline 失败/成功出口的自动入库钩子。
"""

from unittest.mock import MagicMock

import pytest

from app.gcode_generation.failure_case_store import (
    FailureCase,
    FailureCaseStore,
    reset_failure_case_store,
)


def _failure_case(**kw) -> FailureCase:
    defaults = dict(
        task_id="gc-1",
        outcome="failure",
        source="safety_validator",
        controller_type="fanuc_0i",
        material_name="45#钢",
        error_codes=["L3-DEPTH"],
        error_messages=["实际切深超限"],
        gcode_text="O1000\nM30",
        total_features=3,
        unstable_features=1,
    )
    defaults.update(kw)
    return FailureCase(**defaults)


# FailureCase 校验规则


class TestFailureCaseValidation:
    def test_failure_requires_source(self):
        with pytest.raises(ValueError, match="source"):
            FailureCase(task_id="t", outcome="failure")

    def test_invalid_outcome_raises(self):
        with pytest.raises(ValueError, match="outcome"):
            FailureCase(task_id="t", outcome="cancelled", source="safety_validator")

    def test_invalid_source_raises(self):
        with pytest.raises(ValueError, match="source"):
            FailureCase(task_id="t", outcome="failure", source="nowhere")

    def test_success_case_strips_failure_fields(self):
        case = FailureCase(
            task_id="t",
            outcome="success",
            source="safety_validator",  # 误传，应被清空
            error_codes=["L1"],
            error_messages=["x"],
            gcode_text="O1000",
        )
        assert case.source == ""
        assert case.error_codes == []
        assert case.error_messages == []
        assert case.gcode_text == ""

    def test_case_id_auto_generated(self):
        c1 = _failure_case()
        c2 = _failure_case()
        assert c1.case_id.startswith("fc_")
        assert c2.case_id != c1.case_id


# 存储读写与统计


class TestFailureCaseStore:
    @pytest.fixture()
    def store(self, tmp_path):
        reset_failure_case_store()
        return FailureCaseStore(db_path=tmp_path / "fc.db")

    def test_record_and_list_roundtrip(self, store):
        case = _failure_case()
        case_id = store.record(case)
        cases = store.list_cases()
        assert len(cases) == 1
        loaded = cases[0]
        assert loaded.case_id == case_id
        assert loaded.error_codes == ["L3-DEPTH"]
        assert loaded.error_messages == ["实际切深超限"]
        assert loaded.gcode_text == "O1000\nM30"
        assert loaded.controller_type == "fanuc_0i"

    def test_list_filter_by_source_and_outcome(self, store):
        store.record(_failure_case(source="safety_validator"))
        store.record(_failure_case(source="unstable_features", error_codes=["UNSTABLE_FEATURES"]))
        store.record(_failure_case(outcome="success", task_id="gc-ok"))
        assert len(store.list_cases(source="safety_validator")) == 1
        assert len(store.list_cases(outcome="failure")) == 2
        assert len(store.list_cases(outcome="success")) == 1

    def test_stats_baseline_report(self, store):
        # 2 失败 + 3 成功 → 一次通过率 0.6
        store.record(_failure_case(source="safety_validator", error_codes=["L3", "L5"]))
        store.record(_failure_case(source="pipeline_exception", error_codes=["ValueError"]))
        for i in range(3):
            store.record(_failure_case(outcome="success", task_id=f"ok-{i}"))
        s = store.stats()
        assert s["total"] == 5
        assert s["failures"] == 2
        assert s["successes"] == 3
        assert s["one_pass_rate"] == 0.6
        assert s["by_source"] == {"safety_validator": 1, "pipeline_exception": 1}
        # 按错误码倒序：L3/L5/ValueError 各 1
        assert s["by_code"] == {"L3": 1, "L5": 1, "ValueError": 1}

    def test_stats_empty(self, store):
        s = store.stats()
        assert s["total"] == 0
        assert s["one_pass_rate"] is None

    def test_count(self, store):
        assert store.count() == 0
        store.record(_failure_case())
        assert store.count() == 1


# pipeline 钩子（M2）


def _patch_store(monkeypatch):
    recorded = []

    class _FakeStore:
        def record(self, case):
            recorded.append(case)
            return case.case_id

    from app.gcode_generation import pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "get_failure_case_store", lambda: _FakeStore())
    # 流水线在 adapter 之前会加载阶段 3 OperationPlan，桩掉文件读取
    monkeypatch.setattr(pipeline_mod, "load_operation_plan", lambda p: MagicMock())
    return recorded


class TestPipelineHooks:
    @pytest.mark.asyncio
    async def test_unstable_failure_recorded(self, monkeypatch):
        from app.process_planning.gcode_generator import GCodeResult
        from app.gcode_generation.gcode_store import (
            GCodeGenerationTaskStatus,
            TaskStore,
        )
        from app.gcode_generation.pipeline import GCodeGenerationPipeline

        recorded = _patch_store(monkeypatch)
        base = GCodeResult(
            program_text="O1000\nM30",
            controller_type="fanuc_0i",
            total_lines=2,
            errors=["unstable: f1"],
        )
        adapter = _FakeAdapterForHooks(base, [_fgcr_hook(stable=False)])
        p = GCodeGenerationPipeline(adapter=adapter)
        p._loader = MagicMock()
        p._loader.load.return_value = _fake_report_for_hooks(unstable=1)
        task = _task_for_hooks(status=GCodeGenerationTaskStatus.PENDING.value)
        p._store = MagicMock()
        p._store.get_task.return_value = task

        result = await p.run_pipeline(task.task_id)
        assert result.status == GCodeGenerationTaskStatus.FAILED.value
        assert len(recorded) == 1
        case = recorded[0]
        assert case.outcome == "failure"
        assert case.source == "unstable_features"
        assert "UNSTABLE_FEATURES" in case.error_codes
        assert case.gcode_text == "O1000\nM30"

    @pytest.mark.asyncio
    async def test_success_recorded_without_gcode(self, monkeypatch):
        from app.process_planning.gcode_generator import GCodeResult
        from app.gcode_generation.gcode_store import (
            GCodeGenerationTaskStatus,
            TaskStore,
        )
        from app.gcode_generation.pipeline import GCodeGenerationPipeline

        recorded = _patch_store(monkeypatch)
        base = GCodeResult(program_text="O1000\nM30", controller_type="fanuc_0i", total_lines=2)
        adapter = _FakeAdapterForHooks(base, [_fgcr_hook(stable=True)])
        p = GCodeGenerationPipeline(adapter=adapter)
        p._loader = MagicMock()
        p._loader.load.return_value = _fake_report_for_hooks(unstable=0)
        p._safety_validator = MagicMock()
        sr = MagicMock()
        sr.is_valid = True
        sr.warnings = []
        p._safety_validator.validate_all.return_value = sr
        task = _task_for_hooks(status=GCodeGenerationTaskStatus.PENDING.value)
        p._store = MagicMock()
        p._store.get_task.return_value = task

        result = await p.run_pipeline(task.task_id)
        assert result.status == GCodeGenerationTaskStatus.GENERATED.value
        assert len(recorded) == 1
        case = recorded[0]
        assert case.outcome == "success"
        assert case.gcode_text == ""

    @pytest.mark.asyncio
    async def test_store_failure_never_breaks_pipeline(self, monkeypatch):
        """案例库抛异常时主流程必须照常返回（非致命钩子硬约束）。"""
        from app.process_planning.gcode_generator import GCodeResult
        from app.gcode_generation.gcode_store import (
            GCodeGenerationTaskStatus,
            TaskStore,
        )
        from app.gcode_generation.pipeline import GCodeGenerationPipeline

        class _BoomStore:
            def record(self, case):
                raise RuntimeError("db down")

        from app.gcode_generation import pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "get_failure_case_store", lambda: _BoomStore())
        base = GCodeResult(
            program_text="O1000\nM30",
            controller_type="fanuc_0i",
            total_lines=2,
            errors=["unstable: f1"],
        )
        adapter = _FakeAdapterForHooks(base, [_fgcr_hook(stable=False)])
        p = GCodeGenerationPipeline(adapter=adapter)
        p._loader = MagicMock()
        p._loader.load.return_value = _fake_report_for_hooks(unstable=1)
        task = _task_for_hooks(status=GCodeGenerationTaskStatus.PENDING.value)
        p._store = MagicMock()
        p._store.get_task.return_value = task

        result = await p.run_pipeline(task.task_id)
        assert result.status == GCodeGenerationTaskStatus.FAILED.value  # 主流程不受影响


# 钩子测试用的小夹具（与 test_gcode_pipeline 风格一致）


def _fgcr_hook(stable=True):
    from app.gcode_generation.gcode_store import FeatureGCodeResult, GCodeReviewStatus

    r = FeatureGCodeResult(
        feature_id="f1",
        feature_type="plane",
        material_id="steel",
        spindle_rpm=2000.0,
        axial_depth_mm=1.0,
        limit_depth_mm=2.0,
        stable=stable,
        safety_margin_ratio=0.5,
    )
    r.review_status = GCodeReviewStatus.PENDING.value
    r.gcode_lines = ["G01 X0 Y0"]
    r.line_range = ((0, 0),)
    return r


def _fake_report_for_hooks(unstable=0):
    r = MagicMock()
    feats = [_fgcr_hook(stable=(i >= unstable)) for i in range(max(unstable, 1))]
    r.feature_results = feats
    r.total_features = len(feats)
    r.stable_features = len(feats) - unstable
    r.unstable_features = unstable
    r.pending_calibration = False
    r.prediction_method = "analytical"
    return r


def _task_for_hooks(status):
    from app.gcode_generation.gcode_store import GCodeGenerationTask

    t = GCodeGenerationTask(
        task_id="gc-hook-1",
        source_chatter_report_path="/tmp/ch.json",
        source_operation_plan_path="/tmp/plan.json",
        status=status,
        controller_type="fanuc_0i",
        workspace_dir="",
    )
    t.total_features = 1
    t.prediction_method = "analytical"
    return t


class _FakeAdapterForHooks:
    def __init__(self, base_result, features):
        self._base = base_result
        self._features = features

    def adapt(self, **kwargs):
        return self._base, self._features


# pipeline 钩子（M4a：成功案例入工艺库）


class _FakeQuadIndex:
    """记录 add() 调用的工艺四元组索引桩（查重恒返回无重复）。"""

    def __init__(self):
        self.added = []
        self.flushed = False

    def add(self, quad):
        self.added.append(quad)

    def flush(self, force=False):
        self.flushed = True
        return True

    def find_similar(self, feature, material="general", top_k=10):
        return []


class TestSuccessHarvestHook:
    @pytest.mark.asyncio
    async def test_success_harvest_ingests_stable_features(self, monkeypatch):
        """成功出口把 stable 特征实证写入工艺四元组索引。"""
        from app.process_planning.gcode_generator import GCodeResult
        from app.gcode_generation.gcode_store import GCodeGenerationTaskStatus
        from app.gcode_generation.pipeline import GCodeGenerationPipeline

        _patch_store(monkeypatch)
        fake_index = _FakeQuadIndex()
        monkeypatch.setattr(
            "app.rag.process_quadruple.get_process_quadruple_index",
            lambda: fake_index,
        )
        base = GCodeResult(program_text="O1000\nM30", controller_type="fanuc_0i", total_lines=2)
        adapter = _FakeAdapterForHooks(base, [_fgcr_hook(stable=True)])
        p = GCodeGenerationPipeline(adapter=adapter)
        p._loader = MagicMock()
        p._loader.load.return_value = _fake_report_for_hooks(unstable=0)
        p._safety_validator = MagicMock()
        sr = MagicMock()
        sr.is_valid = True
        sr.warnings = []
        p._safety_validator.validate_all.return_value = sr
        task = _task_for_hooks(status=GCodeGenerationTaskStatus.PENDING.value)
        p._store = MagicMock()
        p._store.get_task.return_value = task

        result = await p.run_pipeline(task.task_id)
        assert result.status == GCodeGenerationTaskStatus.GENERATED.value
        assert len(fake_index.added) == 1
        quad = fake_index.added[0]
        assert quad.feature == "face"  # plane → face
        assert quad.source == "generated_validated"
        assert quad.material == "steel"
        assert fake_index.flushed

    @pytest.mark.asyncio
    async def test_harvest_failure_never_breaks_pipeline(self, monkeypatch):
        """工艺索引异常时主流程必须照常 GENERATED（非致命钩子硬约束）。"""
        from app.process_planning.gcode_generator import GCodeResult
        from app.gcode_generation.gcode_store import GCodeGenerationTaskStatus
        from app.gcode_generation.pipeline import GCodeGenerationPipeline

        _patch_store(monkeypatch)

        def _boom():
            raise RuntimeError("rag index down")

        monkeypatch.setattr(
            "app.rag.process_quadruple.get_process_quadruple_index", _boom
        )
        base = GCodeResult(program_text="O1000\nM30", controller_type="fanuc_0i", total_lines=2)
        adapter = _FakeAdapterForHooks(base, [_fgcr_hook(stable=True)])
        p = GCodeGenerationPipeline(adapter=adapter)
        p._loader = MagicMock()
        p._loader.load.return_value = _fake_report_for_hooks(unstable=0)
        p._safety_validator = MagicMock()
        sr = MagicMock()
        sr.is_valid = True
        sr.warnings = []
        p._safety_validator.validate_all.return_value = sr
        task = _task_for_hooks(status=GCodeGenerationTaskStatus.PENDING.value)
        p._store = MagicMock()
        p._store.get_task.return_value = task

        result = await p.run_pipeline(task.task_id)
        assert result.status == GCodeGenerationTaskStatus.GENERATED.value  # 主流程不受影响
