"""编排器 AI 深度参与升级测试（2026-09 全量升格）。

覆盖：
- ``app/agent/memory.py``：OrchestratorMemory 记录/检索/持久化/路径校验
- ``app/agent/knowledge_augmenter.py``：LLM 提案、物理钳制、降级回退
- orchestrator CONDITIONAL 规划（安全不变量/连通性闭包/LLM 回退）
- W1.1 LLM 诊断修复分支（白名单外错误）
- FEED_OUT_OF_RANGE：校验器产出（G94/G95 模态感知）+ 修复闭环 clamp
- StepResult AI 标注回填
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.agent.knowledge_augmenter import KnowledgeAugmenter, normalize_material
from app.agent.memory import OrchestratorMemory, summarize_pipeline_for_memory
from app.agent.orchestrator import (
    AgentOrchestrator,
    PipelineResult,
    StepResult,
    StepStatus,
)
from app.gcode_generation.safety_validator import SafetyValidator

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# 记忆
# ---------------------------------------------------------------------------


class TestMemory:
    def test_record_and_recall_by_tags(self, tmp_path):
        mem = OrchestratorMemory(base_dir=str(tmp_path))
        mem.record("钛合金 pocket 转速下调", memory_type="correction", importance=0.9, tags=["dxf_to_gcode", "titanium"])
        mem.record("无关记忆", tags=["other"])
        hits = mem.recall(["titanium"])
        assert len(hits) == 1
        assert "转速下调" in hits[0]["content"]
        assert mem.recall(["不存在的标签"]) == []

    def test_persist_and_reload(self, tmp_path):
        mem = OrchestratorMemory(base_dir=str(tmp_path))
        mem.record("经验 A", tags=["x"], importance=0.7)
        mem2 = OrchestratorMemory(base_dir=str(tmp_path))
        assert len(mem2) == 1
        assert mem2.recall(["x"])[0]["content"] == "经验 A"

    def test_invalid_agent_id_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            OrchestratorMemory(base_dir=str(tmp_path), agent_id="..-evil-..")

    def test_summarize_pipeline_repairs_and_escalation(self):
        result = PipelineResult(pipeline_id="p", success=False)
        result.repair_history = [{"applied": ["追加程序结束指令 M30"], "error_codes": ["NO_PROGRAM_END"]}]
        result.fallback_triggered = True
        result.fallback_reason = "预算耗尽"
        entries = summarize_pipeline_for_memory(
            "dxf_to_gcode", {"material_name": "45#钢"}, result
        )
        types = {e["memory_type"] for e in entries}
        assert "repair" in types and "escalation" in types
        assert any("45#钢" in e["tags"] for e in entries)


# ---------------------------------------------------------------------------
# 知识增强
# ---------------------------------------------------------------------------


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


def _rule_output() -> dict[str, Any]:
    return {
        "status": "success",
        "parameters": {"spindle_rpm": 6000, "feed_rate_mm_per_min": 800, "depth_of_cut_mm": 2.0},
        "operations": [],
        "confidence": 0.8,
    }


def _context() -> dict[str, Any]:
    return {
        "input": {"material_name": "6061铝合金"},
        "dxf_parse": {"features": [{"type": "pocket"}]},
    }


class TestKnowledgeAugmenter:
    async def test_disabled_returns_rule(self):
        aug = KnowledgeAugmenter(llm_client=_FakeLLM("{}"), enabled=False)
        out = await aug.augment(_rule_output(), _context())
        assert out["decision_source"] == "rule"

    async def test_llm_adjustment_applied_and_clamped(self):
        proposal = json.dumps(
            {
                "adjustments": {"spindle_rpm": 999999, "feed_rate_mm_per_min": 600, "evil_key": 1},
                "rationale": "降低进给提高表面质量",
            },
            ensure_ascii=False,
        )
        aug = KnowledgeAugmenter(llm_client=_FakeLLM(proposal), quadruple_index=_FakeQuadIndex())
        out = await aug.augment(_rule_output(), _context())
        # 合法调整被应用
        assert out["parameters"]["feed_rate_mm_per_min"] == 600
        # 越界转速被物理钳制丢弃，保持规则值
        assert out["parameters"]["spindle_rpm"] == 6000
        # 白名单外参数被拒
        assert out["decision_source"] == "ai"
        assert any("evil_key" in r for r in out["ai_rejected_adjustments"])
        assert out["knowledge_refs"][0]["process"] == "rough_mill"

    async def test_llm_failure_falls_back_to_rule(self):
        aug = KnowledgeAugmenter(
            llm_client=_FakeLLM("", exc=RuntimeError("boom")), quadruple_index=_FakeQuadIndex()
        )
        out = await aug.augment(_rule_output(), _context())
        assert out["decision_source"] == "rule"
        assert out["parameters"]["spindle_rpm"] == 6000

    async def test_llm_invalid_json_falls_back(self):
        aug = KnowledgeAugmenter(llm_client=_FakeLLM("不是JSON"), quadruple_index=_FakeQuadIndex())
        out = await aug.augment(_rule_output(), _context())
        assert out["decision_source"] == "rule"

    async def test_no_knowledge_no_llm_call(self):
        llm = _FakeLLM("{}")
        aug = KnowledgeAugmenter(llm_client=llm, quadruple_index=None)
        # 无四元组索引且未注入 → 检索为空 → 不调用 LLM
        out = await aug.augment(_rule_output(), {"input": {}, "dxf_parse": {"features": []}})
        assert llm.calls == 0
        assert out["decision_source"] == "rule"

    async def test_ai_confirmed_rule_when_no_adjustments(self):
        aug = KnowledgeAugmenter(
            llm_client=_FakeLLM('{"adjustments": {}, "rationale": "参数合理"}'),
            quadruple_index=_FakeQuadIndex(),
        )
        out = await aug.augment(_rule_output(), _context())
        assert out["decision_source"] == "ai_confirmed_rule"

    def test_clamp_upper_bounds_and_exact_whitelist(self):
        """P2-7：切深/切宽上界 + 精确键名白名单。"""
        assert KnowledgeAugmenter.clamp_parameter("depth_of_cut_mm", 1e9)[0] is None
        assert KnowledgeAugmenter.clamp_parameter("width_of_cut_mm", 1e9)[0] is None
        assert KnowledgeAugmenter.clamp_parameter("depth_of_cut_mm", 5.0) == (5.0, "")
        # 子串键不再放行（feed_override_pct 不按 mm/min 校验）
        assert KnowledgeAugmenter.clamp_parameter("feed_override_pct", 10)[0] is None

    def test_normalize_material(self):
        assert normalize_material("TC4钛合金") == "titanium"
        assert normalize_material("6061铝合金") == "aluminum"
        assert normalize_material("45#钢") == "steel"
        assert normalize_material("") == "general"


# ---------------------------------------------------------------------------
# CONDITIONAL 规划
# ---------------------------------------------------------------------------


def _orchestrator(tmp_path, **kwargs) -> AgentOrchestrator:
    return AgentOrchestrator(trace_log_dir=str(tmp_path / "traces"), memory=False, **kwargs)


class TestConditionalPlanner:
    async def test_features_provided_skips_dxf_parse(self, tmp_path):
        orch = _orchestrator(tmp_path)
        steps, meta = await orch._plan_steps_conditionally(
            "dxf_to_gcode", {"features": [{"type": "hole"}]}
        )
        names = [n for n, _ in steps]
        assert "dxf_parse" not in names
        assert meta["source"] == "static_features_provided"
        pu_cfg = dict(steps)["process_understanding"]
        assert pu_cfg["input_key"] == "input"

    async def test_llm_unavailable_falls_back(self, tmp_path, monkeypatch):
        from app.ai import llm_client as mod

        async def _boom():
            raise RuntimeError("no llm")

        monkeypatch.setattr(mod, "get_llm_client", _boom)
        orch = _orchestrator(tmp_path)
        steps, meta = await orch._plan_steps_conditionally("dxf_to_gcode", {})
        assert meta["source"] == "llm_fallback"
        assert len(steps) == 5  # 静态全表

    async def test_llm_plan_with_connectivity_closure(self, tmp_path, monkeypatch):
        from app.ai import llm_client as mod

        class _C:
            async def chat_completion(self, messages, max_tokens=256, temperature=0.1, model=None):
                return {
                    "content": json.dumps(
                        {
                            "include": [
                                "parameter_recommend",
                                "gcode_generate",
                                "validate_safety",
                            ],
                            "rationale": "描述已含工艺要求",
                        },
                        ensure_ascii=False,
                    )
                }

        async def _get():
            return _C()

        monkeypatch.setattr(mod, "get_llm_client", _get)
        orch = _orchestrator(tmp_path)
        steps, meta = await orch._plan_steps_conditionally("dxf_to_gcode", {"description": "钛合金件"})
        names = [n for n, _ in steps]
        # P2-3 修复后：process_understanding 无上游依赖（parameter_recommend
        # 直接从 DXF 桥接特征构建 part_description）——LLM 未选它即真正剔除；
        # dxf_parse 同理剔除。LLM 规划获得真实的裁剪自由度。
        assert meta["source"] == "llm"
        assert names == ["parameter_recommend", "gcode_generate", "validate_safety"]

    async def test_validate_safety_cannot_be_dropped(self, tmp_path, monkeypatch):
        from app.ai import llm_client as mod

        class _C:
            async def chat_completion(self, messages, max_tokens=256, temperature=0.1, model=None):
                return {"content": json.dumps({"include": ["dxf_parse"], "rationale": "乱来"})}

        async def _get():
            return _C()

        monkeypatch.setattr(mod, "get_llm_client", _get)
        orch = _orchestrator(tmp_path)
        steps, _ = await orch._plan_steps_conditionally("dxf_to_gcode", {})
        names = [n for n, _ in steps]
        assert "validate_safety" in names and "gcode_generate" in names


# ---------------------------------------------------------------------------
# FEED_OUT_OF_RANGE：校验产出 + 修复
# ---------------------------------------------------------------------------


class TestFeedOutOfRange:
    def test_validator_emits_feed_out_of_range_with_recommendation(self):
        validator = SafetyValidator()
        gcode = "G01 X10 F99999\nM30"
        report = validator.validate_gcode_text(gcode)
        assert "FEED_OUT_OF_RANGE" in report.error_codes
        issue = next(i for i in report.issues if i.code == "FEED_OUT_OF_RANGE")
        assert issue.recommended == 20000.0

    def test_g95_per_rev_feed_not_flagged(self):
        validator = SafetyValidator()
        gcode = "G95\nG01 X10 F0.2\nG94\nM30"
        report = validator.validate_gcode_text(gcode)
        assert "FEED_OUT_OF_RANGE" not in report.error_codes

    def test_in_range_feed_not_flagged(self):
        validator = SafetyValidator()
        report = validator.validate_gcode_text("G01 X10 F800\nM30")
        assert "FEED_OUT_OF_RANGE" not in report.error_codes

    async def test_repair_loop_clamps_feed_text(self, tmp_path):
        orch = _orchestrator(tmp_path)
        gcode = "G01 X10 F99999\nM30"
        gen_output = {"status": "success", "gcode": gcode}
        validate_result = StepResult(
            step_name="validate_safety",
            status=StepStatus.COMPLETED,
            output={
                "status": "validation_failed",
                "safety_valid": False,
                "safety_report": SafetyValidator().validate_gcode_text(gcode).to_dict(),
            },
        )
        result = PipelineResult(pipeline_id="p", success=False)
        steps = [("gcode_generate", {}), ("validate_safety", {})]
        context = {"gcode_generate": gen_output}
        escalate = await orch._maybe_repair(
            result=result,
            steps=steps,
            context=context,
            pipeline_id="p",
            validate_result=validate_result,
            repair_attempt=0,
        )
        assert escalate is False
        assert result.repair_history[0]["source"] == "rule"
        assert "F20000" in context["gcode_generate"]["gcode"]


# ---------------------------------------------------------------------------
# LLM 诊断修复分支（白名单外错误）
# ---------------------------------------------------------------------------


class TestLLMRepair:
    def _fabricate(self, tmp_path, llm_content: str | None, llm_exc: Exception | None = None):
        orch = _orchestrator(tmp_path)
        gen_output = {"status": "success", "gcode": "G01 X1 F100\nG01 X2 F100\nM30\n"}

        async def _llm_repair_stub(report, context):
            if llm_exc:
                return None  # 真实实现对异常内部捕获并返回 None
            return llm_content

        orch._llm_repair_gcode = _llm_repair_stub  # type: ignore[method-assign]
        report = {
            "issues": [
                {"code": "MYSTERY_ERROR", "severity": "error", "message": "未知错误", "context": {}}
            ]
        }
        validate_result = StepResult(
            step_name="validate_safety",
            status=StepStatus.COMPLETED,
            output={"safety_valid": False, "safety_report": report},
        )
        result = PipelineResult(pipeline_id="p", success=False)
        return orch, result, validate_result, {"gcode_generate": gen_output}

    async def test_llm_repair_applies_and_continues(self, tmp_path):
        orch, result, validate_result, context = self._fabricate(
            tmp_path, "G01 X1 F100\nG01 X2 F100\nM30\n(已修复)"
        )
        escalate = await orch._maybe_repair(
            result=result,
            steps=[("validate_safety", {})],
            context=context,
            pipeline_id="p",
            validate_result=validate_result,
            repair_attempt=0,
        )
        assert escalate is False
        assert result.repair_history[0]["source"] == "llm"
        assert "已修复" in context["gcode_generate"]["gcode"]

    async def test_llm_unavailable_escalates_to_human(self, tmp_path):
        orch, result, validate_result, context = self._fabricate(tmp_path, None, llm_exc=RuntimeError("x"))
        escalate = await orch._maybe_repair(
            result=result,
            steps=[("validate_safety", {})],
            context=context,
            pipeline_id="p",
            validate_result=validate_result,
            repair_attempt=0,
        )
        assert escalate is True
        assert validate_result.status == StepStatus.FAILED
        assert "需人工介入" in (validate_result.error or "")


# ---------------------------------------------------------------------------
# AI 标注回填
# ---------------------------------------------------------------------------


class TestAIMetadata:
    async def test_step_result_mirrors_decision_source(self, tmp_path):
        orch = _orchestrator(tmp_path)

        async def _ai_step(input_data, context):
            return {"status": "success", "value": 1, "decision_source": "ai", "ai_explanation": "因为"}

        orch.register_step("ai_step", _ai_step)
        result = await orch._execute_step("ai_step", {}, {}, "p")
        assert result.decision_source == "ai"
        assert result.ai_metadata.get("ai_explanation") == "因为"
        assert result.to_dict()["decision_source"] == "ai"
