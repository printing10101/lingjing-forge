"""知识图谱 ↔ PostgreSQL 同步逻辑（M1.2）。

设计要点：
    - **自定义序列化**：手动将 NetworkX 节点 / 边属性映射到 ORM 字段，
      不使用 NetworkX 自带 ``nx.write_gpickle`` 等持久化（其不支持事务）。
    - **批量落库**：提供 ``flush_to_repository`` / ``load_from_repository``
      两种方向同步，整体在一个数据库会话中提交，确保一致性。
    - **依赖注入**：可通过 ``session_factory`` 注入外部 sessionmaker；
      未注入时使用 Repository 内置 sessionmaker。
    - **不抛静默错误**：所有数据库异常向上抛，由调用方决定重试或回滚。

方向：
    - ``flush_to_repository(g, ...)``：内存图 → 数据库（upsert）。
    - ``load_from_repository(g, ...)``：数据库 → 内存图（覆盖式）。
    - ``sync_to_repository(g, ...)``：便捷方法，先 load 后 flush（双向
      合并：DB 已存在则保留 DB 版本作为权威，再用 in-memory 覆盖更新）。
"""

from __future__ import annotations

import logging
from typing import Any, cast
from collections.abc import Sequence

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.knowledge_graph.graph_store import GraphStore
from app.knowledge_graph.models import KGEdge, KGNode
from app.knowledge_graph.repository import (
    KnowledgeGraphRepository,
    SessionFactory,
    _new_edge_id,
    get_sync_sessionmaker,
)

logger = logging.getLogger(__name__)


class GraphPersistence:
    """知识图谱持久化同步器。

    示例::

        g = GraphStore()
        g.add_node("material", "M-45steel", {"name": "45 steel"})
        g.add_node("tool", "T-endmill-10", {"name": "Endmill D10"})
        g.add_edge("T-endmill-10", "M-45steel", "SUITABLE_FOR",
                   {"confidence": 0.9, "source": "rule"})

        # 落库
        persistence = GraphPersistence()
        persistence.flush_to_repository(g)

        # 重启后重新加载
        g2 = GraphStore()
        persistence.load_from_repository(g2)
        assert g2.node_count() == 2
    """

    def __init__(
        self,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._session_factory: SessionFactory | None = session_factory
        # Repository 仅用于复用其内部 _session() 与字段映射逻辑
        self._repo = KnowledgeGraphRepository(session_factory=session_factory)

    # ------------------------------------------------------------------ utils

    def _session(self) -> "Session":
        """获取一个新 Session。"""
        if self._session_factory is not None:
            return self._session_factory()
        factory = get_sync_sessionmaker()
        if factory is None:
            raise RuntimeError("Database not configured: set DB_URL or inject session_factory")
        return factory()

    # ============================================================== schema ops

    def init_schema(self) -> None:
        """幂等创建 kg_nodes / kg_edges 表。"""
        self._repo.init_schema()

    def drop_schema(self) -> None:
        """删除 kg_nodes / kg_edges 表（谨慎使用）。"""
        self._repo.drop_schema()

    # ============================================================== 写入方向

    def flush_to_repository(
        self,
        graph: GraphStore,
        *,
        clear_first: bool = False,
    ) -> dict[str, int]:
        """将内存图中的节点和关系全部 upsert 到数据库。

        Args:
            graph: 内存图。
            clear_first: 若为 True，先清空 kg_nodes / kg_edges 表（连带级联）。
                默认 False（upsert 语义）。

        Returns:
            包含 ``nodes_written`` / ``edges_written`` 字段的统计字典。
        """
        nodes_written = 0
        edges_written = 0

        with self._session() as session:
            try:
                if clear_first:
                    # 先清空关系再清空节点，避免某些数据库（如 SQLite 默认
                    # 关闭外键）下出现孤儿关系。
                    session.query(KGEdge).delete()
                    session.flush()
                    session.query(KGNode).delete()
                    session.flush()

                # 节点
                # 内联 upsert 逻辑到同一会话，保证事务原子性
                # （之前调用 self._repo.upsert_node 会开启独立会话并独立 commit，
                # 导致 clear_first 回滚时已 commit 的 upsert 无法回滚）
                for nid, data in graph._graph.nodes(data=True):
                    node_type = data.get("type", "")
                    properties = dict(data.get("properties", {}))
                    existing_node = session.get(KGNode, str(nid))
                    if existing_node is None:
                        session.add(
                            KGNode(
                                node_id=str(nid),
                                node_type=node_type,
                                properties=properties,
                            )
                        )
                    else:
                        # ORM 经典 Column 风格：mypy 把属性推断为 Column[T]，
                        # 运行时是真实值，cast 解包读取、ignore 赋值
                        existing_node.node_type = node_type
                        merged = dict(cast(Any, existing_node.properties) or {})
                        merged.update(properties)
                        existing_node.properties = merged  # type: ignore[assignment]
                    nodes_written += 1

                # 关系
                # 同样内联 upsert 逻辑，避免独立会话破坏原子性
                from sqlalchemy import and_, select

                for u, v, k, data in graph._graph.edges(keys=True, data=True):
                    edge_type = k
                    properties = dict(data.get("properties", {}))
                    confidence = properties.get("confidence", 0.5)
                    try:
                        confidence_f = float(confidence)
                    except (TypeError, ValueError):
                        confidence_f = 0.5
                    if not (0.0 <= confidence_f <= 1.0):
                        confidence_f = 0.5
                    # 避免 ``confidence`` 字段同时出现在 properties
                    props_for_db = {key: val for key, val in properties.items() if key != "confidence"}
                    # 查询现有边（不主动校验端点节点存在性，因为节点
                    # 已在同一事务内 upsert，外键约束会保证一致性）
                    stmt = select(KGEdge).where(
                        and_(
                            KGEdge.source_id == str(u),
                            KGEdge.target_id == str(v),
                            KGEdge.edge_type == edge_type,
                        )
                    )
                    existing_edge = session.execute(stmt).scalar_one_or_none()
                    if existing_edge is None:
                        session.add(
                            KGEdge(
                                edge_id=_new_edge_id(),
                                source_id=str(u),
                                target_id=str(v),
                                edge_type=edge_type,
                                confidence=confidence_f,
                                properties=props_for_db,
                            )
                        )
                    else:
                        existing_edge.confidence = confidence_f  # type: ignore[assignment]
                        merged = dict(cast(Any, existing_edge.properties) or {})
                        merged.update(props_for_db)
                        existing_edge.properties = merged  # type: ignore[assignment]
                    edges_written += 1

                session.commit()
            except (OSError, RuntimeError, ValueError, TypeError) as exc:
                session.rollback()
                logger.error("flush_to_repository failed: %s", exc)
                raise
            except SQLAlchemyError as exc:
                # 捕获 SQLAlchemyError 等数据库异常，显式 rollback
                session.rollback()
                logger.error("flush_to_repository DB error: %s", exc)
                raise

        logger.info(
            "flush_to_repository: wrote %d nodes, %d edges",
            nodes_written,
            edges_written,
        )
        return {
            "nodes_written": nodes_written,
            "edges_written": edges_written,
        }

    # ============================================================== 读取方向

    def load_from_repository(
        self,
        graph: GraphStore,
        *,
        node_limit: int = 100000,
        edge_limit: int = 1000000,
        replace: bool = True,
    ) -> dict[str, int]:
        """从数据库加载节点和关系到内存图。

        Args:
            graph: 目标内存图。
            node_limit: 节点加载上限（防御性）。
            edge_limit: 关系加载上限。
            replace: 是否先清空内存图（默认 True，覆盖语义）。

        Returns:
            包含 ``nodes_loaded`` / ``edges_loaded`` 字段的统计字典。
        """
        if replace:
            graph.clear()

        nodes_loaded = 0
        edges_loaded = 0

        with self._session() as session:
            # 节点优先（关系依赖节点）
            node_rows = self._repo.list_all_nodes(limit=node_limit)
            for orm_node in node_rows:
                graph.add_node(
                    node_type=cast(str, orm_node.node_type),
                    node_id=cast(str, orm_node.node_id),
                    properties=dict(cast(Any, orm_node.properties) or {}),
                )
                nodes_loaded += 1

            # 边：使用 Repository 的按类型遍历方式以利用索引
            distinct_types = self._distinct_edge_types(session=session)
            for etype in distinct_types:
                for orm_edge in self._repo.list_edges_by_type(etype, limit=edge_limit):
                    props = dict(cast(Any, orm_edge.properties) or {})
                    props.setdefault("confidence", float(cast(float, orm_edge.confidence)))
                    # source / evidence 等附加字段保留
                    # 注意：upsert 流程中若 properties 已包含 confidence，
                    # 不会被覆盖（add_edge 内部会再次校验）
                    try:
                        graph.add_edge(
                            source_id=cast(str, orm_edge.source_id),
                            target_id=cast(str, orm_edge.target_id),
                            edge_type=cast(str, orm_edge.edge_type),
                            properties=props,
                        )
                        edges_loaded += 1
                    except ValueError as exc:
                        # 端点缺失（外键本应阻止，但极端情况下数据
                        # 可能不完整）；记录警告并跳过该边。
                        logger.warning(
                            "Skipping edge %s->%s[%s] during load: %s",
                            orm_edge.source_id,
                            orm_edge.target_id,
                            orm_edge.edge_type,
                            exc,
                        )

        logger.info(
            "load_from_repository: loaded %d nodes, %d edges",
            nodes_loaded,
            edges_loaded,
        )
        return {
            "nodes_loaded": nodes_loaded,
            "edges_loaded": edges_loaded,
        }

    def _distinct_edge_types(self, *, session: "Session") -> Sequence[str]:
        """查询数据库中所有不同的关系类型。"""
        from sqlalchemy import distinct, select

        from app.knowledge_graph.models import KGEdge

        from sqlalchemy.sql import Select

        stmt: Select[Any] = select(distinct(KGEdge.edge_type)).order_by(KGEdge.edge_type.asc())
        result = session.execute(stmt).scalars().all()
        return list(result)


__all__ = ["GraphPersistence"]
