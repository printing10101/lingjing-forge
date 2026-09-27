"""统一 AgentRuntime 门面（自进化 M2）。

``AgentRuntime.run(task)`` 是产品内 Agent 的统一入口：
GUI / agent_gateway / headless 三端同源（当前交付 runtime 核 +
headless 形态；三端接线为 M2 收尾项）。每次运行产出
``AgentTrace``——任务、多轮工具调用轨迹、最终回答、所用提示词版本
与模型——默认追加到 JSONL 轨迹存储（可注入关闭）。

用法（headless）::

    from app.agent.runtime import get_agent_runtime

    trace = await get_agent_runtime().run(
        "评估这段 G 代码是否安全：O1000\\nG01 X10 F800\\nM30"
    )
    print(trace.status, trace.final_answer)
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.agent.runtime.loop import AgentTrace, ReactLoop
from app.agent.runtime.tools import ToolRegistry, create_default_registry
from app.agent.runtime.trace import TrajectoryStore, get_trajectory_store

logger = logging.getLogger(__name__)

__all__ = ["AgentRuntime", "get_agent_runtime", "reset_agent_runtime"]


class AgentRuntime:
    """Agent 运行时（工具注册表 + ReAct 循环 + 轨迹存储）。"""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        llm_client: Any = None,
        trace_store: TrajectoryStore | None = None,
        max_steps: int = 8,
        record_trace: bool = True,
    ) -> None:
        self._registry = registry or create_default_registry()
        self._llm_client = llm_client
        self._trace_store = trace_store
        self._record_trace = record_trace
        self._loop = ReactLoop(registry=self._registry, llm_client=llm_client, max_steps=max_steps)

    @property
    def registry(self) -> ToolRegistry:
        """工具注册表（三端共享同一工具面）。"""
        return self._registry

    @property
    def trace_store(self) -> TrajectoryStore:
        return self._trace_store or get_trajectory_store()

    async def run(self, task: str, *, record: bool | None = None) -> AgentTrace:
        """执行任务。record=False 可关闭本次轨迹落盘（测试/评估用）。"""
        trace = await self._loop.run(task)
        should_record = self._record_trace if record is None else record
        if should_record:
            self.trace_store.append(trace.to_dict())
        logger.info(
            "AgentRuntime 完成 trace_id=%s status=%s tool_calls=%d steps=%d duration=%.0fms",
            trace.trace_id,
            trace.status,
            trace.tool_calls,
            len(trace.steps),
            trace.duration_ms,
        )
        return trace


_runtime: AgentRuntime | None = None
_runtime_lock = threading.Lock()


def get_agent_runtime() -> AgentRuntime:
    """获取全局 AgentRuntime（线程安全懒加载）。"""
    global _runtime
    if _runtime is not None:
        return _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = AgentRuntime()
        return _runtime


def reset_agent_runtime() -> None:
    """重置全局单例（测试用）。"""
    global _runtime
    with _runtime_lock:
        _runtime = None
