"""飞轮合成数据生成器测试（W4.1）。

- G 代码合成器：结构合法（程序号/M30）、参数渗透（S/F 值）
- generate_synthetic_dataset：小网格端到端（真实体素校验 + Kienzle 切削力，
  DatasetStore 打桩），通过/失败样本均落库、血缘记录、上限截断
"""

from __future__ import annotations

from typing import Any

import pytest

from app.pipelines.synthetic_data_gen import (
    SyntheticGenSummary,
    build_synth_gcode,
    generate_synthetic_dataset,
)

pytestmark = pytest.mark.asyncio


class _FakeVersionContract:
    def __init__(self, version: str):
        self.version = version


class _FakeDatasetStore:
    def __init__(self):
        self.created: list[tuple[str, Any]] = []
        self.committed: list[tuple[str, list[dict], dict | None]] = []
        self._names: dict[str, str] = {}  # name -> dataset_id（模拟重名约束）

    async def list_datasets(self, *, owner_id=None, status=None, limit=100, offset=0):
        return [{"id": ds_id, "name": name} for name, ds_id in self._names.items()]

    async def create(self, name, schema, *, owner_id, description=""):
        if name in self._names:
            raise ValueError(f"数据集 name 已存在: {name}")
        self.created.append((name, schema))
        ds_id = f"ds_{name}"
        self._names[name] = ds_id
        return ds_id

    async def commit_version(self, dataset_id, records, *, version=None, lineage=None):
        self.committed.append((dataset_id, records, lineage))
        return _FakeVersionContract(version or "v1")


@pytest.fixture()
def fake_store(monkeypatch):
    store = _FakeDatasetStore()
    import app.data.dataset_store as mod

    monkeypatch.setattr(mod, "get_dataset_store", lambda: store)
    return store


def test_build_synth_gcode_structure():
    gcode = build_synth_gcode(
        rpm=3000, feed=400, depth=1.0,
        stock_length=100, stock_width=80, stock_height=30,
    )
    assert gcode.startswith("O1001")
    assert "S3000 M03" in gcode
    assert "F400" in gcode
    assert gcode.rstrip().endswith("M30")
    # 3 层切削，每层有下切与平面进给
    assert gcode.count("G01 Z-") >= 3


async def test_generate_dataset_end_to_end(fake_store, monkeypatch):
    monkeypatch.setenv("LNN_FLYWHEEL_SYNTHETIC_ENABLED", "1")
    summary = await generate_synthetic_dataset(
        material="45steel",
        rpm_values=[2000.0, 4000.0],
        feed_values=[300.0],
        depth_values=[0.5, 8.0],  # 8mm×3层=24mm 接近 30mm 毛坯底面 → 预期产生失败样本
        max_combinations=10,
        dataset_name="synth_test",
    )
    assert isinstance(summary, SyntheticGenSummary)
    assert summary.total == 4
    assert summary.voxel_passed + summary.voxel_failed == summary.total
    # 落库契约
    assert len(fake_store.created) == 1
    name, schema = fake_store.created[0]
    assert name == "synth_test"
    assert "voxel_passed" in schema.fields
    dataset_id, records, lineage = fake_store.committed[0]
    assert dataset_id == "ds_synth_test"
    assert len(records) == 4
    assert summary.version == "v1"
    assert lineage.operation == "augment"
    assert lineage.metadata["material"] == "45steel"
    # 样本字段完整
    sample = records[0]
    for key in ("sample_id", "gcode", "voxel_passed", "force_method", "force_Fx"):
        assert key in sample


async def test_grid_cap_truncates(fake_store):
    summary = await generate_synthetic_dataset(
        rpm_values=[1000.0] * 5,
        feed_values=[100.0] * 5,
        depth_values=[1.0] * 10,
        max_combinations=5,
        dataset_name="synth_cap",
    )
    assert summary.combos_skipped == 250 - 5
    assert summary.total <= 5


async def test_duplicate_dataset_name_reuses_id(fake_store):
    """P1-2 回归：第二次生成不得因重名数据集崩溃，而是追加版本。"""
    s1 = await generate_synthetic_dataset(
        rpm_values=[2000.0], feed_values=[300.0], depth_values=[0.5],
        dataset_name="synth_reuse",
    )
    s2 = await generate_synthetic_dataset(
        rpm_values=[4000.0], feed_values=[300.0], depth_values=[0.5],
        dataset_name="synth_reuse",
    )
    assert s1.dataset_id == s2.dataset_id == "ds_synth_reuse"
    # 只 create 一次，commit 两次（两个版本）
    assert len(fake_store.created) == 1
    assert len(fake_store.committed) == 2


async def test_disabled_env_short_circuits_at_api_layer(monkeypatch):
    from app.pipelines.synthetic_data_gen import synthetic_enabled

    monkeypatch.setenv("LNN_FLYWHEEL_SYNTHETIC_ENABLED", "0")
    assert synthetic_enabled() is False
    monkeypatch.setenv("LNN_FLYWHEEL_SYNTHETIC_ENABLED", "1")
    assert synthetic_enabled() is True
