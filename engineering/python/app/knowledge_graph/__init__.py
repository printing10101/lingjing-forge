"""知识图谱模块。

包级 re-export 改为惰性（PEP 562）：``query_api`` 依赖 networkx、``graph_store``
依赖 SQLAlchemy，而本体扩展提案（``ontology_proposal``）刻意零第三方依赖，需要能在
最小环境（MCP 沙箱 / 精简镜像 / 单测）中单独导入。若在此处直接 import 两个重依赖
模块，``import app.knowledge_graph.ontology_proposal`` 会因为包初始化而失败。

用法不变：``from app.knowledge_graph import KnowledgeGraphQueryAPI`` 仍然可用。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["KnowledgeGraphQueryAPI", "GraphStore"]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "KnowledgeGraphQueryAPI": ("app.knowledge_graph.query_api", "KnowledgeGraphQueryAPI"),
    "GraphStore": ("app.knowledge_graph.graph_store", "GraphStore"),
}


def __getattr__(name: str) -> Any:
    """按需导入重依赖导出项，避免包初始化即拖入 networkx / SQLAlchemy。"""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_path, attr = target
    value = getattr(import_module(module_path), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
