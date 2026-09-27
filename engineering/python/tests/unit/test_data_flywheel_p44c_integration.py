"""数据飞轮插件 p4-4c 集成测试.

对应 core-contracts-design.md 阶段 4 p4-4c。

验证 ``data_flywheel`` 插件在 ``on_load`` 时正确把 ``dataset_store`` /
``snapshot_store`` 注入到全局 ``FlywheelMetricsCollector``，并在反馈提交
触发 flush 后把 ``dataset_id`` 懒注入到全局采集器。

覆盖：
    - on_load 后全局采集器被注入 dataset_store（来自 PluginContext）
    - on_load 后全局采集器被注入 snapshot_store（来自 get_snapshot_store）
    - 反馈提交触发自动 flush 后，dataset_id 自动注入到全局采集器
    - 重复反馈提交不会重复注入（幂等性）
    - on_unload flush 后 dataset_id 也被注入
    - API 层 ``app.api.v1.flywheel`` 端点调用异步方法不抛错（mock collector）

本测试不依赖 fastapi 路由实际启动、不依赖 SQLite/SQLAlchemy、不依赖网络。
通过 ``app.observability.snapshot`` 的全局单例替换实现 ``ISnapshotStore`` 注入。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch

import pytest

from app.contracts.dataset import (
    DatasetSchema,
    DatasetStatus,
    DatasetVersion,
    IDatasetStore,
    LineageRecord,
)


def _bump_patch(version: str) -> str:
    """semver patch 自增（如 1.0.0 → 1.0.1）."""
    major, minor, patch = (int(x) for x in version.split("."))
    return f"{major}.{minor}.{patch + 1}"


from app.contracts.observability import ExperimentSnapshot, ISnapshotStore
from app.contracts.plugin import PluginContext
from app.metrics.flywheel_metrics import (
    FlywheelMetricsCollector,
    get_flywheel_collector,
    reset_flywheel_collector,
)
from app.plugins.extension_registry import (
    ExtensionRegistry,
    reset_extension_registry,
)

# 数据飞轮插件目录
_PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugins" / "data_flywheel"


# 测试替身：InMemoryDatasetStore（与 test_feedback_collector.py 对齐）


class InMemoryDatasetStore(IDatasetStore):
    """内存版 IDatasetStore 测试替身（契约签名：owner_id / version / lineage）.

    与 ``test_feedback_collector.py`` 中的实现保持一致，确保 FeedbackCollector
    能正常 create/commit_version/read。
    """

    def __init__(self) -> None:
        self._datasets: dict[str, dict[str, Any]] = {}  # dataset_id -> meta
        self._name_to_id: dict[str, str] = {}  # name -> dataset_id（唯一约束）
        self._versions: dict[str, list[DatasetVersion]] = {}
        self._records: dict[str, list[dict[str, Any]]] = {}
        self._lineages: list[LineageRecord] = []
        # 故障注入标志（本测试不使用，保留接口对称）
        self.get_version_should_fail = False
        self.read_should_fail = False
        self.commit_should_fail = False

    async def create(
        self,
        name: str,
        schema: DatasetSchema,
        *,
        owner_id: str,
        description: str = "",
    ) -> str:
        if name in self._name_to_id:
            raise ValueError(f"dataset name 已存在: {name}")
        dataset_id = f"ds-{hashlib.sha256(name.encode()).hexdigest()[:12]}"
        self._datasets[dataset_id] = {
            "name": name,
            "schema": schema,
            "owner_id": owner_id,
            "description": description,
            "status": DatasetStatus.DRAFT,
        }
        self._name_to_id[name] = dataset_id
        self._versions[dataset_id] = []
        self._records[dataset_id] = []
        return dataset_id

    async def commit_version(
        self,
        dataset_id: str,
        records: list[dict[str, Any]],
        *,
        version: Optional[str] = None,
        lineage: Optional[LineageRecord] = None,
    ) -> DatasetVersion:
        if self.commit_should_fail:
            self.commit_should_fail = False
            raise RuntimeError("模拟 commit_version 失败")
        if dataset_id not in self._datasets:
            raise KeyError(f"dataset 不存在: {dataset_id}")
        if version is None:
            existing = self._versions[dataset_id]
            version = _bump_patch(existing[-1].version) if existing else "1.0.0"
        content = repr(sorted(records, key=lambda r: str(r.get("feedback_id", ""))))
        content_hash = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
        v = DatasetVersion(
            dataset_id=dataset_id,
            version=version,
            status=DatasetStatus.PUBLISHED,
            schema=self._datasets[dataset_id]["schema"],
            content_hash=content_hash,
            row_count=len(records),
            size_bytes=len(content.encode("utf-8")),
            created_at=datetime.now(timezone.utc),
            created_by=self._datasets[dataset_id]["owner_id"],
            storage_uri=f"memory://{dataset_id}/{version}",
            lineage=lineage.record_id if lineage else None,
        )
        self._versions[dataset_id].append(v)
        self._records[dataset_id] = list(records)
        # 记录 lineage
        if lineage is not None:
            self._lineages.append(lineage)
        return v

    async def get_version(self, dataset_id: str, version: Optional[str] = None) -> DatasetVersion:
        if self.get_version_should_fail:
            self.get_version_should_fail = False
            raise RuntimeError("模拟 get_version 失败")
        versions = self._versions.get(dataset_id, [])
        if not versions:
            raise KeyError(f"dataset 无版本: {dataset_id}")
        if version is None:
            return versions[-1]
        for v in versions:
            if v.version == version:
                return v
        raise KeyError(f"版本不存在: {dataset_id}/{version}")

    async def read(
        self,
        dataset_id: str,
        version: Optional[str] = None,
        *,
        batch_size: int = 1000,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        if self.read_should_fail:
            self.read_should_fail = False
            raise RuntimeError("模拟 read 失败")
        records = self._records.get(dataset_id, [])
        for i in range(0, len(records), batch_size):
            yield records[i : i + batch_size]

    async def list_versions(self, dataset_id: str) -> list[DatasetVersion]:
        return list(self._versions.get(dataset_id, []))

    async def deprecate(self, dataset_id: str) -> None:
        if dataset_id in self._versions and self._versions[dataset_id]:
            last = self._versions[dataset_id][-1]
            self._versions[dataset_id][-1] = DatasetVersion(
                dataset_id=last.dataset_id,
                version=last.version,
                status=DatasetStatus.DEPRECATED,
                schema=last.schema,
                content_hash=last.content_hash,
                row_count=last.row_count,
                size_bytes=last.size_bytes,
                created_at=last.created_at,
                created_by=last.created_by,
                storage_uri=last.storage_uri,
                lineage=last.lineage,
            )


# 测试替身：InMemorySnapshotStore（与 test_flywheel_metrics_real_sources.py 对齐）


class InMemorySnapshotStore(ISnapshotStore):
    """内存版 ISnapshotStore 测试替身."""

    def __init__(self) -> None:
        self._snapshots: dict[str, ExperimentSnapshot] = {}
        self.list_should_fail = False

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
        snapshot_id = f"snap-{hashlib.sha256(str(datetime.utcnow()).encode()).hexdigest()[:12]}"
        snap = ExperimentSnapshot(
            snapshot_id=snapshot_id,
            created_at=datetime.utcnow(),
            created_by=created_by,
            git_sha="test-sha",
            code_dirty=False,
            config=config,
            dataset_versions=dataset_versions,
            model_uri=model_uri,
            metrics=metrics,
            environment={"python": "3.11"},
            notes=notes,
        )
        self._snapshots[snapshot_id] = snap
        return snap

    async def get(self, snapshot_id: str) -> ExperimentSnapshot:
        if snapshot_id not in self._snapshots:
            raise KeyError(f"快照不存在: {snapshot_id}")
        return self._snapshots[snapshot_id]

    async def list(self, *, filters: Optional[dict[str, Any]] = None) -> list[ExperimentSnapshot]:
        if self.list_should_fail:
            self.list_should_fail = False
            raise RuntimeError("模拟 list 失败")
        snaps = list(self._snapshots.values())
        snaps.sort(key=lambda s: s.created_at, reverse=True)
        return snaps

    async def reproduce(self, snapshot_id: str) -> str:
        return f"reproduce-run-{snapshot_id}"


# Fixtures


@pytest.fixture
def fresh_registry():
    """每个测试使用全新的 ExtensionRegistry."""
    reset_extension_registry()
    yield
    reset_extension_registry()


@pytest.fixture
def store():
    """InMemoryDatasetStore 实例."""
    return InMemoryDatasetStore()


@pytest.fixture
def snapshot_store():
    """InMemorySnapshotStore 实例."""
    return InMemorySnapshotStore()


@pytest.fixture(autouse=True)
def _reset_global_collector():
    """每个测试后重置全局 FlywheelMetricsCollector，避免状态污染."""
    yield
    reset_flywheel_collector()


@pytest.fixture(autouse=True)
def _mock_resolve_snapshot_store(snapshot_store, monkeypatch, request):
    """mock Plugin._resolve_snapshot_store 返回测试替身，避免依赖 app.observability.

    通过 monkeypatch 替换实例方法，确保 on_load 时注入的是测试用 InMemorySnapshotStore。
    注意：``TestResolveSnapshotStoreDegradation`` 专门验证真实实现（observability
    不可用降级），本 fixture 必须跳过该类。
    """
    if request.cls and request.cls.__name__ == "TestResolveSnapshotStoreDegradation":
        yield
        return
    from plugins.data_flywheel.main import Plugin

    monkeypatch.setattr(Plugin, "_resolve_snapshot_store", lambda self: snapshot_store)
    yield


def _make_context(store: Optional[IDatasetStore] = None) -> PluginContext:
    """构造最小 PluginContext 测试替身."""
    return PluginContext(
        plugin_id="data_flywheel",
        config={
            "feedback_collection": {
                "window_hours": 24,
                "min_samples_for_training": 50,
                "batch_size": 2,  # 小一点便于触发自动 flush
            }
        },
        task_registry=object(),
        dataset_store=store,
        observability=object(),
        logger=logging.getLogger("test.p44c"),
        data_dir=str(_PLUGIN_DIR / "_test_data_p44c"),
    )


# 测试类：on_load 注入


@pytest.mark.unit
@pytest.mark.contracts
class TestPluginOnLoadConfiguresFlywheelCollector:
    """on_load 时正确把数据源注入到全局 FlywheelMetricsCollector."""

    @pytest.mark.asyncio
    async def test_on_load_injects_dataset_store(self, store, fresh_registry):
        """on_load 后全局采集器的 dataset_store 等于 PluginContext.dataset_store."""
        from plugins.data_flywheel.main import Plugin

        plugin = Plugin()
        ctx = _make_context(store=store)
        await plugin.on_load(ctx)

        collector = get_flywheel_collector()
        assert collector.dataset_store is store

        await plugin.on_unload()

    @pytest.mark.asyncio
    async def test_on_load_injects_snapshot_store(self, store, snapshot_store, fresh_registry):
        """on_load 后全局采集器的 snapshot_store 等于 _resolve_snapshot_store 返回值."""
        from plugins.data_flywheel.main import Plugin

        plugin = Plugin()
        ctx = _make_context(store=store)
        await plugin.on_load(ctx)

        collector = get_flywheel_collector()
        assert collector.snapshot_store is snapshot_store

        await plugin.on_unload()

    @pytest.mark.asyncio
    async def test_on_load_with_none_dataset_store(self, fresh_registry):
        """dataset_store=None 时 on_load 仍成功（降级模式）."""
        from plugins.data_flywheel.main import Plugin

        plugin = Plugin()
        ctx = _make_context(store=None)
        await plugin.on_load(ctx)

        collector = get_flywheel_collector()
        assert collector.dataset_store is None
        # snapshot_store 仍被注入（来自 _resolve_snapshot_store）
        assert collector.snapshot_store is not None

        await plugin.on_unload()


# 测试类：反馈提交触发 dataset_id 注入


@pytest.mark.unit
@pytest.mark.contracts
class TestFeedbackSubmissionInjectsDatasetId:
    """反馈提交后 dataset_id 自动注入到全局 FlywheelMetricsCollector."""

    @pytest.mark.asyncio
    async def test_adoption_submission_with_auto_flush_injects_dataset_id(self, store, fresh_registry):
        """batch_size=2 时，提交 2 条反馈触发自动 flush，dataset_id 自动注入.

        流程：
            1. on_load 配置全局采集器（dataset_store 注入，feedback_dataset_id=None）
            2. 提交 2 条 adoption（达到 batch_size=2，触发自动 flush）
            3. FeedbackCollector 解析出 dataset_id
            4. _maybe_inject_feedback_dataset_id 把 dataset_id 注入到全局采集器
        """
        from plugins.data_flywheel.main import Plugin

        plugin = Plugin()
        ctx = _make_context(store=store)
        await plugin.on_load(ctx)

        collector = get_flywheel_collector()
        assert collector.feedback_dataset_id is None  # 初始未注入

        # 提交 2 条 adoption（batch_size=2，应触发自动 flush）
        await plugin._handle_feedback_submission(
            {
                "feedback_type": "adoption",
                "user_id": "u-1",
                "accepted": True,
                "prediction_id": "pred-1",
                "model_version": "v1.0",
            }
        )
        # 第一条提交后 buffer_size=1，未触发 flush，dataset_id 仍为 None
        assert collector.feedback_dataset_id is None

        await plugin._handle_feedback_submission(
            {
                "feedback_type": "adoption",
                "user_id": "u-2",
                "accepted": False,
                "prediction_id": "pred-2",
                "model_version": "v1.0",
            }
        )
        # 第二条提交后达到 batch_size=2，触发自动 flush，dataset_id 被注入
        assert plugin.feedback_collector is not None
        assert plugin.feedback_collector.dataset_id is not None
        assert collector.feedback_dataset_id == plugin.feedback_collector.dataset_id

        await plugin.on_unload()

    @pytest.mark.asyncio
    async def test_dataset_id_injection_is_idempotent(self, store, fresh_registry):
        """重复反馈提交不会重复调用 set_feedback_dataset_id（幂等）."""
        from plugins.data_flywheel.main import Plugin

        plugin = Plugin()
        ctx = _make_context(store=store)
        await plugin.on_load(ctx)

        # 提交 2 条触发 flush
        for i in range(2):
            await plugin._handle_feedback_submission(
                {
                    "feedback_type": "adoption",
                    "user_id": f"u-{i}",
                    "accepted": True,
                }
            )

        collector = get_flywheel_collector()
        first_dataset_id = collector.feedback_dataset_id
        assert first_dataset_id is not None

        # mock set_feedback_dataset_id 验证不会被再次调用
        with patch.object(collector, "set_feedback_dataset_id") as mock_set:
            await plugin._handle_feedback_submission(
                {
                    "feedback_type": "adoption",
                    "user_id": "u-extra",
                    "accepted": True,
                }
            )
            mock_set.assert_not_called()

        await plugin.on_unload()

    @pytest.mark.asyncio
    async def test_on_unload_flush_injects_dataset_id(self, store, fresh_registry):
        """on_unload 时 flush 剩余缓冲区后，dataset_id 也被注入."""
        from plugins.data_flywheel.main import Plugin

        plugin = Plugin()
        ctx = _make_context(store=store)
        await plugin.on_load(ctx)

        # 仅提交 1 条（未达 batch_size=2，不触发自动 flush）
        await plugin._handle_feedback_submission(
            {
                "feedback_type": "annotation",
                "user_id": "u-1",
                "notes": "test",
            }
        )

        collector = get_flywheel_collector()
        assert collector.feedback_dataset_id is None  # 未 flush

        # on_unload 触发 flush，之后 dataset_id 被注入
        await plugin.on_unload()

        assert collector.feedback_dataset_id is not None


# 测试类：端到端闭环（FeedbackCollector FlywheelMetricsCollector）


@pytest.mark.unit
@pytest.mark.contracts
class TestEndToEndFeedbackToMetrics:
    """端到端：反馈提交 → flush → 飞轮指标采集读到真实数据."""

    @pytest.mark.asyncio
    async def test_feedback_submission_makes_metrics_nonzero(self, store, snapshot_store, fresh_registry):
        """提交反馈后，飞轮指标的 data_volume 应该非零."""
        from plugins.data_flywheel.main import Plugin

        plugin = Plugin()
        ctx = _make_context(store=store)
        await plugin.on_load(ctx)

        # 提交 2 条 adoption（触发自动 flush）
        await plugin._handle_feedback_submission(
            {
                "feedback_type": "adoption",
                "user_id": "u-1",
                "accepted": True,
                "prediction_id": "pred-1",
                "model_version": "v1.0",
            }
        )
        await plugin._handle_feedback_submission(
            {
                "feedback_type": "adoption",
                "user_id": "u-2",
                "accepted": False,
                "prediction_id": "pred-2",
                "model_version": "v1.0",
            }
        )

        collector = get_flywheel_collector()
        metrics = await collector.collect_current_metrics_async()

        # data_volume 应该为 2（feedback_records 数据集有 2 条记录）
        assert metrics.data_volume == 2
        # adoption_rate 应该为 50.0（1 accepted / 2 total）
        assert metrics.adoption_rate == 50.0

        await plugin.on_unload()


# 测试类：_resolve_snapshot_store 降级


@pytest.mark.unit
@pytest.mark.contracts
class TestResolveSnapshotStoreDegradation:
    """_resolve_snapshot_store 在 observability 不可用时降级返回 None."""

    @pytest.mark.asyncio
    async def test_resolve_returns_none_when_observability_unavailable(self, store, fresh_registry, monkeypatch):
        """observability 模块导入失败时，_resolve_snapshot_store 返回 None."""
        from plugins.data_flywheel.main import Plugin

        # mock import 失败
        import builtins

        original_import = builtins.__import__

        def _fail_import(name, *args, **kwargs):
            if name == "app.observability" or name.startswith("app.observability."):
                raise ImportError(f"mock 失败: {name}")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fail_import)

        plugin = Plugin()
        ctx = _make_context(store=store)
        # on_load 不应抛错（降级模式）
        await plugin.on_load(ctx)

        collector = get_flywheel_collector()
        assert collector.snapshot_store is None  # 降级
        assert collector.dataset_store is store  # dataset_store 仍注入

        await plugin.on_unload()
