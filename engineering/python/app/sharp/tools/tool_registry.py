"""SHARP 工具注册中心（M2.6）。

管理 ReAct 循环可调用的工具集合，按名称查找工具实例。

职责
----
- 注册工具实例（`register(tool)`）
- 按名称查找（`get(name) -> BaseTool`）
- 列出全部工具（`list_tools()`）
- 生成 prompt 文本（`to_prompt_text()`）
- 工厂方法（`create_default_registry(...)`）：根据依赖自动装配默认工具集

设计原则
--------
- **单一职责**：仅管理工具实例，不负责创建（创建由工厂方法完成）
- **依赖注入**：工具实例由外部传入依赖（KG/RAG/LLM），便于测试与消融
- **消融支持**：工厂方法支持 `ablation_mode`，按模式跳过特定工具
"""

from __future__ import annotations

import logging


from app.sharp.tools.base import BaseTool
from app.sharp.tools.kg_tools import (
    KGQueryEntityTool,
    KGQueryNeighborsTool,
    KGQueryPathTool,
    KGQueryRelationTool,
)
from app.sharp.tools.text_tools import (
    TextEntityLookupTool,
    TextRetrieveTool,
)

logger = logging.getLogger(__name__)


# 工具注册中心


class ToolRegistry:
    """工具注册中心。

    使用方式：

        registry = ToolRegistry()
        registry.register(KGQueryEntityTool(query_api))
        tool = registry.get("kg.query_entity")
        result = await tool.execute(ToolCall(...))

    或使用工厂方法：

        registry = ToolRegistry.create_default_registry(
            query_api=..., rag_engine=..., llm_router=...,
        )
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    # 注册与查找

    def register(self, tool: BaseTool) -> None:
        """注册工具实例。重复注册将覆盖。"""
        name = tool.name
        if name in self._tools:
            logger.warning("工具 %s 已存在，将被覆盖", name)
        self._tools[name] = tool

    def unregister(self, name: str) -> bool:
        """注销工具。返回是否成功。"""
        return self._tools.pop(name, None) is not None

    def get(self, name: str) -> BaseTool | None:
        """按名称查找工具。"""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """是否存在某工具。"""
        return name in self._tools

    def list_tools(self) -> list[str]:
        """列出全部工具名。"""
        return sorted(self._tools.keys())

    @property
    def size(self) -> int:
        return len(self._tools)

    # Prompt 生成

    def to_prompt_text(self, tool_names: list[str] | None = None) -> str:
        """生成工具集描述文本，用于 LLM prompt 注入。

        Args:
            tool_names: 仅包含指定工具（默认全部）
        """
        if tool_names is None:
            tool_names = self.list_tools()
        lines = [self._tools[n].to_prompt_text() for n in tool_names if n in self._tools]
        return "\n".join(lines)

    # 工厂方法

    @classmethod
    def create_default_registry(
        cls,
        query_api=None,
        rag_engine=None,
        llm_router=None,
        ablation_mode: str | None = None,
    ) -> "ToolRegistry":
        """根据依赖自动装配默认工具集。

        Args:
            query_api: `KnowledgeGraphQueryAPI` 实例（None 则跳过 KG 工具）
            rag_engine: `RagRetrievalEngine` 实例（None 则跳过文本工具）
            llm_router: `LLMRouter` 实例（None 则跳过 LLM 工具）
            ablation_mode: 消融模式
                - "no_toolset": 仅注册 LLM 推理工具
                - 其他模式：注册全部可用工具

        Returns:
            ToolRegistry 实例
        """
        registry = cls()

        # 消融模式 no_toolset：仅注册 LLM 推理
        if ablation_mode == "no_toolset":
            if llm_router is not None:
                from app.sharp.tools.llm_tools import LLMReasonTool

                registry.register(LLMReasonTool(llm_router))
            else:
                logger.warning("no_toolset 模式但未提供 llm_router，注册空工具集")
            return registry

        # 注册 KG 工具
        if query_api is not None:
            registry.register(KGQueryEntityTool(query_api))
            registry.register(KGQueryRelationTool(query_api))
            registry.register(KGQueryNeighborsTool(query_api))
            registry.register(KGQueryPathTool(query_api))
        else:
            logger.debug("未提供 query_api，跳过 KG 工具注册")

        # 注册文本工具
        if rag_engine is not None:
            registry.register(TextRetrieveTool(rag_engine))
            registry.register(TextEntityLookupTool(rag_engine))
        else:
            logger.debug("未提供 rag_engine，跳过文本工具注册")

        # 注册 LLM 工具
        if llm_router is not None:
            from app.sharp.tools.llm_tools import (
                LLMExtractTool,
                LLMReasonTool,
            )

            registry.register(LLMReasonTool(llm_router))
            registry.register(LLMExtractTool(llm_router))
        else:
            logger.debug("未提供 llm_router，跳过 LLM 工具注册")

        return registry


__all__ = ["ToolRegistry"]
