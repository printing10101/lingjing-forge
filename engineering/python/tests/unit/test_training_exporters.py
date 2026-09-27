"""训练集导出适配器（exporters.py）单测.

锁定 WP2 数据契约：特征顺序 = 注册表 input_features、材料编码稳定、
manifest 血缘字段完整、诚实边界（data_insufficient 拒绝导出）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from app.training.exporters import (
    DATA_INSUFFICIENT_MODELS,
    DataInsufficientError,
    MATERIAL_ENCODING,
    export_registry_training_set,
)

pytestmark = pytest.mark.unit


def _record(material: str = "45steel", rpm: float = 4000.0, feed: float = 400.0, depth: float = 1.0,
            fx: float = 3.0, fy: float = 4.0, fz: float = 0.0) -> dict:
    return {
        "sample_id": "s1",
        "material": material,
        "tool": "endmill_d10",
        "spindle_rpm": rpm,
        "feed_rate": feed,
        "depth_of_cut": depth,
        "gcode": "O1",
        "voxel_passed": True,
        "voxel_severity": "none",
        "collision_count": 0,
        "force_Fx": fx,
        "force_Fy": fy,
        "force_Fz": fz,
        "force_method": "kienzle",
        "generated_at": "2026-09-08T00:00:00+00:00",
    }


class _FakeStore:
    """DatasetStore 最小异步实现（list/get_version/read）。"""

    def __init__(self, records: list[dict], version: str = "1.0.2"):
        self.records = records
        self.version = version

    async def list_datasets(self, limit: int = 1000):
        return [{"id": "ds-1", "name": "synthetic_machining_params_v1"}]

    async def get_version(self, dataset_id: str, version: str | None = None):
        assert dataset_id == "ds-1"
        return SimpleNamespace(
            version=version or self.version,
            content_hash="sha256:abc",
            row_count=len(self.records),
            lineage="lineage_1",
        )

    async def read(self, dataset_id: str, version: str | None = None, *, batch_size: int = 1000):
        yield list(self.records)


@pytest.mark.asyncio
async def test_happy_path_contract(tmp_path):
    records = [
        _record(material="45steel", rpm=2000.0, feed=200.0, depth=0.5, fx=3.0, fy=4.0, fz=0.0),
        _record(material="6061-T6", rpm=6000.0, feed=800.0, depth=2.0, fx=6.0, fy=8.0, fz=0.0),
    ]
    export = await export_registry_training_set("cutting_force", tmp_path, store=_FakeStore(records))
    export.write()

    x = np.load(tmp_path / "X.npy")
    y = np.load(tmp_path / "y.npy")

    assert x.dtype == np.float64 and y.dtype == np.float64
    assert x.shape == (2, 4)
    # 特征顺序 = 注册表 input_features
    from app.ai.lnn.inference._lnn_registry import LNNModelRegistry

    assert list(json.loads((tmp_path / "feature_manifest.json").read_text(encoding="utf-8"))["feature_order"]) == (
        list(LNNModelRegistry.PREDEFINED_MODELS["cutting_force"].input_features)
    )
    # 列序：rpm / feed / depth / material_encoded
    assert x[0].tolist() == [2000.0, 200.0, 0.5, float(MATERIAL_ENCODING["45steel"])]
    # 合力 = ‖(3,4,0)‖ = 5
    np.testing.assert_allclose(y, [5.0, 10.0])

    manifest = export.feature_manifest
    assert manifest["source"]["content_hash"] == "sha256:abc"
    assert manifest["source"]["version"] == "1.0.2"
    assert manifest["source"]["lineage"] == "lineage_1"
    assert manifest["row_count"] == 2
    assert manifest["target_formula"].startswith("sqrt(")


@pytest.mark.asyncio
async def test_unknown_material_skipped_and_counted(tmp_path):
    records = [
        _record(material="45steel", fx=3.0, fy=4.0),
        _record(material="unobtanium", fx=1.0, fy=1.0),
    ]
    export = await export_registry_training_set("cutting_force", tmp_path, store=_FakeStore(records))
    assert export.feature_manifest["row_count"] == 1
    assert export.feature_manifest["skipped_unknown_material"] == 1


@pytest.mark.asyncio
async def test_all_unknown_material_raises(tmp_path):
    with pytest.raises(ValueError, match="MATERIAL_ENCODING"):
        await export_registry_training_set(
            "cutting_force", tmp_path, store=_FakeStore([_record(material="unobtanium")])
        )


@pytest.mark.parametrize("model_name", sorted(DATA_INSUFFICIENT_MODELS))
@pytest.mark.asyncio
async def test_data_insufficient_models_rejected(tmp_path, model_name):
    with pytest.raises(DataInsufficientError):
        await export_registry_training_set(model_name, tmp_path, store=_FakeStore([_record()]))


@pytest.mark.asyncio
async def test_unknown_model_rejected(tmp_path):
    with pytest.raises(ValueError, match="无本导出器映射"):
        await export_registry_training_set("nonexistent_model", tmp_path, store=_FakeStore([_record()]))


@pytest.mark.asyncio
async def test_schema_drift_detected(tmp_path):
    bad = _record()
    del bad["force_Fx"]
    with pytest.raises(ValueError, match="缺少导出所需字段"):
        await export_registry_training_set("cutting_force", tmp_path, store=_FakeStore([bad]))


@pytest.mark.asyncio
async def test_empty_dataset_rejected(tmp_path):
    with pytest.raises(ValueError, match="为空"):
        await export_registry_training_set("cutting_force", tmp_path, store=_FakeStore([]))


@pytest.mark.asyncio
async def test_missing_dataset_hint_mentions_generator(tmp_path):
    store = _FakeStore([_record()])

    async def _no_datasets(limit=1000):
        return []

    store.list_datasets = _no_datasets  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="generate_synthetic_dataset"):
        await export_registry_training_set("cutting_force", tmp_path, store=store)
