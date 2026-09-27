"""SHARP KG 工具集（M2.2）。

封装 `KnowledgeGraphQueryAPI`，提供 4 个 KG 查询工具供 ReAct 循环调用。

所有工具均为同步实现，通过 `asyncio.to_thread` 包装为异步以兼容 ReAct 循环。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.sharp.tools.base import BaseTool

logger = logging.getLogger(__name__)


# 工具实现


class KGQueryEntityTool(BaseTool):
    """查询实体属性。

    工具名：`kg.query_entity`
    调用 `KnowledgeGraphQueryAPI.node(node_id)` 获取实体属性。
    """

    def __init__(self, query_api) -> None:
        """Args:
        query_api: `KnowledgeGraphQueryAPI` 实例
        """
        self._api = query_api

    @property
    def name(self) -> str:
        return "kg.query_entity"

    @property
    def description(self) -> str:
        return "查询知识图谱中某实体的属性（按实体 ID 精确取节点）"

    @property
    def arguments_schema(self) -> dict[str, str]:
        return {
            "entity_id": "实体 ID，如 'tool-endmill-6mm' / 'material-tc4'",
        }

    async def _execute(self, arguments: dict[str, Any]) -> Any:
        entity_id = arguments.get("entity_id")
        if not entity_id:
            raise ValueError("entity_id 参数不能为空")
        return await asyncio.to_thread(self._api.node, entity_id)


class KGQueryRelationTool(BaseTool):
    """查询关系是否存在。

    工具名：`kg.query_relation`
    调用 `KnowledgeGraphQueryAPI.edges(...)` 查询 head→tail 的关系是否存在。
    """

    def __init__(self, query_api) -> None:
        self._api = query_api

    @property
    def name(self) -> str:
        return "kg.query_relation"

    @property
    def description(self) -> str:
        return "查询知识图谱中两个实体之间的某类关系是否存在，返回关系详情与置信度"

    @property
    def arguments_schema(self) -> dict[str, str]:
        return {
            "source_id": "头实体 ID",
            "target_id": "尾实体 ID",
            "edge_type": "关系类型，如 'SUITABLE_FOR' / 'APPLIED_TO' / 'USED'",
        }

    async def _execute(self, arguments: dict[str, Any]) -> Any:
        source_id = arguments.get("source_id")
        target_id = arguments.get("target_id")
        edge_type = arguments.get("edge_type")
        if not all([source_id, target_id, edge_type]):
            raise ValueError("source_id / target_id / edge_type 均不能为空")
        edges = await asyncio.to_thread(
            self._api.edges,
            edge_type=edge_type,
            source_id=source_id,
            target_id=target_id,
        )
        return {
            "exists": len(edges) > 0,
            "edges": edges,
            "count": len(edges),
        }


class KGQueryNeighborsTool(BaseTool):
    """查询邻居（多跳）。

    工具名：`kg.query_neighbors`
    调用 `KnowledgeGraphQueryAPI.neighbors(node_id, max_hops)` 获取邻居。
    """

    def __init__(self, query_api) -> None:
        self._api = query_api

    @property
    def name(self) -> str:
        return "kg.query_neighbors"

    @property
    def description(self) -> str:
        return "查询某实体在知识图谱中的 N 跳邻居，用于发现关联实体与潜在证据"

    @property
    def arguments_schema(self) -> dict[str, str]:
        return {
            "node_id": "起始实体 ID",
            "max_hops": "最大跳数，默认 1（取值 1-3）",
            "limit": "返回数量上限，默认 50",
        }

    async def _execute(self, arguments: dict[str, Any]) -> Any:
        node_id = arguments.get("node_id")
        if not node_id:
            raise ValueError("node_id 参数不能为空")
        max_hops = int(arguments.get("max_hops", 1))
        max_hops = max(1, min(max_hops, 3))  # 限制 1-3 跳
        limit = int(arguments.get("limit", 50))
        neighbors = await asyncio.to_thread(self._api.neighbors, node_id, max_hops, limit)
        return {
            "node_id": node_id,
            "max_hops": max_hops,
            "neighbors": neighbors,
            "count": len(neighbors),
        }


class KGQueryPathTool(BaseTool):
    """查询两点间路径。

    工具名：`kg.query_path`
    基于 `KnowledgeGraphQueryAPI.neighbors` 双向 BFS 查找两点间路径。
    """

    def __init__(self, query_api) -> None:
        self._api = query_api

    @property
    def name(self) -> str:
        return "kg.query_path"

    @property
    def description(self) -> str:
        return "查询知识图谱中两个实体之间的路径，用于验证间接关联性"

    @property
    def arguments_schema(self) -> dict[str, str]:
        return {
            "source_id": "起点实体 ID",
            "target_id": "终点实体 ID",
            "max_hops": "最大搜索深度，默认 2（取值 1-3）",
        }

    async def _execute(self, arguments: dict[str, Any]) -> Any:
        source_id = arguments.get("source_id")
        target_id = arguments.get("target_id")
        if not all([source_id, target_id]):
            raise ValueError("source_id / target_id 不能为空")
        # all() 校验后收窄 Optional（mypy 不追踪 all 的收窄）
        assert source_id is not None and target_id is not None
        max_hops = int(arguments.get("max_hops", 2))
        max_hops = max(1, min(max_hops, 3))

        # 简化实现：从 source 出发 BFS，查找是否能到达 target
        # 复用 neighbors API，逐跳展开
        paths = await asyncio.to_thread(self._find_paths, source_id, target_id, max_hops)
        return {
            "source_id": source_id,
            "target_id": target_id,
            "paths": paths,
            "path_count": len(paths),
            "reachable": len(paths) > 0,
        }

    def _find_paths(self, source_id: str, target_id: str, max_hops: int) -> list[list[dict]]:
        """同步路径查找（在 to_thread 中执行）。"""
        if source_id == target_id:
            return [[{"node_id": source_id}]]

        # BFS 查找路径
        visited: set[str] = {source_id}
        queue: list[tuple[str, list[dict]]] = [(source_id, [{"node_id": source_id}])]
        paths: list[list[dict]] = []

        for _ in range(max_hops):
            next_queue: list[tuple[str, list[dict]]] = []
            for current_id, current_path in queue:
                try:
                    neighbors = self._api.neighbors(current_id, max_hops=1, limit=50)
                except Exception as e:
                    # KG 邻居查询失败不阻断路径搜索，但记录可定位的 warning 便于排查数据质量问题。
                    logger.warning(
                        "KG neighbors query failed for node %s: %s",
                        current_id,
                        e,
                    )
                    continue
                for nb in neighbors:
                    nb_id = nb["node_id"]
                    if nb_id in visited:
                        continue
                    new_path = current_path + [
                        {
                            "node_id": nb_id,
                            "via_edge": nb.get("via_edge"),
                            "direction": nb.get("direction"),
                        }
                    ]
                    if nb_id == target_id:
                        paths.append(new_path)
                        if len(paths) >= 5:  # 最多返回 5 条路径
                            return paths
                    else:
                        visited.add(nb_id)
                        next_queue.append((nb_id, new_path))
            queue = next_queue
            if not queue:
                break
        return paths


__all__ = [
    "KGQueryEntityTool",
    "KGQueryRelationTool",
    "KGQueryNeighborsTool",
    "KGQueryPathTool",
]
