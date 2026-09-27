"""自进化 REST 端点测试（自进化 M1）。

覆盖：统计、提案清单、循环触发（LLM 不可用降级）、提案审核
（promote/reject/rollback）、进化报告、404。
"""

from typing import Any

import pytest

import app.evolution.loop as loop_mod
from app.ai.prompts import ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID, reset_prompt_registry
from app.ai.prompts import persistence as pv
from app.evolution.loop import EvolutionEngine
from app.gcode_generation.failure_case_store import reset_failure_case_store

pytestmark = pytest.mark.api


class _FakeLLM:
    def __init__(self, content: str = "", exc: Exception | None = None):
        self._content = content
        self._exc = exc

    async def chat_completion(self, messages, max_tokens=2048, temperature=0.7, model=None):
        if self._exc:
            raise self._exc
        return {"content": self._content, "model": "fake", "finish_reason": "stop", "usage": {}}


_PROPOSAL_JSON = '{"template": "修复助手 v2 全文", "rationale": "测试提案"}'


@pytest.fixture()
def engine_env(tmp_path, monkeypatch):
    """隔离注册表/存储/报告目录，并注入可控引擎。"""
    monkeypatch.setenv("PROMPT_VERSIONS_STORE", str(tmp_path / "pv.json"))
    monkeypatch.setenv("FAILURE_CASES_DB", str(tmp_path / "fc.db"))
    monkeypatch.setattr(loop_mod, "_report_dir", lambda: tmp_path / "reports")
    reset_prompt_registry()
    reset_failure_case_store()
    engine = EvolutionEngine(llm_client=_FakeLLM(_PROPOSAL_JSON), report_dir=None)
    monkeypatch.setattr(loop_mod, "get_evolution_engine", lambda: engine)
    yield engine
    reset_prompt_registry()
    reset_failure_case_store()


def _propose(engine: EvolutionEngine) -> tuple[str, int]:
    import asyncio

    # 同步测试上下文：直接驱动 async propose（无运行中事件循环）
    proposal = asyncio.run(engine.propose())
    return proposal.prompt_id, proposal.version


class TestEvolutionAPI:
    def test_stats(self, client, engine_env):
        resp = client.get("/api/v1/evolution/stats")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert "stats" in data and "top_failure_classes" in data
        assert ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID in data["prompt_versions"]

    def test_loop_run_creates_proposal(self, client, engine_env, tmp_path):
        resp = client.post("/api/v1/evolution/loop/run", json={})
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["ok"] is True
        assert data["proposal"]["version"] == 2
        assert data["auto_promoted"] is False
        assert data["report_path"]
        assert len(list((tmp_path / "reports").glob("evolution_*.json"))) == 1

    def test_proposals_list(self, client, engine_env):
        _propose(engine_env)
        resp = client.get("/api/v1/evolution/proposals")
        assert resp.status_code == 200
        records = resp.json()["data"]["records"]
        assert len(records) == 1
        assert records[0]["status"] == "proposed"

    def test_promote_then_rollback(self, client, engine_env):
        prompt_id, version = _propose(engine_env)
        resp = client.post(f"/api/v1/evolution/proposals/{prompt_id}/{version}/promote")
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        # 测试引擎未注入 gate_fn → 走真实 replay 门控（种子 10 例，机制贯通）
        assert body["ok"] is True
        assert body["final_status"] in ("applied", "rolled_back")
        assert body["gate"] is not None

        if body["final_status"] == "applied":
            resp = client.post(f"/api/v1/evolution/proposals/{prompt_id}/{version}/rollback")
            assert resp.status_code == 200
            assert resp.json()["data"]["final_status"] == "rolled_back"

    def test_reject(self, client, engine_env):
        prompt_id, version = _propose(engine_env)
        resp = client.post(f"/api/v1/evolution/proposals/{prompt_id}/{version}/reject")
        assert resp.status_code == 200
        assert resp.json()["data"]["final_status"] == "rejected"

    def test_proposal_404(self, client, engine_env):
        resp = client.post(
            f"/api/v1/evolution/proposals/{ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID}/99/reject"
        )
        assert resp.status_code == 404

    def test_reports(self, client, engine_env, tmp_path):
        import asyncio

        asyncio.run(engine_env.run_loop())
        resp = client.get("/api/v1/evolution/reports")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["report_path"]
        assert data["files"]
