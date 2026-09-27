"""模型热更新管理器单元测试.

对应 core-contracts-design.md 阶段 4 p4-5 /
plugins/data_flywheel/hot_update_manager.py + main.py 集成.

覆盖：
    - DeploymentRecord 数据类（__post_init__ 校验、to_dict、observation_ends_at）
    - ModelStage / DeploymentStatus 枚举
    - HotUpdateManager 构造与属性
    - canary_deploy 全生命周期
    - observe_deployment 三态决策（continue / promote / rollback）
    - promote（晋升）与 rollback（回滚）
    - 流量分配 select_model_for_request（注入 rng 确定性测试）
    - 降级模式（model_registry_service=None）
    - 并发灰度拒绝（同名 canary 已 OBSERVING 时拒绝新部署）
    - 终态后操作抛异常（PROMOTED/ROLLED_BACK/FAILED 状态下 observe/promote/rollback）
    - 配置解析优先级（显式参数 > config > 默认值）
    - 查询 API（get_deployment / list_deployments / list_model_stages）
    - 全局单例（get / configure / reset）
    - Plugin 集成（on_load 配置、TASK_HANDLER 注册、health_check 扩展、
      _handle_hot_update_request 7 action 分发、hot_update_manager property）

测试替身：
    - ``FakeModelRegistryService``：模拟 ModelRegistryService 单例，
      register_model/list_models/get_model_entry 可控。
    - ``InMemoryDatasetStore``：与 p4-2 测试对齐的内存版 IDatasetStore（plugin 集成用）。

不依赖 torch / sklearn / fastapi / SQLite / 文件系统，可在无 WinSock 环境运行。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch

import pytest
import pytest_asyncio

from app.contracts.dataset import (
    DatasetSchema,
    DatasetStatus,
    DatasetVersion,
    IDatasetStore,
    LineageRecord,
)
from app.contracts.observability import ExperimentSnapshot, ISnapshotStore
from app.contracts.plugin import PluginContext
from app.plugins.extension_registry import (
    ExtensionRegistry,
    reset_extension_registry,
)
from plugins.data_flywheel.hot_update_manager import (
    DeploymentRecord,
    DeploymentStatus,
    HotUpdateManager,
    ModelStage,
    ObservationDecision,
    configure_hot_update_manager,
    get_hot_update_manager,
    reset_hot_update_manager,
)


# 测试替身：FakeModelRegistryService


class FakeModelRegistryService:
    """模拟 ModelRegistryService.

    - register_model 总是返回 True（成功）
    - list_models 返回已注册的 model_name 列表
    - get_model_entry 返回最近一次注册的 model_info
    """

    def __init__(self) -> None:
        self._registered: dict[str, Any] = {}
        self.register_call_count: int = 0
        self.list_call_count: int = 0

    def register_model(self, model_info: Any) -> bool:
        self.register_call_count += 1
        name = getattr(model_info, "name", None) or str(model_info)
        self._registered[name] = model_info
        return True

    def list_models(self, return_objects: bool = False):
        self.list_call_count += 1
        if return_objects:
            return list(self._registered.values())
        return list(self._registered.keys())

    def get_model_entry(self, model_name: str):
        return self._registered.get(model_name)


# 测试替身：InMemoryDatasetStore（与 test_feedback_collector.py 对齐）


class InMemoryDatasetStore(IDatasetStore):
    """内存版 IDatasetStore 测试替身（plugin 集成用）."""

    def __init__(self) -> None:
        self._datasets: dict[str, dict[str, Any]] = {}
        self._name_to_id: dict[str, str] = {}
        self._versions: dict[str, list[DatasetVersion]] = {}
        self._records: dict[str, list[dict[str, Any]]] = {}

    async def create(
        self,
        name: str,
        schema: DatasetSchema,
        *,
        owner_id: str,
        description: str = "",
    ) -> str:
        if name in self._name_to_id:
            return self._name_to_id[name]
        dataset_id = f"ds-{hashlib.sha256(name.encode()).hexdigest()[:12]}"
        self._datasets[dataset_id] = {
            "name": name,
            "schema": schema,
            "owner_id": owner_id,
            "description": description,
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
        if dataset_id not in self._datasets:
            raise KeyError(f"dataset 不存在: {dataset_id}")
        v = DatasetVersion(
            dataset_id=dataset_id,
            version=version or f"1.{len(self._versions[dataset_id])}.0",
            status=DatasetStatus.PUBLISHED,
            schema=self._datasets[dataset_id]["schema"],
            content_hash="sha256:test",
            row_count=len(records),
            size_bytes=0,
            created_at=datetime.utcnow(),
            created_by=self._datasets[dataset_id]["owner_id"],
            storage_uri=f"memory://{dataset_id}",
            lineage=None,
        )
        self._versions[dataset_id].append(v)
        self._records[dataset_id] = list(records)
        return v

    async def get_version(self, dataset_id: str, version: Optional[str] = None) -> DatasetVersion:
        versions = self._versions.get(dataset_id, [])
        if not versions:
            raise KeyError(f"dataset 无版本: {dataset_id}")
        return versions[-1]

    async def read(
        self,
        dataset_id: str,
        version: Optional[str] = None,
        *,
        batch_size: int = 1000,
    ):
        records = self._records.get(dataset_id, [])
        yield records

    async def list_versions(self, dataset_id: str) -> list[DatasetVersion]:
        return list(self._versions.get(dataset_id, []))

    async def deprecate(self, dataset_id: str, version: str) -> None:
        pass


# 测试替身：InMemorySnapshotStore


class InMemorySnapshotStore(ISnapshotStore):
    """内存版 ISnapshotStore 测试替身."""

    def __init__(self) -> None:
        self._snapshots: dict[str, ExperimentSnapshot] = {}

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
        snap_id = f"snap-{hashlib.sha256(str(datetime.utcnow()).encode()).hexdigest()[:12]}"
        snap = ExperimentSnapshot(
            snapshot_id=snap_id,
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
        self._snapshots[snap_id] = snap
        return snap

    async def get(self, snapshot_id: str) -> ExperimentSnapshot:
        return self._snapshots[snapshot_id]

    async def list(self, *, filters: Optional[dict[str, Any]] = None):
        return list(self._snapshots.values())

    async def reproduce(self, snapshot_id: str) -> str:
        return f"reproduce-{snapshot_id}"


# Fixtures


_PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugins" / "data_flywheel"


@pytest.fixture
def fake_registry_service() -> FakeModelRegistryService:
    return FakeModelRegistryService()


@pytest.fixture
def snapshot_store() -> InMemorySnapshotStore:
    return InMemorySnapshotStore()


@pytest.fixture
def manager(fake_registry_service, snapshot_store) -> HotUpdateManager:
    """构造一个完整配置的 HotUpdateManager（非降级模式）."""
    return HotUpdateManager(
        model_registry_service=fake_registry_service,
        snapshot_store=snapshot_store,
        config={
            "canary_ratio": 0.1,
            "observation_hours": 24,
            "rollback_on_failure": True,
            "rollback_metric_drop": 0.05,
        },
    )


@pytest.fixture
def degraded_manager(snapshot_store) -> HotUpdateManager:
    """降级模式 HotUpdateManager（无 model_registry_service）."""
    return HotUpdateManager(
        model_registry_service=None,
        snapshot_store=snapshot_store,
    )


@pytest.fixture(autouse=True)
def _reset_global_manager():
    """每个测试后重置全局 HotUpdateManager 单例."""
    yield
    reset_hot_update_manager()


@pytest.fixture
def fresh_registry():
    """每个测试使用全新的 ExtensionRegistry."""
    reset_extension_registry()
    yield
    reset_extension_registry()


def _make_plugin_context(
    store: Optional[IDatasetStore] = None,
    hot_update_config: Optional[dict[str, Any]] = None,
) -> PluginContext:
    """构造最小 PluginContext 测试替身."""
    config: dict[str, Any] = {
        "feedback_collection": {
            "window_hours": 24,
            "min_samples_for_training": 50,
            "batch_size": 100,
        }
    }
    if hot_update_config is not None:
        config["hot_update"] = hot_update_config
    return PluginContext(
        plugin_id="data_flywheel",
        config=config,
        task_registry=object(),
        dataset_store=store,
        observability=object(),
        logger=logging.getLogger("test.p45"),
        data_dir=str(_PLUGIN_DIR / "_test_data_p45"),
    )


# 测试 1: 枚举与数据类


@pytest.mark.unit
@pytest.mark.contracts
class TestEnumsAndDataclasses:
    """ModelStage / DeploymentStatus / DeploymentRecord 数据类."""

    def test_model_stage_values(self):
        assert ModelStage.STAGING.value == "staging"
        assert ModelStage.CANARY.value == "canary"
        assert ModelStage.PRODUCTION.value == "production"
        assert ModelStage.ARCHIVED.value == "archived"

    def test_deployment_status_values(self):
        assert DeploymentStatus.DEPLOYING.value == "deploying"
        assert DeploymentStatus.OBSERVING.value == "observing"
        assert DeploymentStatus.PROMOTED.value == "promoted"
        assert DeploymentStatus.ROLLED_BACK.value == "rolled_back"
        assert DeploymentStatus.FAILED.value == "failed"

    def test_deployment_record_post_init_valid(self):
        """合法参数构造 DeploymentRecord 不抛错."""
        record = DeploymentRecord(
            deployment_id="dep-test",
            model_name="ltc-chatter",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            canary_ratio=0.1,
            observation_hours=24,
            started_at=datetime.utcnow(),
            rollback_on_failure=True,
            rollback_metric_drop=0.05,
            promote_on_success=True,
            eval_metric="f1",
        )
        assert record.status == DeploymentStatus.OBSERVING
        assert record.rollback_reason is None
        assert record.promoted_at is None
        assert record.ended_at is None
        assert record.observation_history == []

    def test_deployment_record_invalid_canary_ratio_high(self):
        with pytest.raises(ValueError, match="canary_ratio"):
            DeploymentRecord(
                deployment_id="dep-test",
                model_name="ltc",
                new_model_uri="model://v3",
                baseline_model_uri="model://v2",
                canary_ratio=1.5,
                observation_hours=24,
                started_at=datetime.utcnow(),
                rollback_on_failure=True,
                rollback_metric_drop=0.05,
                promote_on_success=True,
                eval_metric="f1",
            )

    def test_deployment_record_invalid_canary_ratio_negative(self):
        with pytest.raises(ValueError, match="canary_ratio"):
            DeploymentRecord(
                deployment_id="dep-test",
                model_name="ltc",
                new_model_uri="model://v3",
                baseline_model_uri="model://v2",
                canary_ratio=-0.1,
                observation_hours=24,
                started_at=datetime.utcnow(),
                rollback_on_failure=True,
                rollback_metric_drop=0.05,
                promote_on_success=True,
                eval_metric="f1",
            )

    def test_deployment_record_invalid_observation_hours(self):
        with pytest.raises(ValueError, match="observation_hours"):
            DeploymentRecord(
                deployment_id="dep-test",
                model_name="ltc",
                new_model_uri="model://v3",
                baseline_model_uri="model://v2",
                canary_ratio=0.1,
                observation_hours=0,
                started_at=datetime.utcnow(),
                rollback_on_failure=True,
                rollback_metric_drop=0.05,
                promote_on_success=True,
                eval_metric="f1",
            )

    def test_deployment_record_invalid_rollback_drop(self):
        with pytest.raises(ValueError, match="rollback_metric_drop"):
            DeploymentRecord(
                deployment_id="dep-test",
                model_name="ltc",
                new_model_uri="model://v3",
                baseline_model_uri="model://v2",
                canary_ratio=0.1,
                observation_hours=24,
                started_at=datetime.utcnow(),
                rollback_on_failure=True,
                rollback_metric_drop=1.5,
                promote_on_success=True,
                eval_metric="f1",
            )

    def test_observation_ends_at(self):
        started = datetime(2026, 7, 13, 10, 0, 0)
        record = DeploymentRecord(
            deployment_id="dep-test",
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            canary_ratio=0.1,
            observation_hours=24,
            started_at=started,
            rollback_on_failure=True,
            rollback_metric_drop=0.05,
            promote_on_success=True,
            eval_metric="f1",
        )
        assert record.observation_ends_at == started + timedelta(hours=24)

    def test_to_dict_serialization(self):
        started = datetime(2026, 7, 13, 10, 0, 0)
        record = DeploymentRecord(
            deployment_id="dep-test",
            model_name="ltc-chatter",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            canary_ratio=0.1,
            observation_hours=24,
            started_at=started,
            rollback_on_failure=True,
            rollback_metric_drop=0.05,
            promote_on_success=True,
            eval_metric="f1",
            baseline_metrics={"f1": 0.88},
            canary_metrics={"f1": 0.92},
        )
        d = record.to_dict()
        assert d["deployment_id"] == "dep-test"
        assert d["model_name"] == "ltc-chatter"
        assert d["new_model_uri"] == "model://v3"
        assert d["canary_ratio"] == 0.1
        assert d["status"] == "observing"
        assert d["started_at"] == started.isoformat()
        assert d["observation_ends_at"] == (started + timedelta(hours=24)).isoformat()
        assert d["baseline_metrics"] == {"f1": 0.88}
        assert d["canary_metrics"] == {"f1": 0.92}
        assert d["rollback_reason"] is None
        assert d["promoted_at"] is None
        assert d["ended_at"] is None
        assert d["observation_history"] == []

    def test_observation_decision_to_dict(self):
        decision = ObservationDecision(
            decision="promote",
            reason="测试晋升",
            baseline_value=0.88,
            canary_value=0.92,
            drop=-0.045,
            observation_remaining_hours=0.0,
            deployment_status=DeploymentStatus.OBSERVING,
        )
        d = decision.to_dict()
        assert d["decision"] == "promote"
        assert d["reason"] == "测试晋升"
        assert d["baseline_value"] == 0.88
        assert d["canary_value"] == 0.92
        assert d["drop"] == -0.045
        assert d["observation_remaining_hours"] == 0.0
        assert d["deployment_status"] == "observing"


# 测试 2: HotUpdateManager 构造与属性


@pytest.mark.unit
@pytest.mark.contracts
class TestHotUpdateManagerConstruction:
    """HotUpdateManager 构造与基本属性."""

    def test_default_config(self, fake_registry_service, snapshot_store):
        m = HotUpdateManager(
            model_registry_service=fake_registry_service,
            snapshot_store=snapshot_store,
        )
        assert m.config == {}
        assert m.model_registry_service is fake_registry_service
        assert m.snapshot_store is snapshot_store
        assert m.is_degraded is False

    def test_custom_config(self, fake_registry_service, snapshot_store):
        m = HotUpdateManager(
            model_registry_service=fake_registry_service,
            snapshot_store=snapshot_store,
            config={"canary_ratio": 0.2, "observation_hours": 48},
        )
        assert m.config == {"canary_ratio": 0.2, "observation_hours": 48}

    def test_config_is_readonly_view(self, manager):
        """config property 返回的是副本，外部修改不影响内部."""
        cfg = manager.config
        cfg["canary_ratio"] = 0.99
        # 内部配置不受影响
        assert manager.config["canary_ratio"] == 0.1

    def test_degraded_mode(self, snapshot_store):
        m = HotUpdateManager(model_registry_service=None, snapshot_store=snapshot_store)
        assert m.is_degraded is True
        assert m.model_registry_service is None

    def test_degraded_mode_logs_warning(self, snapshot_store, caplog):
        with caplog.at_level(logging.WARNING):
            HotUpdateManager(model_registry_service=None, snapshot_store=snapshot_store)
        assert any("降级模式" in r.message for r in caplog.records)


# 测试 3: canary_deploy


@pytest.mark.unit
@pytest.mark.contracts
class TestCanaryDeploy:
    """canary_deploy 入口."""

    @pytest.mark.asyncio
    async def test_canary_deploy_success(self, manager):
        record = await manager.canary_deploy(
            model_name="ltc-chatter",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92, "mae": 0.05},
            baseline_metrics={"f1": 0.88, "mae": 0.07},
            eval_metric="f1",
        )
        assert record.deployment_id.startswith("dep-")
        assert record.model_name == "ltc-chatter"
        assert record.new_model_uri == "model://v3"
        assert record.baseline_model_uri == "model://v2"
        assert record.canary_ratio == 0.1  # 来自 config
        assert record.observation_hours == 24  # 来自 config
        assert record.status == DeploymentStatus.OBSERVING
        assert record.baseline_metrics == {"f1": 0.88, "mae": 0.07}
        assert record.canary_metrics == {"f1": 0.92, "mae": 0.05}

    @pytest.mark.asyncio
    async def test_canary_deploy_explicit_overrides_config(self, manager):
        """显式参数覆盖 config 默认值."""
        record = await manager.canary_deploy(
            model_name="ltc-chatter",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
            canary_ratio=0.3,
            observation_hours=48,
            rollback_metric_drop=0.1,
        )
        assert record.canary_ratio == 0.3
        assert record.observation_hours == 48
        assert record.rollback_metric_drop == 0.1

    @pytest.mark.asyncio
    async def test_canary_deploy_default_values_when_config_missing(self, fake_registry_service, snapshot_store):
        """无 config 时使用默认值."""
        m = HotUpdateManager(
            model_registry_service=fake_registry_service,
            snapshot_store=snapshot_store,
        )
        record = await m.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v2",
            baseline_model_uri="model://v1",
            eval_metrics={"f1": 0.9},
        )
        assert record.canary_ratio == 0.1  # 默认
        assert record.observation_hours == 24  # 默认
        assert record.rollback_on_failure is True  # 默认
        assert record.rollback_metric_drop == 0.05  # 默认

    @pytest.mark.asyncio
    async def test_canary_deploy_empty_model_name_rejected(self, manager):
        with pytest.raises(ValueError, match="model_name"):
            await manager.canary_deploy(
                model_name="",
                new_model_uri="model://v3",
                baseline_model_uri="model://v2",
                eval_metrics={"f1": 0.9},
            )

    @pytest.mark.asyncio
    async def test_canary_deploy_empty_new_uri_rejected(self, manager):
        with pytest.raises(ValueError, match="new_model_uri"):
            await manager.canary_deploy(
                model_name="ltc",
                new_model_uri="",
                baseline_model_uri="model://v2",
                eval_metrics={"f1": 0.9},
            )

    @pytest.mark.asyncio
    async def test_canary_deploy_empty_baseline_uri_rejected(self, manager):
        with pytest.raises(ValueError, match="baseline_model_uri"):
            await manager.canary_deploy(
                model_name="ltc",
                new_model_uri="model://v3",
                baseline_model_uri="",
                eval_metrics={"f1": 0.9},
            )

    @pytest.mark.asyncio
    async def test_canary_deploy_same_uri_rejected(self, manager):
        with pytest.raises(ValueError, match="不能相同"):
            await manager.canary_deploy(
                model_name="ltc",
                new_model_uri="model://v2",
                baseline_model_uri="model://v2",
                eval_metrics={"f1": 0.9},
            )

    @pytest.mark.asyncio
    async def test_canary_deploy_empty_eval_metrics_rejected(self, manager):
        with pytest.raises(ValueError, match="eval_metrics"):
            await manager.canary_deploy(
                model_name="ltc",
                new_model_uri="model://v3",
                baseline_model_uri="model://v2",
                eval_metrics={},
            )

    @pytest.mark.asyncio
    async def test_canary_deploy_updates_model_stages(self, manager):
        """canary_deploy 后 model_stages 含 CANARY 和 PRODUCTION."""
        await manager.canary_deploy(
            model_name="ltc-chatter",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )
        stages = manager.list_model_stages("ltc-chatter")
        assert stages["canary"] == "model://v3"
        assert stages["production"] == "model://v2"
        assert stages["staging"] is None
        assert stages["archived"] is None

    @pytest.mark.asyncio
    async def test_canary_deploy_degraded_mode(self, degraded_manager):
        """降级模式下 canary_deploy 仍成功（仅不注册到 ModelRegistryService）."""
        record = await degraded_manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )
        assert record.status == DeploymentStatus.OBSERVING
        assert degraded_manager.is_degraded is True

    @pytest.mark.asyncio
    async def test_canary_deploy_calls_model_registry_service(self, fake_registry_service, snapshot_store):
        """非降级模式下 _register_canary_to_model_registry 被调用（list_models）."""
        m = HotUpdateManager(
            model_registry_service=fake_registry_service,
            snapshot_store=snapshot_store,
        )
        await m.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )
        # list_models 至少被调用一次（best-effort 检查是否已注册）
        assert fake_registry_service.list_call_count >= 1


# 测试 4: 并发灰度拒绝


@pytest.mark.unit
@pytest.mark.contracts
class TestConcurrentCanaryRejection:
    """同名模型已有 OBSERVING canary 时拒绝新部署."""

    @pytest.mark.asyncio
    async def test_reject_new_deploy_when_observing(self, manager):
        """同名模型已有 OBSERVING canary → 第二次 canary_deploy 抛 ValueError."""
        await manager.canary_deploy(
            model_name="ltc-chatter",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )
        with pytest.raises(ValueError, match="已有进行中的灰度部署"):
            await manager.canary_deploy(
                model_name="ltc-chatter",
                new_model_uri="model://v4",
                baseline_model_uri="model://v2",
                eval_metrics={"f1": 0.93},
            )

    @pytest.mark.asyncio
    async def test_allow_new_deploy_after_promote(self, manager):
        """promote 后再 canary_deploy 新版本允许."""
        record1 = await manager.canary_deploy(
            model_name="ltc-chatter",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )
        await manager.promote(record1.deployment_id)
        # 现在 v3 是 production，可以再灰度 v4
        record2 = await manager.canary_deploy(
            model_name="ltc-chatter",
            new_model_uri="model://v4",
            baseline_model_uri="model://v3",
            eval_metrics={"f1": 0.94},
        )
        assert record2.status == DeploymentStatus.OBSERVING

    @pytest.mark.asyncio
    async def test_allow_new_deploy_after_rollback(self, manager):
        """rollback 后再 canary_deploy 新版本允许."""
        record1 = await manager.canary_deploy(
            model_name="ltc-chatter",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )
        await manager.rollback(record1.deployment_id, reason="测试回滚")
        record2 = await manager.canary_deploy(
            model_name="ltc-chatter",
            new_model_uri="model://v4",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.94},
        )
        assert record2.status == DeploymentStatus.OBSERVING

    @pytest.mark.asyncio
    async def test_different_model_names_can_deploy_concurrently(self, manager):
        """不同 model_name 可以同时进行灰度."""
        r1 = await manager.canary_deploy(
            model_name="ltc-a",
            new_model_uri="model://a-v2",
            baseline_model_uri="model://a-v1",
            eval_metrics={"f1": 0.9},
        )
        r2 = await manager.canary_deploy(
            model_name="ltc-b",
            new_model_uri="model://b-v2",
            baseline_model_uri="model://b-v1",
            eval_metrics={"f1": 0.9},
        )
        assert r1.status == DeploymentStatus.OBSERVING
        assert r2.status == DeploymentStatus.OBSERVING


# 测试 5: observe_deployment 三态决策


@pytest.mark.unit
@pytest.mark.contracts
class TestObserveDecision:
    """observe_deployment 决策（continue / promote / rollback）."""

    @pytest.mark.asyncio
    async def test_decision_continue_in_observation_period(self, manager):
        """观察期内、无退化 → continue."""
        record = await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
            baseline_metrics={"f1": 0.88},
            observation_hours=24,
        )
        # 立即观察（started_at + 1 秒）
        now = record.started_at + timedelta(seconds=1)
        decision = await manager.observe_deployment(
            record.deployment_id,
            current_canary_metrics={"f1": 0.91},
            now=now,
        )
        assert decision.decision == "continue"
        assert decision.observation_remaining_hours is not None
        assert decision.observation_remaining_hours > 23.0
        assert decision.baseline_value == 0.88
        assert decision.canary_value == 0.91

    @pytest.mark.asyncio
    async def test_decision_promote_after_observation_ends(self, manager):
        """观察期结束、无退化 → promote."""
        record = await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
            baseline_metrics={"f1": 0.88},
            observation_hours=24,
            promote_on_success=True,
        )
        now = record.started_at + timedelta(hours=25)
        decision = await manager.observe_deployment(
            record.deployment_id,
            current_canary_metrics={"f1": 0.92},
            now=now,
        )
        assert decision.decision == "promote"
        assert decision.observation_remaining_hours == 0.0

    @pytest.mark.asyncio
    async def test_decision_continue_when_promote_on_success_false(self, manager):
        """观察期结束但 promote_on_success=False → continue（等外部晋升）."""
        record = await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
            baseline_metrics={"f1": 0.88},
            observation_hours=24,
            promote_on_success=False,
        )
        now = record.started_at + timedelta(hours=25)
        decision = await manager.observe_deployment(
            record.deployment_id,
            current_canary_metrics={"f1": 0.92},
            now=now,
        )
        assert decision.decision == "continue"
        assert "promote_on_success=False" in decision.reason

    @pytest.mark.asyncio
    async def test_decision_rollback_on_metric_drop(self, manager):
        """canary 指标退化超过阈值 → rollback."""
        # baseline f1=0.88, canary f1=0.80 drop=(0.88-0.80)/0.88=0.0909 > 0.05
        record = await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
            baseline_metrics={"f1": 0.88},
            rollback_metric_drop=0.05,
            rollback_on_failure=True,
        )
        now = record.started_at + timedelta(seconds=1)
        decision = await manager.observe_deployment(
            record.deployment_id,
            current_canary_metrics={"f1": 0.80},
            now=now,
        )
        assert decision.decision == "rollback"
        assert decision.drop is not None
        assert decision.drop > 0.05

    @pytest.mark.asyncio
    async def test_decision_no_rollback_when_drop_below_threshold(self, manager):
        """canary 指标退化但未超过阈值 → continue（不回滚）."""
        # baseline f1=0.88, canary f1=0.87 drop=(0.88-0.87)/0.88=0.011 < 0.05
        record = await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
            baseline_metrics={"f1": 0.88},
            rollback_metric_drop=0.05,
            rollback_on_failure=True,
        )
        now = record.started_at + timedelta(seconds=1)
        decision = await manager.observe_deployment(
            record.deployment_id,
            current_canary_metrics={"f1": 0.87},
            now=now,
        )
        assert decision.decision == "continue"

    @pytest.mark.asyncio
    async def test_decision_no_rollback_when_rollback_on_failure_false(self, manager):
        """rollback_on_failure=False 即使退化也不回滚 → continue."""
        record = await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
            baseline_metrics={"f1": 0.88},
            rollback_metric_drop=0.05,
            rollback_on_failure=False,
        )
        now = record.started_at + timedelta(seconds=1)
        decision = await manager.observe_deployment(
            record.deployment_id,
            current_canary_metrics={"f1": 0.50},  # 严重退化
            now=now,
        )
        assert decision.decision == "continue"

    @pytest.mark.asyncio
    async def test_decision_with_no_baseline_metrics(self, manager):
        """无 baseline_metrics 时 drop=None，观察期内 → continue."""
        record = await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
            baseline_metrics=None,  # 无基线
        )
        now = record.started_at + timedelta(seconds=1)
        decision = await manager.observe_deployment(
            record.deployment_id,
            current_canary_metrics={"f1": 0.50},
            now=now,
        )
        assert decision.decision == "continue"
        assert decision.drop is None
        assert decision.baseline_value is None

    @pytest.mark.asyncio
    async def test_observation_history_recorded(self, manager):
        """每次 observe_deployment 都追加到 observation_history."""
        record = await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
            baseline_metrics={"f1": 0.88},
            observation_hours=24,
        )
        now1 = record.started_at + timedelta(seconds=1)
        now2 = record.started_at + timedelta(seconds=2)
        await manager.observe_deployment(record.deployment_id, {"f1": 0.91}, now=now1)
        await manager.observe_deployment(record.deployment_id, {"f1": 0.92}, now=now2)
        updated = await manager.get_deployment(record.deployment_id)
        assert len(updated.observation_history) == 2
        assert updated.observation_history[0]["canary_metrics"] == {"f1": 0.91}
        assert updated.observation_history[1]["canary_metrics"] == {"f1": 0.92}

    @pytest.mark.asyncio
    async def test_observe_nonexistent_deployment(self, manager):
        with pytest.raises(KeyError, match="deployment 不存在"):
            await manager.observe_deployment("dep-nonexistent", {"f1": 0.9})


# 测试 6: promote / rollback


@pytest.mark.unit
@pytest.mark.contracts
class TestPromoteAndRollback:
    """promote / rollback 终态转换."""

    @pytest.mark.asyncio
    async def test_promote_success(self, manager):
        record = await manager.canary_deploy(
            model_name="ltc-chatter",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )
        promoted = await manager.promote(record.deployment_id)
        assert promoted.status == DeploymentStatus.PROMOTED
        assert promoted.promoted_at is not None
        assert promoted.ended_at is not None

        # model_stages：new_model_uri 升为 PRODUCTION，旧 production 归档
        stages = manager.list_model_stages("ltc-chatter")
        assert stages["production"] == "model://v3"
        assert stages["archived"] == "model://v2"
        assert stages["canary"] is None

    @pytest.mark.asyncio
    async def test_rollback_success(self, manager):
        record = await manager.canary_deploy(
            model_name="ltc-chatter",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )
        rolled_back = await manager.rollback(record.deployment_id, reason="指标退化")
        assert rolled_back.status == DeploymentStatus.ROLLED_BACK
        assert rolled_back.rollback_reason == "指标退化"
        assert rolled_back.ended_at is not None

        # model_stages：canary 归档，baseline 恢复为 production
        stages = manager.list_model_stages("ltc-chatter")
        assert stages["production"] == "model://v2"
        assert stages["archived"] == "model://v3"
        assert stages["canary"] is None

    @pytest.mark.asyncio
    async def test_rollback_default_reason(self, manager):
        record = await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )
        rolled_back = await manager.rollback(record.deployment_id)
        assert rolled_back.rollback_reason == "未提供回滚原因"

    @pytest.mark.asyncio
    async def test_promote_nonexistent_deployment(self, manager):
        with pytest.raises(KeyError, match="deployment 不存在"):
            await manager.promote("dep-nonexistent")

    @pytest.mark.asyncio
    async def test_rollback_nonexistent_deployment(self, manager):
        with pytest.raises(KeyError, match="deployment 不存在"):
            await manager.rollback("dep-nonexistent")

    @pytest.mark.asyncio
    async def test_promote_after_promote_rejected(self, manager):
        record = await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )
        await manager.promote(record.deployment_id)
        with pytest.raises(ValueError, match="非 OBSERVING"):
            await manager.promote(record.deployment_id)

    @pytest.mark.asyncio
    async def test_rollback_after_rollback_rejected(self, manager):
        record = await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )
        await manager.rollback(record.deployment_id, reason="测试")
        with pytest.raises(ValueError, match="终态"):
            await manager.rollback(record.deployment_id)

    @pytest.mark.asyncio
    async def test_rollback_after_promote_rejected(self, manager):
        record = await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )
        await manager.promote(record.deployment_id)
        with pytest.raises(ValueError, match="终态"):
            await manager.rollback(record.deployment_id)

    @pytest.mark.asyncio
    async def test_observe_after_promote_rejected(self, manager):
        record = await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )
        await manager.promote(record.deployment_id)
        with pytest.raises(ValueError, match="终态"):
            await manager.observe_deployment(record.deployment_id, {"f1": 0.9})

    @pytest.mark.asyncio
    async def test_observe_after_rollback_rejected(self, manager):
        record = await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )
        await manager.rollback(record.deployment_id, reason="测试")
        with pytest.raises(ValueError, match="终态"):
            await manager.observe_deployment(record.deployment_id, {"f1": 0.9})


# 测试 7: 流量分配 select_model_for_request


@pytest.mark.unit
@pytest.mark.contracts
class TestSelectModelForRequest:
    """按 canary_ratio 分配流量."""

    @pytest.mark.asyncio
    async def test_select_returns_canary_when_rng_below_ratio(self, manager):
        """rng.random() < canary_ratio → 返回 canary_uri."""
        await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
            canary_ratio=0.1,
        )
        rng = random.Random(0)
        # 强制 rng.random() 返回 0.05 < 0.1
        with patch.object(rng, "random", return_value=0.05):
            uri = manager.select_model_for_request("ltc", rng=rng)
        assert uri == "model://v3"

    @pytest.mark.asyncio
    async def test_select_returns_production_when_rng_above_ratio(self, manager):
        """rng.random() >= canary_ratio → 返回 production_uri."""
        await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
            canary_ratio=0.1,
        )
        rng = random.Random(0)
        with patch.object(rng, "random", return_value=0.5):
            uri = manager.select_model_for_request("ltc", rng=rng)
        assert uri == "model://v2"

    @pytest.mark.asyncio
    async def test_select_returns_production_when_no_canary(self, manager):
        """无 CANARY（已 promote）→ 直接返回 PRODUCTION."""
        record = await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )
        await manager.promote(record.deployment_id)
        uri = manager.select_model_for_request("ltc")
        assert uri == "model://v3"  # 已晋升为 production

    @pytest.mark.asyncio
    async def test_select_unknown_model_raises(self, manager):
        """未部署的模型 → KeyError."""
        with pytest.raises(KeyError, match="无任何已部署版本"):
            manager.select_model_for_request("nonexistent")

    @pytest.mark.asyncio
    async def test_select_canary_ratio_distribution(self, manager):
        """大量采样验证流量分配比例近似 canary_ratio."""
        await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://canary",
            baseline_model_uri="model://prod",
            eval_metrics={"f1": 0.92},
            canary_ratio=0.3,
        )
        rng = random.Random(42)
        canary_count = 0
        total = 10000
        for _ in range(total):
            uri = manager.select_model_for_request("ltc", rng=rng)
            if uri == "model://canary":
                canary_count += 1
        # 容忍 ±3% 误差
        ratio = canary_count / total
        assert 0.27 <= ratio <= 0.33, f"canary 比例 {ratio} 偏离 0.3"

    def test_select_model_for_request_is_sync(self, manager):
        """select_model_for_request 是同步方法（非 async）."""
        # 在未部署场景下也确认它是同步调用
        import inspect

        assert not inspect.iscoroutinefunction(manager.select_model_for_request)

    @pytest.mark.asyncio
    async def test_get_production_model(self, manager):
        await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )
        assert manager.get_production_model("ltc") == "model://v2"

    @pytest.mark.asyncio
    async def test_get_canary_model(self, manager):
        await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )
        assert manager.get_canary_model("ltc") == "model://v3"

    @pytest.mark.asyncio
    async def test_get_production_model_unknown(self, manager):
        assert manager.get_production_model("nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_canary_model_after_promote(self, manager):
        record = await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )
        await manager.promote(record.deployment_id)
        assert manager.get_canary_model("ltc") is None
        assert manager.get_production_model("ltc") == "model://v3"


# 测试 8: 查询 API


@pytest.mark.unit
@pytest.mark.contracts
class TestQueryAPI:
    """get_deployment / list_deployments / list_model_stages."""

    @pytest.mark.asyncio
    async def test_get_deployment(self, manager):
        record = await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )
        fetched = await manager.get_deployment(record.deployment_id)
        assert fetched.deployment_id == record.deployment_id

    @pytest.mark.asyncio
    async def test_get_deployment_not_found(self, manager):
        with pytest.raises(KeyError, match="deployment 不存在"):
            await manager.get_deployment("dep-nonexistent")

    @pytest.mark.asyncio
    async def test_list_deployments_empty(self, manager):
        result = await manager.list_deployments()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_deployments_all(self, manager):
        await manager.canary_deploy(
            model_name="ltc-a",
            new_model_uri="model://a-v3",
            baseline_model_uri="model://a-v2",
            eval_metrics={"f1": 0.92},
        )
        await manager.canary_deploy(
            model_name="ltc-b",
            new_model_uri="model://b-v3",
            baseline_model_uri="model://b-v2",
            eval_metrics={"f1": 0.92},
        )
        result = await manager.list_deployments()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_deployments_filter_by_model_name(self, manager):
        await manager.canary_deploy(
            model_name="ltc-a",
            new_model_uri="model://a-v3",
            baseline_model_uri="model://a-v2",
            eval_metrics={"f1": 0.92},
        )
        await manager.canary_deploy(
            model_name="ltc-b",
            new_model_uri="model://b-v3",
            baseline_model_uri="model://b-v2",
            eval_metrics={"f1": 0.92},
        )
        result = await manager.list_deployments(model_name="ltc-a")
        assert len(result) == 1
        assert result[0].model_name == "ltc-a"

    @pytest.mark.asyncio
    async def test_list_deployments_filter_by_status(self, manager):
        r1 = await manager.canary_deploy(
            model_name="ltc-a",
            new_model_uri="model://a-v3",
            baseline_model_uri="model://a-v2",
            eval_metrics={"f1": 0.92},
        )
        r2 = await manager.canary_deploy(
            model_name="ltc-b",
            new_model_uri="model://b-v3",
            baseline_model_uri="model://b-v2",
            eval_metrics={"f1": 0.92},
        )
        await manager.promote(r1.deployment_id)
        observing = await manager.list_deployments(status=DeploymentStatus.OBSERVING)
        promoted = await manager.list_deployments(status=DeploymentStatus.PROMOTED)
        assert len(observing) == 1
        assert observing[0].deployment_id == r2.deployment_id
        assert len(promoted) == 1
        assert promoted[0].deployment_id == r1.deployment_id

    @pytest.mark.asyncio
    async def test_list_deployments_sorted_by_started_at_desc(self, manager):
        """list_deployments 按启动时间倒序（最新在前）."""
        r1 = await manager.canary_deploy(
            model_name="ltc-a",
            new_model_uri="model://a-v3",
            baseline_model_uri="model://a-v2",
            eval_metrics={"f1": 0.92},
        )
        # 强制 r2 启动时间晚于 r1
        r1.started_at = datetime(2026, 7, 13, 10, 0, 0)
        r2 = await manager.canary_deploy(
            model_name="ltc-b",
            new_model_uri="model://b-v3",
            baseline_model_uri="model://b-v2",
            eval_metrics={"f1": 0.92},
        )
        r2.started_at = datetime(2026, 7, 13, 11, 0, 0)
        result = await manager.list_deployments()
        assert result[0].deployment_id == r2.deployment_id
        assert result[1].deployment_id == r1.deployment_id

    @pytest.mark.asyncio
    async def test_list_model_stages_unknown_model(self, manager):
        stages = manager.list_model_stages("nonexistent")
        assert stages == {
            "staging": None,
            "canary": None,
            "production": None,
            "archived": None,
        }


# 测试 9: 全局单例


@pytest.mark.unit
@pytest.mark.contracts
class TestGlobalSingleton:
    """get / configure / reset 全局单例."""

    def test_get_returns_degraded_instance_when_unconfigured(self):
        reset_hot_update_manager()
        m = get_hot_update_manager()
        assert m.is_degraded is True

    def test_configure_sets_singleton(self, fake_registry_service, snapshot_store):
        m = configure_hot_update_manager(
            model_registry_service=fake_registry_service,
            snapshot_store=snapshot_store,
            config={"canary_ratio": 0.2},
        )
        assert m is get_hot_update_manager()
        assert m.is_degraded is False
        assert m.config["canary_ratio"] == 0.2

    def test_configure_overwrites_existing(self, fake_registry_service, snapshot_store):
        configure_hot_update_manager(
            model_registry_service=fake_registry_service,
            snapshot_store=snapshot_store,
            config={"canary_ratio": 0.1},
        )
        m2 = configure_hot_update_manager(
            model_registry_service=fake_registry_service,
            snapshot_store=snapshot_store,
            config={"canary_ratio": 0.3},
        )
        assert m2.config["canary_ratio"] == 0.3

    def test_reset_clears_singleton(self, fake_registry_service, snapshot_store):
        configure_hot_update_manager(
            model_registry_service=fake_registry_service,
            snapshot_store=snapshot_store,
        )
        reset_hot_update_manager()
        # 重新 get 应返回新的降级实例
        m = get_hot_update_manager()
        assert m.is_degraded is True


# 测试 10: Plugin 集成


@pytest.mark.unit
@pytest.mark.contracts
class TestPluginIntegration:
    """Plugin.on_load / on_unload / health_check / _handle_hot_update_request."""

    @pytest.fixture(autouse=True)
    def _mock_resolve_stores(self, monkeypatch, snapshot_store, fake_registry_service):
        """mock Plugin 的 _resolve_snapshot_store / _resolve_model_registry_service."""
        from plugins.data_flywheel.main import Plugin

        monkeypatch.setattr(Plugin, "_resolve_snapshot_store", lambda self: snapshot_store)
        monkeypatch.setattr(
            Plugin,
            "_resolve_model_registry_service",
            lambda self: fake_registry_service,
        )

    @pytest.mark.asyncio
    async def test_on_load_configures_hot_update_manager(self, fresh_registry, fake_registry_service):
        from plugins.data_flywheel.main import Plugin

        plugin = Plugin()
        ctx = _make_plugin_context(
            store=InMemoryDatasetStore(),
            hot_update_config={"canary_ratio": 0.15, "observation_hours": 12},
        )
        await plugin.on_load(ctx)

        # 全局 HotUpdateManager 已配置
        manager = get_hot_update_manager()
        assert manager.is_degraded is False
        assert manager.model_registry_service is fake_registry_service
        assert manager.config["canary_ratio"] == 0.15
        assert manager.config["observation_hours"] == 12
        assert plugin._hot_update_configured is True

        await plugin.on_unload()

    @pytest.mark.asyncio
    async def test_on_load_degraded_when_model_registry_unavailable(self, fresh_registry, monkeypatch):
        """_resolve_model_registry_service 返回 None → 降级模式."""
        from plugins.data_flywheel.main import Plugin

        # 覆盖 autouse fixture 的 mock
        monkeypatch.setattr(Plugin, "_resolve_model_registry_service", lambda self: None)
        plugin = Plugin()
        ctx = _make_plugin_context(store=InMemoryDatasetStore())
        await plugin.on_load(ctx)

        manager = get_hot_update_manager()
        assert manager.is_degraded is True

        await plugin.on_unload()

    @pytest.mark.asyncio
    async def test_on_unload_resets_global_manager(self, fresh_registry):
        from plugins.data_flywheel.main import Plugin

        plugin = Plugin()
        ctx = _make_plugin_context(store=InMemoryDatasetStore())
        await plugin.on_load(ctx)
        assert plugin._hot_update_configured is True

        await plugin.on_unload()
        assert plugin._hot_update_configured is False
        # 全局单例已重置，get_hot_update_manager 返回降级实例
        assert get_hot_update_manager().is_degraded is True

    @pytest.mark.asyncio
    async def test_health_check_includes_hot_update_stats(self, fresh_registry):
        from plugins.data_flywheel.main import Plugin

        plugin = Plugin()
        ctx = _make_plugin_context(store=InMemoryDatasetStore())
        await plugin.on_load(ctx)

        # 创建一个部署
        manager = get_hot_update_manager()
        await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )

        health = plugin.health_check()
        assert "hot_update_manager_available" in health["checks"]
        assert health["checks"]["hot_update_manager_available"] is True
        assert "hot_update_stats" in health
        assert health["hot_update_stats"]["total_deployments"] == 1
        assert health["hot_update_stats"]["observing_count"] == 1
        assert health["hot_update_stats"]["promoted_count"] == 0

        await plugin.on_unload()

    @pytest.mark.asyncio
    async def test_hot_update_manager_property(self, fresh_registry):
        from plugins.data_flywheel.main import Plugin

        plugin = Plugin()
        ctx = _make_plugin_context(store=InMemoryDatasetStore())
        await plugin.on_load(ctx)

        assert plugin.hot_update_manager is not None
        assert isinstance(plugin.hot_update_manager, HotUpdateManager)

        await plugin.on_unload()
        # 卸载后 property 返回 None
        assert plugin.hot_update_manager is None

    @pytest.mark.asyncio
    async def test_handler_not_configured_raises(self, fresh_registry):
        """未 on_load 时调用 _handle_hot_update_request 抛 RuntimeError."""
        from plugins.data_flywheel.main import Plugin

        plugin = Plugin()
        with pytest.raises(RuntimeError, match="HotUpdateManager 未配置"):
            await plugin._handle_hot_update_request({"action": "list_deployments"})


# 测试 11: _handle_hot_update_request 7 action 分发


@pytest.mark.unit
@pytest.mark.contracts
class TestHotUpdateRequestDispatch:
    """_handle_hot_update_request 7 action 分发."""

    @pytest_asyncio.fixture
    async def loaded_plugin(self, fresh_registry, fake_registry_service) -> Any:
        """已 on_load 的 Plugin 实例（yield pattern）."""
        from plugins.data_flywheel.main import Plugin

        plugin = Plugin()
        ctx = _make_plugin_context(
            store=InMemoryDatasetStore(),
            hot_update_config={"canary_ratio": 0.1, "observation_hours": 24},
        )
        await plugin.on_load(ctx)
        yield plugin
        await plugin.on_unload()

    @pytest.mark.asyncio
    async def test_action_canary_deploy(self, loaded_plugin):
        result = await loaded_plugin._handle_hot_update_request(
            {
                "action": "canary_deploy",
                "model_name": "ltc",
                "new_model_uri": "model://v3",
                "baseline_model_uri": "model://v2",
                "eval_metrics": {"f1": 0.92},
                "baseline_metrics": {"f1": 0.88},
                "eval_metric": "f1",
            }
        )
        assert result["action"] == "canary_deploy"
        assert "deployment" in result
        assert result["deployment"]["model_name"] == "ltc"
        assert result["deployment"]["status"] == "observing"

    @pytest.mark.asyncio
    async def test_action_canary_deploy_missing_fields(self, loaded_plugin):
        with pytest.raises(ValueError, match="model_name"):
            await loaded_plugin._handle_hot_update_request({"action": "canary_deploy", "new_model_uri": "model://v3"})

    @pytest.mark.asyncio
    async def test_action_observe_continue(self, loaded_plugin):
        deploy_result = await loaded_plugin._handle_hot_update_request(
            {
                "action": "canary_deploy",
                "model_name": "ltc",
                "new_model_uri": "model://v3",
                "baseline_model_uri": "model://v2",
                "eval_metrics": {"f1": 0.92},
                "baseline_metrics": {"f1": 0.88},
                "observation_hours": 24,
            }
        )
        dep_id = deploy_result["deployment"]["deployment_id"]
        observe_result = await loaded_plugin._handle_hot_update_request(
            {
                "action": "observe",
                "deployment_id": dep_id,
                "current_canary_metrics": {"f1": 0.91},
            }
        )
        assert observe_result["action"] == "observe"
        assert observe_result["decision"]["decision"] == "continue"

    @pytest.mark.asyncio
    async def test_action_observe_missing_deployment_id(self, loaded_plugin):
        with pytest.raises(ValueError, match="deployment_id"):
            await loaded_plugin._handle_hot_update_request(
                {
                    "action": "observe",
                    "current_canary_metrics": {"f1": 0.91},
                }
            )

    @pytest.mark.asyncio
    async def test_action_promote(self, loaded_plugin):
        deploy_result = await loaded_plugin._handle_hot_update_request(
            {
                "action": "canary_deploy",
                "model_name": "ltc",
                "new_model_uri": "model://v3",
                "baseline_model_uri": "model://v2",
                "eval_metrics": {"f1": 0.92},
            }
        )
        dep_id = deploy_result["deployment"]["deployment_id"]
        promote_result = await loaded_plugin._handle_hot_update_request({"action": "promote", "deployment_id": dep_id})
        assert promote_result["action"] == "promote"
        assert promote_result["deployment"]["status"] == "promoted"

    @pytest.mark.asyncio
    async def test_action_rollback(self, loaded_plugin):
        deploy_result = await loaded_plugin._handle_hot_update_request(
            {
                "action": "canary_deploy",
                "model_name": "ltc",
                "new_model_uri": "model://v3",
                "baseline_model_uri": "model://v2",
                "eval_metrics": {"f1": 0.92},
            }
        )
        dep_id = deploy_result["deployment"]["deployment_id"]
        rollback_result = await loaded_plugin._handle_hot_update_request(
            {"action": "rollback", "deployment_id": dep_id, "reason": "测试回滚"}
        )
        assert rollback_result["action"] == "rollback"
        assert rollback_result["deployment"]["status"] == "rolled_back"
        assert rollback_result["deployment"]["rollback_reason"] == "测试回滚"

    @pytest.mark.asyncio
    async def test_action_list_deployments(self, loaded_plugin):
        # 创建 2 个部署
        for name in ("ltc-a", "ltc-b"):
            await loaded_plugin._handle_hot_update_request(
                {
                    "action": "canary_deploy",
                    "model_name": name,
                    "new_model_uri": f"model://{name}-v3",
                    "baseline_model_uri": f"model://{name}-v2",
                    "eval_metrics": {"f1": 0.92},
                }
            )
        result = await loaded_plugin._handle_hot_update_request({"action": "list_deployments"})
        assert result["action"] == "list_deployments"
        assert result["count"] == 2
        assert len(result["deployments"]) == 2

    @pytest.mark.asyncio
    async def test_action_list_deployments_filter_by_status(self, loaded_plugin):
        r1 = await loaded_plugin._handle_hot_update_request(
            {
                "action": "canary_deploy",
                "model_name": "ltc-a",
                "new_model_uri": "model://a-v3",
                "baseline_model_uri": "model://a-v2",
                "eval_metrics": {"f1": 0.92},
            }
        )
        await loaded_plugin._handle_hot_update_request(
            {
                "action": "canary_deploy",
                "model_name": "ltc-b",
                "new_model_uri": "model://b-v3",
                "baseline_model_uri": "model://b-v2",
                "eval_metrics": {"f1": 0.92},
            }
        )
        # 把 ltc-a promote
        await loaded_plugin._handle_hot_update_request(
            {"action": "promote", "deployment_id": r1["deployment"]["deployment_id"]}
        )
        result = await loaded_plugin._handle_hot_update_request(
            {"action": "list_deployments", "filter_status": "promoted"}
        )
        assert result["count"] == 1
        assert result["deployments"][0]["model_name"] == "ltc-a"

    @pytest.mark.asyncio
    async def test_action_list_deployments_invalid_status(self, loaded_plugin):
        with pytest.raises(ValueError, match="filter_status 不合法"):
            await loaded_plugin._handle_hot_update_request({"action": "list_deployments", "filter_status": "invalid"})

    @pytest.mark.asyncio
    async def test_action_get_deployment(self, loaded_plugin):
        deploy_result = await loaded_plugin._handle_hot_update_request(
            {
                "action": "canary_deploy",
                "model_name": "ltc",
                "new_model_uri": "model://v3",
                "baseline_model_uri": "model://v2",
                "eval_metrics": {"f1": 0.92},
            }
        )
        dep_id = deploy_result["deployment"]["deployment_id"]
        result = await loaded_plugin._handle_hot_update_request({"action": "get_deployment", "deployment_id": dep_id})
        assert result["action"] == "get_deployment"
        assert result["deployment"]["deployment_id"] == dep_id

    @pytest.mark.asyncio
    async def test_action_get_deployment_missing_id(self, loaded_plugin):
        with pytest.raises(ValueError, match="deployment_id"):
            await loaded_plugin._handle_hot_update_request({"action": "get_deployment"})

    @pytest.mark.asyncio
    async def test_action_select_model(self, loaded_plugin):
        await loaded_plugin._handle_hot_update_request(
            {
                "action": "canary_deploy",
                "model_name": "ltc",
                "new_model_uri": "model://v3",
                "baseline_model_uri": "model://v2",
                "eval_metrics": {"f1": 0.92},
                "canary_ratio": 0.0,  # 强制 100% 走 production
            }
        )
        result = await loaded_plugin._handle_hot_update_request({"action": "select_model", "model_name": "ltc"})
        assert result["action"] == "select_model"
        assert result["model_uri"] == "model://v2"

    @pytest.mark.asyncio
    async def test_action_select_model_missing_name(self, loaded_plugin):
        with pytest.raises(ValueError, match="model_name"):
            await loaded_plugin._handle_hot_update_request({"action": "select_model"})

    @pytest.mark.asyncio
    async def test_action_missing_action_field(self, loaded_plugin):
        with pytest.raises(ValueError, match="action 不能为空"):
            await loaded_plugin._handle_hot_update_request({})

    @pytest.mark.asyncio
    async def test_action_unsupported(self, loaded_plugin):
        with pytest.raises(ValueError, match="不支持的 action"):
            await loaded_plugin._handle_hot_update_request({"action": "unknown_action"})


# 测试 12: 完整生命周期端到端


@pytest.mark.unit
@pytest.mark.contracts
class TestFullLifecycle:
    """canary_deploy → observe → promote/rollback 完整闭环."""

    @pytest.mark.asyncio
    async def test_promote_lifecycle(self, manager):
        """canary_deploy → observe(continue) → observe(promote) → promote."""
        record = await manager.canary_deploy(
            model_name="ltc-chatter",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
            baseline_metrics={"f1": 0.88},
            observation_hours=24,
            promote_on_success=True,
        )

        # 1. 观察期中（continue）
        d1 = await manager.observe_deployment(
            record.deployment_id,
            {"f1": 0.91},
            now=record.started_at + timedelta(hours=1),
        )
        assert d1.decision == "continue"

        # 2. 观察期结束（promote 决策）
        d2 = await manager.observe_deployment(
            record.deployment_id,
            {"f1": 0.92},
            now=record.started_at + timedelta(hours=25),
        )
        assert d2.decision == "promote"

        # 3. 执行晋升
        promoted = await manager.promote(record.deployment_id)
        assert promoted.status == DeploymentStatus.PROMOTED

        # 4. 流量分配：production 已切换为新版本
        assert manager.get_production_model("ltc-chatter") == "model://v3"
        assert manager.get_canary_model("ltc-chatter") is None

    @pytest.mark.asyncio
    async def test_rollback_lifecycle(self, manager):
        """canary_deploy → observe(rollback 决策) → rollback."""
        record = await manager.canary_deploy(
            model_name="ltc-chatter",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
            baseline_metrics={"f1": 0.88},
            rollback_metric_drop=0.05,
            rollback_on_failure=True,
        )

        # canary 指标大幅退化
        d = await manager.observe_deployment(
            record.deployment_id,
            {"f1": 0.70},  # drop = (0.88-0.70)/0.88 = 0.205 > 0.05
            now=record.started_at + timedelta(hours=1),
        )
        assert d.decision == "rollback"

        # 执行回滚
        rolled_back = await manager.rollback(record.deployment_id, reason=d.reason)
        assert rolled_back.status == DeploymentStatus.ROLLED_BACK

        # 流量分配：production 恢复为 baseline
        assert manager.get_production_model("ltc-chatter") == "model://v2"
        assert manager.get_canary_model("ltc-chatter") is None

    @pytest.mark.asyncio
    async def test_iterative_deployments_after_promote(self, manager):
        """多次迭代：v2→v3（promote）→ v4（promote）."""
        # 第一次：v2 v3
        r1 = await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v3",
            baseline_model_uri="model://v2",
            eval_metrics={"f1": 0.92},
        )
        await manager.promote(r1.deployment_id)
        assert manager.get_production_model("ltc") == "model://v3"

        # 第二次：v3 v4
        r2 = await manager.canary_deploy(
            model_name="ltc",
            new_model_uri="model://v4",
            baseline_model_uri="model://v3",
            eval_metrics={"f1": 0.94},
        )
        await manager.promote(r2.deployment_id)
        assert manager.get_production_model("ltc") == "model://v4"

        # model_stages 中 archived 应该包含 v2 和 v3
        stages = manager.list_model_stages("ltc")
        assert stages["production"] == "model://v4"
        # archived 只记录最后一次被替换的 production（v3）
        assert stages["archived"] == "model://v3"
