"""知识图谱健康检查核心逻辑（M1.5）。

实现三大检测模块：
    1. 孤立节点检测：识别无入边或出边的节点
    2. 矛盾关系检测：识别互逆关系对（A→B 且 B→A）
    3. 老旧数据检测：识别超过5年未更新的节点

设计原则：
    - 只读访问：所有检测操作不修改图谱数据
    - 性能优化：使用批量查询和集合运算
    - 可扩展：各检测模块独立，便于添加新检查类型
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone


from app.knowledge_graph.graph_store import GraphStore
import time

logger = logging.getLogger(__name__)


# 数据模型


@dataclass
class IsolatedNodeResult:
    """孤立节点检测结果。"""

    node_id: str
    node_type: str
    reason: str  # 判定依据，如 "no_in_edges" / "no_out_edges" / "no_edges"


@dataclass
class ContradictoryEdgeResult:
    """矛盾关系检测结果。"""

    source_id: str
    target_id: str
    edge_type_forward: str  # A→B 的关系类型
    edge_type_reverse: str  # B→A 的关系类型
    forward_created_at: str | None = None
    reverse_created_at: str | None = None


@dataclass
class StaleNodeResult:
    """老旧数据检测结果。"""

    node_id: str
    node_type: str
    last_updated: str | None
    age_days: int
    threshold_years: int


@dataclass
class HealthCheckResult:
    """健康检查综合结果。"""

    isolated_nodes: list[IsolatedNodeResult] = field(default_factory=list)
    contradictory_edges: list[ContradictoryEdgeResult] = field(default_factory=list)
    stale_nodes: list[StaleNodeResult] = field(default_factory=list)
    check_timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    total_nodes: int = 0
    total_edges: int = 0
    check_duration_seconds: float = 0.0

    @property
    def issue_count(self) -> int:
        """问题总数。"""
        return len(self.isolated_nodes) + len(self.contradictory_edges) + len(self.stale_nodes)


# 健康检查器


class HealthChecker:
    """知识图谱健康检查器。

    示例::

        checker = HealthChecker(graph_store)
        result = checker.run_all_checks()
        # 发现 {result.issue_count} 个问题

    也可单独运行某项检查::

        isolated = checker.check_isolated_nodes()
        contradictory = checker.check_contradictory_edges()
        stale = checker.check_stale_nodes(threshold_years=5)
    """

    def __init__(self, graph_store: GraphStore) -> None:
        self._graph = graph_store

    def run_all_checks(
        self,
        *,
        stale_threshold_years: int = 5,
    ) -> HealthCheckResult:
        """运行所有健康检查。

        Args:
            stale_threshold_years: 老旧数据阈值（年），默认5年。

        Returns:
            综合检查结果。
        """
        start = time.monotonic()

        result = HealthCheckResult()
        result.total_nodes = self._graph.node_count()
        result.total_edges = self._graph.edge_count()

        result.isolated_nodes = self.check_isolated_nodes()
        result.contradictory_edges = self.check_contradictory_edges()
        result.stale_nodes = self.check_stale_nodes(threshold_years=stale_threshold_years)

        elapsed = time.monotonic() - start
        result.check_duration_seconds = round(elapsed, 3)
        result.check_timestamp = datetime.now(timezone.utc).isoformat()

        logger.info(
            "Health check completed in %.2fs: %d isolated nodes, %d contradictory edges, %d stale nodes",
            elapsed,
            len(result.isolated_nodes),
            len(result.contradictory_edges),
            len(result.stale_nodes),
        )
        return result

    # ============================================================== 孤立节点检测

    def check_isolated_nodes(self) -> list[IsolatedNodeResult]:
        """检测所有孤立节点（无入边或出边）。

        Returns:
            孤立节点结果列表，按 node_id 排序。
        """
        results: list[IsolatedNodeResult] = []
        graph = self._graph.graph()

        for node_id, data in self._graph.graph().nodes(data=True):
            node_type = data.get("type", "")
            in_degree = graph.in_degree(node_id)
            out_degree = graph.out_degree(node_id)

            if in_degree == 0 and out_degree == 0:
                results.append(
                    IsolatedNodeResult(
                        node_id=node_id,
                        node_type=node_type,
                        reason="no_edges",
                    )
                )
            elif in_degree == 0:
                results.append(
                    IsolatedNodeResult(
                        node_id=node_id,
                        node_type=node_type,
                        reason="no_in_edges",
                    )
                )
            elif out_degree == 0:
                results.append(
                    IsolatedNodeResult(
                        node_id=node_id,
                        node_type=node_type,
                        reason="no_out_edges",
                    )
                )

        results.sort(key=lambda x: x.node_id)
        return results

    # ============================================================== 矛盾关系检测

    def check_contradictory_edges(self) -> list[ContradictoryEdgeResult]:
        """检测所有互逆关系对（A→B 且 B→A）。

        对于 MultiDiGraph，两个节点之间可能存在多条不同类型的边。
        我们检测任意两条方向相反的边。

        Returns:
            矛盾关系结果列表，按 (source_id, target_id) 排序。
        """
        results: list[ContradictoryEdgeResult] = []
        seen_pairs: set[tuple[str, str]] = set()
        graph = self._graph.graph()

        for u, v, key_u_v, data_u_v in graph.edges(keys=True, data=True):
            # 检查反向边是否存在
            if graph.has_edge(v, u):
                # 避免重复报告（AB 和 BA 只报告一次）
                pair_key = tuple(sorted([u, v]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                # 获取反向边的信息
                for _, _, key_v_u, data_v_u in graph.edges(v, u, keys=True, data=True):
                    results.append(
                        ContradictoryEdgeResult(
                            source_id=u,
                            target_id=v,
                            edge_type_forward=key_u_v,
                            edge_type_reverse=key_v_u,
                            forward_created_at=data_u_v.get("properties", {}).get("created_at"),
                            reverse_created_at=data_v_u.get("properties", {}).get("created_at"),
                        )
                    )

        results.sort(key=lambda x: (x.source_id, x.target_id))
        return results

    # ============================================================== 老旧数据检测

    def check_stale_nodes(
        self,
        *,
        threshold_years: int = 5,
    ) -> list[StaleNodeResult]:
        """检测所有超过指定年限未更新的节点。

        Args:
            threshold_years: 阈值年数，默认5年。

        Returns:
            老旧节点结果列表，按 age_days 降序排列。
        """
        results: list[StaleNodeResult] = []
        now = datetime.now(timezone.utc)

        for node_id, data in self._graph.graph().nodes(data=True):
            node_type = data.get("type", "")
            properties = data.get("properties", {})

            # 尝试从 properties 或节点属性中获取更新时间
            updated_at_str = properties.get("updated_at") or properties.get("last_updated")

            age_days = 0
            last_updated = updated_at_str

            if updated_at_str:
                try:
                    # 尝试解析 ISO8601 格式
                    updated_at = self._parse_timestamp(updated_at_str)
                    age_days = (now - updated_at).days
                except (ValueError, TypeError):
                    # 无法解析时，使用创建时间
                    created_at_str = properties.get("created_at")
                    if created_at_str:
                        try:
                            created_at = self._parse_timestamp(created_at_str)
                            age_days = (now - created_at).days
                            last_updated = created_at_str
                        except (ValueError, TypeError):
                            age_days = -1  # 无法确定
                    else:
                        age_days = -1
            else:
                # 没有更新时间，尝试创建时间
                created_at_str = properties.get("created_at")
                if created_at_str:
                    try:
                        created_at = self._parse_timestamp(created_at_str)
                        age_days = (now - created_at).days
                        last_updated = created_at_str
                    except (ValueError, TypeError):
                        age_days = -1
                else:
                    age_days = -1

            # 判断是否超过阈值
            if age_days >= 0 and age_days >= threshold_years * 365:
                results.append(
                    StaleNodeResult(
                        node_id=node_id,
                        node_type=node_type,
                        last_updated=last_updated,
                        age_days=age_days,
                        threshold_years=threshold_years,
                    )
                )

        # 按 age_days 降序排列
        results.sort(key=lambda x: x.age_days, reverse=True)
        return results

    # ============================================================== 辅助方法

    @staticmethod
    def _parse_timestamp(ts_str: str) -> datetime:
        """解析时间戳字符串为 datetime 对象。

        支持多种 ISO8601 格式。
        """
        # 移除尾部 Z 并替换为 +00:00
        ts_str = ts_str.strip()
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"

        # 尝试多种格式
        formats = [
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f%z",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(ts_str, fmt)
                # 如果没有时区信息，假定 UTC
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue

        # 最后尝试 fromisoformat（Python 3.7+）
        try:
            dt = datetime.fromisoformat(ts_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError as iso_err:
            # 所有格式均解析失败，记录最后一个解析异常便于排查，随后抛出统一错误
            logger.debug("fromisoformat also failed for %r: %s", ts_str, iso_err)

        raise ValueError(f"Unable to parse timestamp: {ts_str!r}")


__all__ = [
    "HealthChecker",
    "HealthCheckResult",
    "IsolatedNodeResult",
    "ContradictoryEdgeResult",
    "StaleNodeResult",
]
