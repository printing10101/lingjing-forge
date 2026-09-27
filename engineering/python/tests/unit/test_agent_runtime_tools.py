"""制造工具集测试（自进化 M2 · AgentRuntime tools）。

覆盖：注册表（注册/查询/提示词文本/签名）、safety/evaluate/process_plan/
failure_stats 工具真实后端、错误观测（非法输入/未知异常降级）。
"""

import json

import pytest

from app.agent.runtime.tools import Tool, ToolRegistry, create_default_registry
from app.gcode_generation.failure_case_store import (
    FailureCase,
    FailureCaseStore,
    reset_failure_case_store,
)

VALID_GCODE = "O1000\nG01 X10 F800\nM30"
INVALID_GCODE = "G01 X10 F-5\nM30"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("FAILURE_CASES_DB", str(tmp_path / "fc.db"))
    reset_failure_case_store()
    yield
    reset_failure_case_store()


class TestRegistry:
    def test_register_get_names(self):
        reg = ToolRegistry()
        reg.register(Tool(name="a_tool", description="测试工具", params={}, fn=lambda: {}))
        assert reg.get("a_tool") is not None
        assert reg.get("nope") is None
        assert reg.names() == ["a_tool"]

    def test_register_empty_name_rejected(self):
        reg = ToolRegistry()
        with pytest.raises(ValueError):
            reg.register(Tool(name=" ", description="x", params={}, fn=lambda: {}))

    def test_prompt_text_contains_signature(self):
        text = create_default_registry().to_prompt_text()
        for name in ("process_plan_run", "safety_validate_gcode", "evaluate_gcode", "rag_recommend_process", "failure_stats_query"):
            assert name in text
        assert "{tools_text}" not in text


class TestSafetyTool:
    def test_valid_gcode(self):
        obs = json.loads(create_default_registry().get("safety_validate_gcode").run(gcode_text=VALID_GCODE))
        assert obs["is_valid"] is True
        assert obs["error_codes"] == []

    def test_invalid_gcode(self):
        obs = json.loads(create_default_registry().get("safety_validate_gcode").run(gcode_text=INVALID_GCODE))
        assert obs["is_valid"] is False
        assert "NEGATIVE_FEED" in obs["error_codes"]

    def test_exception_degrades_to_error_observation(self):
        # 触发类型错误：gcode_text 传不可迭代对象
        obs_text = create_default_registry().get("safety_validate_gcode").run(gcode_text=12345)
        assert '"error"' in obs_text


class TestEvaluateTool:
    def test_evaluation_dimensions(self):
        obs = json.loads(create_default_registry().get("evaluate_gcode").run(gcode_text=VALID_GCODE))
        assert obs["passed"] is True
        assert obs["composite_score"] == 100.0
        assert set(obs["dimensions"]) == {"syntax", "geometry", "physics"}
        assert obs["dimensions"]["geometry"]["status"] == "skipped"

    def test_failure_classes_surfaced(self):
        obs = json.loads(create_default_registry().get("evaluate_gcode").run(gcode_text=INVALID_GCODE))
        assert obs["passed"] is False
        assert "NEGATIVE_FEED" in obs["failure_classes"]


class TestProcessPlanTool:
    def test_real_pipeline_plans_hole_feature(self):
        desc = json.dumps(
            {"material": "45钢", "part_type": "plate", "holes": [
                {"type": "hole", "id": "H001", "hole_type": "through_hole",
                 "position": {"x": 20.0, "y": 20.0}, "diameter": 8.0, "depth": 12.0,
                 "tolerance_grade": "IT8", "surface": "A"}
            ]},
            ensure_ascii=False,
        )
        obs = json.loads(create_default_registry().get("process_plan_run").run(part_description=desc))
        assert obs["success"] is True
        assert obs["operation_count"] >= 1

    def test_invalid_json_degrades(self):
        obs = json.loads(create_default_registry().get("process_plan_run").run(part_description="不是JSON"))
        assert '"error"' in json.dumps(obs, ensure_ascii=False)


class TestFailureStatsTool:
    def test_stats_with_seeded_cases(self):
        store = FailureCaseStore()
        store.record(
            FailureCase(task_id="t1", outcome="failure", source="react_agent",
                        error_codes=["LLM_INVALID_OUTPUT"], error_messages=["x"])
        )
        obs = json.loads(create_default_registry().get("failure_stats_query").run())
        assert obs["total"] == 1
        assert obs["failures"] == 1


class TestRagTool:
    def test_recommend_with_seeded_index(self, monkeypatch):
        from app.rag import process_quadruple as pq

        class _FakeIndex:
            def recommend_process(self, feature, material="general", top_k=3):
                return [{"feature": feature, "process": "drill", "parameters": {"spindle_rpm": 1200}}]

        monkeypatch.setattr(pq, "get_process_quadruple_index", lambda: _FakeIndex())
        obs = json.loads(
            create_default_registry().get("rag_recommend_process").run(feature="hole", material="steel")
        )
        assert obs["count"] == 1
        assert obs["recommendations"][0]["process"] == "drill"
