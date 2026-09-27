"""失败入册测试（自进化 M0：执行留痕）。

覆盖：
- ``app/agent/failure_recorder.py``：failure/success 记录、原始输出截断、
  案例库异常时永不抛出
- 编排器接线：规划提案非法输出入册（llm_planning）、LLM 不可用不入册、
  修复提案非法输出入册（gcode_repair）、安全升级人工入册
  （safety_validator）、一次通过成功入册、修复后通过不入册
- ``KnowledgeAugmenter``：参数提案非法输出入册（llm_param_aug）、
  提示词版本随输出透出
- ``NL2CADService``：解析失败入册（nl2cad_extract）、LLM 不可用不入册
"""

import json
from typing import Any

import pytest

from app.agent import failure_recorder
from app.agent.failure_recorder import (
    record_agent_failure,
    record_agent_success,
    record_llm_invalid_output,
)
from app.agent.knowledge_augmenter import KnowledgeAugmenter
from app.agent.orchestrator import (
    AgentOrchestrator,
    PipelineResult,
    StepResult,
    StepStatus,
)
from app.ai.prompts import (
    AUGMENTER_PARAM_PROPOSAL_SYSTEM_ID,
    ORCHESTRATOR_PLANNING_USER_ID,
)
from app.gcode_generation.failure_case_store import FailureCaseStore, reset_failure_case_store


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    reset_failure_case_store()
    return FailureCaseStore(db_path=tmp_path / "fc.db")


@pytest.fixture()
def recorded(monkeypatch):
    """捕获 failure_recorder 全部写入（不落真实库）。"""
    cases = []

    class _FakeStore:
        def record(self, case):
            cases.append(case)
            return case.case_id

    monkeypatch.setattr(failure_recorder, "get_failure_case_store", lambda: _FakeStore())
    return cases


def _orchestrator(tmp_path) -> AgentOrchestrator:
    return AgentOrchestrator(trace_log_dir=str(tmp_path / "traces"), memory=False)


class _FakeLLM:
    def __init__(self, content: str, exc: Exception | None = None):
        self._content = content
        self._exc = exc
        self.calls = 0

    async def chat_completion(self, messages, max_tokens=2048, temperature=0.7, model=None):
        self.calls += 1
        if self._exc:
            raise self._exc
        return {"content": self._content, "model": "fake", "finish_reason": "stop", "usage": {}}


class _FakeQuadIndex:
    def recommend_process(self, feature, material="general", top_k=5):
        return [
            {
                "feature": feature,
                "process": "rough_mill",
                "tool": "endmill_d10",
                "parameters": {"spindle_rpm": 1500, "feed_rate_mm_per_min": 200},
                "confidence": 0.8,
                "source": "manual",
            }
        ]


# ---------------------------------------------------------------------------
# failure_recorder 单元
# ---------------------------------------------------------------------------


class TestRecorderUnit:
    def test_record_failure_persists_fields(self, store, monkeypatch):
        monkeypatch.setattr(failure_recorder, "get_failure_case_store", lambda: store)
        record_agent_failure(
            task_id="pipe-1",
            source="llm_planning",
            error_codes=["LLM_INVALID_OUTPUT"],
            error_messages=["坏输出"],
            gcode_text="O1000",
            controller_type="fanuc_0i",
            material_name="45#钢",
        )
        case = store.list_cases(source="llm_planning")[0]
        assert case.outcome == "failure"
        assert case.task_id == "pipe-1"
        assert case.gcode_text == "O1000"
        assert case.material_name == "45#钢"
        assert case.controller_type == "fanuc_0i"

    def test_record_success_strips_failure_fields(self, recorded):
        record_agent_success(task_id="pipe-ok", controller_type="fanuc_0i")
        assert len(recorded) == 1
        case = recorded[0]
        assert case.outcome == "success"
        assert case.source == ""
        assert case.gcode_text == ""

    def test_invalid_output_truncates_raw(self, recorded):
        record_llm_invalid_output(
            task_id="t", source="llm_planning", raw_output="x" * 1000
        )
        msg = recorded[0].error_messages[0]
        assert "LLM_INVALID_OUTPUT" in recorded[0].error_codes
        assert len(msg) < 500  # 400 截断 + 前缀

    def test_boom_store_never_raises(self, monkeypatch):
        class _Boom:
            def record(self, case):
                raise RuntimeError("db down")

        monkeypatch.setattr(failure_recorder, "get_failure_case_store", lambda: _Boom())
        assert record_agent_failure(task_id="t", source="llm_planning", error_codes=["X"], error_messages=[]) is None
        assert record_agent_success(task_id="t") is None


# ---------------------------------------------------------------------------
# 编排器接线
# ---------------------------------------------------------------------------


class TestOrchestratorRecording:
    @pytest.mark.asyncio
    async def test_planning_invalid_output_recorded(self, tmp_path, monkeypatch, recorded):
        from app.ai import llm_client as mod

        async def _get():
            return _FakeLLM("抱歉，我不知道该怎么规划")  # 应答了但无 JSON

        monkeypatch.setattr(mod, "get_llm_client", _get)
        orch = _orchestrator(tmp_path)
        steps, meta = await orch._plan_steps_conditionally("dxf_to_gcode", {}, pipeline_id="pipe-1")
        assert meta["source"] == "llm_fallback"
        assert len(recorded) == 1
        case = recorded[0]
        assert case.source == "llm_planning"
        assert case.task_id == "pipe-1"
        assert case.error_codes == ["LLM_INVALID_OUTPUT"]
        assert "规划器原始输出" in case.error_messages[0]

    @pytest.mark.asyncio
    async def test_planning_unavailable_not_recorded(self, tmp_path, monkeypatch, recorded):
        """LLM 不可用属环境信号：不入案例库（口径硬约束）。"""
        from app.ai import llm_client as mod

        async def _boom():
            raise RuntimeError("no llm")

        monkeypatch.setattr(mod, "get_llm_client", _boom)
        orch = _orchestrator(tmp_path)
        steps, meta = await orch._plan_steps_conditionally("dxf_to_gcode", {}, pipeline_id="pipe-1")
        assert meta["source"] == "llm_fallback"
        assert recorded == []

    @pytest.mark.asyncio
    async def test_planning_success_carries_prompt_version(self, tmp_path, monkeypatch, recorded):
        from app.ai import llm_client as mod

        plan = json.dumps(
            {"include": ["parameter_recommend", "gcode_generate", "validate_safety"], "rationale": "ok"},
            ensure_ascii=False,
        )

        async def _get():
            return _FakeLLM(plan)

        monkeypatch.setattr(mod, "get_llm_client", _get)
        orch = _orchestrator(tmp_path)
        steps, meta = await orch._plan_steps_conditionally("dxf_to_gcode", {}, pipeline_id="pipe-1")
        assert meta["source"] == "llm"
        assert meta["prompt_id"] == ORCHESTRATOR_PLANNING_USER_ID
        assert meta["prompt_version"] >= 1
        assert recorded == []  # 成功不记失败案例

    @pytest.mark.asyncio
    async def test_repair_invalid_output_recorded(self, tmp_path, monkeypatch, recorded):
        from app.ai import llm_client as mod

        async def _get():
            return _FakeLLM("无法修复")  # 单行文本 → 合法性守卫拦截

        monkeypatch.setattr(mod, "get_llm_client", _get)
        orch = _orchestrator(tmp_path)
        context = {"pipeline_id": "pipe-2", "gcode_generate": {"gcode": "G01 X1 F100\nM30"}}
        out = await orch._llm_repair_gcode({"issues": []}, context)
        assert out is None
        assert len(recorded) == 1
        case = recorded[0]
        assert case.source == "gcode_repair"
        assert case.task_id == "pipe-2"
        assert case.gcode_text == "G01 X1 F100\nM30"

    @pytest.mark.asyncio
    async def test_repair_unavailable_not_recorded(self, tmp_path, monkeypatch, recorded):
        from app.ai import llm_client as mod

        async def _boom():
            raise RuntimeError("no llm")

        monkeypatch.setattr(mod, "get_llm_client", _boom)
        orch = _orchestrator(tmp_path)
        context = {"pipeline_id": "pipe-2", "gcode_generate": {"gcode": "G01 X1 F100\nM30"}}
        out = await orch._llm_repair_gcode({"issues": []}, context)
        assert out is None
        assert recorded == []

    def test_escalation_recorded_as_safety_validator(self, tmp_path, recorded):
        orch = _orchestrator(tmp_path)
        result = PipelineResult(pipeline_id="pipe-3", success=False)
        result.steps.append(
            StepResult(
                step_name="validate_safety",
                status=StepStatus.FAILED,
                output={"error_codes": ["NO_PROGRAM_END"]},
                error="预算耗尽，需人工介入",
            )
        )
        context = {
            "input": {"material_name": "45#钢"},
            "gcode_generate": {"gcode": "O1000\nG01 X1", "controller_type": "fanuc_0i"},
        }
        orch._record_pipeline_outcome(result, context)
        assert len(recorded) == 1
        case = recorded[0]
        assert case.outcome == "failure"
        assert case.source == "safety_validator"
        assert case.error_codes == ["NO_PROGRAM_END"]
        assert case.gcode_text == "O1000\nG01 X1"
        assert case.material_name == "45#钢"
        assert case.controller_type == "fanuc_0i"

    def test_one_pass_success_recorded(self, tmp_path, recorded):
        orch = _orchestrator(tmp_path)
        result = PipelineResult(pipeline_id="pipe-4", success=True)
        result.steps.append(
            StepResult(
                step_name="validate_safety",
                status=StepStatus.COMPLETED,
                output={"safety_valid": True},
            )
        )
        orch._record_pipeline_outcome(result, {"input": {}, "gcode_generate": {"gcode": "O1000\nM30"}})
        assert len(recorded) == 1
        case = recorded[0]
        assert case.outcome == "success"
        assert case.gcode_text == ""  # 成功不入 G 代码（控制库体积）

    def test_success_after_repair_not_recorded(self, tmp_path, recorded):
        """经修复后通过：介于成败之间，不入册（不拉高一次通过率）。"""
        orch = _orchestrator(tmp_path)
        result = PipelineResult(pipeline_id="pipe-5", success=True)
        result.repair_count = 1
        result.steps.append(StepResult(step_name="validate_safety", status=StepStatus.COMPLETED))
        orch._record_pipeline_outcome(result, {"input": {}, "gcode_generate": {"gcode": "X"}})
        assert recorded == []

    def test_pipeline_without_validate_not_recorded(self, tmp_path, recorded):
        orch = _orchestrator(tmp_path)
        result = PipelineResult(pipeline_id="pipe-6", success=True)
        result.steps.append(StepResult(step_name="parameter_recommend", status=StepStatus.COMPLETED))
        orch._record_pipeline_outcome(result, {"input": {}})
        assert recorded == []

    def test_recorder_failure_never_breaks_caller(self, tmp_path, monkeypatch):
        class _Boom:
            def record(self, case):
                raise RuntimeError("db down")

        monkeypatch.setattr(failure_recorder, "get_failure_case_store", lambda: _Boom())
        orch = _orchestrator(tmp_path)
        result = PipelineResult(pipeline_id="pipe-7", success=False)
        result.steps.append(
            StepResult(
                step_name="validate_safety",
                status=StepStatus.FAILED,
                output={"error_codes": ["X"]},
                error="escalated",
            )
        )
        orch._record_pipeline_outcome(result, {"input": {}, "gcode_generate": {"gcode": "G"}})
        # 不抛异常即通过


# ---------------------------------------------------------------------------
# 知识增强接线
# ---------------------------------------------------------------------------


def _aug_context() -> dict[str, Any]:
    return {
        "input": {"material_name": "6061铝合金"},
        "dxf_parse": {"features": [{"type": "pocket"}]},
        "pipeline_id": "pipe-9",
    }


def _rule_output() -> dict[str, Any]:
    return {
        "status": "success",
        "parameters": {"spindle_rpm": 6000, "feed_rate_mm_per_min": 800, "depth_of_cut_mm": 2.0},
        "operations": [],
        "confidence": 0.8,
    }


class TestKnowledgeAugmenterRecording:
    @pytest.mark.asyncio
    async def test_invalid_proposal_recorded(self, recorded):
        aug = KnowledgeAugmenter(llm_client=_FakeLLM("不是JSON"), quadruple_index=_FakeQuadIndex())
        out = await aug.augment(_rule_output(), _aug_context())
        assert out["decision_source"] == "rule"
        assert len(recorded) == 1
        case = recorded[0]
        assert case.source == "llm_param_aug"
        assert case.task_id == "pipe-9"
        assert case.error_codes == ["LLM_INVALID_OUTPUT"]

    @pytest.mark.asyncio
    async def test_unavailable_not_recorded(self, recorded):
        aug = KnowledgeAugmenter(
            llm_client=_FakeLLM("", exc=RuntimeError("boom")), quadruple_index=_FakeQuadIndex()
        )
        out = await aug.augment(_rule_output(), _aug_context())
        assert out["decision_source"] == "rule"
        assert recorded == []

    @pytest.mark.asyncio
    async def test_prompt_version_in_enriched_output(self, recorded):
        proposal = json.dumps({"adjustments": {}, "rationale": "参数合理"}, ensure_ascii=False)
        aug = KnowledgeAugmenter(llm_client=_FakeLLM(proposal), quadruple_index=_FakeQuadIndex())
        out = await aug.augment(_rule_output(), _aug_context())
        assert out["prompt_id"] == AUGMENTER_PARAM_PROPOSAL_SYSTEM_ID
        assert out["prompt_version"] >= 1


# ---------------------------------------------------------------------------
# NL2CAD 接线
# ---------------------------------------------------------------------------


class TestNL2CADRecording:
    def _service(self, llm):
        from app.api.v1.nl2cad.services import NL2CADService

        svc = NL2CADService.__new__(NL2CADService)  # 绕过 CadQueryGenerator 构造
        svc._llm_client = llm
        return svc

    @pytest.mark.asyncio
    async def test_parse_failure_recorded(self, recorded):
        svc = self._service(_FakeLLM("这不是JSON输出"))
        params = await svc.extract_params_from_nl("一个 50x30x20 的方块")
        assert params["_fallback"] == "rule_based"
        assert len(recorded) == 1
        case = recorded[0]
        assert case.source == "nl2cad_extract"
        assert case.task_id.startswith("nl2cad:")
        assert "NL2CAD 参数提取原始输出" in case.error_messages[0]

    @pytest.mark.asyncio
    async def test_llm_unavailable_not_recorded(self, recorded):
        svc = self._service(_FakeLLM("", exc=RuntimeError("no llm")))
        params = await svc.extract_params_from_nl("一个 50x30x20 的方块")
        assert params["_fallback"] == "rule_based"
        assert recorded == []

    @pytest.mark.asyncio
    async def test_refine_parse_failure_recorded(self, recorded):
        svc = self._service(_FakeLLM("坏掉的输出 {{"))
        refined = await svc.refine_params({"shape_type": "box"}, "改小一点")
        assert refined["_fallback"] == "rule_based"
        assert len(recorded) == 1
        assert recorded[0].source == "nl2cad_extract"
