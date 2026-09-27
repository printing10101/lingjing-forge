"""通用 ReAct 循环（自进化 M2 · 统一 AgentRuntime 的推理核）。

骨架借鉴 ``app/sharp/react`` 已验证的形态（工具注册表 / 终止条件 /
轨迹记录 / 错误熔断），泛化掉 Triple 领域耦合，提示词走 Prompt
Registry（版本可追溯，演化循环可迭代）。

文本协议（非 function-calling）：Thought/Action/Action Input/Final
Answer——与 Ollama 本地模型的指令遵循能力兼容，这也是 SHARP 在本
项目落地时验证过的选择。

失败口径（与 failure_recorder 一致）：LLM 应答但格式非法属模型质量
信号，入册 ``react_agent``；LLM 不可用/超时属环境信号，只体现在
trace.status，不入册。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.agent.failure_recorder import record_llm_invalid_output
from app.ai.prompts import AGENT_RUNTIME_REACT_SYSTEM_ID, get_prompt_registry
from app.agent.runtime.tools import ToolRegistry, create_default_registry

logger = logging.getLogger(__name__)

__all__ = ["AgentTrace", "ReactLoop"]

#: 连续格式非法熔断阈值（借鉴 SHARP error_circuit）
MAX_CONSECUTIVE_INVALID = 3
#: 单条轨迹观测截断（与 tools.MAX_OBSERVATION_CHARS 一致，防上下文膨胀）
_OBS_TRUNC = 1500


@dataclass
class AgentTrace:
    """一次 Agent 运行的完整轨迹（训练数据的基本单位）。"""

    trace_id: str
    task: str
    status: str  # completed | max_steps | invalid_output_circuit | llm_error
    steps: list[dict[str, Any]] = field(default_factory=list)
    final_answer: str = ""
    error: str = ""
    prompt_id: str = ""
    prompt_version: int = 0
    model: str = ""
    tool_calls: int = 0
    invalid_outputs: int = 0
    started_at: float = 0.0
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "task": self.task,
            "status": self.status,
            "steps": list(self.steps),
            "final_answer": self.final_answer,
            "error": self.error,
            "prompt_id": self.prompt_id,
            "prompt_version": self.prompt_version,
            "model": self.model,
            "tool_calls": self.tool_calls,
            "invalid_outputs": self.invalid_outputs,
            "started_at": self.started_at,
            "duration_ms": round(self.duration_ms, 1),
        }


class ReactLoop:
    """LLM + 工具的通用 ReAct 循环（全部依赖注入，便于测试）。"""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        llm_client: Any = None,
        max_steps: int = 8,
        max_tokens: int = 1024,
    ) -> None:
        self._registry = registry or create_default_registry()
        self._llm_client = llm_client
        self.max_steps = max(1, int(max_steps))
        self.max_tokens = max_tokens

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    async def run(self, task: str) -> AgentTrace:
        """执行一次 ReAct 循环。任何实现层异常都收敛为带 status 的轨迹。"""
        registry = get_prompt_registry()
        system_text, template = registry.render(
            AGENT_RUNTIME_REACT_SYSTEM_ID, tools_text=self._registry.to_prompt_text()
        )
        trace = AgentTrace(
            trace_id=f"art_{uuid.uuid4().hex[:12]}",
            task=task,
            status="running",
            prompt_id=template.prompt_id,
            prompt_version=template.version,
            started_at=time.time(),
        )
        try:
            await self._loop(task, system_text, trace)
        except Exception as e:  # noqa: BLE001 - 兜底：循环自身异常不算工具失败
            trace.status = "llm_error"
            trace.error = f"{type(e).__name__}: {e}"
            logger.error("ReAct 循环异常 trace_id=%s: %s", trace.trace_id, e, exc_info=True)
        trace.duration_ms = (time.time() - trace.started_at) * 1000
        return trace

    # ------------------------------------------------------------------
    # 循环体
    # ------------------------------------------------------------------

    async def _loop(self, task: str, system_text: str, trace: AgentTrace) -> None:
        client = await self._get_llm_client()
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_text},
            {"role": "user", "content": task},
        ]
        consecutive_invalid = 0

        for step_no in range(1, self.max_steps + 1):
            try:
                response = await client.chat_completion(messages, max_tokens=self.max_tokens, temperature=0.2)
            except Exception as e:  # LLM 不可用：环境信号，不入册
                trace.status = "llm_error"
                trace.error = f"{type(e).__name__}: {e}"
                logger.info("ReAct LLM 不可用 trace_id=%s: %s", trace.trace_id, type(e).__name__)
                return

            raw = str(response.get("content", "") or "")
            model = str(response.get("model", "") or "")
            if model and not trace.model:
                trace.model = model

            parsed = self._parse(raw)

            if parsed["kind"] == "final":
                trace.final_answer = parsed["answer"]
                trace.status = "completed"
                trace.steps.append({"step": step_no, "thought": parsed["thought"], "final_answer": parsed["answer"]})
                return

            if parsed["kind"] == "action":
                consecutive_invalid = 0
                observation, is_error = await self._invoke_tool(parsed["action"], parsed["action_input"])
                trace.tool_calls += 1
                trace.steps.append(
                    {
                        "step": step_no,
                        "thought": parsed["thought"],
                        "action": parsed["action"],
                        "action_input": parsed["action_input"],
                        "observation": observation[:_OBS_TRUNC],
                        "observation_error": is_error,
                    }
                )
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": f"Observation: {observation[:_OBS_TRUNC]}\n请继续。"})
                continue

            # 格式非法：模型质量信号 → 入册（react_agent），并给一次纠正机会
            consecutive_invalid += 1
            trace.invalid_outputs += 1
            trace.steps.append({"step": step_no, "invalid_raw": raw[:_OBS_TRUNC], "consecutive": consecutive_invalid})
            record_llm_invalid_output(
                task_id=trace.trace_id,
                source="react_agent",
                raw_output=raw,
            )
            if consecutive_invalid >= MAX_CONSECUTIVE_INVALID:
                trace.status = "invalid_output_circuit"
                trace.error = f"连续 {consecutive_invalid} 轮输出格式非法，熔断"
                return
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": "你的上一轮回复不符合格式要求（需要 Thought/Action/Action Input 或 "
                    "Thought/Final Answer）。请严格按系统提示的格式重新回复。",
                }
            )

        trace.status = "max_steps"
        trace.error = f"步数预算（{self.max_steps}）耗尽仍未给出 Final Answer"

    # ------------------------------------------------------------------
    # 解析与工具调用
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(raw: str) -> dict[str, Any]:
        """解析一轮 LLM 输出 → final / action / invalid。"""
        text = raw.strip()
        final_match = re.search(r"Final Answer\s*[:：]\s*(.+)", text, re.DOTALL)
        if final_match:
            thought = ReactLoop._extract_thought(text[: final_match.start()])
            return {"kind": "final", "answer": final_match.group(1).strip(), "thought": thought}

        action_match = re.search(r"Action\s*[:：]\s*(\S+)", text)
        if action_match:
            thought = ReactLoop._extract_thought(text[: action_match.start()])
            input_match = re.search(r"Action Input\s*[:：]\s*(\{.*)", text, re.DOTALL)
            action_input = ReactLoop._extract_json(input_match.group(1)) if input_match else None
            if isinstance(action_input, dict):
                return {
                    "kind": "action",
                    "action": action_match.group(1),
                    "action_input": action_input,
                    "thought": thought,
                }
            # Action Input 缺失或非 JSON → 非法
            return {"kind": "invalid", "reason": "Action Input 缺失或不是 JSON 对象"}

        return {"kind": "invalid", "reason": "未找到 Final Answer 或 Action"}

    @staticmethod
    def _extract_thought(prefix: str) -> str:
        match = re.search(r"Thought\s*[:：]\s*(.+)", prefix)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _extract_json(text: str) -> Any:
        """从文本提取第一个 JSON 对象（容忍围栏/前后缀）。"""
        if not text:
            return None
        cleaned = re.sub(r"```(?:json)?", "", text).strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None

    async def _invoke_tool(self, name: str, action_input: dict[str, Any]) -> tuple[str, bool]:
        """调用工具，返回 (观测文本, 是否为错误观测)。未知工具也是观测。"""
        tool = self._registry.get(name)
        if tool is None:
            available = ", ".join(self._registry.names())
            return (
                json.dumps(
                    {"error": f"未知工具: {name}", "available_tools": available},
                    ensure_ascii=False,
                ),
                True,
            )
        # 工具实现为同步 CPU 函数 → 放线程，不阻塞事件循环
        observation = await asyncio.to_thread(tool.run, **action_input)
        return observation, '"error"' in observation[:64]

    async def _get_llm_client(self) -> Any:
        if self._llm_client is not None:
            return self._llm_client
        from app.ai.llm_client import get_llm_client

        return await get_llm_client()
