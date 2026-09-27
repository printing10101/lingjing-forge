"""SHARP Hybrid Knowledge Toolset（M2）。

对应论文 §4.4 "Hybrid Knowledge Toolset"，提供 ReAct 循环可调用的
8 个工具 + 1 个证据重排序器 + 工具注册中心。

工具分类
--------
- **KG 工具**（4 个，基于 `KnowledgeGraphQueryAPI`）：
  - `kg.query_entity`      查询实体属性
  - `kg.query_relation`    查询关系是否存在
  - `kg.query_neighbors`   查询邻居（多跳）
  - `kg.query_path`        查询两点间路径
- **文本工具**（2 个，基于 `RagRetrievalEngine`）：
  - `text.retrieve`        文档检索
  - `text.entity_lookup`   实体倒排索引查询
- **LLM 工具**（2 个，基于 `LLMRouter`）：
  - `llm.reason`           LLM 综合推理
  - `llm.extract`          LLM 实体/关系抽取
- **聚合工具**（1 个）：
  - `aggregate.evidence`   证据聚合（在 service 模块实现，此处仅注册）

导出
----
- `BaseTool` / `ToolCall` / `ToolResult`   工具基类与调用结构
- `ToolRegistry`                            工具注册中心
- `EvidenceReranker`                        证据重排序器
- 各具体工具类
"""

from __future__ import annotations

from app.sharp.tools.base import BaseTool, ToolCall, ToolResult
from app.sharp.tools.kg_tools import (
    KGQueryEntityTool,
    KGQueryRelationTool,
    KGQueryNeighborsTool,
    KGQueryPathTool,
)
from app.sharp.tools.text_tools import (
    TextRetrieveTool,
    TextEntityLookupTool,
)
from app.sharp.tools.llm_tools import (
    LLMReasonTool,
    LLMExtractTool,
)
from app.sharp.tools.reranker import EvidenceReranker
from app.sharp.tools.tool_registry import ToolRegistry

__all__ = [
    # 基类
    "BaseTool",
    "ToolCall",
    "ToolResult",
    # KG 工具
    "KGQueryEntityTool",
    "KGQueryRelationTool",
    "KGQueryNeighborsTool",
    "KGQueryPathTool",
    # 文本工具
    "TextRetrieveTool",
    "TextEntityLookupTool",
    # LLM 工具
    "LLMReasonTool",
    "LLMExtractTool",
    # 重排序与注册中心
    "EvidenceReranker",
    "ToolRegistry",
]
