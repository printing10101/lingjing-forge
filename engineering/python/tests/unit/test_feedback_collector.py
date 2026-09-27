"""数据飞轮 - 反馈采集层单元测试.

对应 core-contracts-design.md 阶段 4 p4-2 / plugins/data_flywheel/feedback_collector.py.

覆盖：
- FeedbackCollector 构造与配置合并
- 三种反馈类型（annotation / adoption / correction）的 record_* 方法
- 缓冲区达 batch_size 自动 flush
- flush 懒注册数据集、lineage 关联、失败回滚
- get_recent_feedback 时间窗口过滤
- 降级模式（dataset_store=None）
- Plugin 集成（main.py 的 FeedbackCollector 装配与反馈提交处理器）

测试替身：
- ``InMemoryDatasetStore``：内存版 IDatasetStore，不依赖 SQLite/文件系统，
  供本地测试与 CI 环境使用。content_hash 计算与真实 DatasetStore 一致
  （sha256 of canonical_json）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Optional

import pytest

from app.contracts.dataset import (
    DatasetSchema,
    DatasetStatus,
    DatasetVersion,
    IDatasetStore,
    LineageRecord,
)
from plugins.data_flywheel.feedback_collector import (
    FEEDBACK_DATASET_NAME,
    FEEDBACK_DATASET_SCHEMA,
    VALID_FEEDBACK_TYPES,
    FeedbackCollector,
)


# 测试替身：InMemoryDatasetStore


def _compute_content_hash(records: list[dict[str, Any]]) -> str:
    """与真实 DatasetStore 一致的 content_hash 计算."""
    canonical = json.dumps(records, sort_keys=True, ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _bump_patch(version: str) -> str:
    """patch 版本号 +1（与 DatasetStore.commit_version 自动递增一致）."""
    parts = version.split(".")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        parts[2] = str(int(parts[2]) + 1)
        return ".".join(parts)
    return "0.0.1"


class InMemoryDatasetStore(IDatasetStore):
    """内存版 IDatasetStore 测试替身.

    - 不依赖 SQLite / 文件系统，可在无 fastapi / 无 WinSock 环境运行
    - content_hash 计算与真实 DatasetStore 一致（sha256 of canonical_json）
    - 支持 create / commit_version / get_version / read / list_versions / deprecate
    - read 返回最新 PUBLISHED 版本的记录（与 DatasetStore.read 语义一致）
    - 通过 ``commit_should_fail`` 标志模拟 commit 失败（测试回滚）
    """

    def __init__(self) -> None:
        self._datasets: dict[str, dict[str, Any]] = {}  # dataset_id -> meta
        self._name_to_id: dict[str, str] = {}  # name -> dataset_id（唯一约束）
        self._versions: dict[str, list[DatasetVersion]] = {}  # dataset_id -> versions
        self._records: dict[str, list[dict[str, Any]]] = {}  # dataset_id -> latest records
        self._lineages: list[LineageRecord] = []
        self.commit_should_fail = False  # 测试钩子：置 True 后下次 commit 抛错
        self.create_should_fail_with_duplicate = False  # 模拟 name 唯一冲突

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
            raise RuntimeError("模拟 commit 失败（测试回滚）")
        if dataset_id not in self._datasets:
            raise KeyError(f"dataset 不存在: {dataset_id}")

        existing = self._versions[dataset_id]
        if version is None:
            version = _bump_patch(existing[-1].version) if existing else "1.0.0"

        content_hash = _compute_content_hash(records)
        size_bytes = len(json.dumps(records, ensure_ascii=False, default=str).encode())

        v = DatasetVersion(
            dataset_id=dataset_id,
            version=version,
            status=DatasetStatus.PUBLISHED,
            schema=self._datasets[dataset_id]["schema"],
            content_hash=content_hash,
            row_count=len(records),
            size_bytes=size_bytes,
            created_at=datetime.utcnow(),
            created_by=self._datasets[dataset_id]["owner_id"],
            storage_uri=f"memory://{dataset_id}/{version}",
            lineage=lineage.record_id if lineage else None,
        )
        existing.append(v)
        # read 总是返回最新 PUBLISHED 版本的记录
        self._records[dataset_id] = list(records)
        if lineage is not None:
            self._lineages.append(lineage)
        return v

    async def get_version(self, dataset_id: str, version: Optional[str] = None) -> DatasetVersion:
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
        records = self._records.get(dataset_id, [])
        for i in range(0, len(records), batch_size):
            yield records[i : i + batch_size]

    async def list_versions(self, dataset_id: str) -> list[DatasetVersion]:
        return list(self._versions.get(dataset_id, []))

    async def deprecate(self, dataset_id: str, version: str) -> None:
        for v in self._versions.get(dataset_id, []):
            if v.version == version:
                v.status = DatasetStatus.DEPRECATED
                return
        raise KeyError(f"版本不存在: {dataset_id}/{version}")


# fixtures


@pytest.fixture
def store() -> InMemoryDatasetStore:
    return InMemoryDatasetStore()


@pytest.fixture
def collector(store: InMemoryDatasetStore) -> FeedbackCollector:
    return FeedbackCollector(dataset_store=store, owner_id="test")


@pytest.fixture
def small_batch_collector(store: InMemoryDatasetStore) -> FeedbackCollector:
    """batch_size=2 的采集器，便于测试自动 flush."""
    return FeedbackCollector(
        dataset_store=store,
        owner_id="test",
        config={"batch_size": 2, "window_hours": 24, "min_samples_for_training": 50},
    )


# 测试：常量与 Schema


@pytest.mark.unit
@pytest.mark.contracts
class TestFeedbackCollectorConstants:
    """反馈采集层常量与 Schema 校验."""

    def test_feedback_dataset_name(self):
        assert FEEDBACK_DATASET_NAME == "feedback_records"

    def test_valid_feedback_types(self):
        assert VALID_FEEDBACK_TYPES == frozenset({"annotation", "adoption", "correction"})

    def test_feedback_dataset_schema_valid(self):
        """schema 自身合法性（DatasetSchema.validate 应无错误）."""
        errors = FEEDBACK_DATASET_SCHEMA.validate()
        assert errors == []

    def test_feedback_dataset_schema_has_required_fields(self):
        """必填字段存在."""
        fields = FEEDBACK_DATASET_SCHEMA.fields
        assert "feedback_id" in fields
        assert "timestamp" in fields
        assert "user_id" in fields
        assert "feedback_type" in fields
        assert fields["feedback_id"]["required"] is True
        assert fields["feedback_type"]["required"] is True

    def test_feedback_dataset_schema_primary_key(self):
        assert FEEDBACK_DATASET_SCHEMA.primary_key == ["feedback_id"]


# 测试：构造与配置


@pytest.mark.unit
@pytest.mark.contracts
class TestFeedbackCollectorConstruction:
    """FeedbackCollector 构造与配置合并."""

    def test_default_config(self, store):
        c = FeedbackCollector(dataset_store=store)
        assert c.config["window_hours"] == 24
        assert c.config["min_samples_for_training"] == 50
        assert c.config["batch_size"] == 100

    def test_custom_config_overrides_default(self, store):
        c = FeedbackCollector(
            dataset_store=store,
            config={"batch_size": 10, "window_hours": 48},
        )
        assert c.config["batch_size"] == 10
        assert c.config["window_hours"] == 48
        # 未覆盖的保留默认
        assert c.config["min_samples_for_training"] == 50

    def test_none_config_values_ignored(self, store):
        """config 中 None 值不覆盖默认."""
        c = FeedbackCollector(
            dataset_store=store,
            config={"batch_size": None, "window_hours": 12},
        )
        assert c.config["batch_size"] == 100  # 默认值保留
        assert c.config["window_hours"] == 12

    def test_initial_state(self, collector):
        assert collector.buffer_size == 0
        assert collector.total_recorded == 0
        assert collector.total_flushed == 0
        assert collector.dataset_id is None

    def test_degraded_mode_logs_warning(self, caplog):
        """dataset_store=None 时构造不抛错，仅记录警告."""
        import logging

        with caplog.at_level(logging.WARNING):
            c = FeedbackCollector(dataset_store=None)
        assert c.buffer_size == 0
        assert any("降级模式" in r.message for r in caplog.records)


# 测试：record_annotation


@pytest.mark.unit
@pytest.mark.contracts
class TestRecordAnnotation:
    """标注反馈记录."""

    @pytest.mark.asyncio
    async def test_record_annotation_returns_feedback_id(self, collector):
        fb_id = await collector.record_annotation(
            user_id="u-1",
            prediction_id="pred-1",
            model_version="v1.0",
            original_output={"label": "chatter"},
            notes="correct",
        )
        assert fb_id.startswith("fb-")
        assert collector.total_recorded == 1
        assert collector.buffer_size == 1

    @pytest.mark.asyncio
    async def test_record_annotation_in_buffer(self, collector):
        await collector.record_annotation(
            user_id="u-1",
            original_output={"x": 1},
        )
        # 缓冲区内容校验
        assert collector.buffer_size == 1
        # feedback_type 字段正确
        # 通过 get_stats 间接校验（不暴露内部 buffer）

    @pytest.mark.asyncio
    async def test_record_annotation_empty_user_id_rejected(self, collector):
        with pytest.raises(ValueError, match="user_id"):
            await collector.record_annotation(user_id="")


# 测试：record_adoption


@pytest.mark.unit
@pytest.mark.contracts
class TestRecordAdoption:
    """采纳/拒绝反馈记录."""

    @pytest.mark.asyncio
    async def test_record_adoption_accepted(self, collector):
        fb_id = await collector.record_adoption(
            user_id="u-1",
            accepted=True,
            prediction_id="pred-1",
        )
        assert fb_id.startswith("fb-")
        assert collector.total_recorded == 1

    @pytest.mark.asyncio
    async def test_record_adoption_rejected(self, collector):
        fb_id = await collector.record_adoption(
            user_id="u-1",
            accepted=False,
        )
        assert fb_id.startswith("fb-")

    @pytest.mark.asyncio
    async def test_record_adoption_non_bool_rejected(self, collector):
        with pytest.raises(ValueError, match="bool"):
            await collector.record_adoption(user_id="u-1", accepted="yes")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_record_adoption_int_rejected(self, collector):
        with pytest.raises(ValueError, match="bool"):
            await collector.record_adoption(user_id="u-1", accepted=1)  # type: ignore[arg-type]


# 测试：record_correction


@pytest.mark.unit
@pytest.mark.contracts
class TestRecordCorrection:
    """修正反馈记录."""

    @pytest.mark.asyncio
    async def test_record_correction_success(self, collector):
        fb_id = await collector.record_correction(
            user_id="u-1",
            original_output={"label": "chatter"},
            corrected_output={"label": "no_chatter"},
            prediction_id="pred-1",
        )
        assert fb_id.startswith("fb-")
        assert collector.total_recorded == 1

    @pytest.mark.asyncio
    async def test_record_correction_empty_original_rejected(self, collector):
        with pytest.raises(ValueError, match="original_output"):
            await collector.record_correction(
                user_id="u-1",
                original_output={},
                corrected_output={"x": 1},
            )

    @pytest.mark.asyncio
    async def test_record_correction_empty_corrected_rejected(self, collector):
        with pytest.raises(ValueError, match="corrected_output"):
            await collector.record_correction(
                user_id="u-1",
                original_output={"x": 1},
                corrected_output={},
            )


# 测试：缓冲区与自动 flush


@pytest.mark.unit
@pytest.mark.contracts
class TestBufferAndAutoFlush:
    """缓冲区达 batch_size 自动 flush."""

    @pytest.mark.asyncio
    async def test_auto_flush_at_batch_size(self, small_batch_collector):
        """batch_size=2，第 2 条触发自动 flush."""
        await small_batch_collector.record_annotation(user_id="u-1")
        assert small_batch_collector.buffer_size == 1
        assert small_batch_collector.total_flushed == 0

        await small_batch_collector.record_annotation(user_id="u-2")
        # 第 2 条触发自动 flush，缓冲区清空
        assert small_batch_collector.buffer_size == 0
        assert small_batch_collector.total_flushed == 2
        assert small_batch_collector.dataset_id is not None

    @pytest.mark.asyncio
    async def test_auto_flush_failure_keeps_buffer(self, small_batch_collector, store):
        """自动 flush 失败时，记录保留在缓冲区."""
        store.commit_should_fail = True
        # 第 1 条
        await small_batch_collector.record_annotation(user_id="u-1")
        # 第 2 条触发 flush，但 commit 失败 记录放回缓冲区
        await small_batch_collector.record_annotation(user_id="u-2")
        # 缓冲区应保留 2 条（flush 失败回滚）
        assert small_batch_collector.buffer_size == 2
        assert small_batch_collector.total_flushed == 0


# 测试：flush


@pytest.mark.unit
@pytest.mark.contracts
class TestFlush:
    """flush 批量提交."""

    @pytest.mark.asyncio
    async def test_flush_empty_buffer_returns_none(self, collector):
        result = await collector.flush()
        assert result is None

    @pytest.mark.asyncio
    async def test_flush_lazy_registers_dataset(self, collector, store):
        """首次 flush 懒注册数据集."""
        await collector.record_annotation(user_id="u-1")
        version = await collector.flush()
        assert version is not None
        assert collector.dataset_id is not None
        # 数据集已注册
        assert collector.dataset_id in store._datasets
        assert store._datasets[collector.dataset_id]["name"] == FEEDBACK_DATASET_NAME

    @pytest.mark.asyncio
    async def test_flush_creates_version_with_correct_row_count(self, collector):
        await collector.record_annotation(user_id="u-1")
        await collector.record_annotation(user_id="u-2")
        await collector.record_annotation(user_id="u-3")
        version = await collector.flush()
        assert version is not None
        assert version.row_count == 3
        assert collector.total_flushed == 3
        assert collector.buffer_size == 0

    @pytest.mark.asyncio
    async def test_flush_increments_version(self, collector):
        await collector.record_annotation(user_id="u-1")
        v1 = await collector.flush()
        await collector.record_annotation(user_id="u-2")
        v2 = await collector.flush()
        # 版本号递增（patch +1）
        assert v1.version != v2.version

    @pytest.mark.asyncio
    async def test_flush_associates_lineage(self, collector, store):
        await collector.record_annotation(user_id="u-1")
        await collector.flush()
        # lineage 已记录
        assert len(store._lineages) == 1
        lineage = store._lineages[0]
        assert lineage.source_type == "manual"
        assert lineage.operation == "feedback_collection"
        # owner_id="test"（collector fixture） source_ref 不带 plugin: 前缀
        assert lineage.source_ref == "test:feedback_collector"
        assert lineage.target.startswith("dataset://")

    @pytest.mark.asyncio
    async def test_flush_failure_rolls_back_to_buffer(self, collector, store):
        """flush 失败时记录放回缓冲区头部."""
        await collector.record_annotation(user_id="u-1")
        await collector.record_annotation(user_id="u-2")
        store.commit_should_fail = True
        with pytest.raises(RuntimeError, match="模拟 commit 失败"):
            await collector.flush()
        # 记录放回缓冲区
        assert collector.buffer_size == 2
        assert collector.total_flushed == 0
        # 重试 flush 成功
        version = await collector.flush()
        assert version is not None
        assert collector.total_flushed == 2

    @pytest.mark.asyncio
    async def test_flush_degraded_mode_raises(self):
        """dataset_store=None 时 flush 抛 RuntimeError."""
        c = FeedbackCollector(dataset_store=None)
        # record 不抛错（降级），但 flush 抛错
        await c.record_annotation(user_id="u-1")
        with pytest.raises(RuntimeError, match="降级模式"):
            await c.flush()

    @pytest.mark.asyncio
    async def test_flush_stable_id_fallback_on_duplicate_name(self, store, monkeypatch):
        """create 失败（name 冲突）时回退到 stable_id."""
        import hashlib

        # 预置 stable 数据集（模拟另一实例已按稳定 id 规则创建同名数据集），
        # 使 create 因 name 冲突失败后可复用 stable_id 完成 flush。
        stable_id = "fb-" + hashlib.sha256(FEEDBACK_DATASET_NAME.encode("utf-8")).hexdigest()[:16]
        store._datasets[stable_id] = {
            "name": FEEDBACK_DATASET_NAME,
            "schema": FEEDBACK_DATASET_SCHEMA,
            "owner_id": "other",
            "description": "",
            "status": "published",
        }
        store._name_to_id[FEEDBACK_DATASET_NAME] = stable_id
        store._versions[stable_id] = []
        store._records[stable_id] = []

        c = FeedbackCollector(dataset_store=store, owner_id="test")
        await c.record_annotation(user_id="u-1")
        # create 会因 name 冲突失败，但 flush 应通过 stable_id 回退成功
        version = await c.flush()
        assert version is not None
        assert c.dataset_id is not None
        assert c.dataset_id.startswith("fb-")


# 测试：get_recent_feedback


@pytest.mark.unit
@pytest.mark.contracts
class TestGetRecentFeedback:
    """get_recent_feedback 时间窗口过滤."""

    @pytest.mark.asyncio
    async def test_get_recent_feedback_returns_all_within_window(self, collector):
        """flush 后读取全部记录."""
        await collector.record_annotation(user_id="u-1")
        await collector.record_adoption(user_id="u-2", accepted=True)
        await collector.flush()

        records = await collector.get_recent_feedback(hours=24)
        assert len(records) == 2

    @pytest.mark.asyncio
    async def test_get_recent_feedback_empty_before_flush(self, collector):
        """未 flush 时返回空列表."""
        await collector.record_annotation(user_id="u-1")
        records = await collector.get_recent_feedback(hours=24)
        assert records == []

    @pytest.mark.asyncio
    async def test_get_recent_feedback_filters_by_time_window(self, collector, monkeypatch):
        """时间窗口外的记录被过滤."""
        # 注入旧时间戳的记录
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        await collector.record_annotation(user_id="u-1")
        # 手动改写缓冲区中记录的 timestamp
        collector._buffer[0]["timestamp"] = old_ts
        await collector.record_annotation(user_id="u-2")  # 新记录
        await collector.flush()

        # 24 小时窗口 只有 1 条新记录
        recent = await collector.get_recent_feedback(hours=24)
        assert len(recent) == 1
        assert recent[0]["user_id"] == "u-2"

        # 72 小时窗口 2 条
        all_recent = await collector.get_recent_feedback(hours=72)
        assert len(all_recent) == 2

    @pytest.mark.asyncio
    async def test_get_recent_feedback_degraded_raises(self):
        c = FeedbackCollector(dataset_store=None)
        with pytest.raises(RuntimeError, match="dataset_store"):
            await c.get_recent_feedback()


# 测试：get_stats


@pytest.mark.unit
@pytest.mark.contracts
class TestGetStats:
    """get_stats 统计信息."""

    @pytest.mark.asyncio
    async def test_stats_after_records(self, collector):
        await collector.record_annotation(user_id="u-1")
        await collector.record_adoption(user_id="u-2", accepted=True)
        stats = await collector.get_stats()
        assert stats["buffer_size"] == 2
        assert stats["total_recorded"] == 2
        assert stats["total_flushed"] == 0
        assert stats["dataset_id"] is None
        assert stats["dataset_store_available"] is True

    @pytest.mark.asyncio
    async def test_stats_after_flush(self, collector):
        await collector.record_annotation(user_id="u-1")
        await collector.flush()
        stats = await collector.get_stats()
        assert stats["buffer_size"] == 0
        assert stats["total_recorded"] == 1
        assert stats["total_flushed"] == 1
        assert stats["dataset_id"] is not None
        assert stats["last_flush_at"] is not None


# 测试：降级模式


@pytest.mark.unit
@pytest.mark.contracts
class TestDegradedMode:
    """dataset_store=None 降级模式."""

    @pytest.mark.asyncio
    async def test_degraded_record_does_not_raise(self):
        c = FeedbackCollector(dataset_store=None)
        # 三种反馈类型都不抛错
        fb1 = await c.record_annotation(user_id="u-1")
        fb2 = await c.record_adoption(user_id="u-2", accepted=True)
        fb3 = await c.record_correction(
            user_id="u-3",
            original_output={"a": 1},
            corrected_output={"a": 2},
        )
        assert fb1 and fb2 and fb3
        assert c.total_recorded == 3
        assert c.buffer_size == 3  # 不会自动 flush（flush 会抛错）

    @pytest.mark.asyncio
    async def test_degraded_auto_flush_does_not_block(self):
        """降级模式下达到 batch_size 不抛错（自动 flush 失败被捕获）."""
        c = FeedbackCollector(
            dataset_store=None,
            config={"batch_size": 1},
        )
        # 第 1 条触发自动 flush，flush 抛 RuntimeError 但被 _record 捕获
        fb_id = await c.record_annotation(user_id="u-1")
        assert fb_id.startswith("fb-")
        # 缓冲区记录保留（flush 失败回滚）
        assert c.buffer_size == 1


# 测试：Plugin 集成（main.py）


@pytest.mark.unit
@pytest.mark.contracts
class TestPluginIntegration:
    """Plugin.on_load / on_unload / health_check 中的 FeedbackCollector 装配."""

    def _make_context(self, store: Optional[IDatasetStore] = None):
        """构造最小 PluginContext 测试替身."""
        from app.contracts.plugin import PluginContext

        return PluginContext(
            plugin_id="data_flywheel",
            config={
                "feedback_collection": {
                    "window_hours": 12,
                    "min_samples_for_training": 30,
                    "batch_size": 5,
                }
            },
            task_registry=object(),  # 非 None 即可
            dataset_store=store,
            observability=object(),
            logger=None,
            data_dir="/tmp/flywheel_test",
        )

    @pytest.mark.asyncio
    async def test_on_load_constructs_feedback_collector(self, store):
        from app.plugins.extension_registry import reset_extension_registry
        from plugins.data_flywheel.main import Plugin

        reset_extension_registry()
        plugin = Plugin()
        ctx = self._make_context(store)
        await plugin.on_load(ctx)

        assert plugin.feedback_collector is not None
        assert plugin.feedback_collector.config["batch_size"] == 5
        assert plugin.feedback_collector.config["window_hours"] == 12

        # health_check 反馈采集器就绪
        health = plugin.health_check()
        assert health["checks"]["feedback_collector_available"] is True
        assert "feedback_stats" in health

        # 清理
        await plugin.on_unload()
        reset_extension_registry()

    @pytest.mark.asyncio
    async def test_on_load_degraded_mode(self):
        """dataset_store=None 时 on_load 仍成功（降级模式）."""
        from app.plugins.extension_registry import reset_extension_registry
        from plugins.data_flywheel.main import Plugin

        reset_extension_registry()
        plugin = Plugin()
        ctx = self._make_context(store=None)
        await plugin.on_load(ctx)

        assert plugin.feedback_collector is not None
        health = plugin.health_check()
        # feedback_collector_available=True（采集器已构造），
        # 但 dataset_store_available=False
        assert health["checks"]["feedback_collector_available"] is True
        assert health["checks"]["dataset_store_available"] is False
        assert health["healthy"] is False  # 因 dataset_store 不可用

        await plugin.on_unload()
        reset_extension_registry()

    @pytest.mark.asyncio
    async def test_on_unload_flushes_buffer(self, store):
        """on_unload 时 flush 剩余缓冲区."""
        from app.plugins.extension_registry import reset_extension_registry
        from plugins.data_flywheel.main import Plugin

        reset_extension_registry()
        plugin = Plugin()
        ctx = self._make_context(store)
        await plugin.on_load(ctx)

        # 写入反馈但不 flush
        await plugin.feedback_collector.record_annotation(user_id="u-1")
        assert plugin.feedback_collector.buffer_size == 1

        # on_unload 应 flush；dataset_id 懒注册（flush 时才创建），
        # 卸载后 collector 已置 None，直接遍历 store 验证落盘
        await plugin.on_unload()
        versions = [v for vs in store._versions.values() for v in vs]
        assert any(v.row_count == 1 for v in versions)
        reset_extension_registry()

    @pytest.mark.asyncio
    async def test_feedback_submission_handler_annotation(self, store):
        """_handle_feedback_submission 处理 annotation."""
        from app.plugins.extension_registry import reset_extension_registry
        from plugins.data_flywheel.main import Plugin

        reset_extension_registry()
        plugin = Plugin()
        ctx = self._make_context(store)
        await plugin.on_load(ctx)

        result = await plugin._handle_feedback_submission(
            {
                "feedback_type": "annotation",
                "user_id": "u-1",
                "prediction_id": "pred-1",
                "notes": "test",
            }
        )
        assert result["success"] is True
        assert result["feedback_id"].startswith("fb-")
        assert result["buffer_size"] == 1

        await plugin.on_unload()
        reset_extension_registry()

    @pytest.mark.asyncio
    async def test_feedback_submission_handler_adoption(self, store):
        """_handle_feedback_submission 处理 adoption."""
        from app.plugins.extension_registry import reset_extension_registry
        from plugins.data_flywheel.main import Plugin

        reset_extension_registry()
        plugin = Plugin()
        ctx = self._make_context(store)
        await plugin.on_load(ctx)

        result = await plugin._handle_feedback_submission(
            {
                "feedback_type": "adoption",
                "user_id": "u-1",
                "accepted": True,
            }
        )
        assert result["success"] is True

        await plugin.on_unload()
        reset_extension_registry()

    @pytest.mark.asyncio
    async def test_feedback_submission_handler_correction(self, store):
        """_handle_feedback_submission 处理 correction."""
        from app.plugins.extension_registry import reset_extension_registry
        from plugins.data_flywheel.main import Plugin

        reset_extension_registry()
        plugin = Plugin()
        ctx = self._make_context(store)
        await plugin.on_load(ctx)

        result = await plugin._handle_feedback_submission(
            {
                "feedback_type": "correction",
                "user_id": "u-1",
                "original_output": {"label": "a"},
                "corrected_output": {"label": "b"},
            }
        )
        assert result["success"] is True

        await plugin.on_unload()
        reset_extension_registry()

    @pytest.mark.asyncio
    async def test_feedback_submission_invalid_type_rejected(self, store):
        from app.plugins.extension_registry import reset_extension_registry
        from plugins.data_flywheel.main import Plugin

        reset_extension_registry()
        plugin = Plugin()
        ctx = self._make_context(store)
        await plugin.on_load(ctx)

        with pytest.raises(ValueError, match="feedback_type"):
            await plugin._handle_feedback_submission({"feedback_type": "invalid", "user_id": "u-1"})

        await plugin.on_unload()
        reset_extension_registry()

    @pytest.mark.asyncio
    async def test_feedback_submission_adoption_requires_bool(self, store):
        from app.plugins.extension_registry import reset_extension_registry
        from plugins.data_flywheel.main import Plugin

        reset_extension_registry()
        plugin = Plugin()
        ctx = self._make_context(store)
        await plugin.on_load(ctx)

        with pytest.raises(ValueError, match="accepted"):
            await plugin._handle_feedback_submission({"feedback_type": "adoption", "user_id": "u-1", "accepted": "yes"})

        await plugin.on_unload()
        reset_extension_registry()

    @pytest.mark.asyncio
    async def test_feedback_submission_correction_requires_outputs(self, store):
        from app.plugins.extension_registry import reset_extension_registry
        from plugins.data_flywheel.main import Plugin

        reset_extension_registry()
        plugin = Plugin()
        ctx = self._make_context(store)
        await plugin.on_load(ctx)

        with pytest.raises(ValueError, match="original_output.*corrected_output"):
            await plugin._handle_feedback_submission({"feedback_type": "correction", "user_id": "u-1"})

        await plugin.on_unload()
        reset_extension_registry()


# 测试：端到端闭环（反馈 flush 读取）


@pytest.mark.unit
@pytest.mark.contracts
class TestEndToEndFeedbackLoop:
    """端到端：记录三种反馈 → flush → 读取验证."""

    @pytest.mark.asyncio
    async def test_mixed_feedback_types_in_single_version(self, collector):
        """三种反馈类型混合同一版本."""
        await collector.record_annotation(
            user_id="u-1",
            prediction_id="pred-1",
            notes="标注正确",
        )
        await collector.record_adoption(
            user_id="u-2",
            accepted=True,
            prediction_id="pred-2",
        )
        await collector.record_correction(
            user_id="u-3",
            original_output={"label": "chatter"},
            corrected_output={"label": "no_chatter"},
            prediction_id="pred-3",
        )

        version = await collector.flush()
        assert version is not None
        assert version.row_count == 3
        assert collector.total_flushed == 3

        # 读取验证
        records = await collector.get_recent_feedback(hours=24)
        assert len(records) == 3
        types = {r["feedback_type"] for r in records}
        assert types == {"annotation", "adoption", "correction"}

    @pytest.mark.asyncio
    async def test_adoption_rate_calculation_source(self, collector):
        """验证 adoption_rate 指标的数据源（p4-4 飞轮指标使用）."""
        # 7 采纳 + 3 拒绝 adoption_rate = 70%
        for i in range(7):
            await collector.record_adoption(user_id=f"u-{i}", accepted=True, prediction_id=f"p-{i}")
        for i in range(3):
            await collector.record_adoption(user_id=f"u-{i + 7}", accepted=False, prediction_id=f"p-{i + 7}")
        await collector.flush()

        records = await collector.get_recent_feedback(hours=24)
        adoptions = [r for r in records if r["feedback_type"] == "adoption"]
        accepted = [r for r in adoptions if r["accepted"] is True]
        adoption_rate = len(accepted) / len(adoptions)
        assert adoption_rate == 0.7

    @pytest.mark.asyncio
    async def test_multiple_flushes_create_multiple_versions(self, collector):
        """多次 flush 创建多个版本（版本递增）."""
        await collector.record_annotation(user_id="u-1")
        v1 = await collector.flush()

        await collector.record_annotation(user_id="u-2")
        v2 = await collector.flush()

        assert v1.version != v2.version
        # 最新版本只包含最新一批记录（read 返回最新版本）
        records = await collector.get_recent_feedback(hours=24)
        assert len(records) == 1
        assert records[0]["user_id"] == "u-2"
