"""注册表模型训练集导出适配器（接线方案 WP2）。

把工程侧数据源（DatasetStore 不可变数据集版本）转换为训练脚本可消费的
``(X, y)`` 矩阵 + 特征清单（feature_manifest.json）。

特征清单是训练侧与推理侧的**唯一数据契约**：
- 特征顺序必须与 ``LNNModelRegistry.PREDEFINED_MODELS[name].input_features``
  逐项一致（错位即训练出与服务不同函数的模型）；
- 携带源数据集 id/版本/content_hash 血缘，训练产物 manifest 必须回填
  这些字段（「越用越懂你」叙事的证据链起点）；
- 材料编码表显式固化，保证跨导出、跨机器编码稳定。

诚实边界（方案 D4）：无数据源的模型（surface_roughness / temperature）声明
在 :data:`DATA_INSUFFICIENT_MODELS`，导出直接失败并给出原因，不硬造数据。
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

#: 特征清单契约版本（字段结构变更时递增）
MANIFEST_SCHEMA_VERSION = 1

#: 材料编码表（固化映射，禁止运行期插入——新材料须在此登记后重训）
MATERIAL_ENCODING: dict[str, int] = {
    "45steel": 0,
    "6061-T6": 1,
    "40Cr": 2,
    "HT200": 3,
    "HRC52": 4,
    "304ss": 5,
    "ti6al4v": 6,
}

#: 声明无可用训练数据的注册表模型（D4：拒绝导出并给出原因）
DATA_INSUFFICIENT_MODELS: dict[str, str] = {
    "surface_roughness": "合成数据集与训练数据湖均无粗糙度标签，现有湖样本为 E2E 测试数据",
    "temperature": "无温度通道数据源（需机床温度采集接入后回灌）",
}

#: 模型 → 目标列定义（target_formula 写入清单，供复核）
_MODEL_TARGETS: dict[str, dict[str, str]] = {
    "cutting_force": {
        "target": "resultant_cutting_force",
        "target_formula": "sqrt(force_Fx^2 + force_Fy^2 + force_Fz^2)",
    },
}

#: cutting_force 特征提取所需的源记录字段
_REQUIRED_SOURCE_FIELDS = ("spindle_rpm", "feed_rate", "depth_of_cut", "material", "force_Fx", "force_Fy", "force_Fz")


class DataInsufficientError(ValueError):
    """模型声明数据不足，拒绝导出（叙事红线：不硬造数据）。"""


@dataclass
class TrainingSetExport:
    """导出产物句柄。"""

    out_dir: Path
    feature_manifest: dict[str, Any] = field(default_factory=dict)
    x_path: Path | None = None
    y_path: Path | None = None

    @property
    def manifest_path(self) -> Path:
        return self.out_dir / "feature_manifest.json"

    def write(self) -> None:
        """落盘特征清单（X/y 由导出函数先落盘，清单最后写 = 完成标记）。"""
        self.manifest_path.write_text(
            json.dumps(self.feature_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _resolve_dataset_id(store: Any, dataset_name: str) -> str:
    """按数据集名解析 id（重名复用，与 synthetic_data_gen 的约定一致）。"""

    async def _resolve() -> str:
        datasets = await store.list_datasets(limit=1000)
        for ds in datasets:
            if ds.get("name") == dataset_name:
                return str(ds.get("id"))
        raise ValueError(
            f"数据集不存在: {dataset_name}。请先运行合成数据生成器"
            "（app.pipelines.synthetic_data_gen.generate_synthetic_dataset）充能。"
        )

    return _resolve()


async def export_registry_training_set(
    model_name: str,
    out_dir: str | Path,
    *,
    dataset_name: str = "synthetic_machining_params_v1",
    dataset_id: str | None = None,
    version: str | None = None,
    store: Any = None,
) -> TrainingSetExport:
    """从 DatasetStore 导出注册表模型的训练集。

    Args:
        model_name: 注册表模型名（当前支持 cutting_force）。
        out_dir: 输出目录（X.npy / y.npy / feature_manifest.json）。
        dataset_name: 源数据集名（dataset_id 未给时按名解析）。
        dataset_id: 源数据集 id（显式给定时优先，跳过按名解析）。
        version: 源数据集版本（None = 最新版本）。
        store: DatasetStore 实例（缺省用 get_dataset_store()，测试可注入内存实现）。

    Returns:
        :class:`TrainingSetExport`（X/y 已落盘，manifest 待 :meth:`~TrainingSetExport.write`）。

    Raises:
        DataInsufficientError: 模型在 :data:`DATA_INSUFFICIENT_MODELS` 中。
        ValueError: 模型无导出映射 / 数据集缺失 / 源记录字段缺失 / 材料未登记编码。
    """
    if model_name in DATA_INSUFFICIENT_MODELS:
        raise DataInsufficientError(f"模型 '{model_name}' 数据不足，拒绝导出：{DATA_INSUFFICIENT_MODELS[model_name]}")
    spec = _MODEL_TARGETS.get(model_name)
    if spec is None:
        raise ValueError(
            f"模型 '{model_name}' 无本导出器映射。可导出：{sorted(_MODEL_TARGETS)}；"
            "wear_prediction 等需 research 侧数据集"
            "（见 docs/development/lnn-权重训练与分发接线方案.md WP2.2）"
        )

    if store is None:
        from app.data.dataset_store import get_dataset_store

        store = get_dataset_store()
    if dataset_id is None:
        dataset_id = await _resolve_dataset_id(store, dataset_name)
    ver = await store.get_version(dataset_id, version)

    # 注册表特征顺序 = 唯一特征契约
    from app.ai.lnn.inference._lnn_registry import LNNModelRegistry

    info = LNNModelRegistry.PREDEFINED_MODELS.get(model_name)
    if info is None:
        raise ValueError(f"模型 '{model_name}' 不在注册表预定义清单中")
    feature_order = list(info.input_features)

    rows: list[dict[str, Any]] = []
    async for batch in store.read(dataset_id, version):
        rows.extend(batch)
    if not rows:
        raise ValueError(f"数据集版本为空: dataset_id={dataset_id} version={ver.version}")

    missing_fields = [f for f in _REQUIRED_SOURCE_FIELDS if f not in rows[0]]
    if missing_fields:
        raise ValueError(f"源数据集缺少导出所需字段: {missing_fields}（schema 漂移？）")

    xs: list[list[float]] = []
    ys: list[float] = []
    skipped_unknown_material = 0
    for row in rows:
        material = str(row["material"])
        encoded = MATERIAL_ENCODING.get(material)
        if encoded is None:
            # 未知材料显式跳过并计数——静默编码为 -1 会污染模型对已知材料的边界
            skipped_unknown_material += 1
            continue
        fx, fy, fz = float(row["force_Fx"]), float(row["force_Fy"]), float(row["force_Fz"])
        ys.append(math.sqrt(fx * fx + fy * fy + fz * fz))
        values = {
            "spindle_rpm": float(row["spindle_rpm"]),
            "feed_rate": float(row["feed_rate"]),
            "depth_of_cut": float(row["depth_of_cut"]),
            "material_encoded": float(encoded),
        }
        xs.append([values[f] for f in feature_order])
    if skipped_unknown_material:
        logger.warning(
            "跳过 %d 条未知材料记录（未登记 MATERIAL_ENCODING），请在编码表登记后重导", skipped_unknown_material
        )
    if not xs:
        raise ValueError("全部记录的材质均未登记 MATERIAL_ENCODING，无可导出样本")

    x_arr = np.asarray(xs, dtype=np.float64)
    y_arr = np.asarray(ys, dtype=np.float64)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    x_path = out / "X.npy"
    y_path = out / "y.npy"
    np.save(x_path, x_arr)
    np.save(y_path, y_arr)

    export = TrainingSetExport(out_dir=out, x_path=x_path, y_path=y_path)
    export.feature_manifest = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "model_name": model_name,
        "registry_version": info.version,
        "feature_order": feature_order,
        "target": spec["target"],
        "target_formula": spec["target_formula"],
        "dtype": "float64",
        "row_count": int(x_arr.shape[0]),
        "input_dim": int(x_arr.shape[1]),
        "skipped_unknown_material": skipped_unknown_material,
        "material_encoding": dict(MATERIAL_ENCODING),
        "source": {
            "kind": "dataset_store",
            "dataset_id": dataset_id,
            "dataset_name": dataset_name,
            "version": ver.version,
            "content_hash": ver.content_hash,
            "source_row_count": int(ver.row_count),
            "lineage": ver.lineage,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return export
