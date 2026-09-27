"""基准测试数据集管理模块。

负责标准几何精度基准数据集的加载、管理与版本控制。
支持阶梯轴、法兰盘、支架三类典型机械零件的基准数据。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DimensionSpec:
    name: str
    nominal: float
    unit: str = "mm"
    tolerance_upper: float = 0.0
    tolerance_lower: float = 0.0
    tolerance_grade: str = "IT7"
    description: str = ""


@dataclass
class FeatureDef:
    name: str
    feature_type: str
    parameters: dict[str, Any] = field(default_factory=dict)
    area: float = 0.0
    volume: float = 0.0
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)
    bbox_3d: tuple[float, float, float, float, float, float] = (
        0,
        0,
        0,
        0,
        0,
        0,
    )


@dataclass
class TopologyRelation:
    feature_a: str
    feature_b: str
    relation: str


@dataclass
class PartMetadata:
    part_id: str
    part_name: str
    part_type: str
    material: str
    material_grade: str = ""
    overall_dimensions: dict[str, float] = field(default_factory=dict)
    tolerance_grade: str = "IT7"
    features: list[FeatureDef] = field(default_factory=list)
    dimensions: list[DimensionSpec] = field(default_factory=list)
    topology: list[TopologyRelation] = field(default_factory=list)
    version: str = "1.0.0"
    created: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "part_id": self.part_id,
            "part_name": self.part_name,
            "part_type": self.part_type,
            "material": self.material,
            "material_grade": self.material_grade,
            "overall_dimensions": self.overall_dimensions,
            "tolerance_grade": self.tolerance_grade,
            "features": [
                {
                    "name": f.name,
                    "feature_type": f.feature_type,
                    "parameters": f.parameters,
                    "area": f.area,
                    "volume": f.volume,
                    "bbox": list(f.bbox),
                    "bbox_3d": list(f.bbox_3d),
                }
                for f in self.features
            ],
            "dimensions": [
                {
                    "name": d.name,
                    "nominal": d.nominal,
                    "unit": d.unit,
                    "tolerance_upper": d.tolerance_upper,
                    "tolerance_lower": d.tolerance_lower,
                    "tolerance_grade": d.tolerance_grade,
                    "description": d.description,
                }
                for d in self.dimensions
            ],
            "topology": [
                {
                    "feature_a": t.feature_a,
                    "feature_b": t.feature_b,
                    "relation": t.relation,
                }
                for t in self.topology
            ],
            "version": self.version,
            "created": self.created,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PartMetadata:
        features = [
            FeatureDef(
                name=f["name"],
                feature_type=f["feature_type"],
                parameters=f.get("parameters", {}),
                area=f.get("area", 0.0),
                volume=f.get("volume", 0.0),
                bbox=tuple(f.get("bbox", (0, 0, 0, 0))),
                bbox_3d=tuple(f.get("bbox_3d", (0, 0, 0, 0, 0, 0))),
            )
            for f in data.get("features", [])
        ]
        dimensions = [
            DimensionSpec(
                name=d["name"],
                nominal=d["nominal"],
                unit=d.get("unit", "mm"),
                tolerance_upper=d.get("tolerance_upper", 0.0),
                tolerance_lower=d.get("tolerance_lower", 0.0),
                tolerance_grade=d.get("tolerance_grade", "IT7"),
                description=d.get("description", ""),
            )
            for d in data.get("dimensions", [])
        ]
        topology = [
            TopologyRelation(
                feature_a=t["feature_a"],
                feature_b=t["feature_b"],
                relation=t["relation"],
            )
            for t in data.get("topology", [])
        ]
        return cls(
            part_id=data["part_id"],
            part_name=data["part_name"],
            part_type=data["part_type"],
            material=data["material"],
            material_grade=data.get("material_grade", ""),
            overall_dimensions=data.get("overall_dimensions", {}),
            tolerance_grade=data.get("tolerance_grade", "IT7"),
            features=features,
            dimensions=dimensions,
            topology=topology,
            version=data.get("version", "1.0.0"),
            created=data.get("created", ""),
            description=data.get("description", ""),
        )


class BenchmarkDataset:
    """基准数据集管理器。

    Attributes:
        root_dir: 基准数据集根目录
        _cache: 已加载的零件元数据缓存
    """

    def __init__(self, root_dir: str | None = None) -> None:
        if root_dir is None:
            # V2.7.0 解耦后代码位于 engineering/python/，固定 dirname 层级会算错根目录；
            # 改为向上查找包含 tests/benchmark 的目录（兼容解耦前后布局）。
            project_root = Path(os.path.abspath(__file__))
            while project_root != project_root.parent and not (project_root / "tests" / "benchmark").exists():
                project_root = project_root.parent
            root_dir = os.path.join(str(project_root), "tests", "benchmark", "geometric")
        self.root_dir = Path(root_dir)
        self._cache: dict[str, PartMetadata] = {}

    def list_parts(self) -> list[str]:
        parts: list[str] = []
        if not self.root_dir.exists():
            return parts
        for entry in self.root_dir.iterdir():
            if entry.is_dir() and (entry / "metadata.json").exists():
                parts.append(entry.name)
        return sorted(parts)

    def load_metadata(self, part_id: str) -> PartMetadata:
        if part_id in self._cache:
            return self._cache[part_id]

        metadata_path = self.root_dir / part_id / "metadata.json"
        if not metadata_path.exists():
            raise FileNotFoundError(f"Metadata not found for part '{part_id}': {metadata_path}")

        with open(metadata_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        metadata = PartMetadata.from_dict(data)
        self._cache[part_id] = metadata
        return metadata

    def get_input_views_dir(self, part_id: str) -> Path:
        views_dir = self.root_dir / part_id / "input_views"
        if not views_dir.exists():
            raise FileNotFoundError(f"Input views not found: {views_dir}")
        return views_dir

    def get_ground_truth_dir(self, part_id: str) -> Path:
        gt_dir = self.root_dir / part_id / "ground_truth"
        if not gt_dir.exists():
            raise FileNotFoundError(f"Ground truth not found: {gt_dir}")
        return gt_dir

    def get_step_path(self, part_id: str) -> Path | None:
        gt_dir = self.get_ground_truth_dir(part_id)
        for f in gt_dir.glob("*.step"):
            return f
        for f in gt_dir.glob("*.stp"):
            return f
        return None

    def get_obj_path(self, part_id: str) -> Path | None:
        gt_dir = self.get_ground_truth_dir(part_id)
        for f in gt_dir.glob("*.obj"):
            return f
        return None

    def get_svg_views(self, part_id: str) -> list[Path]:
        views_dir = self.get_input_views_dir(part_id)
        return sorted(views_dir.glob("*.svg"))

    def get_png_views(self, part_id: str) -> list[Path]:
        views_dir = self.get_input_views_dir(part_id)
        return sorted(views_dir.glob("*.png"))

    def load_all(self) -> dict[str, PartMetadata]:
        result: dict[str, PartMetadata] = {}
        for part_id in self.list_parts():
            try:
                result[part_id] = self.load_metadata(part_id)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                # 修复：原代码用裸 except Exception 静默吞掉所有错误，
                # 元数据 JSON 解析失败、权限不足等真问题都被掩盖。
                logger.warning(
                    "load_all: 跳过 part_id=%s | exc=%s: %s",
                    part_id,
                    type(exc).__name__,
                    exc,
                )
                continue
        return result

    def save_metadata(self, part_id: str, metadata: PartMetadata) -> None:
        part_dir = self.root_dir / part_id
        part_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = part_dir / "metadata.json"
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata.to_dict(), f, indent=2, ensure_ascii=False)
        self._cache[part_id] = metadata

    def get_version_info(self, part_id: str) -> dict[str, Any]:
        metadata = self.load_metadata(part_id)
        return {
            "part_id": part_id,
            "version": metadata.version,
            "created": metadata.created,
            "feature_count": len(metadata.features),
            "dimension_count": len(metadata.dimensions),
        }

    def export_part(self, part_id: str, target_dir: str) -> None:
        """导出一个完整零件数据集到目标目录。"""

        src_dir = self.root_dir / part_id
        if not src_dir.exists():
            raise FileNotFoundError(f"Part directory not found: {src_dir}")
        dst_dir = Path(target_dir) / part_id
        if dst_dir.exists():
            shutil.rmtree(dst_dir)
        shutil.copytree(src_dir, dst_dir)
