"""ILineageStore 的 SQLite 实现.

对应 ADR-005 阶段 2 / core-contracts-design.md 第 4 章。

血缘图模型：
    每条 LineageRecord 描述 "target 由 inputs 经 operation 产出"。
    - target / inputs / outputs 均为 URI（dataset:// / model:// / artifact://）
    - 上游查询：找 outputs 含 target_uri 的记录，再对这些记录的 inputs 递归
    - 下游查询：找 inputs 含 target_uri 的记录，再对这些记录的 outputs 递归

实现说明：
    inputs_json / outputs_json 为 JSON 数组存在 Text 列，跨数据库 JSON 查询兼容性
    较差，因此递归查询在 Python 层完成。数据规模在实验管理场景下可接受
    （典型 < 10^4 条）。后续若需扩展，可引入 SQLAlchemy JSON 函数或图数据库。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import deque
from datetime import datetime

from app.utils.time import utcnow
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.dataset import ILineageStore, LineageRecord
from app.database.connection import get_sessionmaker
from app.database.models.dataset import LineageRecord as LineageRecordORM

logger = logging.getLogger(__name__)


# 工厂与转换辅助


def make_lineage_record(
    *,
    target: str,
    source_type: str,
    source_ref: str,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    operation: str = "",
    metadata: dict[str, Any] | None = None,
    record_id: str | None = None,
    timestamp: datetime | None = None,
) -> LineageRecord:
    """构造 LineageRecord，自动生成 record_id 与 timestamp.

    便于调用方避免手动管理 uuid 与时间戳。
    """
    return LineageRecord(
        record_id=record_id or str(uuid.uuid4()),
        target=target,
        source_type=source_type,
        source_ref=source_ref,
        inputs=list(inputs) if inputs else [],
        outputs=list(outputs) if outputs else [],
        operation=operation,
        timestamp=timestamp or utcnow(),
        metadata=dict(metadata) if metadata else {},
    )


def _orm_to_contract(orm: LineageRecordORM) -> LineageRecord:
    return LineageRecord(
        record_id=str(orm.id),
        target=str(orm.target),
        source_type=str(orm.source_type),
        source_ref=str(orm.source_ref),
        inputs=json.loads(str(orm.inputs_json)) if orm.inputs_json else [],
        outputs=json.loads(str(orm.outputs_json)) if orm.outputs_json else [],
        operation=str(orm.operation),
        timestamp=cast(datetime, orm.timestamp),  # ORM nullable=False
        metadata=json.loads(str(orm.meta_json)) if orm.meta_json else {},
    )


class LineageStore(ILineageStore):
    """ILineageStore 默认实现：SQLite 持久化 + Python 层递归查询."""

    async def _get_session(self) -> AsyncSession:
        sessionmaker = get_sessionmaker()
        if sessionmaker is None:
            raise RuntimeError("数据库未配置，无法获取 session")
        return sessionmaker()

    async def record(self, lineage: LineageRecord) -> str:
        """记录一条血缘。返回 record_id。"""
        orm = LineageRecordORM(
            id=lineage.record_id,
            target=lineage.target,
            source_type=lineage.source_type,
            source_ref=lineage.source_ref,
            inputs_json=json.dumps(lineage.inputs, ensure_ascii=False),
            outputs_json=json.dumps(lineage.outputs, ensure_ascii=False),
            operation=lineage.operation,
            timestamp=lineage.timestamp,
            meta_json=json.dumps(lineage.metadata, ensure_ascii=False),
        )
        async with await self._get_session() as session:
            session.add(orm)
            await session.commit()
        logger.info(
            "血缘已记录: id=%s target=%s operation=%s",
            lineage.record_id,
            lineage.target,
            lineage.operation,
        )
        return lineage.record_id

    async def _load_all(self) -> list[LineageRecord]:
        """加载全部血缘记录（用于 Python 层递归查询）."""
        async with await self._get_session() as session:
            stmt = select(LineageRecordORM).order_by(LineageRecordORM.timestamp.asc())
            result = await session.execute(stmt)
            return [_orm_to_contract(orm) for orm in result.scalars().all()]

    async def get_upstream(self, target_uri: str, *, depth: int = 10) -> list[LineageRecord]:
        """查询上游血缘（递归到 depth 层）。

        返回的列表按发现顺序（BFS），不含 target_uri 自身。
        """
        if depth <= 0:
            return []

        all_records = await self._load_all()
        # target_uri 直接产出它的 records
        # 索引：output_uri [records]
        output_index: dict[str, list[LineageRecord]] = {}
        for rec in all_records:
            for out_uri in rec.outputs:
                output_index.setdefault(out_uri, []).append(rec)

        visited: set[str] = set()
        result: list[LineageRecord] = []
        queue: deque[tuple[str, int]] = deque([(target_uri, 0)])

        while queue:
            uri, cur_depth = queue.popleft()
            if cur_depth >= depth:
                continue
            for rec in output_index.get(uri, []):
                if rec.record_id in visited:
                    continue
                visited.add(rec.record_id)
                result.append(rec)
                # 继续向上追溯该 record 的每个 input
                for in_uri in rec.inputs:
                    queue.append((in_uri, cur_depth + 1))

        return result

    async def get_downstream(self, target_uri: str, *, depth: int = 10) -> list[LineageRecord]:
        """查询下游血缘（递归到 depth 层）。"""
        if depth <= 0:
            return []

        all_records = await self._load_all()
        # 索引：input_uri [records]
        input_index: dict[str, list[LineageRecord]] = {}
        for rec in all_records:
            for in_uri in rec.inputs:
                input_index.setdefault(in_uri, []).append(rec)

        visited: set[str] = set()
        result: list[LineageRecord] = []
        queue: deque[tuple[str, int]] = deque([(target_uri, 0)])

        while queue:
            uri, cur_depth = queue.popleft()
            if cur_depth >= depth:
                continue
            for rec in input_index.get(uri, []):
                if rec.record_id in visited:
                    continue
                visited.add(rec.record_id)
                result.append(rec)
                for out_uri in rec.outputs:
                    queue.append((out_uri, cur_depth + 1))

        return result

    async def visualize(self, target_uri: str) -> dict[str, Any]:
        """返回节点/边数据，前端渲染血缘图.

        合并上游与下游，构造无向图节点/边列表。
        """
        upstream = await self.get_upstream(target_uri, depth=10)
        downstream = await self.get_downstream(target_uri, depth=10)

        nodes: dict[str, dict[str, Any]] = {
            target_uri: {
                "id": target_uri,
                "label": target_uri,
                "type": _infer_uri_type(target_uri),
                "is_target": True,
            }
        }
        edges: list[dict[str, Any]] = []

        # 上游：inputs target（record.target 为直接下游 URI）
        for rec in upstream:
            for in_uri in rec.inputs:
                if in_uri not in nodes:
                    nodes[in_uri] = {
                        "id": in_uri,
                        "label": in_uri,
                        "type": _infer_uri_type(in_uri),
                        "is_target": False,
                    }
            if rec.target not in nodes:
                nodes[rec.target] = {
                    "id": rec.target,
                    "label": rec.target,
                    "type": _infer_uri_type(rec.target),
                    "is_target": rec.target == target_uri,
                }
            for in_uri in rec.inputs:
                edges.append(
                    {
                        "source": in_uri,
                        "target": rec.target,
                        "operation": rec.operation,
                        "record_id": rec.record_id,
                    }
                )

        # 下游：target outputs
        for rec in downstream:
            for out_uri in rec.outputs:
                if out_uri not in nodes:
                    nodes[out_uri] = {
                        "id": out_uri,
                        "label": out_uri,
                        "type": _infer_uri_type(out_uri),
                        "is_target": False,
                    }
            if rec.target not in nodes:
                nodes[rec.target] = {
                    "id": rec.target,
                    "label": rec.target,
                    "type": _infer_uri_type(rec.target),
                    "is_target": rec.target == target_uri,
                }
            for in_uri in rec.inputs:
                edges.append(
                    {
                        "source": in_uri,
                        "target": rec.target,
                        "operation": rec.operation,
                        "record_id": rec.record_id,
                    }
                )

        return {
            "nodes": list(nodes.values()),
            "edges": edges,
            "target": target_uri,
        }


def _infer_uri_type(uri: str) -> str:
    """根据 URI scheme 推断类型：dataset / model / artifact / unknown."""
    if uri.startswith("dataset://"):
        return "dataset"
    if uri.startswith("model://"):
        return "model"
    if uri.startswith("artifact://"):
        return "artifact"
    return "unknown"


# 单例访问


_singleton: LineageStore | None = None


def get_lineage_store() -> LineageStore:
    """获取 LineageStore 单例."""
    global _singleton
    if _singleton is None:
        _singleton = LineageStore()
    return _singleton


__all__ = [
    "LineageStore",
    "get_lineage_store",
    "make_lineage_record",
]
