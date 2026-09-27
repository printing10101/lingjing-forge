"""DAG 工作流运行状态持久化层.

提供 workflow_runs / workflow_run_nodes 表的 CRUD 操作，支持断点续跑。

设计要点：
    1. 所有方法均为 async，使用 SQLAlchemy 异步 session
    2. 序列化/反序列化 WorkflowSpec 与 Artifact 由调用方（runner）负责，
       本层只处理 dict 与 ORM 模型的转换
    3. update_node_state 使用 upsert 语义，支持断点续跑时更新已有记录
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from collections.abc import AsyncIterator

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import get_sessionmaker
from app.database.models.workflow import (
    WorkflowRun,
    WorkflowRunNode,
    _new_run_id,
    _new_node_id,
)

logger = logging.getLogger(__name__)


class DAGStore:
    """工作流运行状态持久化存储.

    并发安全说明：SQLite + StaticPool 共享单连接，多个 AsyncSession 并发
    checkout 同一连接会导致事务交错、UPDATE 静默丢失（节点状态随机停留在
    running）——所有 session 操作必须经 ``_locked_session`` 串行化。
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def _locked_session(self) -> AsyncIterator[AsyncSession]:
        """获取互斥保护的数据库 session（串行化写操作，防共享连接事务交错）."""
        async with self._lock:
            async with await self._get_session() as session:
                yield session

    async def _get_session(self) -> AsyncSession:
        """获取异步数据库 session（仅内部使用，外部一律走 _locked_session）."""
        sessionmaker = get_sessionmaker()
        if sessionmaker is None:
            raise RuntimeError("数据库未配置，无法获取 session")
        return sessionmaker()

    async def create_run(
        self,
        spec_dict: dict[str, Any],
        *,
        name: str,
        version: str = "1.0.0",
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        owner_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """创建工作流运行记录，返回 workflow_run_id."""
        run_id = _new_run_id()
        async with self._locked_session() as session:
            run = WorkflowRun(
                id=run_id,
                name=name,
                version=version,
                spec=spec_dict,
                status="pending",
                inputs=inputs,
                outputs=outputs,
                owner_id=owner_id,
                meta=metadata or {},
            )
            session.add(run)
            await session.commit()
        return run_id

    async def get_run(self, workflow_run_id: str) -> dict[str, Any] | None:
        """获取工作流运行记录（含节点状态）."""
        async with self._locked_session() as session:
            stmt = select(WorkflowRun).where(WorkflowRun.id == workflow_run_id)
            result = await session.execute(stmt)
            run = result.scalar_one_or_none()
            if run is None:
                return None
            run_dict = run.to_dict()
            # nodes 关系使用 selectin lazy，已自动加载
            run_dict["nodes"] = [n.to_dict() for n in run.nodes]
            return run_dict

    async def update_run_status(
        self,
        workflow_run_id: str,
        status: str,
        *,
        error: str | None = None,
        outputs: dict[str, Any] | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> bool:
        """更新工作流运行状态。返回是否成功."""
        values: dict[str, Any] = {"status": status}
        if error is not None:
            values["error"] = error[:2048] if error else None
        if outputs is not None:
            values["outputs"] = outputs
        if started_at is not None:
            values["started_at"] = started_at
        if completed_at is not None:
            values["completed_at"] = completed_at

        async with self._locked_session() as session:
            stmt = update(WorkflowRun).where(WorkflowRun.id == workflow_run_id).values(**values)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def delete_run(self, workflow_run_id: str) -> bool:
        """删除工作流运行记录（级联删除节点）."""
        async with self._locked_session() as session:
            stmt = delete(WorkflowRun).where(WorkflowRun.id == workflow_run_id)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def list_runs(
        self,
        *,
        status: str | None = None,
        owner_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """列出工作流运行记录."""
        async with self._locked_session() as session:
            stmt = select(WorkflowRun).order_by(WorkflowRun.created_at.desc())
            if status:
                stmt = stmt.where(WorkflowRun.status == status)
            if owner_id:
                stmt = stmt.where(WorkflowRun.owner_id == owner_id)
            stmt = stmt.limit(limit).offset(offset)
            result = await session.execute(stmt)
            runs = result.scalars().all()
            return [r.to_dict() for r in runs]

    # Node state operations

    async def init_node_states(
        self,
        workflow_run_id: str,
        nodes: list[dict[str, Any]],
    ) -> None:
        """批量初始化节点状态为 pending.

        Args:
            workflow_run_id: 工作流运行 ID.
            nodes: 节点信息列表，每个元素需包含 node_id, task_type, params.
        """
        async with self._locked_session() as session:
            for node_info in nodes:
                node = WorkflowRunNode(
                    id=_new_node_id(),
                    workflow_run_id=workflow_run_id,
                    node_id=node_info["node_id"],
                    task_type=node_info["task_type"],
                    status="pending",
                    params=node_info.get("params"),
                    retry_count=0,
                )
                session.add(node)
            await session.commit()

    async def get_node_states(self, workflow_run_id: str) -> list[dict[str, Any]]:
        """获取工作流所有节点状态."""
        async with self._locked_session() as session:
            stmt = (
                select(WorkflowRunNode)
                .where(WorkflowRunNode.workflow_run_id == workflow_run_id)
                .order_by(WorkflowRunNode.created_at)
            )
            result = await session.execute(stmt)
            nodes = result.scalars().all()
            return [n.to_dict() for n in nodes]

    async def get_node_state(self, workflow_run_id: str, node_id: str) -> dict[str, Any] | None:
        """获取单个节点状态."""
        async with self._locked_session() as session:
            stmt = select(WorkflowRunNode).where(
                WorkflowRunNode.workflow_run_id == workflow_run_id,
                WorkflowRunNode.node_id == node_id,
            )
            result = await session.execute(stmt)
            node = result.scalar_one_or_none()
            return node.to_dict() if node else None

    async def reset_skipped_nodes(self, workflow_run_id: str) -> int:
        """断点续跑：把上次运行遗留的 skipped 节点重置为 pending（允许重跑）.

        failed / completed 节点保持原状（failed 由调度器重跑，completed 跳过）。
        """
        async with self._locked_session() as session:
            stmt = (
                update(WorkflowRunNode)
                .where(
                    WorkflowRunNode.workflow_run_id == workflow_run_id,
                    WorkflowRunNode.status == "skipped",
                )
                .values(status="pending", error=None)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount or 0

    async def update_node_state(
        self,
        workflow_run_id: str,
        node_id: str,
        *,
        status: str | None = None,
        job_id: str | None = None,
        params: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
        error: str | None = None,
        retry_count: int | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> bool:
        """更新节点状态。返回是否成功."""
        values: dict[str, Any] = {}
        if status is not None:
            values["status"] = status
        if job_id is not None:
            values["job_id"] = job_id
        if params is not None:
            values["params"] = params
        if inputs is not None:
            values["inputs"] = inputs
        if outputs is not None:
            values["outputs"] = outputs
        if metrics is not None:
            values["metrics"] = metrics
        if error is not None:
            values["error"] = error[:2048] if error else None
        if retry_count is not None:
            values["retry_count"] = retry_count
        if started_at is not None:
            values["started_at"] = started_at
        if completed_at is not None:
            values["completed_at"] = completed_at

        if not values:
            return False

        values["updated_at"] = datetime.now(timezone.utc)

        async with self._locked_session() as session:
            stmt = (
                update(WorkflowRunNode)
                .where(
                    WorkflowRunNode.workflow_run_id == workflow_run_id,
                    WorkflowRunNode.node_id == node_id,
                )
                .values(**values)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def get_completed_node_outputs(self, workflow_run_id: str) -> dict[str, dict[str, Any]]:
        """获取所有已完成节点的输出，用于 artifact 引用解析.

        Returns:
            {node_id: {output_name: artifact_dict, ...}, ...}
        """
        async with self._locked_session() as session:
            stmt = select(WorkflowRunNode).where(
                WorkflowRunNode.workflow_run_id == workflow_run_id,
                WorkflowRunNode.status == "completed",
            )
            result = await session.execute(stmt)
            nodes = result.scalars().all()
            return {str(n.node_id): dict(n.outputs or {}) for n in nodes}


# 单例
_dag_store: DAGStore | None = None


def get_dag_store() -> DAGStore:
    """获取 DAGStore 单例."""
    global _dag_store
    if _dag_store is None:
        _dag_store = DAGStore()
    return _dag_store
