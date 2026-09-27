"""KnowledgeGraph Repository —— 节点 / 关系同步 CRUD 封装（M1.2）。

设计要点：
    - **同步 API**：与任务 M0.4 ``MachiningRecordRepository`` 风格一致，
      便于 pytest / 任务脚本直接调用。
    - **依赖注入**：可通过 ``session_factory`` 注入外部 sessionmaker
      （如 FastAPI ``Depends(get_db)``），未注入时使用本模块内部维护的
      懒加载全局 sessionmaker。
    - **字段映射**：NetworkX 图属性 / Pydantic 模型 / ORM 模型解耦，
      Repository 层负责字段装配。
    - **唯一性约束**：
        * 节点按 ``node_id`` 主键 upsert。
        * 关系按 ``(source_id, target_id, edge_type)`` 唯一约束 upsert。
    - **异常语义**：未找到返回 ``None``，依赖完整性冲突抛回原始异常。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast
from collections.abc import Callable, Sequence

from sqlalchemy import and_, func, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database.sync_session import get_sync_sessionmaker
from app.knowledge_graph.models import Base, KGEdge, KGNode

logger = logging.getLogger(__name__)


# 同步引擎与 sessionmaker 统一由 app.database.sync_session 提供，
# 避免与 database/repository/machining_record_repo.py 重复定义
# _SyncSingletons。``get_sync_sessionmaker`` 已通过 import 暴露在本模块
# 命名空间，外部代码原 import 路径保持向后兼容。


def get_sync_engine() -> Engine:
    """获取同步 SQLAlchemy ``Engine``（懒加载）。

    向后兼容封装：委托给 :func:`app.database.sync_session.get_sync_engine`。
    """
    from app.database.sync_session import get_sync_engine as _get

    engine = _get()
    if engine is None:
        raise RuntimeError("同步 Engine 未初始化（get_sync_engine 返回 None）")
    return engine


# Repository


SessionFactory = Callable[[], "Session"]


def _new_edge_id() -> str:
    """生成关系主键 ID。"""
    return f"kgedge_{uuid.uuid4().hex[:24]}"


class KnowledgeGraphRepository:
    """知识图谱同步仓储（节点 + 关系）。

    用法::

        repo = KnowledgeGraphRepository()
        repo.upsert_node("material", "M-45steel", {"name": "45 steel"})
        node = repo.get_node("M-45steel")
        edges = repo.get_edges_by_type("SUITABLE_FOR")
        repo.upsert_edge("T-endmill-10", "M-45steel", "SUITABLE_FOR",
                          confidence=0.9, properties={"source": "rule"})

    也支持 FastAPI 风格依赖注入::

        def get_repo() -> KnowledgeGraphRepository:
            return KnowledgeGraphRepository(
                session_factory=lambda: Session(...)
            )
    """

    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        self._session_factory: SessionFactory | None = session_factory

    # ------------------------------------------------------------------ utils

    def _session(self) -> "Session":
        """获取一个新 Session。"""
        if self._session_factory is not None:
            return self._session_factory()
        factory = get_sync_sessionmaker()
        if factory is None:
            raise RuntimeError("Database not configured: set DB_URL or inject session_factory")
        return factory()

    # ----------------------------------------------------------------- schema

    def init_schema(self) -> None:
        """在当前数据库上创建知识图谱相关表（幂等）。

        使用 ``with self._session()`` 上下文管理器确保 Session 被正确关闭，
        避免连接泄漏。
        """
        with self._session() as session:
            engine = session.get_bind()
            Base.metadata.create_all(engine)

    def drop_schema(self) -> None:
        """删除知识图谱相关表（谨慎使用）。

        同样使用上下文管理器确保 Session 被正确关闭。
        """
        with self._session() as session:
            engine = session.get_bind()
            Base.metadata.drop_all(engine)

    # ============================================================== 节点操作

    def upsert_node(
        self,
        node_id: str,
        node_type: str,
        properties: dict[str, Any] | None = None,
    ) -> KGNode:
        """插入或更新节点，按 ``node_id`` 主键匹配。

        Args:
            node_id: 节点唯一 ID。
            node_type: 节点类型（如 material / tool / feature / process）。
            properties: 节点属性字典，缺省 ``{}``。

        Returns:
            持久化后的 ORM 节点对象（已 ``expire_on_commit=False``）。

        Raises:
            IntegrityError: 唯一性 / 外键冲突。
        """
        props = dict(properties or {})
        with self._session() as session:
            existing = session.get(KGNode, node_id)
            if existing is None:
                orm_obj = KGNode(
                    node_id=node_id,
                    node_type=node_type,
                    properties=props,
                )
                session.add(orm_obj)
            else:
                # ORM 经典 Column 风格：cast 解包读取、ignore 赋值
                existing.node_type = node_type  # type: ignore[assignment]
                merged = dict(cast(Any, existing.properties) or {})
                merged.update(props)
                existing.properties = merged  # type: ignore[assignment]
                orm_obj = existing
            try:
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                logger.warning("Database error on upsert node %s: %s", node_id, exc)
                raise
            session.refresh(orm_obj)
            session.expunge(orm_obj)
            return orm_obj

    def get_node(self, node_id: str) -> KGNode | None:
        """按主键查询节点。"""
        with self._session() as session:
            orm_obj = session.get(KGNode, node_id)
            if orm_obj is None:
                return None
            session.expunge(orm_obj)
            return orm_obj

    def list_nodes_by_type(
        self,
        node_type: str,
        *,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[KGNode]:
        """按节点类型批量查询。"""
        if limit <= 0:
            return []
        with self._session() as session:
            stmt = (
                select(KGNode)
                .where(KGNode.node_type == node_type)
                .order_by(KGNode.node_id.asc())
                .limit(limit)
                .offset(max(offset, 0))
            )
            orm_objs: Sequence[KGNode] = session.execute(stmt).scalars().all()
            for obj in orm_objs:
                session.expunge(obj)
            return list(orm_objs)

    def list_all_nodes(
        self,
        *,
        limit: int = 10000,
        offset: int = 0,
    ) -> list[KGNode]:
        """全表分页查询所有节点。"""
        if limit <= 0:
            return []
        with self._session() as session:
            stmt = select(KGNode).order_by(KGNode.node_id.asc()).limit(limit).offset(max(offset, 0))
            orm_objs: Sequence[KGNode] = session.execute(stmt).scalars().all()
            for obj in orm_objs:
                session.expunge(obj)
            return list(orm_objs)

    def delete_node(self, node_id: str) -> bool:
        """按主键删除节点；级联删除其关联关系。

        实现说明：
            - 优先采用 ORM 级别的 cascade 行为：先批量删除该节点关联的
              所有 ``KGEdge`` 记录，再删除节点本身。
            - 同时保留 ``ondelete=CASCADE`` 数据库级约束作为兜底。
            - 此举避免对数据库外键强制启用（SQLite 默认关闭）的依赖。
        """
        with self._session() as session:
            orm_obj = session.get(KGNode, node_id)
            if orm_obj is None:
                return False
            # 批量删除关联关系（避免 N+1 查询）
            from sqlalchemy import delete

            edge_delete_stmt = delete(KGEdge).where(
                or_(
                    KGEdge.source_id == node_id,
                    KGEdge.target_id == node_id,
                )
            )
            session.execute(edge_delete_stmt)
            session.delete(orm_obj)
            try:
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                logger.error("Database error on delete node %s: %s", node_id, exc)
                raise
            return True

    def count_nodes(self, node_type: str | None = None) -> int:
        """统计节点数量；可选按 ``node_type`` 过滤。"""
        with self._session() as session:
            stmt = select(func.count()).select_from(KGNode)
            if node_type is not None:
                stmt = stmt.where(KGNode.node_type == node_type)
            return int(session.execute(stmt).scalar_one())

    # ============================================================== 关系操作

    def upsert_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
        confidence: float = 0.5,
        properties: dict[str, Any] | None = None,
    ) -> KGEdge:
        """插入或更新关系，按 ``(source_id, target_id, edge_type)`` 唯一键匹配。

        Args:
            source_id: 起始节点 ID。
            target_id: 目标节点 ID。
            edge_type: 关系类型。
            confidence: 可信度，取值 [0, 1]。
            properties: 关系附加属性（如 source / evidence）。

        Returns:
            持久化后的 ORM 关系对象。

        Raises:
            IntegrityError: 外键冲突（节点不存在）或唯一性冲突。
            ValueError: 置信度超出 [0, 1] 范围。
        """
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"confidence must be in [0, 1], got {confidence!r}")
        props = dict(properties or {})

        with self._session() as session:
            # 主动校验端点节点存在性，避免某些数据库（如 SQLite 默认
            # 不强制外键）下出现孤儿关系。
            src = session.get(KGNode, source_id)
            tgt = session.get(KGNode, target_id)
            missing = [
                nid
                for nid, orm_obj in (
                    (source_id, src),
                    (target_id, tgt),
                )
                if orm_obj is None
            ]
            if missing:
                raise ValueError("Cannot upsert edge: endpoint node(s) not found: " + ", ".join(missing))

            stmt = select(KGEdge).where(
                and_(
                    KGEdge.source_id == source_id,
                    KGEdge.target_id == target_id,
                    KGEdge.edge_type == edge_type,
                )
            )
            existing = session.execute(stmt).scalar_one_or_none()
            if existing is None:
                orm_obj = KGEdge(
                    edge_id=_new_edge_id(),
                    source_id=source_id,
                    target_id=target_id,
                    edge_type=edge_type,
                    confidence=float(confidence),
                    properties=props,
                )
                session.add(orm_obj)
            else:
                existing.confidence = float(confidence)  # type: ignore[assignment]
                merged = dict(cast(Any, existing.properties) or {})
                merged.update(props)
                existing.properties = merged  # type: ignore[assignment]
                orm_obj = existing
            try:
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                logger.warning(
                    "Database error on upsert edge %s->%s[%s]: %s",
                    source_id,
                    target_id,
                    edge_type,
                    exc,
                )
                raise
            session.refresh(orm_obj)
            session.expunge(orm_obj)
            return orm_obj

    def get_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
    ) -> KGEdge | None:
        """按业务唯一键 ``(source_id, target_id, edge_type)`` 查询关系。"""
        with self._session() as session:
            stmt = select(KGEdge).where(
                and_(
                    KGEdge.source_id == source_id,
                    KGEdge.target_id == target_id,
                    KGEdge.edge_type == edge_type,
                )
            )
            orm_obj = session.execute(stmt).scalar_one_or_none()
            if orm_obj is None:
                return None
            session.expunge(orm_obj)
            return orm_obj

    def list_edges_by_type(
        self,
        edge_type: str,
        *,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[KGEdge]:
        """按关系类型查询边。"""
        if limit <= 0:
            return []
        with self._session() as session:
            stmt = (
                select(KGEdge)
                .where(KGEdge.edge_type == edge_type)
                .order_by(KGEdge.source_id.asc(), KGEdge.target_id.asc())
                .limit(limit)
                .offset(max(offset, 0))
            )
            orm_objs: Sequence[KGEdge] = session.execute(stmt).scalars().all()
            for obj in orm_objs:
                session.expunge(obj)
            return list(orm_objs)

    def list_edges_by_source(
        self,
        source_id: str,
        edge_type: str | None = None,
        *,
        limit: int = 1000,
    ) -> list[KGEdge]:
        """按起始节点 ID 查询出边；可选 ``edge_type`` 过滤。"""
        if limit <= 0:
            return []
        with self._session() as session:
            stmt = select(KGEdge).where(KGEdge.source_id == source_id)
            if edge_type is not None:
                stmt = stmt.where(KGEdge.edge_type == edge_type)
            stmt = stmt.order_by(KGEdge.edge_type.asc(), KGEdge.target_id.asc()).limit(limit)
            orm_objs: Sequence[KGEdge] = session.execute(stmt).scalars().all()
            for obj in orm_objs:
                session.expunge(obj)
            return list(orm_objs)

    def list_edges_by_target(
        self,
        target_id: str,
        edge_type: str | None = None,
        *,
        limit: int = 1000,
    ) -> list[KGEdge]:
        """按目标节点 ID 查询入边；可选 ``edge_type`` 过滤。"""
        if limit <= 0:
            return []
        with self._session() as session:
            stmt = select(KGEdge).where(KGEdge.target_id == target_id)
            if edge_type is not None:
                stmt = stmt.where(KGEdge.edge_type == edge_type)
            stmt = stmt.order_by(KGEdge.edge_type.asc(), KGEdge.source_id.asc()).limit(limit)
            orm_objs: Sequence[KGEdge] = session.execute(stmt).scalars().all()
            for obj in orm_objs:
                session.expunge(obj)
            return list(orm_objs)

    def list_edges_by_confidence(
        self,
        min_confidence: float,
        *,
        max_confidence: float = 1.0,
        edge_type: str | None = None,
        limit: int = 1000,
    ) -> list[KGEdge]:
        """按可信度区间查询边；可选 ``edge_type`` 过滤。"""
        if limit <= 0:
            return []
        if min_confidence > max_confidence:
            raise ValueError(f"min_confidence ({min_confidence}) must be <= max_confidence ({max_confidence})")
        with self._session() as session:
            stmt = select(KGEdge).where(
                and_(
                    KGEdge.confidence >= float(min_confidence),
                    KGEdge.confidence <= float(max_confidence),
                )
            )
            if edge_type is not None:
                stmt = stmt.where(KGEdge.edge_type == edge_type)
            stmt = stmt.order_by(KGEdge.confidence.desc()).limit(limit)
            orm_objs: Sequence[KGEdge] = session.execute(stmt).scalars().all()
            for obj in orm_objs:
                session.expunge(obj)
            return list(orm_objs)

    def delete_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
    ) -> bool:
        """按业务唯一键删除关系。"""
        with self._session() as session:
            stmt = select(KGEdge).where(
                and_(
                    KGEdge.source_id == source_id,
                    KGEdge.target_id == target_id,
                    KGEdge.edge_type == edge_type,
                )
            )
            orm_obj = session.execute(stmt).scalar_one_or_none()
            if orm_obj is None:
                return False
            session.delete(orm_obj)
            try:
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                logger.error(
                    "Database error on delete edge %s->%s[%s]: %s",
                    source_id,
                    target_id,
                    edge_type,
                    exc,
                )
                raise
            return True

    def count_edges(self, edge_type: str | None = None) -> int:
        """统计关系数量；可选按 ``edge_type`` 过滤。"""
        with self._session() as session:
            stmt = select(func.count()).select_from(KGEdge)
            if edge_type is not None:
                stmt = stmt.where(KGEdge.edge_type == edge_type)
            return int(session.execute(stmt).scalar_one())


__all__ = [
    "KnowledgeGraphRepository",
    "get_sync_sessionmaker",
    "get_sync_engine",
    "SessionFactory",
]
