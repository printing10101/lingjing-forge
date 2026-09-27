"""ReAct 循环测试（自进化 M2 · AgentRuntime loop）。

覆盖：脚本化 LLM 的完成路径（工具调用→Final Answer）、未知工具观测、
连续非法输出熔断（含 react_agent 失败入册）、步数预算耗尽、LLM 异常
（环境信号不入册）、轨迹元数据（提示词版本/模型/耗时）。
"""

import json
from typing import Any

import pytest

import app.agent.failure_recorder as fr_mod
from app.agent.runtime.loop import MAX_CONSECUTIVE_INVALID, ReactLoop
from app.agent.runtime.tools import Tool, ToolRegistry
from app.gcode_generation.failure_case_store import reset_failure_case_store

pytestmark = pytest.mark.asyncio


class _ScriptedLLM:
    """按脚本顺序回放响应的 LLM 桩（记录收到的消息供断言）。"""

    def __init__(self, responses: list[str], exc: Exception | None = None):
        self._responses = list(responses)
        self._exc = exc
        self.calls = 0
        self.messages_history: list[list[dict[str, str]]] = []

    async def chat_completion(self, messages, max_tokens=1024, temperature=0.2, model=None):
        self.calls += 1
        self.messages_history.append([dict(m) for m in messages])
        if self._exc:
            raise self._exc
        content = self._responses.pop(0) if self._responses else "Final Answer: 完成"
        return {"content": content, "model": "scripted-fake", "finish_reason": "stop", "usage": {}}


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        Tool(
            name="echo_tool",
            description="回显输入",
            params={"text": "要回显的文本"},
            fn=lambda text="": {"echo": text},
        )
    )
    reg.register(
        Tool(
            name="boom_tool",
            description="总是抛异常",
            params={},
            fn=lambda: (_ for _ in ()).throw(RuntimeError("tool down")),
        )
    )
    return reg


def _action_raw(tool: str, action_input: str, thought: str = "分析") -> str:
    return f"Thought: {thought}\nAction: {tool}\nAction Input: {action_input}"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("FAILURE_CASES_DB", str(tmp_path / "fc.db"))
    reset_failure_case_store()
    yield
    reset_failure_case_store()


class TestHappyPath:
    async def test_tool_then_final(self):
        llm = _ScriptedLLM(
            [
                _action_raw("echo_tool", '{"text": "你好"}'),
                "Thought: 已得到结果\nFinal Answer: 回显结果是：你好",
            ]
        )
        trace = await ReactLoop(registry=_registry(), llm_client=llm, max_steps=5).run("回显'你好'")
        assert trace.status == "completed"
        assert trace.final_answer == "回显结果是：你好"
        assert trace.tool_calls == 1
        assert trace.steps[0]["action"] == "echo_tool"
        assert json.loads(trace.steps[0]["observation"])["echo"] == "你好"
        # 元数据：提示词版本 + 模型
        assert trace.prompt_id == "agent_runtime.react.system"
        assert trace.prompt_version >= 1
        assert trace.model == "scripted-fake"
        # 观测以 Observation 消息回注对话
        second_call = llm.messages_history[1]
        assert any("Observation" in m["content"] for m in second_call if m["role"] == "user")

    async def test_tools_text_rendered_into_system_prompt(self):
        llm = _ScriptedLLM(["Final Answer: ok"])
        await ReactLoop(registry=_registry(), llm_client=llm).run("任务")
        system_msg = llm.messages_history[0][0]
        assert system_msg["role"] == "system"
        assert "echo_tool" in system_msg["content"]
        assert "boom_tool" in system_msg["content"]


class TestToolErrors:
    async def test_unknown_tool_is_observation_not_crash(self):
        llm = _ScriptedLLM(
            [
                _action_raw("no_such_tool", "{}"),
                "Final Answer: 完成",
            ]
        )
        trace = await ReactLoop(registry=_registry(), llm_client=llm).run("任务")
        assert trace.status == "completed"
        step = trace.steps[0]
        assert step["observation_error"] is True
        assert "no_such_tool" in step["observation"]

    async def test_tool_exception_degrades_to_error_observation(self):
        llm = _ScriptedLLM(
            [
                _action_raw("boom_tool", "{}"),
                "Final Answer: 工具挂了但我知道了",
            ]
        )
        trace = await ReactLoop(registry=_registry(), llm_client=llm).run("任务")
        assert trace.status == "completed"
        assert '"error"' in trace.steps[0]["observation"]


class TestInvalidOutputCircuit:
    async def test_consecutive_invalid_records_and_circuits(self, monkeypatch):
        recorded = []

        class _FakeStore:
            def record(self, case):
                recorded.append(case)
                return case.case_id

        monkeypatch.setattr(fr_mod, "get_failure_case_store", lambda: _FakeStore())
        llm = _ScriptedLLM(["随便聊聊天", "还是不按格式", "依旧不按格式"] * 2)
        trace = await ReactLoop(registry=_registry(), llm_client=llm, max_steps=10).run("任务")
        assert trace.status == "invalid_output_circuit"
        assert trace.invalid_outputs == MAX_CONSECUTIVE_INVALID
        # 模型质量信号入册 react_agent
        assert len(recorded) == MAX_CONSECUTIVE_INVALID
        assert recorded[0].source == "react_agent"
        assert recorded[0].task_id == trace.trace_id

    async def test_invalid_then_recover_completes(self, monkeypatch):
        recorded = []

        class _FakeStore:
            def record(self, case):
                recorded.append(case)
                return case.case_id

        monkeypatch.setattr(fr_mod, "get_failure_case_store", lambda: _FakeStore())
        llm = _ScriptedLLM(
            [
                "我没有按格式来",
                "Final Answer: 恢复后完成",
            ]
        )
        trace = await ReactLoop(registry=_registry(), llm_client=llm).run("任务")
        assert trace.status == "completed"
        assert trace.invalid_outputs == 1
        assert len(recorded) == 1

    async def test_action_without_json_input_is_invalid(self):
        llm = _ScriptedLLM(["Action: echo_tool\nAction Input: 不是JSON"] * 4)
        trace = await ReactLoop(registry=_registry(), llm_client=llm, max_steps=10).run("任务")
        assert trace.status == "invalid_output_circuit"


class TestBudgetAndErrors:
    async def test_max_steps_exhausted(self):
        llm = _ScriptedLLM([_action_raw("echo_tool", '{"text": "x"}')] * 10)
        trace = await ReactLoop(registry=_registry(), llm_client=llm, max_steps=3).run("任务")
        assert trace.status == "max_steps"
        assert trace.tool_calls == 3

    async def test_llm_exception_is_environment_signal(self, monkeypatch):
        recorded = []

        class _FakeStore:
            def record(self, case):
                recorded.append(case)
                return case.case_id

        monkeypatch.setattr(fr_mod, "get_failure_case_store", lambda: _FakeStore())
        llm = _ScriptedLLM([], exc=RuntimeError("no llm"))
        trace = await ReactLoop(registry=_registry(), llm_client=llm).run("任务")
        assert trace.status == "llm_error"
        assert "no llm" in trace.error
        assert recorded == []  # 环境信号不入册（口径硬约束）

    async def test_final_answer_format_tolerates_multiline(self):
        llm = _ScriptedLLM(["Thought: 好\nFinal Answer: 第一行\n第二行"])
        trace = await ReactLoop(registry=_registry(), llm_client=llm).run("任务")
        assert trace.status == "completed"
        assert "第二行" in trace.final_answer
