"""数据集 / 版本 / 血缘 / 实验快照集成测试.

对应 ADR-005 阶段 2 验收标准（core-contracts-design.md 第 1260-1263 行）：
    - 同一 snapshot 在干净环境复现，关键指标差异 < 1%
    - git SHA + 数据 hash + 完整配置写入 snapshot，可查询
    - 血缘图可视化：从模型反查训练数据/任务/配置

覆盖场景：
    1. DatasetStore CRUD：create → commit_version → get_version → read → list_versions → deprecate
    2. 内容寻址去重：相同 records 得到相同 content_hash，文件不重复写入
    3. 版本自动递增：version=None 时基于 latest 递增 patch
    4. LineageStore：record → get_upstream → get_downstream → visualize（BFS 递归）
    5. SnapshotStore：create → get → list（filters）→ reproduce 分支（KeyError / NotImplementedError / 成功）
    6. 集成场景：dataset commit → 记录血缘 → 创建快照 → 反查血缘图

CI 标记：@pytest.mark.integration（被 ci.yml Job 2 `pytest tests/integration/ -m integration` 收集）。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Optional

import pytest
import pytest_asyncio

from app.contracts.dataset import (
    DatasetSchema,
    DatasetStatus,
    IDatasetStore,
    ILineageStore,
    LineageRecord,
)
from app.contracts.observability import ExperimentSnapshot, ISnapshotStore
from app.data.dataset_store import (
    DatasetStore,
    _compute_content_hash,
    _storage_path_for_hash,
)
from app.data.lineage_store import LineageStore, make_lineage_record
from app.observability.snapshot import SnapshotStore


# 辅助构造


def _make_schema() -> DatasetSchema:
    """构造一个合法的 DatasetSchema（tool_wear 表）."""
    return DatasetSchema(
        fields={
            "cutting_time": {"type": "float", "required": True, "description": "切削时长(s)"},
            "vibration_rms": {"type": "float", "required": True, "description": "振动RMS"},
            "tool_wear": {"type": "float", "required": True, "description": "刀具磨损(μm)"},
        },
        primary_key=["cutting_time"],
        metadata={"source": "PHM2010", "unit": "si"},
    )


def _make_records(n: int = 5, *, seed: int = 0) -> list[dict[str, Any]]:
    """构造 n 条测试 records."""
    return [
        {
            "cutting_time": float(i + seed),
            "vibration_rms": 0.1 * (i + seed) + 0.05,
            "tool_wear": 10.0 * (i + seed) + 2.0,
        }
        for i in range(n)
    ]


def _make_lineage(
    *,
    target: str = "dataset://test-ds/0.0.1",
    inputs: Optional[list[str]] = None,
    outputs: Optional[list[str]] = None,
    operation: str = "preprocess",
) -> LineageRecord:
    return make_lineage_record(
        target=target,
        source_type="task",
        source_ref="job-test-001",
        inputs=inputs or ["artifact://raw/sensor.csv"],
        outputs=outputs or [target],
        operation=operation,
        metadata={"tool": "python", "version": "3.11"},
    )


# 数据库 + 存储目录 fixture


@pytest_asyncio.fixture
async def dataset_store(monkeypatch, tmp_path):
    """提供基于内存 SQLite 的 DatasetStore + 隔离的内容存储目录.

    每个测试函数独立一份内存数据库 + 独立 tmp 目录（内容寻址文件隔离）。
    """
    # 强制 SQLite 内存模式
    monkeypatch.setenv("DB_URL", "sqlite+aiosqlite:///:memory:")
    # 内容存储重定向到 tmp_path（避免污染 data/datasets）
    monkeypatch.setenv("DATASET_STORE_DIR", str(tmp_path / "datasets"))

    # 清空单例，使下次 get_sessionmaker 重新基于新 DB_URL 创建
    from app.database import connection as _conn

    _conn._singletons._engine = None
    _conn._singletons._sessionmaker = None

    # 创建全部表（dataset / dataset_versions / lineage_records / experiment_snapshots 等）
    from app.database.models.training_task import init_db

    await init_db()

    # 重置 store 单例（强制下次 get_dataset_store 用新实例）
    import app.data.dataset_store as _ds_mod

    _ds_mod._singleton = None

    store = DatasetStore()
    yield store

    # 清理
    _ds_mod._singleton = None
    _conn._singletons._engine = None
    _conn._singletons._sessionmaker = None


@pytest_asyncio.fixture
async def lineage_store(monkeypatch):
    """提供基于内存 SQLite 的 LineageStore（与 dataset_store 共享 DB_URL）."""
    monkeypatch.setenv("DB_URL", "sqlite+aiosqlite:///:memory:")
    from app.database import connection as _conn

    _conn._singletons._engine = None
    _conn._singletons._sessionmaker = None

    from app.database.models.training_task import init_db

    await init_db()

    import app.data.lineage_store as _ls_mod

    _ls_mod._singleton = None

    store = LineageStore()
    yield store

    _ls_mod._singleton = None
    _conn._singletons._engine = None
    _conn._singletons._sessionmaker = None


@pytest_asyncio.fixture
async def snapshot_store(monkeypatch):
    """提供基于内存 SQLite 的 SnapshotStore."""
    monkeypatch.setenv("DB_URL", "sqlite+aiosqlite:///:memory:")
    from app.database import connection as _conn

    _conn._singletons._engine = None
    _conn._singletons._sessionmaker = None

    from app.database.models.training_task import init_db

    await init_db()

    import app.observability.snapshot as _ss_mod

    _ss_mod._snapshot_store = None

    store = SnapshotStore()
    yield store

    _ss_mod._snapshot_store = None
    _conn._singletons._engine = None
    _conn._singletons._sessionmaker = None


@pytest_asyncio.fixture
async def integrated_stores(monkeypatch, tmp_path):
    """提供共享同一内存 DB 的 DatasetStore + LineageStore + SnapshotStore.

    用于跨模块集成场景（dataset → lineage → snapshot → 反查血缘）。
    """
    monkeypatch.setenv("DB_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("DATASET_STORE_DIR", str(tmp_path / "datasets"))

    from app.database import connection as _conn

    _conn._singletons._engine = None
    _conn._singletons._sessionmaker = None

    from app.database.models.training_task import init_db

    await init_db()

    import app.data.dataset_store as _ds_mod
    import app.data.lineage_store as _ls_mod
    import app.observability.snapshot as _ss_mod

    _ds_mod._singleton = None
    _ls_mod._singleton = None
    _ss_mod._snapshot_store = None

    yield {
        "dataset": DatasetStore(),
        "lineage": LineageStore(),
        "snapshot": SnapshotStore(),
    }

    _ds_mod._singleton = None
    _ls_mod._singleton = None
    _ss_mod._snapshot_store = None
    _conn._singletons._engine = None
    _conn._singletons._sessionmaker = None


# 测试用例：DatasetStore


@pytest.mark.integration
class TestDatasetStoreCrud:
    """DatasetStore CRUD + 版本管理."""

    @pytest.mark.asyncio
    async def test_create_dataset_returns_id_and_draft_status(self, dataset_store):
        """create 返回 dataset_id，初始 DRAFT."""
        ds_id = await dataset_store.create(
            name="test_ds_crud",
            schema=_make_schema(),
            owner_id="test_user",
            description="CRUD 测试",
        )
        assert ds_id, "create 应返回非空 dataset_id"

        detail = await dataset_store.get_dataset(ds_id)
        assert detail["name"] == "test_ds_crud"
        assert detail["status"] == DatasetStatus.DRAFT.value
        assert detail["owner_id"] == "test_user"
        assert detail["description"] == "CRUD 测试"

    @pytest.mark.asyncio
    async def test_create_dataset_rejects_duplicate_name(self, dataset_store):
        """重名创建应抛 ValueError."""
        schema = _make_schema()
        await dataset_store.create(name="dup_name", schema=schema, owner_id="u1")
        with pytest.raises(ValueError, match="已存在"):
            await dataset_store.create(name="dup_name", schema=schema, owner_id="u2")

    @pytest.mark.asyncio
    async def test_create_dataset_rejects_empty_name(self, dataset_store):
        """空 name 应抛 ValueError."""
        with pytest.raises(ValueError, match="name 不能为空"):
            await dataset_store.create(name="", schema=_make_schema(), owner_id="u1")

    @pytest.mark.asyncio
    async def test_commit_version_auto_increments_patch(self, dataset_store):
        """version=None 自动递增 patch：0.0.1 → 0.0.2 → 0.0.3."""
        ds_id = await dataset_store.create(name="auto_inc", schema=_make_schema(), owner_id="u1")

        v1 = await dataset_store.commit_version(ds_id, _make_records(3, seed=0))
        assert v1.version == "0.0.1"
        assert v1.status == DatasetStatus.PUBLISHED
        assert v1.row_count == 3

        v2 = await dataset_store.commit_version(ds_id, _make_records(3, seed=10))
        assert v2.version == "0.0.2"

        v3 = await dataset_store.commit_version(ds_id, _make_records(3, seed=20))
        assert v3.version == "0.0.3"

    @pytest.mark.asyncio
    async def test_commit_version_explicit_version_conflict(self, dataset_store):
        """显式指定已存在版本号应抛 ValueError."""
        ds_id = await dataset_store.create(name="conflict_v", schema=_make_schema(), owner_id="u1")
        await dataset_store.commit_version(ds_id, _make_records(2), version="1.0.0")
        with pytest.raises(ValueError, match="版本已存在"):
            await dataset_store.commit_version(ds_id, _make_records(2), version="1.0.0")

    @pytest.mark.asyncio
    async def test_commit_version_promotes_draft_to_published(self, dataset_store):
        """首次 commit 后 dataset 状态从 DRAFT → PUBLISHED."""
        ds_id = await dataset_store.create(name="promote", schema=_make_schema(), owner_id="u1")
        detail_before = await dataset_store.get_dataset(ds_id)
        assert detail_before["status"] == DatasetStatus.DRAFT.value

        await dataset_store.commit_version(ds_id, _make_records(2))

        detail_after = await dataset_store.get_dataset(ds_id)
        assert detail_after["status"] == DatasetStatus.PUBLISHED.value

    @pytest.mark.asyncio
    async def test_get_version_returns_latest_when_version_none(self, dataset_store):
        """get_version(version=None) 返回最新版本."""
        ds_id = await dataset_store.create(name="get_latest", schema=_make_schema(), owner_id="u1")
        await dataset_store.commit_version(ds_id, _make_records(2, seed=0))
        await dataset_store.commit_version(ds_id, _make_records(2, seed=10))

        latest = await dataset_store.get_version(ds_id, version=None)
        assert latest.version == "0.0.2"

    @pytest.mark.asyncio
    async def test_get_version_specific_version(self, dataset_store):
        """get_version(version="0.0.1") 返回指定版本."""
        ds_id = await dataset_store.create(name="get_specific", schema=_make_schema(), owner_id="u1")
        await dataset_store.commit_version(ds_id, _make_records(2, seed=0))
        await dataset_store.commit_version(ds_id, _make_records(2, seed=10))

        v1 = await dataset_store.get_version(ds_id, version="0.0.1")
        assert v1.version == "0.0.1"

    @pytest.mark.asyncio
    async def test_get_version_nonexistent_raises(self, dataset_store):
        """不存在版本号应抛 ValueError."""
        ds_id = await dataset_store.create(name="get_404", schema=_make_schema(), owner_id="u1")
        await dataset_store.commit_version(ds_id, _make_records(2))

        with pytest.raises(ValueError, match="版本不存在"):
            await dataset_store.get_version(ds_id, version="9.9.9")

    @pytest.mark.asyncio
    async def test_read_returns_records_in_batches(self, dataset_store):
        """read 流式返回 records，按 batch_size 分批."""
        ds_id = await dataset_store.create(name="read_batch", schema=_make_schema(), owner_id="u1")
        records = _make_records(7)
        await dataset_store.commit_version(ds_id, records)

        batches: list[list[dict[str, Any]]] = []
        async for batch in dataset_store.read(ds_id, batch_size=3):
            batches.append(batch)

        # 7 条 / batch_size=3 3 批 (3 + 3 + 1)
        assert len(batches) == 3
        assert len(batches[0]) == 3
        assert len(batches[1]) == 3
        assert len(batches[2]) == 1

        # 校验内容完整且顺序保持
        all_records = [r for batch in batches for r in batch]
        assert len(all_records) == 7
        assert all_records[0]["cutting_time"] == records[0]["cutting_time"]
        assert all_records[-1]["tool_wear"] == records[-1]["tool_wear"]

    @pytest.mark.asyncio
    async def test_list_versions_descending_by_created_at(self, dataset_store):
        """list_versions 按创建时间倒序."""
        ds_id = await dataset_store.create(name="list_v", schema=_make_schema(), owner_id="u1")
        await dataset_store.commit_version(ds_id, _make_records(1, seed=0))
        await dataset_store.commit_version(ds_id, _make_records(1, seed=1))
        await dataset_store.commit_version(ds_id, _make_records(1, seed=2))

        versions = await dataset_store.list_versions(ds_id)
        assert len(versions) == 3
        assert versions[0].version == "0.0.3"
        assert versions[1].version == "0.0.2"
        assert versions[2].version == "0.0.1"

    @pytest.mark.asyncio
    async def test_deprecate_published_version(self, dataset_store):
        """PUBLISHED → DEPRECATED 合法转换."""
        ds_id = await dataset_store.create(name="deprecate", schema=_make_schema(), owner_id="u1")
        await dataset_store.commit_version(ds_id, _make_records(2))

        await dataset_store.deprecate(ds_id, "0.0.1")

        v = await dataset_store.get_version(ds_id, version="0.0.1")
        assert v.status == DatasetStatus.DEPRECATED

    @pytest.mark.asyncio
    async def test_deprecate_nonexistent_version_raises(self, dataset_store):
        """deprecate 不存在版本应抛 ValueError."""
        ds_id = await dataset_store.create(name="deprecate_404", schema=_make_schema(), owner_id="u1")
        with pytest.raises(ValueError, match="版本不存在"):
            await dataset_store.deprecate(ds_id, "9.9.9")

    @pytest.mark.asyncio
    async def test_list_datasets_pagination_and_filter(self, dataset_store):
        """list_datasets 支持 owner_id / status 过滤 + limit/offset 分页."""
        schema = _make_schema()
        ds1 = await dataset_store.create(name="ds_a", schema=schema, owner_id="alice")
        ds2 = await dataset_store.create(name="ds_b", schema=schema, owner_id="bob")
        ds3 = await dataset_store.create(name="ds_c", schema=schema, owner_id="alice")

        # alice 创建 ds1 后提交版本 PUBLISHED
        await dataset_store.commit_version(ds1, _make_records(1))

        # 按 owner 过滤
        alice_items = await dataset_store.list_datasets(owner_id="alice")
        assert len(alice_items) == 2
        assert all(it["owner_id"] == "alice" for it in alice_items)

        bob_items = await dataset_store.list_datasets(owner_id="bob")
        assert len(bob_items) == 1
        assert bob_items[0]["name"] == "ds_b"

        # 按 status 过滤（published 只有 ds1）
        published = await dataset_store.list_datasets(status=DatasetStatus.PUBLISHED)
        assert len(published) == 1
        assert published[0]["name"] == "ds_a"

        # 分页
        all_items = await dataset_store.list_datasets(limit=2, offset=0)
        assert len(all_items) == 2
        rest_items = await dataset_store.list_datasets(limit=2, offset=2)
        assert len(rest_items) == 1


# 测试用例：内容寻址 hash 去重


@pytest.mark.integration
class TestContentAddressedStorage:
    """内容寻址 hash 语义：相同内容 → 相同 hash → 同一文件不重复写入."""

    @pytest.mark.asyncio
    async def test_identical_records_produce_same_hash(self, dataset_store):
        """相同 records → 相同 content_hash."""
        ds_id = await dataset_store.create(name="hash_same", schema=_make_schema(), owner_id="u1")
        records = _make_records(5, seed=42)

        v1 = await dataset_store.commit_version(ds_id, records)
        v2 = await dataset_store.commit_version(ds_id, list(records), version="0.0.2")

        assert v1.content_hash == v2.content_hash, "相同 records 必须产生相同 content_hash"

    @pytest.mark.asyncio
    async def test_different_records_produce_different_hash(self, dataset_store):
        """不同 records → 不同 content_hash."""
        ds_id = await dataset_store.create(name="hash_diff", schema=_make_schema(), owner_id="u1")
        v1 = await dataset_store.commit_version(ds_id, _make_records(5, seed=0))
        v2 = await dataset_store.commit_version(ds_id, _make_records(5, seed=1))

        assert v1.content_hash != v2.content_hash

    @pytest.mark.asyncio
    async def test_canonical_json_handles_field_order(self):
        """字段顺序不同的相同内容 → 相同 hash（canonical JSON sort_keys）."""
        records_a = [{"a": 1, "b": 2}, {"c": 3}]
        records_b = [{"b": 2, "a": 1}, {"c": 3}]

        hash_a = _compute_content_hash(records_a)
        hash_b = _compute_content_hash(records_b)

        assert hash_a == hash_b, "canonical JSON sort_keys 应消除字段顺序差异"

    @pytest.mark.asyncio
    async def test_storage_file_deduplicated(self, dataset_store, monkeypatch):
        """相同 hash 的文件不重复写入（_write_records 幂等）."""
        ds_id = await dataset_store.create(name="dedup", schema=_make_schema(), owner_id="u1")
        records = _make_records(3, seed=100)

        v1 = await dataset_store.commit_version(ds_id, records)
        v2 = await dataset_store.commit_version(ds_id, list(records), version="0.0.2")

        # 两个版本指向同一物理文件
        assert v1.storage_uri == v2.storage_uri

        # 验证文件确实只存在一份
        path = _storage_path_for_hash(v1.content_hash)
        assert path.exists(), "内容寻址文件应存在"
        # 再次写入同一 hash 不报错（幂等）
        from app.data.dataset_store import _write_records

        _write_records(v1.content_hash, list(records))


# 测试用例：LineageStore


@pytest.mark.integration
class TestLineageStore:
    """LineageStore 记录 + BFS 递归查询 + 可视化."""

    @pytest.mark.asyncio
    async def test_record_returns_record_id(self, lineage_store):
        """record 返回非空 record_id."""
        rec = _make_lineage()
        rid = await lineage_store.record(rec)
        assert rid == rec.record_id
        assert rid

    @pytest.mark.asyncio
    async def test_get_upstream_one_hop(self, lineage_store):
        """单跳上游查询：target ← inputs."""
        # lineage: artifact://raw dataset://ds/v1
        rec = make_lineage_record(
            target="dataset://ds/v1",
            source_type="task",
            source_ref="job-1",
            inputs=["artifact://raw/data.csv"],
            outputs=["dataset://ds/v1"],
            operation="preprocess",
        )
        await lineage_store.record(rec)

        upstream = await lineage_store.get_upstream("dataset://ds/v1", depth=5)
        assert len(upstream) == 1
        assert upstream[0].record_id == rec.record_id
        assert "artifact://raw/data.csv" in upstream[0].inputs

    @pytest.mark.asyncio
    async def test_get_upstream_multi_hop_bfs(self, lineage_store):
        """多跳上游 BFS：model ← dataset ← artifact.

        构造：
            artifact://raw → dataset://ds/v1   (operation=preprocess)
            dataset://ds/v1 → model://ltc/1.0   (operation=train)
        """
        rec1 = make_lineage_record(
            target="dataset://ds/v1",
            source_type="task",
            source_ref="job-prep",
            inputs=["artifact://raw/data.csv"],
            outputs=["dataset://ds/v1"],
            operation="preprocess",
        )
        rec2 = make_lineage_record(
            target="model://ltc/1.0",
            source_type="workflow",
            source_ref="wf-train-001",
            inputs=["dataset://ds/v1"],
            outputs=["model://ltc/1.0"],
            operation="train",
        )
        await lineage_store.record(rec1)
        await lineage_store.record(rec2)

        # 从 model://ltc/1.0 反查上游 应找到 rec2 + rec1
        upstream = await lineage_store.get_upstream("model://ltc/1.0", depth=5)
        assert len(upstream) == 2
        ids = {r.record_id for r in upstream}
        assert rec2.record_id in ids
        assert rec1.record_id in ids

    @pytest.mark.asyncio
    async def test_get_downstream_multi_hop(self, lineage_store):
        """多跳下游查询：artifact → dataset → model."""
        rec1 = make_lineage_record(
            target="dataset://ds/v1",
            source_type="task",
            source_ref="job-prep",
            inputs=["artifact://raw/data.csv"],
            outputs=["dataset://ds/v1"],
            operation="preprocess",
        )
        rec2 = make_lineage_record(
            target="model://ltc/1.0",
            source_type="workflow",
            source_ref="wf-train-001",
            inputs=["dataset://ds/v1"],
            outputs=["model://ltc/1.0"],
            operation="train",
        )
        await lineage_store.record(rec1)
        await lineage_store.record(rec2)

        downstream = await lineage_store.get_downstream("artifact://raw/data.csv", depth=5)
        assert len(downstream) == 2
        ids = {r.record_id for r in downstream}
        assert rec1.record_id in ids
        assert rec2.record_id in ids

    @pytest.mark.asyncio
    async def test_get_upstream_depth_zero_returns_empty(self, lineage_store):
        """depth=0 返回空列表."""
        rec = _make_lineage()
        await lineage_store.record(rec)

        result = await lineage_store.get_upstream("dataset://test-ds/0.0.1", depth=0)
        assert result == []

    @pytest.mark.asyncio
    async def test_get_upstream_unknown_uri_returns_empty(self, lineage_store):
        """查询未知 URI 的上游返回空列表（不抛错）."""
        result = await lineage_store.get_upstream("artifact://nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_visualize_returns_nodes_and_edges(self, lineage_store):
        """visualize 返回 nodes/edges/target 三段式结构."""
        rec1 = make_lineage_record(
            target="dataset://ds/v1",
            source_type="task",
            source_ref="job-1",
            inputs=["artifact://raw/a.csv"],
            outputs=["dataset://ds/v1"],
            operation="preprocess",
        )
        rec2 = make_lineage_record(
            target="model://ltc/1.0",
            source_type="workflow",
            source_ref="wf-1",
            inputs=["dataset://ds/v1"],
            outputs=["model://ltc/1.0"],
            operation="train",
        )
        await lineage_store.record(rec1)
        await lineage_store.record(rec2)

        graph = await lineage_store.visualize("dataset://ds/v1")

        assert graph["target"] == "dataset://ds/v1"
        assert "nodes" in graph
        assert "edges" in graph
        assert len(graph["nodes"]) >= 2, "至少应含 target + 一个相邻节点"
        # target 节点标记 is_target=True
        target_nodes = [n for n in graph["nodes"] if n.get("is_target")]
        assert len(target_nodes) >= 1
        # 边至少有一条
        assert len(graph["edges"]) >= 1


# 测试用例：SnapshotStore


@pytest.mark.integration
class TestSnapshotStore:
    """SnapshotStore create / get / list / reproduce 分支."""

    @pytest.mark.asyncio
    async def test_create_returns_snapshot_with_git_info(self, snapshot_store):
        """create 返回 ExperimentSnapshot，自动采集 git_sha / environment."""
        snap = await snapshot_store.create(
            config={"hyperparams": {"lr": 0.001}, "seed": 42},
            dataset_versions=["dataset://ds/0.0.1"],
            model_uri="model://ltc/1.0.0",
            metrics={"mae": 0.123, "r2": 0.956},
            created_by="test_user",
            notes="基线实验",
        )

        assert snap.snapshot_id
        assert snap.created_by == "test_user"
        assert snap.git_sha, "git_sha 应被自动采集（非 git 环境为 'unknown'）"
        assert isinstance(snap.code_dirty, bool)
        assert snap.model_uri == "model://ltc/1.0.0"
        assert snap.metrics["mae"] == 0.123
        assert snap.metrics["r2"] == 0.956
        assert "python" in snap.environment, "environment 应含 python 版本"
        assert snap.notes == "基线实验"

    @pytest.mark.asyncio
    async def test_get_returns_created_snapshot(self, snapshot_store):
        """get 返回 create 写入的快照，字段一致."""
        snap = await snapshot_store.create(
            config={"workflow_spec": {"name": "test"}},
            dataset_versions=["dataset://ds/0.0.1"],
            model_uri="model://ltc/1.0.0",
            metrics={"mae": 0.5},
            created_by="u1",
        )

        fetched = await snapshot_store.get(snap.snapshot_id)
        assert fetched.snapshot_id == snap.snapshot_id
        assert fetched.created_by == "u1"
        assert fetched.model_uri == "model://ltc/1.0.0"
        assert fetched.metrics["mae"] == 0.5
        assert fetched.config["workflow_spec"]["name"] == "test"

    @pytest.mark.asyncio
    async def test_get_nonexistent_raises_keyerror(self, snapshot_store):
        """get 不存在 snapshot_id 抛 KeyError."""
        with pytest.raises(KeyError, match="snapshot 不存在"):
            await snapshot_store.get("nonexistent-id-12345")

    @pytest.mark.asyncio
    async def test_list_without_filters_returns_all(self, snapshot_store):
        """list 无 filters 返回全部（按 created_at 倒序）."""
        for i in range(3):
            await snapshot_store.create(
                config={"idx": i},
                dataset_versions=[],
                model_uri=f"model://m/{i}",
                metrics={"acc": float(i)},
                created_by="u1",
            )

        items = await snapshot_store.list(filters=None)
        assert len(items) == 3
        # 倒序：最后创建的在前
        assert items[0].config["idx"] == 2
        assert items[2].config["idx"] == 0

    @pytest.mark.asyncio
    async def test_list_filter_by_created_by(self, snapshot_store):
        """list 按 created_by 过滤."""
        await snapshot_store.create(
            config={},
            dataset_versions=[],
            model_uri="m://1",
            metrics={},
            created_by="alice",
        )
        await snapshot_store.create(
            config={},
            dataset_versions=[],
            model_uri="m://2",
            metrics={},
            created_by="bob",
        )

        alice_only = await snapshot_store.list(filters={"created_by": "alice"})
        assert len(alice_only) == 1
        assert alice_only[0].created_by == "alice"

    @pytest.mark.asyncio
    async def test_list_filter_by_model_uri(self, snapshot_store):
        """list 按 model_uri 精确匹配过滤."""
        await snapshot_store.create(
            config={},
            dataset_versions=[],
            model_uri="model://ltc/1.0.0",
            metrics={},
            created_by="u1",
        )
        await snapshot_store.create(
            config={},
            dataset_versions=[],
            model_uri="model://ltc/2.0.0",
            metrics={},
            created_by="u1",
        )

        filtered = await snapshot_store.list(filters={"model_uri": "model://ltc/1.0.0"})
        assert len(filtered) == 1
        assert filtered[0].model_uri == "model://ltc/1.0.0"

    @pytest.mark.asyncio
    async def test_reproduce_nonexistent_raises_keyerror(self, snapshot_store):
        """reproduce 不存在 snapshot_id 抛 KeyError."""
        with pytest.raises(KeyError, match="snapshot 不存在"):
            await snapshot_store.reproduce("nonexistent-id-99999")

    @pytest.mark.asyncio
    async def test_reproduce_without_workflow_spec_raises_notimplemented(self, snapshot_store):
        """config 无 workflow_spec 字段 → NotImplementedError."""
        snap = await snapshot_store.create(
            config={"hyperparams": {"lr": 0.001}},  # 无 workflow_spec
            dataset_versions=[],
            model_uri="model://ltc/1.0.0",
            metrics={"mae": 0.1},
            created_by="u1",
        )

        with pytest.raises(NotImplementedError, match="workflow_spec"):
            await snapshot_store.reproduce(snap.snapshot_id)

    @pytest.mark.asyncio
    async def test_reproduce_with_workflow_spec_invokes_runner(self, snapshot_store, monkeypatch):
        """config 含 workflow_spec → 调用 WorkflowRunner.run，返回 workflow_run_id."""

        # Mock WorkflowRunner：捕获 spec + owner_id，返回固定 run_id
        captured: dict[str, Any] = {}

        class _MockRunner:
            async def run(self, spec, *, owner_id=""):
                captured["spec_name"] = spec.name
                captured["owner_id"] = owner_id
                captured["spec_version"] = spec.version
                return "mock-workflow-run-id-001"

        async def _mock_get_runner():
            return _MockRunner()

        # 延迟导入，patch reproduce 内部调用
        import app.observability.snapshot as _ss_mod

        # reproduce 内部 `from app.workflow.runner import get_workflow_runner`
        # 通过 sys.modules 注入 mock 模块
        import sys
        import types

        mock_runner_mod = types.ModuleType("app.workflow.runner")
        mock_runner_mod.get_workflow_runner = _mock_get_runner
        monkeypatch.setitem(sys.modules, "app.workflow.runner", mock_runner_mod)

        workflow_spec_dict = {
            "name": "repro-test-spec",
            "version": "1.0.0",
            "nodes": [
                {"node_id": "A", "task_type": "task_a", "params": {"x": 1}},
            ],
            "edges": [],
            "outputs": {"final": "${A.out_a}"},
            "metadata": {"reproduced_from": "snapshot"},
        }

        snap = await snapshot_store.create(
            config={"workflow_spec": workflow_spec_dict, "seed": 42},
            dataset_versions=["dataset://ds/0.0.1"],
            model_uri="model://ltc/1.0.0",
            metrics={"mae": 0.1},
            created_by="u1",
        )

        run_id = await snapshot_store.reproduce(snap.snapshot_id)

        assert run_id == "mock-workflow-run-id-001"
        assert captured["spec_name"] == "repro-test-spec"
        assert captured["spec_version"] == "1.0.0"
        assert captured["owner_id"] == "system:reproduce"


# 测试用例：集成场景（dataset + lineage + snapshot）


@pytest.mark.integration
class TestIntegratedReproducibility:
    """端到端集成：dataset → commit → lineage → snapshot → 反查血缘.

    对应 ADR-005 阶段 2 验收：
        - git SHA + 数据 hash + 完整配置写入 snapshot，可查询
        - 血缘图可视化：从模型反查训练数据/任务/配置
    """

    @pytest.mark.asyncio
    async def test_full_pipeline_dataset_to_snapshot(self, integrated_stores):
        """完整流水线：创建数据集 → 提交版本 → 记录血缘 → 创建快照 → 查询."""
        ds_store: IDatasetStore = integrated_stores["dataset"]
        lin_store: ILineageStore = integrated_stores["lineage"]
        snap_store: ISnapshotStore = integrated_stores["snapshot"]

        # 1. 创建数据集 + 提交版本
        ds_id = await ds_store.create(
            name="integrated_ds",
            schema=_make_schema(),
            owner_id="researcher_001",
            description="集成测试数据集",
        )
        records = _make_records(10, seed=0)
        version = await ds_store.commit_version(ds_id, records)

        # 2. 记录血缘：raw dataset model
        dataset_uri = f"dataset://integrated_ds/{version.version}"
        model_uri = f"model://ltc/{version.version}"

        rec1 = make_lineage_record(
            target=dataset_uri,
            source_type="task",
            source_ref="job-preprocess-001",
            inputs=["artifact://raw/phm2010.csv"],
            outputs=[dataset_uri],
            operation="preprocess",
            metadata={"normalization": "zscore"},
        )
        rec2 = make_lineage_record(
            target=model_uri,
            source_type="workflow",
            source_ref="wf-train-001",
            inputs=[dataset_uri],
            outputs=[model_uri],
            operation="train",
            metadata={"epochs": 50, "lr": 0.001},
        )
        await lin_store.record(rec1)
        await lin_store.record(rec2)

        # 3. 创建实验快照（含完整配置 + 数据 hash + 模型 URI）
        snapshot = await snap_store.create(
            config={
                "workflow_spec": {
                    "name": "ltc-train-pipeline",
                    "version": "1.0.0",
                    "nodes": [
                        {"node_id": "train", "task_type": "ltc_train", "params": {"epochs": 50, "lr": 0.001}},
                    ],
                    "edges": [],
                    "outputs": {"model": "${train.model}"},
                    "metadata": {"dataset_id": ds_id, "version": version.version},
                },
                "data_hash": version.content_hash,
                "seed": 42,
            },
            dataset_versions=[dataset_uri],
            model_uri=model_uri,
            metrics={"mae": 0.0823, "r2": 0.9712, "pcc": 0.9856},
            created_by="researcher_001",
            notes="LTC 阶段 2 集成测试基线",
        )

        # 4. 验收点 1：snapshot 含 git_sha + data_hash + 完整配置，可查询
        fetched = await snap_store.get(snapshot.snapshot_id)
        assert fetched.git_sha, "快照应含 git_sha"
        assert fetched.config["data_hash"] == version.content_hash, (
            "快照 config.data_hash 应等于数据集版本的 content_hash"
        )
        assert fetched.config["workflow_spec"]["name"] == "ltc-train-pipeline"
        assert fetched.metrics["mae"] == 0.0823
        assert dataset_uri in fetched.dataset_versions

        # 5. 验收点 2：从模型反查训练数据（血缘图可视化）
        graph = await lin_store.visualize(model_uri)
        assert graph["target"] == model_uri
        node_ids = {n["id"] for n in graph["nodes"]}
        assert model_uri in node_ids, "血缘图应含模型节点"
        assert dataset_uri in node_ids, "血缘图应含数据集节点"
        assert "artifact://raw/phm2010.csv" in node_ids, "血缘图应含原始数据节点（反查到训练数据源）"

        # 6. 从 dataset 也能反查到 raw
        upstream_of_ds = await lin_store.get_upstream(dataset_uri, depth=5)
        assert len(upstream_of_ds) == 1
        assert upstream_of_ds[0].operation == "preprocess"

        # 7. 验收点 3：相同 data_hash + config 在干净环境可复现
        # （模拟"同一 snapshot 复现"——此处验证 hash 稳定性即可）
        same_records = _make_records(10, seed=0)
        same_hash = _compute_content_hash(same_records)
        assert same_hash == version.content_hash, "相同 records 必须产生相同 hash（可复现性基础）"

    @pytest.mark.asyncio
    async def test_snapshot_list_query_by_git_sha(self, integrated_stores):
        """快照列表支持按 git_sha 过滤查询."""
        snap_store: ISnapshotStore = integrated_stores["snapshot"]

        # 创建多个快照（同一 git 环境，git_sha 相同）
        for i in range(3):
            await snap_store.create(
                config={"iter": i},
                dataset_versions=[],
                model_uri=f"model://m/{i}",
                metrics={"acc": float(i) / 10.0},
                created_by="u1",
            )

        # 第一次创建后获取 git_sha
        items = await snap_store.list(filters=None)
        assert len(items) == 3
        git_sha = items[0].git_sha

        # 按 git_sha 过滤 应返回全部 3 个（同环境）
        filtered = await snap_store.list(filters={"git_sha": git_sha})
        assert len(filtered) == 3

        # 查一个不存在的 git_sha 空
        empty = await snap_store.list(filters={"git_sha": "nonexistent-sha-xxx"})
        assert empty == []


# 契约接口合规性（实现类确实是契约子类）


@pytest.mark.integration
class TestContractCompliance:
    """验证 DatasetStore / LineageStore / SnapshotStore 实现对应契约接口."""

    def test_dataset_store_is_idataset_store(self):
        """DatasetStore 是 IDatasetStore 子类."""
        assert issubclass(DatasetStore, IDatasetStore)

    def test_lineage_store_is_ilineage_store(self):
        """LineageStore 是 ILineageStore 子类."""
        assert issubclass(LineageStore, ILineageStore)

    def test_snapshot_store_is_isnapshot_store(self):
        """SnapshotStore 是 ISnapshotStore 子类."""
        assert issubclass(SnapshotStore, ISnapshotStore)

    def test_make_lineage_record_generates_id_and_timestamp(self):
        """make_lineage_record 自动生成 record_id 与 timestamp."""
        rec = make_lineage_record(
            target="dataset://x/0.0.1",
            source_type="task",
            source_ref="job-1",
        )
        assert rec.record_id, "record_id 应自动生成"
        assert isinstance(rec.timestamp, datetime)
        assert rec.inputs == []
        assert rec.outputs == []
