"""实验快照存储：ISnapshotStore 实现.

对应 core-contracts-design.md 第 7 章 / ADR-005 阶段 2.

设计要点：
    1. 自动采集 git_sha / environment（通过 GitCollector + sys/version_info）
    2. 持久化到 experiment_snapshots 表（SQLAlchemy 异步 ORM）
    3. reproduce 入口：根据 snapshot 重建 WorkflowSpec 并提交 WorkflowRunner
    4. snapshot 一旦创建不可修改（无 update 方法）

reproduce 策略：
    - snapshot.config 中可包含 ``workflow_spec`` 字段（dict 形式的 WorkflowSpec）
    - 若存在，反序列化为 WorkflowSpec 后调用 WorkflowRunner.run()
    - 若不存在，抛出 NotImplementedError 并提示用户补充复现 spec
    - 复现 run 的 owner_id = "system:reproduce"
"""

from __future__ import annotations

import json
import logging
import platform
import sys
import uuid
from datetime import datetime
from app.utils.time import utcnow
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.observability import ExperimentSnapshot, ISnapshotStore
from app.contracts.task import WorkflowSpec
from app.database.connection import get_sessionmaker
from app.database.models.dataset import ExperimentSnapshot as ExperimentSnapshotORM
from app.observability.git_collector import collect_git_info

logger = logging.getLogger(__name__)


class SnapshotStore(ISnapshotStore):
    """实验快照存储（SQLite 持久化）."""

    async def _get_session(self) -> AsyncSession:
        sessionmaker = get_sessionmaker()
        if sessionmaker is None:
            raise RuntimeError("数据库未配置，无法获取 session")
        return sessionmaker()

    # ISnapshotStore 实现

    async def create(
        self,
        *,
        config: dict[str, Any],
        dataset_versions: list[str],
        model_uri: str,
        metrics: dict[str, float],
        created_by: str,
        notes: str = "",
    ) -> ExperimentSnapshot:
        """创建快照，自动采集 git_sha / environment，写入存储."""
        # 自动采集 git 信息
        git_info = collect_git_info()
        # 自动采集环境信息
        environment = _collect_environment()

        snapshot_id = str(uuid.uuid4())
        created_at = utcnow()

        # 契约层校验（ExperimentSnapshot dataclass __post_init__）
        contract = ExperimentSnapshot(
            snapshot_id=snapshot_id,
            created_at=created_at,
            created_by=created_by,
            git_sha=git_info.git_sha,
            code_dirty=git_info.code_dirty,
            config=config,
            dataset_versions=list(dataset_versions),
            model_uri=model_uri,
            metrics=metrics,
            environment=environment,
            notes=notes,
        )

        # 持久化
        orm = ExperimentSnapshotORM(
            id=snapshot_id,
            created_at=created_at,
            created_by=created_by,
            git_sha=git_info.git_sha,
            code_dirty=git_info.code_dirty,
            config_json=json.dumps(config, ensure_ascii=False, default=str),
            dataset_versions_json=json.dumps(list(dataset_versions), ensure_ascii=False),
            model_uri=model_uri,
            metrics_json=json.dumps(metrics, ensure_ascii=False, default=float),
            environment_json=json.dumps(environment, ensure_ascii=False),
            notes=notes,
        )
        async with await self._get_session() as session:
            session.add(orm)
            await session.commit()

        logger.info(
            "SnapshotStore.create: snapshot_id=%s git_sha=%s dirty=%s model=%s",
            snapshot_id,
            git_info.git_sha,
            git_info.code_dirty,
            model_uri,
        )
        return contract

    async def get(self, snapshot_id: str) -> ExperimentSnapshot:
        """按 ID 取快照，不存在抛 KeyError."""
        async with await self._get_session() as session:
            stmt = select(ExperimentSnapshotORM).where(ExperimentSnapshotORM.id == snapshot_id)
            result = await session.execute(stmt)
            orm = result.scalar_one_or_none()
            if orm is None:
                raise KeyError(f"snapshot 不存在: {snapshot_id}")
            return _orm_to_contract(orm)

    async def list(self, *, filters: dict[str, Any] | None = None) -> list[ExperimentSnapshot]:
        """列出快照（按 created_at 降序）.

        支持的 filters：
            - created_by: str
            - git_sha: str
            - model_uri: str（精确匹配）
        """
        async with await self._get_session() as session:
            stmt = select(ExperimentSnapshotORM).order_by(ExperimentSnapshotORM.created_at.desc())
            if filters:
                if "created_by" in filters:
                    stmt = stmt.where(ExperimentSnapshotORM.created_by == filters["created_by"])
                if "git_sha" in filters:
                    stmt = stmt.where(ExperimentSnapshotORM.git_sha == filters["git_sha"])
                if "model_uri" in filters:
                    stmt = stmt.where(ExperimentSnapshotORM.model_uri == filters["model_uri"])
            result = await session.execute(stmt)
            orms = result.scalars().all()
            return [_orm_to_contract(o) for o in orms]

    async def reproduce(self, snapshot_id: str) -> str:
        """根据 snapshot 恢复环境并启动复现任务.

        Returns:
            workflow_run_id（复现工作流的运行 ID）

        Raises:
            KeyError: snapshot 不存在
            NotImplementedError: snapshot.config 未包含 workflow_spec 字段
            ValueError: workflow_spec 反序列化失败
        """
        snapshot = await self.get(snapshot_id)

        workflow_spec_dict = snapshot.config.get("workflow_spec")
        if not workflow_spec_dict or not isinstance(workflow_spec_dict, dict):
            raise NotImplementedError(
                f"snapshot {snapshot_id} 的 config 未包含可复现的 workflow_spec 字段。"
                "请在创建快照时将完整 WorkflowSpec 序列化到 config['workflow_spec']。"
            )

        spec = _workflow_spec_from_dict(workflow_spec_dict)

        # 延迟导入避免循环依赖
        from app.workflow.runner import get_workflow_runner

        # [C1] get_workflow_runner() 是 async 函数，必须 await
        runner = await get_workflow_runner()
        workflow_run_id = await runner.run(
            spec,
            owner_id="system:reproduce",
        )
        logger.info(
            "SnapshotStore.reproduce: snapshot_id=%s → workflow_run_id=%s",
            snapshot_id,
            workflow_run_id,
        )
        return workflow_run_id


# 辅助函数


def _collect_environment() -> dict[str, str]:
    """采集当前运行环境信息."""
    env: dict[str, str] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
    # 关键包版本（best-effort，包不存在时跳过）
    for pkg in ("torch", "numpy", "pandas", "scikit-learn", "xgboost", "sqlalchemy"):
        try:
            mod = __import__(pkg)
            env[pkg] = getattr(mod, "__version__", "unknown")
        except Exception:
            env[pkg] = "not-installed"
    return env


def _orm_to_contract(orm: ExperimentSnapshotORM) -> ExperimentSnapshot:
    """ORM → 契约 dataclass.

    ORM 用经典 ``Column(...)`` 声明式风格，mypy 会把实例属性推断为
    ``Column[T]`` 而非 ``T``（SQLAlchemy 1.x 无 Mapped 注解）。
    这里通过 ``cast`` 显式解包，声明 ORM 边界处的运行时值类型。
    """
    from typing import cast

    try:
        config = json.loads(cast(str, orm.config_json)) if orm.config_json else {}
    except json.JSONDecodeError:
        config = {"_decode_error": True, "raw": cast(str, orm.config_json)}

    try:
        dataset_versions = json.loads(cast(str, orm.dataset_versions_json)) if orm.dataset_versions_json else []
    except json.JSONDecodeError:
        dataset_versions = []

    try:
        metrics = json.loads(cast(str, orm.metrics_json)) if orm.metrics_json else {}
    except json.JSONDecodeError:
        metrics = {}

    try:
        environment = json.loads(cast(str, orm.environment_json)) if orm.environment_json else {}
    except json.JSONDecodeError:
        environment = {}

    return ExperimentSnapshot(
        snapshot_id=cast(str, orm.id),
        created_at=cast(datetime, orm.created_at),
        created_by=cast(str, orm.created_by),
        git_sha=cast(str, orm.git_sha),
        code_dirty=cast(bool, orm.code_dirty),
        config=config,
        dataset_versions=list(dataset_versions),
        model_uri=cast(str, orm.model_uri),
        metrics=metrics,
        environment=environment,
        lineage_record_id=cast(str | None, orm.lineage_record_id),
        mlflow_run_id=cast(str | None, orm.mlflow_run_id),
        notes=cast(str, orm.notes) or "",
    )


def _infer_artifact_type(uri: str) -> str:
    """按 URI scheme 推断 Artifact.type（契约约定）。

    映射：
    - ``dataset://`` → "dataset"
    - ``model://`` → "model"
    - ``report://`` / ``file://...report`` → "report"
    - ``metrics://`` → "metrics"
    - 其它 → "file"
    """
    if uri.startswith("dataset://"):
        return "dataset"
    if uri.startswith("model://"):
        return "model"
    if uri.startswith("metrics://"):
        return "metrics"
    if uri.startswith("report://") or "report" in uri:
        return "report"
    return "file"


def _workflow_spec_from_dict(spec_dict: dict[str, Any]) -> WorkflowSpec:
    """从 dict 反序列化 WorkflowSpec.

    Args:
        spec_dict: 形如
            {
                "name": "ltc-train",
                "version": "1.0.0",
                "nodes": [{"node_id": "...", "task_type": "...", "params": {...}}, ...],
                "edges": [{"upstream": "...", "downstream": "..."}, ...],
                "inputs": {...},   # optional
                "outputs": {...},  # optional
                "metadata": {...}  # optional
            }
    """
    from app.contracts.task import Artifact, WorkflowEdge, WorkflowNode

    nodes = [
        WorkflowNode(
            node_id=n["node_id"],
            task_type=n["task_type"],
            params=n.get("params", {}),
            inputs=n.get("inputs", {}),
            retry=n.get("retry", 0),
            timeout_seconds=n.get("timeout_seconds", 3600),
        )
        for n in spec_dict.get("nodes", [])
    ]
    edges = [WorkflowEdge(upstream=e["upstream"], downstream=e["downstream"]) for e in spec_dict.get("edges", [])]
    # inputs 形如 {"name": {"type": "dataset", "uri": "...", "metadata": {...}}}
    # 兼容旧格式（含 mime_type 而非 type）：type 缺省时按 URI scheme 推断
    inputs: dict[str, Artifact] = {}
    for name, art_dict in spec_dict.get("inputs", {}).items():
        if isinstance(art_dict, dict):
            uri = art_dict.get("uri", "")
            artifact_type = art_dict.get("type") or _infer_artifact_type(uri)
            metadata = dict(art_dict.get("metadata", {}))
            # 兼容旧格式：mime_type 并入 metadata（非契约字段，不直接传 Artifact）
            if "mime_type" in art_dict:
                metadata.setdefault("mime_type", art_dict["mime_type"])
            inputs[name] = Artifact(
                name=name,
                type=artifact_type,
                uri=uri,
                metadata=metadata,
            )

    return WorkflowSpec(
        name=spec_dict["name"],
        version=spec_dict["version"],
        nodes=nodes,
        edges=edges,
        inputs=inputs,
        outputs=spec_dict.get("outputs", {}),
        metadata=spec_dict.get("metadata", {}),
    )


# 单例


_snapshot_store: SnapshotStore | None = None


def get_snapshot_store() -> SnapshotStore:
    """获取全局 SnapshotStore 单例."""
    global _snapshot_store
    if _snapshot_store is None:
        _snapshot_store = SnapshotStore()
    return _snapshot_store


__all__ = [
    "SnapshotStore",
    "get_snapshot_store",
]
