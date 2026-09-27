"""统一 AgentRuntime（自进化 M2）。

产品内 Agent 的统一运行时：制造工具集 + 通用 ReAct 循环 + 轨迹
JSONL 存储。GUI / agent_gateway / headless 三端同源入口。

组件：
- ``tools``：制造工具集（与 mcp_server 同源后端实现）
- ``loop``：通用 ReAct 循环（文本协议，提示词版本可追溯）
- ``trace``：轨迹 JSONL 存储（未来 RL rollout 数据源）
- ``runtime``：AgentRuntime 门面
"""

from app.agent.runtime.loop import AgentTrace, ReactLoop
from app.agent.runtime.runtime import AgentRuntime, get_agent_runtime, reset_agent_runtime
from app.agent.runtime.tools import Tool, ToolRegistry, create_default_registry
from app.agent.runtime.trace import (
    TrajectoryStore,
    get_trajectory_store,
    reset_trajectory_store,
)

__all__ = [
    "AgentTrace",
    "AgentRuntime",
    "ReactLoop",
    "Tool",
    "ToolRegistry",
    "TrajectoryStore",
    "create_default_registry",
    "get_agent_runtime",
    "get_trajectory_store",
    "reset_agent_runtime",
    "reset_trajectory_store",
]
