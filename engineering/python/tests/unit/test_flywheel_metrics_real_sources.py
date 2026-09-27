"""飞轮指标真实数据源单元测试.

对应 core-contracts-design.md 阶段 4 p4-4b。

覆盖 ``app/metrics/flywheel_metrics.py`` 改造后的所有真实数据源采集逻辑：
- 无数据源时所有指标返回 0（从 0 开始）
- 有数据源但数据集为空时返回 0
- ``data_volume`` 从 ``IDatasetStore.get_version().row_count`` 取值
- ``model_quality`` / ``uncertainty_mean`` 从 ``ISnapshotStore`` 最新快照取值
- ``adoption_rate`` 扫描 adoption 反馈记录计算 accepted 比例
- ``feedback_delay`` 扫描 ``metadata['prediction_timestamp']`` 计算延迟
- 单个指标采集失败不影响其他指标（错误容忍）
- ``set_feedback_dataset_id()`` 懒注入
- ``configure_flywheel_collector()`` / ``reset_flywheel_collector()`` 全局配置
- deprecated 同步方法返回零值 + DeprecationWarning

测试替身
--------
- ``InMemoryDatasetStore``：内存版 ``IDatasetStore``（复用 test_feedback_collector 模式）
- ``InMemorySnapshotStore``：内存版 ``ISnapshotStore``，支持 create/get/list

设计原则
--------
- 不依赖 fastapi / SQLite / 文件系统
- 不依赖 WinSock（避免 asyncio ProactorEventLoop 问题）
- 使用 ``pytest.mark.asyncio`` 标记异步测试
- 每个测试后 ``reset_flywheel_collector()`` 清理全局状态
"""

from __future__ import annotations

import hashlib
import uuid
import json
import warnings
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
from app.contracts.observability import (
    ExperimentSnapshot,
    ISnapshotStore,
)
from app.metrics.flywheel_metrics import (
    ADOPTION_FEEDBACK_TYPE,
    FEEDBACK_DATASET_NAME,
    MODEL_QUALITY_METRIC_KEY,
    PREDICTION_TIMESTAMP_KEY,
    UNCERTAINTY_MEAN_METRIC_KEY,
    FlywheelMetrics,
    FlywheelMetricsCollector,
    _parse_iso8601,
    configure_flywheel_collector,
    get_flywheel_collector,
    reset_flywheel_collector,
    save_report_to_file,
)
from plugins.data_flywheel.feedback_collector import (
    FeedbackCollector,
)


# 测试替身：InMemoryDatasetStore


def _compute_content_hash(records: list[dict[str, Any]]) -> str:
    """与真实 DatasetStore 一致的 content_hash 计算."""
    canonical = json.dumps(records, sort_keys=True, ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _bump_patch(version: str) -> str:
    """patch 版本号 +1."""
    parts = version.split(".")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        parts[2] = str(int(parts[2]) + 1)
        return ".".join(parts)
    return "0.0.1"


class InMemoryDatasetStore(IDatasetStore):
    """内存版 IDatasetStore 测试替身.

    与 ``test_feedback_collector.InMemoryDatasetStore`` 一致，复制一份避免跨
    测试文件 import 私有 fixture。支持 create / commit_version / get_version /
    read / list_versions / deprecate。
    """

    def __init__(self) -> None:
        self._datasets: dict[str, dict[str, Any]] = {}
        self._name_to_id: dict[str, str] = {}
        self._versions: dict[str, list[DatasetVersion]] = {}
        self._records: dict[str, list[dict[str, Any]]] = {}
        self._lineages: list[LineageRecord] = []
        self.commit_should_fail = False
        self.get_version_should_fail = False
        self.read_should_fail = False

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
            raise RuntimeError("模拟 commit 失败")
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
        self._records[dataset_id] = list(records)
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

    async def deprecate(self, dataset_id: str, version: str) -> None:
        for v in self._versions.get(dataset_id, []):
            if v.version == version:
                v.status = DatasetStatus.DEPRECATED
                return
        raise KeyError(f"版本不存在: {dataset_id}/{version}")


# 测试替身：InMemorySnapshotStore


class InMemorySnapshotStore(ISnapshotStore):
    """内存版 ISnapshotStore 测试替身.

    支持 ``create`` / ``get`` / ``list`` / ``reproduce``。``list`` 按
    ``created_at`` 降序返回（与契约一致）。``reproduce`` 返回固定字符串
    ``"wf-reproduce-<snapshot_id>"``。
    """

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
        # Windows 时钟精度低：str(datetime.utcnow()) 在连续调用时可能相同，
        # 导致 snapshot_id 碰撞、后建快照覆盖先建快照（历史 flaky 根因）。
        snapshot_id = f"snap-{uuid.uuid4().hex[:12]}"
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
            raise KeyError(f"snapshot 不存在: {snapshot_id}")
        return self._snapshots[snapshot_id]

    async def list(self, *, filters: Optional[dict[str, Any]] = None) -> list[ExperimentSnapshot]:
        if self.list_should_fail:
            # 持续失败（不自动重置）：模拟真实故障——collect 内多个指标
            # 各自调用 list，一次性失败会让后续调用读到数据（测试意图破坏）
            raise RuntimeError("模拟 list 失败")
        snaps = list(self._snapshots.values())
        # 按 created_at 降序
        snaps.sort(key=lambda s: s.created_at, reverse=True)
        if not filters:
            return snaps
        # 简单过滤
        result = []
        for s in snaps:
            match = True
            for k, v in filters.items():
                if getattr(s, k, None) != v:
                    match = False
                    break
            if match:
                result.append(s)
        return result

    async def reproduce(self, snapshot_id: str) -> str:
        if snapshot_id not in self._snapshots:
            raise KeyError(f"snapshot 不存在: {snapshot_id}")
        return f"wf-reproduce-{snapshot_id}"


# 辅助：构造反馈记录


def _make_feedback_record(
    *,
    feedback_id: str,
    feedback_type: str,
    timestamp: str,
    user_id: str = "u-1",
    accepted: Optional[bool] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """构造一条反馈记录（与 FeedbackCollector 落盘格式一致）."""
    return {
        "feedback_id": feedback_id,
        "timestamp": timestamp,
        "user_id": user_id,
        "feedback_type": feedback_type,
        "prediction_id": f"pred-{feedback_id}",
        "model_version": "v1.0",
        "original_output": {},
        "corrected_output": {},
        "accepted": accepted,
        "notes": "",
        "metadata": metadata or {},
    }


async def _seed_feedback_dataset(
    store: InMemoryDatasetStore,
    records: list[dict[str, Any]],
) -> str:
    """创建 feedback_records 数据集并 commit 一批记录，返回 dataset_id."""
    from plugins.data_flywheel.feedback_collector import FEEDBACK_DATASET_SCHEMA

    dataset_id = await store.create(
        name=FEEDBACK_DATASET_NAME,
        schema=FEEDBACK_DATASET_SCHEMA,
        owner_id="test",
    )
    await store.commit_version(dataset_id, records)
    return dataset_id


# fixtures


@pytest.fixture
def dataset_store() -> InMemoryDatasetStore:
    return InMemoryDatasetStore()


@pytest.fixture
def snapshot_store() -> InMemorySnapshotStore:
    return InMemorySnapshotStore()


@pytest.fixture(autouse=True)
def _reset_global_collector():
    """每个测试后重置全局单例，避免状态污染."""
    yield
    reset_flywheel_collector()


@pytest.fixture
def collector_with_stores(
    dataset_store: InMemoryDatasetStore,
    snapshot_store: InMemorySnapshotStore,
) -> FlywheelMetricsCollector:
    """构造带真实数据源的 collector（feedback_dataset_id 通过 set 注入）."""
    c = FlywheelMetricsCollector(
        dataset_store=dataset_store,
        snapshot_store=snapshot_store,
    )
    return c


# 测试：常量


@pytest.mark.unit
@pytest.mark.contracts
class TestFlywheelMetricsConstants:
    """模块级常量校验."""

    def test_feedback_dataset_name(self):
        assert FEEDBACK_DATASET_NAME == "feedback_records"

    def test_adoption_feedback_type(self):
        assert ADOPTION_FEEDBACK_TYPE == "adoption"

    def test_prediction_timestamp_key(self):
        assert PREDICTION_TIMESTAMP_KEY == "prediction_timestamp"

    def test_model_quality_metric_key(self):
        assert MODEL_QUALITY_METRIC_KEY == "model_quality"

    def test_uncertainty_mean_metric_key(self):
        assert UNCERTAINTY_MEAN_METRIC_KEY == "uncertainty_mean"


# 测试：从 0 开始（无数据源）


@pytest.mark.unit
@pytest.mark.contracts
class TestStartFromZero:
    """无数据源或空数据源时所有指标返回 0."""

    @pytest.mark.asyncio
    async def test_no_data_sources_returns_all_zeros(self):
        """无 dataset_store / snapshot_store 时所有指标为 0."""
        c = FlywheelMetricsCollector()
        m = await c.collect_current_metrics_async()

        assert m.data_volume == 0
        assert m.model_quality == 0.0
        assert m.adoption_rate == 0.0
        assert m.uncertainty_mean == 0.0
        assert m.feedback_delay == 0.0

    @pytest.mark.asyncio
    async def test_no_dataset_id_returns_zeros(self, dataset_store, snapshot_store):
        """有 store 但 feedback_dataset_id=None 时相关指标为 0."""
        c = FlywheelMetricsCollector(
            dataset_store=dataset_store,
            snapshot_store=snapshot_store,
        )
        # feedback_dataset_id 未注入
        m = await c.collect_current_metrics_async()
        assert m.data_volume == 0
        assert m.adoption_rate == 0.0
        assert m.feedback_delay == 0.0

    @pytest.mark.asyncio
    async def test_empty_dataset_returns_zeros(self, dataset_store, snapshot_store):
        """数据集存在但版本无记录时返回 0."""
        c = FlywheelMetricsCollector(
            dataset_store=dataset_store,
            snapshot_store=snapshot_store,
        )
        dataset_id = await _seed_feedback_dataset(dataset_store, [])
        c.set_feedback_dataset_id(dataset_id)

        m = await c.collect_current_metrics_async()
        assert m.data_volume == 0
        assert m.adoption_rate == 0.0
        assert m.feedback_delay == 0.0

    @pytest.mark.asyncio
    async def test_empty_snapshot_store_returns_zeros(self, dataset_store, snapshot_store):
        """snapshot_store 为空时 model_quality / uncertainty_mean 为 0."""
        c = FlywheelMetricsCollector(
            dataset_store=dataset_store,
            snapshot_store=snapshot_store,
        )
        m = await c.collect_current_metrics_async()
        assert m.model_quality == 0.0
        assert m.uncertainty_mean == 0.0


# 测试：data_volume 从 IDatasetStore 取值


@pytest.mark.unit
@pytest.mark.contracts
class TestDataVolumeFromDatasetStore:
    """data_volume 从 feedback_records 数据集 row_count 取值."""

    @pytest.mark.asyncio
    async def test_data_volume_matches_row_count(self, collector_with_stores, dataset_store):
        records = [
            _make_feedback_record(
                feedback_id=f"fb-{i}",
                feedback_type="adoption",
                timestamp=datetime.now(timezone.utc).isoformat(),
                accepted=True,
            )
            for i in range(5)
        ]
        dataset_id = await _seed_feedback_dataset(dataset_store, records)
        collector_with_stores.set_feedback_dataset_id(dataset_id)

        m = await collector_with_stores.collect_current_metrics_async()
        assert m.data_volume == 5

    @pytest.mark.asyncio
    async def test_data_volume_zero_when_get_version_fails(self, collector_with_stores, dataset_store):
        """get_version 抛错时 data_volume 回退为 0（错误容忍）."""
        dataset_id = await _seed_feedback_dataset(
            dataset_store,
            [
                _make_feedback_record(
                    feedback_id="fb-x",
                    feedback_type="adoption",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    accepted=True,
                )
            ],
        )
        collector_with_stores.set_feedback_dataset_id(dataset_id)
        dataset_store.get_version_should_fail = True

        m = await collector_with_stores.collect_current_metrics_async()
        assert m.data_volume == 0


# 测试：model_quality / uncertainty_mean 从 ISnapshotStore 取值


@pytest.mark.unit
@pytest.mark.contracts
class TestModelMetricsFromSnapshotStore:
    """model_quality / uncertainty_mean 从最新快照 metrics 取值."""

    @pytest.mark.asyncio
    async def test_model_quality_from_latest_snapshot(self, collector_with_stores, snapshot_store):
        await snapshot_store.create(
            config={},
            dataset_versions=[],
            model_uri="model://v1",
            metrics={"model_quality": 82.5, "uncertainty_mean": 0.18},
            created_by="test",
        )
        m = await collector_with_stores.collect_current_metrics_async()
        assert m.model_quality == 82.5
        assert m.uncertainty_mean == 0.18

    @pytest.mark.asyncio
    async def test_latest_snapshot_picks_most_recent(self, collector_with_stores, snapshot_store):
        """多个快照时取 created_at 最新的."""
        old = await snapshot_store.create(
            config={},
            dataset_versions=[],
            model_uri="model://old",
            metrics={"model_quality": 70.0, "uncertainty_mean": 0.3},
            created_by="test",
        )
        # 强制旧快照时间
        old.created_at = datetime.utcnow() - timedelta(days=2)

        new = await snapshot_store.create(
            config={},
            dataset_versions=[],
            model_uri="model://new",
            metrics={"model_quality": 90.0, "uncertainty_mean": 0.1},
            created_by="test",
        )
        # 确保新快照比旧快照晚（create 内部用 utcnow）
        new.created_at = datetime.utcnow()

        m = await collector_with_stores.collect_current_metrics_async()
        assert m.model_quality == 90.0
        assert m.uncertainty_mean == 0.1

    @pytest.mark.asyncio
    async def test_model_quality_missing_metric_key_returns_zero(self, collector_with_stores, snapshot_store):
        """快照 metrics 中无 model_quality 键时返回 0."""
        await snapshot_store.create(
            config={},
            dataset_versions=[],
            model_uri="model://v1",
            metrics={"accuracy": 0.95},  # 没有 model_quality
            created_by="test",
        )
        m = await collector_with_stores.collect_current_metrics_async()
        assert m.model_quality == 0.0
        assert m.uncertainty_mean == 0.0

    @pytest.mark.asyncio
    async def test_model_quality_snapshot_list_fails_returns_zero(self, collector_with_stores, snapshot_store):
        """snapshot_store.list() 抛错时 model_quality / uncertainty_mean 为 0."""
        await snapshot_store.create(
            config={},
            dataset_versions=[],
            model_uri="model://v1",
            metrics={"model_quality": 88.0, "uncertainty_mean": 0.2},
            created_by="test",
        )
        snapshot_store.list_should_fail = True

        m = await collector_with_stores.collect_current_metrics_async()
        assert m.model_quality == 0.0
        assert m.uncertainty_mean == 0.0


# 测试：adoption_rate 从反馈记录计算


@pytest.mark.unit
@pytest.mark.contracts
class TestAdoptionRateFromFeedback:
    """adoption_rate 扫描 adoption 类型反馈，计算 accepted=True 比例."""

    @pytest.mark.asyncio
    async def test_adoption_rate_all_accepted(self, collector_with_stores, dataset_store):
        records = [
            _make_feedback_record(
                feedback_id=f"fb-{i}",
                feedback_type="adoption",
                timestamp=datetime.now(timezone.utc).isoformat(),
                accepted=True,
            )
            for i in range(4)
        ]
        dataset_id = await _seed_feedback_dataset(dataset_store, records)
        collector_with_stores.set_feedback_dataset_id(dataset_id)

        m = await collector_with_stores.collect_current_metrics_async()
        assert m.adoption_rate == 100.0

    @pytest.mark.asyncio
    async def test_adoption_rate_half_accepted(self, collector_with_stores, dataset_store):
        records = [
            _make_feedback_record(
                feedback_id="fb-1",
                feedback_type="adoption",
                timestamp=datetime.now(timezone.utc).isoformat(),
                accepted=True,
            ),
            _make_feedback_record(
                feedback_id="fb-2",
                feedback_type="adoption",
                timestamp=datetime.now(timezone.utc).isoformat(),
                accepted=False,
            ),
        ]
        dataset_id = await _seed_feedback_dataset(dataset_store, records)
        collector_with_stores.set_feedback_dataset_id(dataset_id)

        m = await collector_with_stores.collect_current_metrics_async()
        assert m.adoption_rate == 50.0

    @pytest.mark.asyncio
    async def test_adoption_rate_no_adoption_records_returns_zero(self, collector_with_stores, dataset_store):
        """有反馈但无 adoption 类型时 adoption_rate 为 0."""
        records = [
            _make_feedback_record(
                feedback_id="fb-1",
                feedback_type="annotation",
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
            _make_feedback_record(
                feedback_id="fb-2",
                feedback_type="correction",
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
        ]
        dataset_id = await _seed_feedback_dataset(dataset_store, records)
        collector_with_stores.set_feedback_dataset_id(dataset_id)

        m = await collector_with_stores.collect_current_metrics_async()
        assert m.adoption_rate == 0.0

    @pytest.mark.asyncio
    async def test_adoption_rate_ignores_annotation_records(self, collector_with_stores, dataset_store):
        """annotation 类型记录不计入 adoption_rate 分母."""
        records = [
            _make_feedback_record(
                feedback_id="fb-1",
                feedback_type="adoption",
                timestamp=datetime.now(timezone.utc).isoformat(),
                accepted=True,
            ),
            _make_feedback_record(
                feedback_id="fb-2",
                feedback_type="adoption",
                timestamp=datetime.now(timezone.utc).isoformat(),
                accepted=False,
            ),
            # 这两条 annotation 不应影响 adoption_rate
            _make_feedback_record(
                feedback_id="fb-3",
                feedback_type="annotation",
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
            _make_feedback_record(
                feedback_id="fb-4",
                feedback_type="annotation",
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
        ]
        dataset_id = await _seed_feedback_dataset(dataset_store, records)
        collector_with_stores.set_feedback_dataset_id(dataset_id)

        m = await collector_with_stores.collect_current_metrics_async()
        assert m.adoption_rate == 50.0  # 1/2，而非 1/4

    @pytest.mark.asyncio
    async def test_adoption_rate_read_fails_returns_zero(self, collector_with_stores, dataset_store):
        """read 抛错时 adoption_rate 回退为 0."""
        records = [
            _make_feedback_record(
                feedback_id="fb-1",
                feedback_type="adoption",
                timestamp=datetime.now(timezone.utc).isoformat(),
                accepted=True,
            )
        ]
        dataset_id = await _seed_feedback_dataset(dataset_store, records)
        collector_with_stores.set_feedback_dataset_id(dataset_id)
        dataset_store.read_should_fail = True

        m = await collector_with_stores.collect_current_metrics_async()
        assert m.adoption_rate == 0.0


# 测试：feedback_delay 计算


@pytest.mark.unit
@pytest.mark.contracts
class TestFeedbackDelayCalculation:
    """feedback_delay 扫描 metadata['prediction_timestamp'] 计算延迟."""

    @pytest.mark.asyncio
    async def test_feedback_delay_average_minutes(self, collector_with_stores, dataset_store):
        """2 条记录，延迟分别为 30 分钟和 60 分钟，平均 45 分钟."""
        base = datetime(2026, 7, 13, 12, 0, 0, tzinfo=timezone.utc)
        records = [
            _make_feedback_record(
                feedback_id="fb-1",
                feedback_type="adoption",
                timestamp=(base + timedelta(minutes=30)).isoformat(),
                accepted=True,
                metadata={"prediction_timestamp": base.isoformat()},
            ),
            _make_feedback_record(
                feedback_id="fb-2",
                feedback_type="adoption",
                timestamp=(base + timedelta(minutes=60)).isoformat(),
                accepted=False,
                metadata={"prediction_timestamp": base.isoformat()},
            ),
        ]
        dataset_id = await _seed_feedback_dataset(dataset_store, records)
        collector_with_stores.set_feedback_dataset_id(dataset_id)

        m = await collector_with_stores.collect_current_metrics_async()
        assert m.feedback_delay == 45.0

    @pytest.mark.asyncio
    async def test_feedback_delay_no_prediction_timestamp_returns_zero(self, collector_with_stores, dataset_store):
        """无 prediction_timestamp 字段的记录跳过，全部跳过时返回 0."""
        records = [
            _make_feedback_record(
                feedback_id="fb-1",
                feedback_type="adoption",
                timestamp=datetime.now(timezone.utc).isoformat(),
                accepted=True,
                metadata={},  # 无 prediction_timestamp
            )
        ]
        dataset_id = await _seed_feedback_dataset(dataset_store, records)
        collector_with_stores.set_feedback_dataset_id(dataset_id)

        m = await collector_with_stores.collect_current_metrics_async()
        assert m.feedback_delay == 0.0

    @pytest.mark.asyncio
    async def test_feedback_delay_negative_delta_skipped(self, collector_with_stores, dataset_store):
        """预测时间晚于反馈时间（异常数据）跳过."""
        feedback_ts = datetime(2026, 7, 13, 12, 0, 0, tzinfo=timezone.utc)
        prediction_ts = feedback_ts + timedelta(hours=1)  # 晚于反馈
        records = [
            _make_feedback_record(
                feedback_id="fb-1",
                feedback_type="adoption",
                timestamp=feedback_ts.isoformat(),
                accepted=True,
                metadata={"prediction_timestamp": prediction_ts.isoformat()},
            )
        ]
        dataset_id = await _seed_feedback_dataset(dataset_store, records)
        collector_with_stores.set_feedback_dataset_id(dataset_id)

        m = await collector_with_stores.collect_current_metrics_async()
        assert m.feedback_delay == 0.0

    @pytest.mark.asyncio
    async def test_feedback_delay_invalid_timestamp_skipped(self, collector_with_stores, dataset_store):
        """prediction_timestamp 格式非法时跳过该记录."""
        base = datetime(2026, 7, 13, 12, 0, 0, tzinfo=timezone.utc)
        records = [
            # 合法记录
            _make_feedback_record(
                feedback_id="fb-1",
                feedback_type="adoption",
                timestamp=(base + timedelta(minutes=30)).isoformat(),
                accepted=True,
                metadata={"prediction_timestamp": base.isoformat()},
            ),
            # 非法 prediction_timestamp
            _make_feedback_record(
                feedback_id="fb-2",
                feedback_type="adoption",
                timestamp=(base + timedelta(minutes=60)).isoformat(),
                accepted=False,
                metadata={"prediction_timestamp": "not-a-date"},
            ),
        ]
        dataset_id = await _seed_feedback_dataset(dataset_store, records)
        collector_with_stores.set_feedback_dataset_id(dataset_id)

        m = await collector_with_stores.collect_current_metrics_async()
        # 只有 fb-1 被计入，延迟 30 分钟
        assert m.feedback_delay == 30.0


# 测试：错误容忍（单个指标失败不影响其他指标）


@pytest.mark.unit
@pytest.mark.contracts
class TestErrorIsolation:
    """单个指标采集失败不影响其他指标."""

    @pytest.mark.asyncio
    async def test_dataset_store_failure_does_not_affect_snapshot_metrics(self, dataset_store, snapshot_store):
        """dataset_store.get_version 失败时 model_quality 仍能从 snapshot 取."""
        c = FlywheelMetricsCollector(
            dataset_store=dataset_store,
            snapshot_store=snapshot_store,
        )
        dataset_id = await _seed_feedback_dataset(
            dataset_store,
            [
                _make_feedback_record(
                    feedback_id="fb-1",
                    feedback_type="adoption",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    accepted=True,
                )
            ],
        )
        c.set_feedback_dataset_id(dataset_id)

        await snapshot_store.create(
            config={},
            dataset_versions=[],
            model_uri="model://v1",
            metrics={"model_quality": 88.0, "uncertainty_mean": 0.15},
            created_by="test",
        )

        # 让 get_version 失败（影响 data_volume）
        dataset_store.get_version_should_fail = True
        # 让 read 失败（影响 adoption_rate / feedback_delay）
        dataset_store.read_should_fail = True

        m = await c.collect_current_metrics_async()
        # 失败的指标为 0
        assert m.data_volume == 0
        assert m.adoption_rate == 0.0
        assert m.feedback_delay == 0.0
        # 未失败的指标正常
        assert m.model_quality == 88.0
        assert m.uncertainty_mean == 0.15

    @pytest.mark.asyncio
    async def test_snapshot_store_failure_does_not_affect_dataset_metrics(self, dataset_store, snapshot_store):
        """snapshot_store.list 失败时 data_volume / adoption_rate 仍能从 dataset 取."""
        c = FlywheelMetricsCollector(
            dataset_store=dataset_store,
            snapshot_store=snapshot_store,
        )
        records = [
            _make_feedback_record(
                feedback_id=f"fb-{i}",
                feedback_type="adoption",
                timestamp=datetime.now(timezone.utc).isoformat(),
                accepted=True,
            )
            for i in range(3)
        ]
        dataset_id = await _seed_feedback_dataset(dataset_store, records)
        c.set_feedback_dataset_id(dataset_id)

        await snapshot_store.create(
            config={},
            dataset_versions=[],
            model_uri="model://v1",
            metrics={"model_quality": 90.0, "uncertainty_mean": 0.1},
            created_by="test",
        )

        snapshot_store.list_should_fail = True

        m = await c.collect_current_metrics_async()
        # 失败的指标为 0
        assert m.model_quality == 0.0
        assert m.uncertainty_mean == 0.0
        # 未失败的指标正常
        assert m.data_volume == 3
        assert m.adoption_rate == 100.0


# 测试：set_feedback_dataset_id 懒注入


@pytest.mark.unit
@pytest.mark.contracts
class TestLazyFeedbackDatasetIdInjection:
    """set_feedback_dataset_id 懒注入."""

    @pytest.mark.asyncio
    async def test_set_feedback_dataset_id_enables_data_volume(self, dataset_store, snapshot_store):
        c = FlywheelMetricsCollector(
            dataset_store=dataset_store,
            snapshot_store=snapshot_store,
        )
        # 未注入前 data_volume=0
        m1 = await c.collect_current_metrics_async()
        assert m1.data_volume == 0

        # 注入数据集
        records = [
            _make_feedback_record(
                feedback_id="fb-1",
                feedback_type="adoption",
                timestamp=datetime.now(timezone.utc).isoformat(),
                accepted=True,
            )
        ]
        dataset_id = await _seed_feedback_dataset(dataset_store, records)
        c.set_feedback_dataset_id(dataset_id)

        m2 = await c.collect_current_metrics_async()
        assert m2.data_volume == 1

    def test_set_feedback_dataset_id_rejects_empty(self, dataset_store):
        c = FlywheelMetricsCollector(dataset_store=dataset_store)
        with pytest.raises(ValueError, match="dataset_id"):
            c.set_feedback_dataset_id("")
        with pytest.raises(ValueError, match="dataset_id"):
            c.set_feedback_dataset_id(None)  # type: ignore[arg-type]

    def test_feedback_dataset_id_property(self, dataset_store):
        c = FlywheelMetricsCollector(dataset_store=dataset_store)
        assert c.feedback_dataset_id is None
        c.set_feedback_dataset_id("ds-test")
        assert c.feedback_dataset_id == "ds-test"


# 测试：全局配置


@pytest.mark.unit
@pytest.mark.contracts
class TestGlobalConfiguration:
    """configure_flywheel_collector / reset_flywheel_collector."""

    def test_get_flywheel_collector_returns_singleton(self):
        c1 = get_flywheel_collector()
        c2 = get_flywheel_collector()
        assert c1 is c2

    def test_configure_replaces_singleton(self, dataset_store, snapshot_store):
        c1 = get_flywheel_collector()
        configured = configure_flywheel_collector(
            dataset_store=dataset_store,
            snapshot_store=snapshot_store,
        )
        c2 = get_flywheel_collector()
        assert c2 is configured
        assert c2 is not c1
        assert c2.dataset_store is dataset_store
        assert c2.snapshot_store is snapshot_store

    def test_reset_clears_singleton(self):
        configure_flywheel_collector()
        assert get_flywheel_collector() is not None
        reset_flywheel_collector()
        # 重置后 get 会创建新实例
        new_one = get_flywheel_collector()
        assert new_one is not None

    @pytest.mark.asyncio
    async def test_configured_collector_uses_injected_stores(self, dataset_store, snapshot_store):
        """配置后的全局单例能从注入的数据源取真实数据."""
        records = [
            _make_feedback_record(
                feedback_id="fb-1",
                feedback_type="adoption",
                timestamp=datetime.now(timezone.utc).isoformat(),
                accepted=True,
            )
        ]
        dataset_id = await _seed_feedback_dataset(dataset_store, records)

        c = configure_flywheel_collector(
            dataset_store=dataset_store,
            snapshot_store=snapshot_store,
            feedback_dataset_id=dataset_id,
        )

        m = await c.collect_current_metrics_async()
        assert m.data_volume == 1
        assert m.adoption_rate == 100.0


# 测试：deprecated 同步方法


@pytest.mark.unit
@pytest.mark.contracts
class TestDeprecatedSyncMethods:
    """deprecated 同步方法返回零值 + DeprecationWarning."""

    def test_collect_current_metrics_returns_zeros_with_warning(self):
        c = FlywheelMetricsCollector()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            m = c.collect_current_metrics()
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "collect_current_metrics_async" in str(w[0].message)
        assert m.data_volume == 0
        assert m.model_quality == 0.0

    def test_get_historical_metrics_returns_empty_with_warning(self):
        c = FlywheelMetricsCollector()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = c.get_historical_metrics(days=7)
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
        assert result == []

    def test_generate_weekly_report_returns_zeros_with_warning(self):
        c = FlywheelMetricsCollector()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            report = c.generate_weekly_report()
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
        assert report["current_metrics"]["data_volume"] == 0
        assert report["historical_metrics"] == []
        assert "summary" in report


# 测试：get_historical_metrics_async


@pytest.mark.unit
@pytest.mark.contracts
class TestHistoricalMetricsAsync:
    """get_historical_metrics_async 从 snapshot_store 构造历史."""

    @pytest.mark.asyncio
    async def test_no_snapshot_store_returns_empty(self):
        c = FlywheelMetricsCollector()
        result = await c.get_historical_metrics_async(days=7)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_snapshots_within_days_window(self, collector_with_stores, snapshot_store):
        now = datetime.utcnow()
        # 3 天前的快照（在 7 天窗口内）
        old_snap = await snapshot_store.create(
            config={},
            dataset_versions=[],
            model_uri="model://old",
            metrics={
                "model_quality": 70.0,
                "uncertainty_mean": 0.3,
                "data_volume": 50,
                "adoption_rate": 60.0,
                "feedback_delay": 30.0,
            },
            created_by="test",
        )
        old_snap.created_at = now - timedelta(days=3)

        # 10 天前的快照（在 7 天窗口外，应被过滤）
        too_old_snap = await snapshot_store.create(
            config={},
            dataset_versions=[],
            model_uri="model://too-old",
            metrics={
                "model_quality": 50.0,
                "uncertainty_mean": 0.5,
            },
            created_by="test",
        )
        too_old_snap.created_at = now - timedelta(days=10)

        result = await collector_with_stores.get_historical_metrics_async(days=7)
        # 只有 1 条在窗口内
        assert len(result) == 1
        assert result[0].model_quality == 70.0

    @pytest.mark.asyncio
    async def test_list_fails_returns_empty(self, collector_with_stores, snapshot_store):
        snapshot_store.list_should_fail = True
        result = await collector_with_stores.get_historical_metrics_async(days=7)
        assert result == []


# 测试：generate_weekly_report_async


@pytest.mark.unit
@pytest.mark.contracts
class TestWeeklyReportAsync:
    """generate_weekly_report_async 完整流程."""

    @pytest.mark.asyncio
    async def test_report_structure_no_data(self, collector_with_stores):
        """无数据时报告结构完整，所有指标为 0."""
        report = await collector_with_stores.generate_weekly_report_async()

        assert report["report_type"] == "weekly"
        assert "generated_at" in report
        assert "period" in report
        assert "current_metrics" in report
        assert "historical_metrics" in report
        assert "trends" in report
        assert "summary" in report

        assert report["current_metrics"]["data_volume"] == 0
        assert report["historical_metrics"] == []
        assert report["trends"] == {}  # < 2 条历史

    @pytest.mark.asyncio
    async def test_report_with_data(self, collector_with_stores, dataset_store, snapshot_store):
        """有数据时报告包含真实指标."""
        records = [
            _make_feedback_record(
                feedback_id=f"fb-{i}",
                feedback_type="adoption",
                timestamp=datetime.now(timezone.utc).isoformat(),
                accepted=True,
            )
            for i in range(3)
        ]
        dataset_id = await _seed_feedback_dataset(dataset_store, records)
        collector_with_stores.set_feedback_dataset_id(dataset_id)

        await snapshot_store.create(
            config={},
            dataset_versions=[],
            model_uri="model://v1",
            metrics={"model_quality": 85.0, "uncertainty_mean": 0.2},
            created_by="test",
        )

        report = await collector_with_stores.generate_weekly_report_async()
        assert report["current_metrics"]["data_volume"] == 3
        assert report["current_metrics"]["model_quality"] == 85.0
        assert report["current_metrics"]["adoption_rate"] == 100.0
        assert "summary" in report
        assert "health_score" in report["summary"]


# 测试：FlywheelMetrics 数据类


@pytest.mark.unit
@pytest.mark.contracts
class TestFlywheelMetricsDataclass:
    """FlywheelMetrics 数据类."""

    def test_default_timestamp_autofill(self):
        m = FlywheelMetrics(
            data_volume=0,
            model_quality=0.0,
            adoption_rate=0.0,
            uncertainty_mean=0.0,
            feedback_delay=0.0,
        )
        assert m.timestamp != ""
        # ISO8601 格式
        datetime.fromisoformat(m.timestamp)

    def test_explicit_timestamp_preserved(self):
        ts = "2026-07-13T12:00:00+00:00"
        m = FlywheelMetrics(
            data_volume=1,
            model_quality=50.0,
            adoption_rate=50.0,
            uncertainty_mean=0.5,
            feedback_delay=15.0,
            timestamp=ts,
        )
        assert m.timestamp == ts

    def test_to_dict(self):
        m = FlywheelMetrics(
            data_volume=10,
            model_quality=80.0,
            adoption_rate=70.0,
            uncertainty_mean=0.2,
            feedback_delay=30.0,
            timestamp="2026-07-13T12:00:00+00:00",
        )
        d = m.to_dict()
        assert d["data_volume"] == 10
        assert d["model_quality"] == 80.0
        assert d["timestamp"] == "2026-07-13T12:00:00+00:00"


# 测试：_parse_iso8601 辅助函数


@pytest.mark.unit
@pytest.mark.contracts
class TestParseIso8601:
    """_parse_iso8601 时间戳解析."""

    def test_with_timezone(self):
        ts = "2026-07-13T12:34:56+00:00"
        dt = _parse_iso8601(ts)
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 7
        assert dt.day == 13
        assert dt.tzinfo is not None

    def test_naive_treated_as_utc(self):
        ts = "2026-07-13T12:34:56"
        dt = _parse_iso8601(ts)
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt.tzinfo == timezone.utc

    def test_with_microseconds(self):
        ts = "2026-07-13T12:34:56.789012+00:00"
        dt = _parse_iso8601(ts)
        assert dt is not None
        assert dt.microsecond == 789012

    def test_invalid_format_returns_none(self):
        assert _parse_iso8601("not-a-date") is None

    def test_empty_string_returns_none(self):
        assert _parse_iso8601("") is None

    def test_non_string_returns_none(self):
        assert _parse_iso8601(None) is None  # type: ignore[arg-type]
        assert _parse_iso8601(123) is None  # type: ignore[arg-type]


# 测试：save_report_to_file


@pytest.mark.unit
@pytest.mark.contracts
class TestSaveReportToFile:
    """save_report_to_file 报告保存."""

    def test_save_creates_json_file(self, tmp_path):
        report = {
            "report_type": "weekly",
            "generated_at": "2026-07-13T12:00:00+00:00",
            "current_metrics": {"data_volume": 5},
        }
        filepath = save_report_to_file(report, str(tmp_path))
        assert filepath.exists()
        assert filepath.suffix == ".json"

        import json as _json

        with open(filepath, "r", encoding="utf-8") as f:
            loaded = _json.load(f)
        assert loaded["report_type"] == "weekly"
        assert loaded["current_metrics"]["data_volume"] == 5

    def test_save_creates_output_dir_if_missing(self, tmp_path):
        output_dir = tmp_path / "new_dir" / "subdir"
        report = {"report_type": "weekly"}
        filepath = save_report_to_file(report, str(output_dir))
        assert filepath.exists()
        assert output_dir.exists()


# 测试：与 FeedbackCollector 端到端集成


@pytest.mark.unit
@pytest.mark.contracts
class TestEndToEndWithFeedbackCollector:
    """端到端：FeedbackCollector 写入 → FlywheelMetricsCollector 读取."""

    @pytest.mark.asyncio
    async def test_feedback_collector_writes_metrics_reads(self, dataset_store, snapshot_store):
        """FeedbackCollector.flush 后 FlywheelMetricsCollector 能读到真实指标."""
        # 1. 用 FeedbackCollector 写入反馈
        fc = FeedbackCollector(dataset_store=dataset_store, owner_id="test")
        await fc.record_adoption(
            user_id="u-1",
            accepted=True,
            prediction_id="pred-1",
            model_version="v1.0",
            original_output={"label": "chatter"},
        )
        await fc.record_adoption(
            user_id="u-2",
            accepted=False,
            prediction_id="pred-2",
            model_version="v1.0",
            original_output={"label": "no_chatter"},
        )
        await fc.flush()

        # 2. 把 FeedbackCollector 解析出的 dataset_id 注入到 FlywheelMetricsCollector
        assert fc.dataset_id is not None
        metrics_collector = FlywheelMetricsCollector(
            dataset_store=dataset_store,
            snapshot_store=snapshot_store,
        )
        metrics_collector.set_feedback_dataset_id(fc.dataset_id)

        # 3. 采集指标
        m = await metrics_collector.collect_current_metrics_async()
        assert m.data_volume == 2
        assert m.adoption_rate == 50.0  # 1 accepted / 2 total

    @pytest.mark.asyncio
    async def test_feedback_with_prediction_timestamp_calculates_delay(self, dataset_store, snapshot_store):
        """带 prediction_timestamp 的反馈能被 feedback_delay 正确计算."""
        fc = FeedbackCollector(dataset_store=dataset_store, owner_id="test")
        pred_ts = datetime.now(timezone.utc) - timedelta(minutes=45)
        await fc.record_adoption(
            user_id="u-1",
            accepted=True,
            prediction_id="pred-1",
            model_version="v1.0",
            original_output={"label": "chatter"},
            metadata={"prediction_timestamp": pred_ts.isoformat()},
        )
        await fc.flush()

        metrics_collector = FlywheelMetricsCollector(
            dataset_store=dataset_store,
            snapshot_store=snapshot_store,
        )
        metrics_collector.set_feedback_dataset_id(fc.dataset_id)

        m = await metrics_collector.collect_current_metrics_async()
        # 延迟应该接近 45 分钟（允许 ±5 分钟波动，因为 feedback_ts 是 now）
        assert 40.0 <= m.feedback_delay <= 50.0
