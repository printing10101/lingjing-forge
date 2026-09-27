"""SHARP 工具基类与调用结构（M2.1）。

定义所有工具统一遵循的接口契约，确保 ReAct 循环可以透明地调用任意工具。

设计原则
--------
- **统一接口**：所有工具实现 `execute(ToolCall) -> ToolResult`，便于 LLM 引用
- **容错优先**：工具内部异常不抛出，统一封装为 `ToolResult(success=False, error=...)`
- **可观测**：每次调用记录耗时、工具名、参数摘要，用于证据链构建
- **同步/异步兼容**：KG 与文本工具为同步，LLM 工具为异步，统一用 async 包装
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# 调用结构


@dataclass
class ToolCall:
    """工具调用请求。

    Attributes
    ----------
    tool_name : str
        工具名，如 "kg.query_entity"
    arguments : dict
        调用参数，如 {"entity_id": "tool-endmill-6mm"}
    thought : str
        LLM 生成该调用时的思考（可选，用于证据链追溯）
    """

    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    thought: str = ""

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "thought": self.thought,
        }


@dataclass
class ToolResult:
    """工具调用结果。

    Attributes
    ----------
    tool_name : str
        工具名
    success : bool
        是否成功
    output : Any
        工具输出（dict / list / str）
    error : str
        失败时的错误描述
    elapsed_ms : float
        耗时（毫秒）
    metadata : dict
        额外元数据（如命中缓存、检索源、token 用量等）
    """

    tool_name: str
    success: bool
    output: Any = None
    error: str = ""
    elapsed_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "metadata": self.metadata,
        }


# 工具基类


class BaseTool(ABC):
    """所有 SHARP 工具的抽象基类。

    子类必须实现：
    - `name` 属性：工具唯一标识（与 strategic_planner 常量对齐）
    - `description` 属性：工具描述（用于 LLM prompt）
    - `arguments_schema` 属性：参数说明 dict
    - `_execute(arguments) -> Any` 方法：实际执行逻辑

    基类提供：
    - `execute(call: ToolCall) -> ToolResult`：统一入口，处理计时与异常捕获
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """工具唯一标识，如 'kg.query_entity'。"""

    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述，用于 LLM prompt 注入。"""

    @property
    @abstractmethod
    def arguments_schema(self) -> dict[str, str]:
        """参数说明 dict，key=参数名，value=参数描述。"""

    @abstractmethod
    async def _execute(self, arguments: dict[str, Any]) -> Any:
        """实际执行逻辑（子类实现）。

        Raises
        ------
        Exception
            任何异常都会被 `execute()` 捕获并转为 ToolResult.error
        """

    async def execute(self, call: ToolCall) -> ToolResult:
        """统一执行入口：计时 + 异常捕获 + 结果封装。"""
        start = time.perf_counter()
        try:
            output = await self._execute(call.arguments)
            elapsed_ms = (time.perf_counter() - start) * 1000
            return ToolResult(
                tool_name=self.name,
                success=True,
                output=output,
                elapsed_ms=elapsed_ms,
                metadata=self._build_metadata(call, output),
            )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return ToolResult(
                tool_name=self.name,
                success=False,
                error=f"{type(e).__name__}: {e}",
                elapsed_ms=elapsed_ms,
            )

    def _build_metadata(self, call: ToolCall, output: Any) -> dict[str, Any]:
        """构建结果元数据（子类可覆盖）。"""
        meta: dict[str, Any] = {}
        if isinstance(output, list):
            meta["result_count"] = len(output)
        elif isinstance(output, dict):
            if "count" in output:
                meta["result_count"] = output.get("count", 0)
            if "_cache_hit" in output:
                meta["cache_hit"] = output["_cache_hit"]
        return meta

    def to_prompt_text(self) -> str:
        """生成工具描述文本，用于 LLM prompt。"""
        args_doc = "\n".join(f"    - {k}: {v}" for k, v in self.arguments_schema.items())
        return f"- {self.name}: {self.description}\n  参数:\n{args_doc}"


__all__ = ["BaseTool", "ToolCall", "ToolResult"]
