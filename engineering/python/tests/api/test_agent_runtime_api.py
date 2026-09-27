"""AgentRuntime REST 端点测试（自进化 M2 三端接线）。

覆盖：POST /agent-runtime/run 全链路（HTTP → 全局单例 → ReAct →
轨迹落盘）、LLM 不可用降级（200 + status=llm_error）、record=False、
GET /agent-runtime/traces、参数校验。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import app.agent.runtime.runtime as runtime_mod
from app.agent.runtime import AgentRuntime, Tool, ToolRegistry
from app.agent.runtime.trace import TrajectoryStore

pytestmark = pytest.mark.api


class _ScriptedLLM:
    def __init__(self, responses: list[str], exc: Exception | None = None):
        self._responses = list(responses)
        self._exc = exc

    async def chat_completion(self, messages, max_tokens=1024, temperature=0.2, model=None):
        if self._exc:
            raise self._exc
        content = self._responses.pop(0) if self._responses else "Final Answer: done"
        return {"content": content, "model": "fake", "finish_reason": "stop", "usage": {}}


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(Tool(name="echo_tool", description="回显", params={"text": "文本"}, fn=lambda text="": {"echo": text}))
    return reg


@pytest.fixture()
def patch_runtime(tmp_path, monkeypatch):
    """把全局 AgentRuntime 单例替换为脚本化 LLM 驱动的真实实例。"""

    def _install(responses: list[str] | None = None, exc: Exception | None = None) -> TrajectoryStore:
        store = TrajectoryStore(tmp_path / "traces.jsonl")
        runtime = AgentRuntime(
            registry=_registry(),
            llm_client=_ScriptedLLM(responses or [], exc=exc),
            trace_store=store,
        )
        monkeypatch.setattr(runtime_mod, "_runtime", runtime)
        return store

    return _install


def _run(client, payload: dict[str, Any]):
    return client.post("/api/agent/v1/agent-runtime/run", json=payload)


class TestAgentRuntimeAPI:
    def test_run_full_chain(self, client, patch_runtime, tmp_path):
        store = patch_runtime(
            [
                'Thought: 需要回显\nAction: echo_tool\nAction Input: {"text": "灵境"}',
                "Thought: 完成\nFinal Answer: 回显结果：灵境",
            ]
        )
        resp = _run(client, {"task": "回显'灵境'"})
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["status"] == "completed"
        assert data["final_answer"] == "回显结果：灵境"
        assert data["tool_calls"] == 1
        assert data["prompt_id"] == "agent_runtime.react.system"
        assert data["steps"][0]["action"] == "echo_tool"
        # 轨迹已落盘
        lines = (tmp_path / "traces.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["task"] == "回显'灵境'"
        assert store.list_recent()[0]["status"] == "completed"

    def test_run_llm_unavailable_returns_error_trace_200(self, client, patch_runtime):
        """LLM 不可用是环境信号：HTTP 200 + status=llm_error（轨迹即产物）。"""
        patch_runtime(exc=RuntimeError("no llm"))
        resp = _run(client, {"task": "任意任务"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "llm_error"
        assert "no llm" in data["error"]
        assert data["final_answer"] == ""

    def test_run_record_false_skips_persistence(self, client, patch_runtime, tmp_path):
        patch_runtime(["Final Answer: ok"])
        resp = _run(client, {"task": "任务", "record": False})
        assert resp.status_code == 200
        assert not (tmp_path / "traces.jsonl").exists()

    def test_run_validation_empty_task_rejected(self, client, patch_runtime):
        patch_runtime(["Final Answer: ok"])
        resp = _run(client, {"task": ""})
        assert resp.status_code == 422

    def test_traces_read(self, client, patch_runtime):
        patch_runtime(["Final Answer: ok"])
        _run(client, {"task": "任务甲"})
        _run(client, {"task": "任务乙"})
        resp = client.get("/api/agent/v1/agent-runtime/traces", params={"limit": 10})
        assert resp.status_code == 200
        traces = resp.json()["data"]
        assert [t["task"] for t in traces] == ["任务乙", "任务甲"]

    def test_traces_empty(self, client, patch_runtime):
        patch_runtime([])
        resp = client.get("/api/agent/v1/agent-runtime/traces")
        assert resp.status_code == 200
        assert resp.json()["data"] == []
