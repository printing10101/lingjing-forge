"""演化循环引擎测试（自进化 M1）。

覆盖：collect_stats、propose（桩 LLM：成功/输出非法/LLM 不可用；候选
不进 live 注册表）、promote（门控注入：PASS 保持 applied / FAIL 自动
回滚 / inconclusive 保持 applied）、reject / rollback 状态机、run_loop
与报告落盘。
"""

import json
from typing import Any

import pytest

import app.evolution.loop as loop_mod
from app.ai.prompts import (
    ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID,
    get_prompt_registry,
    reset_prompt_registry,
)
from app.ai.prompts import persistence as pv
from app.evolution.loop import EvolutionEngine
from app.gcode_generation.failure_case_store import (
    FailureCase,
    FailureCaseStore,
    reset_failure_case_store,
)

pytestmark = pytest.mark.asyncio


class _FakeLLM:
    def __init__(self, content: str = "", exc: Exception | None = None):
        self._content = content
        self._exc = exc
        self.calls = 0

    async def chat_completion(self, messages, max_tokens=2048, temperature=0.7, model=None):
        self.calls += 1
        if self._exc:
            raise self._exc
        return {"content": self._content, "model": "fake", "finish_reason": "stop", "usage": {}}


def _proposal_payload(template: str = "修复助手 v2：只修列出的错误码，保持 M30。", rationale="补白名单说明"):
    return json.dumps({"template": template, "rationale": rationale}, ensure_ascii=False)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """注册表 / 版本存储 / 案例库 / 报告目录全部隔离。"""
    monkeypatch.setenv("PROMPT_VERSIONS_STORE", str(tmp_path / "pv.json"))
    monkeypatch.setenv("FAILURE_CASES_DB", str(tmp_path / "fc.db"))
    monkeypatch.setattr(loop_mod, "_report_dir", lambda: tmp_path / "reports")
    reset_prompt_registry()
    reset_failure_case_store()
    yield
    reset_prompt_registry()
    reset_failure_case_store()


def _engine(llm=None, gate_fn=None) -> EvolutionEngine:
    return EvolutionEngine(llm_client=llm, gate_fn=gate_fn, report_dir=None)


def _seed_failures(n: int = 2) -> None:
    store = FailureCaseStore()
    for i in range(n):
        store.record(
            FailureCase(
                task_id=f"t-{i}",
                outcome="failure",
                source="gcode_repair",
                error_codes=["LLM_INVALID_OUTPUT"],
                error_messages=["bad output"],
            )
        )


class TestCollectStats:
    async def test_stats_and_top_classes(self):
        _seed_failures(2)
        engine = _engine()
        collected = engine.collect_stats()
        assert collected["stats"]["failures"] == 2
        assert "source:gcode_repair" in collected["top_failure_classes"]
        assert "LLM_INVALID_OUTPUT" in collected["top_failure_classes"]

    async def test_stats_empty_store(self):
        collected = _engine().collect_stats()
        assert collected["stats"]["total"] == 0
        assert collected["top_failure_classes"] == []


class TestPropose:
    async def test_propose_success_stays_out_of_live_registry(self):
        _seed_failures(1)
        llm = _FakeLLM(_proposal_payload())
        engine = _engine(llm=llm)
        result = await engine.propose()
        assert result.ok is True
        assert result.prompt_id == ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID
        assert result.version == 2  # 默认 v1 → 候选 v2
        # 候选未进 live 注册表：get() 仍是默认 v1（提案制核心保证）
        assert get_prompt_registry().get(ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID).version == 1
        # 持久化为 proposed
        record = pv.find_record(result.prompt_id, result.version)
        assert record["status"] == "proposed"
        assert record["base_version"] == 1
        assert record["template"].startswith("修复助手 v2")

    async def test_propose_llm_unavailable(self):
        engine = _engine(llm=_FakeLLM(exc=RuntimeError("no llm")))
        result = await engine.propose()
        assert result.ok is False
        assert "LLM 不可用" in result.error
        assert pv.load_records() == []

    async def test_propose_invalid_output(self):
        engine = _engine(llm=_FakeLLM("抱歉，无法生成"))
        result = await engine.propose()
        assert result.ok is False
        assert "非法" in result.error

    async def test_propose_unknown_target(self):
        engine = _engine(llm=_FakeLLM(_proposal_payload()))
        result = await engine.propose(target_prompt_id="ghost.prompt")
        assert result.ok is False
        assert "未注册" in result.error

    async def test_propose_reproposes_same_version_after_reject(self):
        """被拒的候选版本号不消耗：重提占用同一候选版本号（v2）。"""
        llm = _FakeLLM(_proposal_payload())
        engine = _engine(llm=llm)
        r1 = await engine.propose()
        engine.reject(r1.prompt_id, r1.version)
        r2 = await engine.propose()
        assert r2.version == r1.version  # live 版本仍 v1 → 候选仍为 v2
        record = pv.find_record(r2.prompt_id, r2.version)
        assert record["status"] == "proposed"  # 覆盖写回 proposed


class TestPromoteGate:
    async def _make_proposed(self, engine: EvolutionEngine):
        return await engine.propose()

    async def test_gate_pass_keeps_applied(self):
        engine = _engine(llm=_FakeLLM(_proposal_payload()), gate_fn=lambda v: (True, {"reasons": ["ok"]}))
        proposal = await self._make_proposed(engine)
        result = engine.promote(proposal.prompt_id, proposal.version)
        assert result.ok and result.final_status == "applied"
        assert result.gate["passed"] is True
        # live 注册表已是 v2
        assert get_prompt_registry().get(ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID).version == 2
        assert pv.find_record(proposal.prompt_id, proposal.version)["status"] == "applied"

    async def test_gate_fail_auto_rolls_back(self):
        engine = _engine(
            llm=_FakeLLM(_proposal_payload()),
            gate_fn=lambda v: (False, {"reasons": ["一次通过率退化"]}),
        )
        proposal = await self._make_proposed(engine)
        result = engine.promote(proposal.prompt_id, proposal.version)
        assert result.ok and result.final_status == "rolled_back"
        # live 注册表已移除候选：回到默认 v1
        assert get_prompt_registry().get(ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID).version == 1
        record = pv.find_record(proposal.prompt_id, proposal.version)
        assert record["status"] == "rolled_back"
        assert record["gate"]["passed"] is False

    async def test_gate_inconclusive_keeps_applied(self):
        engine = _engine(
            llm=_FakeLLM(_proposal_payload()),
            gate_fn=lambda v: (True, {"inconclusive": True, "reasons": ["样本不足"]}),
        )
        proposal = await self._make_proposed(engine)
        result = engine.promote(proposal.prompt_id, proposal.version)
        assert result.final_status == "applied"
        assert get_prompt_registry().get(ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID).version == 2

    async def test_promote_requires_proposed_status(self):
        engine = _engine(gate_fn=lambda v: (True, {}))
        result = engine.promote(ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID, 99)
        assert result.ok is False
        assert "不存在" in result.error


class TestRejectRollback:
    async def _make_proposed(self, engine: EvolutionEngine):
        return await engine.propose()

    async def test_reject(self):
        engine = _engine(llm=_FakeLLM(_proposal_payload()))
        proposal = await self._make_proposed(engine)
        result = engine.reject(proposal.prompt_id, proposal.version)
        assert result.ok and result.final_status == "rejected"
        assert pv.find_record(proposal.prompt_id, proposal.version)["status"] == "rejected"
        # live 注册表从未受影响
        assert get_prompt_registry().get(ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID).version == 1

    async def test_rollback_applied(self):
        engine = _engine(
            llm=_FakeLLM(_proposal_payload()),
            gate_fn=lambda v: (True, {"reasons": ["ok"]}),
        )
        proposal = await self._make_proposed(engine)
        engine.promote(proposal.prompt_id, proposal.version)
        result = engine.rollback(proposal.prompt_id, proposal.version)
        assert result.ok and result.final_status == "rolled_back"
        assert get_prompt_registry().get(ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID).version == 1

    async def test_rollback_requires_applied_status(self):
        engine = _engine(llm=_FakeLLM(_proposal_payload()))
        proposal = await self._make_proposed(engine)
        result = engine.rollback(proposal.prompt_id, proposal.version)  # 仍为 proposed
        assert result.ok is False


class TestRunLoop:
    async def test_run_loop_with_proposal_and_report(self, tmp_path):
        _seed_failures(1)
        engine = _engine(llm=_FakeLLM(_proposal_payload()))
        result = await engine.run_loop()
        assert result.ok is True
        assert result.proposal is not None
        assert result.proposal["version"] == 2
        assert result.auto_promoted is False  # 提案制：不自动发布
        assert result.report_path
        report_file = next((tmp_path / "reports").glob("evolution_*.json"))
        report = json.loads(report_file.read_text(encoding="utf-8"))
        assert report["proposal"]["prompt_id"] == ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID

    async def test_run_loop_llm_down_still_reports(self, tmp_path):
        _seed_failures(1)
        engine = _engine(llm=_FakeLLM(exc=RuntimeError("no llm")))
        result = await engine.run_loop()
        # LLM 不可用不算循环失败：统计与报告仍有价值
        assert result.ok is True
        assert result.proposal is None
        assert result.report_path

    async def test_run_loop_auto_apply(self, tmp_path, monkeypatch):
        engine = EvolutionEngine(
            llm_client=_FakeLLM(_proposal_payload()),
            gate_fn=lambda v: (True, {"reasons": ["ok"]}),
            report_dir=None,
            auto_apply=True,
        )
        _seed_failures(1)
        result = await engine.run_loop()
        assert result.auto_promoted is True
        assert result.gate["passed"] is True
        assert get_prompt_registry().get(ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID).version == 2
