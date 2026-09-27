"""AgentRuntime 门面测试（自进化 M2）。

覆盖：headless 端到端（任务→工具调用→Final Answer→轨迹落盘）、
record=False 不落盘、轨迹读取、全局单例。
"""

import json
from typing import Any

import pytest

from app.agent.runtime import (
    AgentRuntime,
    Tool,
    ToolRegistry,
    get_agent_runtime,
    reset_agent_runtime,
)
from app.agent.runtime.trace import TrajectoryStore, reset_trajectory_store

pytestmark = pytest.mark.asyncio


class _ScriptedLLM:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)

    async def chat_completion(self, messages, max_tokens=1024, temperature=0.2, model=None):
        content = self._responses.pop(0) if self._responses else "Final Answer: done"
        return {"content": content, "model": "fake", "finish_reason": "stop", "usage": {}}


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(Tool(name="echo_tool", description="回显", params={"text": "文本"}, fn=lambda text="": {"echo": text}))
    return reg


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    trace_path = tmp_path / "traces" / "agent_traces.jsonl"
    monkeypatch.setenv("LNN_AGENT_RUNTIME_TRACE", str(trace_path))
    reset_trajectory_store()
    reset_agent_runtime()
    yield trace_path
    reset_trajectory_store()
    reset_agent_runtime()


class TestAgentRuntime:
    async def test_end_to_end_headless(self, _isolated):
        """验收基线：一行 headless 跑完 任务→多轮工具→最终回答→轨迹落盘。"""
        runtime = AgentRuntime(
            registry=_registry(),
            llm_client=_ScriptedLLM(
                [
                    'Thought: 需要回显\nAction: echo_tool\nAction Input: {"text": "灵境"}',
                    "Thought: 完成\nFinal Answer: 回显结果：灵境",
                ]
            ),
            trace_store=TrajectoryStore(_isolated),
        )
        trace = await runtime.run("回显'灵境'")
        assert trace.status == "completed"
        assert trace.final_answer == "回显结果：灵境"
        assert trace.tool_calls == 1
        # 轨迹落盘：JSONL 一行，含任务与提示词版本
        lines = _isolated.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        stored = json.loads(lines[0])
        assert stored["task"] == "回显'灵境'"
        assert stored["prompt_id"] == "agent_runtime.react.system"
        assert stored["steps"][0]["action"] == "echo_tool"

    async def test_record_false_skips_persistence(self, _isolated):
        runtime = AgentRuntime(
            registry=_registry(),
            llm_client=_ScriptedLLM(["Final Answer: ok"]),
            trace_store=TrajectoryStore(_isolated),
        )
        await runtime.run("任务", record=False)
        assert not _isolated.exists()

    async def test_list_recent(self, _isolated):
        store = TrajectoryStore(_isolated)
        runtime = AgentRuntime(registry=_registry(), llm_client=_ScriptedLLM(["Final Answer: a"]), trace_store=store)
        await runtime.run("任务一")
        await runtime.run("任务二")
        recent = store.list_recent()
        assert [t["task"] for t in recent] == ["任务二", "任务一"]

    async def test_global_singleton(self):
        runtime_a = get_agent_runtime()
        runtime_b = get_agent_runtime()
        assert runtime_a is runtime_b
        assert set(runtime_a.registry.names()) >= {"evaluate_gcode", "safety_validate_gcode"}

    async def test_corrupt_trace_lines_skipped(self, _isolated):
        _isolated.parent.mkdir(parents=True, exist_ok=True)
        _isolated.write_text("{broken\n", encoding="utf-8")
        store = TrajectoryStore(_isolated)
        runtime = AgentRuntime(registry=_registry(), llm_client=_ScriptedLLM(["Final Answer: x"]), trace_store=store)
        await runtime.run("任务")
        recent = store.list_recent()
        assert len(recent) == 1  # 坏行跳过，好行可读
