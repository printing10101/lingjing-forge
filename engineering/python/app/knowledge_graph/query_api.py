"""知识图谱查询 API（稳定契约层）。

为什么单独一个查询层？
    - 业务方不应该直接 ``GraphStore``，那是底层 NetworkX 实现细节
    - 查询 API 把"找刀具-材料-工艺之间的关联"这种业务语义收拢在一处
    - 未来无论是换底层（图数据库 Neo4j / NebulaGraph）还是加缓存，
      都不会破坏调用方

设计：
    - :class:`KnowledgeGraphQueryAPI` 接受一个 :class:`GraphStore` 实例
    - 业务查询全部走 query() 入口，按 query_type 分发
    - 返回结构与 GraphStore 保持一致（dict 字段稳定）
    - 不会修改图，只读
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .graph_store import GraphStore

logger = logging.getLogger(__name__)


# 工具


# 通配符到正则的转换
def _wildcard_to_regex(pattern: str) -> str:
    """把 SQL LIKE 风格通配符（``%`` / ``_``）转换成正则。"""
    out = []
    for ch in pattern:
        if ch == "%":
            out.append(".*")
        elif ch == "_":
            out.append(".")
        else:
            out.append(re.escape(ch))
    return "^" + "".join(out) + "$"


def _match(value: Any, pattern: str | None) -> bool:
    if pattern is None:
        return True
    if value is None:
        return False
    return re.match(_wildcard_to_regex(pattern), str(value)) is not None


# 知识图谱查询 API


class KnowledgeGraphQueryAPI:
    """知识图谱的稳定查询层。

    支持的 query_type:
        - ``node``           : 按 ID 精确取节点
        - ``nodes_by_type``  : 按类型列出节点
        - ``search_nodes``   : 按 ID 通配符 + type 过滤搜索节点
        - ``edges``          : 按关系类型 / 起止 / 置信度区间查关系
        - ``neighbors``      : 取节点的 N 跳邻居
        - ``tools_for_material``  : 业务快捷查询 - 某材料适配的所有刀具
        - ``materials_for_tool``  : 业务快捷查询 - 某刀具能加工的所有材料
        - ``process_chain``       : 业务快捷查询 - 某 feature 的工艺链
        - ``stats``               : 图规模统计
    """

    def __init__(self, store: GraphStore):
        self._store = store

    # ============================================================== 基础查询

    def node(self, node_id: str) -> dict[str, Any] | None:
        """按 ID 精确取节点。"""
        return self._store.get_node(node_id)

    def nodes_by_type(self, node_type: str, limit: int = 1000) -> list[dict[str, Any]]:
        """按类型列出节点（按 node_id 排序）。"""
        return self._store.list_nodes_by_type(node_type)[:limit]

    def search_nodes(
        self,
        id_pattern: str | None = None,
        node_type: str | None = None,
        prop_filter: dict[str, Any] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """按 ID 通配符 + 类型 + 属性过滤搜索节点。

        Args:
            id_pattern: ID 通配符（``%`` / ``_``），如 ``"tool-%"``。
            node_type: 节点类型，如 ``"material"``。
            prop_filter: 属性键值精确匹配，如 ``{"hardness": "HRC60"}``。
            limit: 返回数量上限。
        """
        results: list[dict[str, Any]] = []
        for nid, data in self._store.graph().nodes(data=True):
            ntype = data.get("type", "")
            if node_type is not None and ntype != node_type:
                continue
            if not _match(nid, id_pattern):
                continue
            props = data.get("properties", {}) or {}
            if prop_filter:
                ok = True
                for k, v in prop_filter.items():
                    if props.get(k) != v:
                        ok = False
                        break
                if not ok:
                    continue
            results.append(
                {
                    "node_id": nid,
                    "node_type": ntype,
                    "properties": dict(props),
                }
            )
            if len(results) >= limit:
                break
        results.sort(key=lambda x: x["node_id"])
        return results

    def edges(
        self,
        edge_type: str | None = None,
        source_id: str | None = None,
        target_id: str | None = None,
        min_confidence: float = 0.0,
        max_confidence: float = 1.0,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """按关系类型 / 起止 / 置信度区间查关系。"""
        if source_id and target_id and edge_type:
            # 快速路径：单条边查询
            edge = self._store.get_edge(source_id, target_id, edge_type)
            if edge is None:
                return []
            conf = (edge.get("properties") or {}).get("confidence", 0.5)
            if min_confidence <= conf <= max_confidence:
                return [
                    {
                        "source_id": source_id,
                        "target_id": target_id,
                        "edge_type": edge_type,
                        "confidence": conf,
                        "properties": edge.get("properties", {}),
                    }
                ]
            return []
        # 多条查询
        out = self._store.list_edges_by_confidence(
            min_confidence=min_confidence,
            max_confidence=max_confidence,
            edge_type=edge_type,
        )
        if source_id:
            out = [e for e in out if e.get("source_id") == source_id]
        if target_id:
            out = [e for e in out if e.get("target_id") == target_id]
        return out[:limit]

    def neighbors(self, node_id: str, max_hops: int = 1, limit: int = 200) -> list[dict[str, Any]]:
        """取节点 N 跳邻居（BFS，按 node_id 去重）。"""
        if not self._store.has_node(node_id):
            return []
        visited: set[str] = {node_id}
        frontier: set[str] = {node_id}
        all_neighbors: list[dict[str, Any]] = []
        for hop in range(1, max_hops + 1):
            next_frontier: set[str] = set()
            for nid in frontier:
                # 出边
                for e in self._store.list_edges_by_source(nid):
                    tgt = e["target_id"]
                    if tgt in visited:
                        continue
                    visited.add(tgt)
                    next_frontier.add(tgt)
                    all_neighbors.append(
                        {
                            "node_id": tgt,
                            "hop": hop,
                            "via_edge": e["edge_type"],
                            "via_source": nid,
                            "direction": "out",
                        }
                    )
                    if len(all_neighbors) >= limit:
                        return all_neighbors
                # 入边
                for e in self._store.list_edges_by_target(nid):
                    src = e["source_id"]
                    if src in visited:
                        continue
                    visited.add(src)
                    next_frontier.add(src)
                    all_neighbors.append(
                        {
                            "node_id": src,
                            "hop": hop,
                            "via_edge": e["edge_type"],
                            "via_source": nid,
                            "direction": "in",
                        }
                    )
                    if len(all_neighbors) >= limit:
                        return all_neighbors
            frontier = next_frontier
            if not frontier:
                break
        return all_neighbors

    def stats(self) -> dict[str, Any]:
        """图规模统计。"""
        g = self._store.graph()
        node_types: dict[str, int] = {}
        for _, d in g.nodes(data=True):
            t = d.get("type", "unknown")
            node_types[t] = node_types.get(t, 0) + 1
        edge_types: dict[str, int] = {}
        for _, _, k in g.edges(keys=True):
            edge_types[k] = edge_types.get(k, 0) + 1
        return {
            "node_count": g.number_of_nodes(),
            "edge_count": g.number_of_edges(),
            "node_types": node_types,
            "edge_types": edge_types,
        }

    # ============================================================== 业务查询

    def tools_for_material(
        self,
        material_id: str,
        min_confidence: float = 0.0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """业务查询：某材料适配的所有刀具（SUITABLE_FOR 关系反向）。"""
        edges = self._store.list_edges_by_target(material_id, edge_type="SUITABLE_FOR")
        out = []
        for e in edges:
            conf = (e.get("properties") or {}).get("confidence", 0.5)
            if conf < min_confidence:
                continue
            tool_node = self._store.get_node(e["source_id"])
            if tool_node is None:
                continue
            out.append(
                {
                    "tool": tool_node,
                    "edge": e,
                    "confidence": conf,
                }
            )
        out.sort(key=lambda x: x.get("confidence", 0.0), reverse=True)
        return out[:limit]

    def materials_for_tool(
        self,
        tool_id: str,
        min_confidence: float = 0.0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """业务查询：某刀具能加工的所有材料（SUITABLE_FOR 关系正向）。"""
        edges = self._store.list_edges_by_source(tool_id, edge_type="SUITABLE_FOR")
        out = []
        for e in edges:
            conf = (e.get("properties") or {}).get("confidence", 0.5)
            if conf < min_confidence:
                continue
            mat_node = self._store.get_node(e["target_id"])
            if mat_node is None:
                continue
            out.append(
                {
                    "material": mat_node,
                    "edge": e,
                    "confidence": conf,
                }
            )
        out.sort(key=lambda x: x.get("confidence", 0.0), reverse=True)
        return out[:limit]

    def process_chain(self, feature_id: str, max_hops: int = 3) -> list[dict[str, Any]]:
        """业务查询：从 feature 出发走 USED / APPLIED_TO 关系得到工艺链。

        返回的是有序的中间节点 + 关系列表。
        """
        if not self._store.has_node(feature_id):
            return []
        chain: list[dict[str, Any]] = []
        # 优先沿 USED 出边
        edges = self._store.list_edges_by_source(feature_id, edge_type="USED")
        edges += self._store.list_edges_by_source(feature_id, edge_type="APPLIED_TO")
        for e in edges:
            tgt = self._store.get_node(e["target_id"])
            if tgt is None:
                continue
            chain.append(
                {
                    "from": feature_id,
                    "to": tgt["node_id"],
                    "edge_type": e["edge_type"],
                    "confidence": (e.get("properties") or {}).get("confidence", 0.5),
                    "target_type": tgt["node_type"],
                    "target_props": tgt["properties"],
                }
            )
        chain.sort(key=lambda x: x.get("confidence", 0.0), reverse=True)
        return chain[: max_hops * 4]

    # ============================================================== 入口

    def query(self, query_type: str, **params: Any) -> dict[str, Any]:
        """统一查询入口。

        Returns:
            ``{"query_type": str, "count": int, "data": ...}``
        """
        method_name = {
            "node": "node",
            "nodes_by_type": "nodes_by_type",
            "search_nodes": "search_nodes",
            "edges": "edges",
            "neighbors": "neighbors",
            "tools_for_material": "tools_for_material",
            "materials_for_tool": "materials_for_tool",
            "process_chain": "process_chain",
            "stats": "stats",
        }.get(query_type)
        if method_name is None:
            return {
                "query_type": query_type,
                "count": 0,
                "data": None,
                "error": f"unknown query_type: {query_type}",
            }
        method = getattr(self, method_name)
        try:
            data = method(**params)
            if isinstance(data, list):
                count = len(data)
            elif isinstance(data, dict):
                count = 1
            else:
                count = 0 if data is None else 1
            return {"query_type": query_type, "count": count, "data": data}
        except (ValueError, TypeError, KeyError, RuntimeError) as e:
            logger.warning("query %s failed: %s", query_type, e)
            return {
                "query_type": query_type,
                "count": 0,
                "data": None,
                "error": "知识图谱查询失败，请检查参数或稍后重试",
            }


__all__ = ["KnowledgeGraphQueryAPI"]
