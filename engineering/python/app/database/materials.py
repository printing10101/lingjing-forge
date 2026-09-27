"""材料数据库模块（符合ISO 4957/683标准）。

提供符合制造业标准的材料参数查询和检索功能。
覆盖碳钢、合金钢、不锈钢、铝合金、钛合金、铸铁等常用机械加工材料。

材料属性来源与验证：
- hardness_hb: 布氏硬度，按ISO 6506-1测定
- yield_strength_mpa: 屈服强度(Re)，按ISO 6892-1
- elongation_pct: 断后伸长率(A%)，按ISO 6892-1
- machinability_index: 可加工性指数(以AISI 1212=100为基准)
- taylor_tool_life_exponent: Taylor刀具寿命公式指数n
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MaterialCuttingRange:
    roughing: tuple[float, float]
    finishing: tuple[float, float]

    def __post_init__(self) -> None:
        if self.roughing[0] < 0 or self.roughing[1] < self.roughing[0]:
            raise ValueError(f"roughing范围无效: {self.roughing}")
        if self.finishing[0] < 0 or self.finishing[1] < self.finishing[0]:
            raise ValueError(f"finishing范围无效: {self.finishing}")


@dataclass
class MaterialEntry:
    id: str
    name: str
    category: str
    hardness_hb: float
    tensile_strength_mpa: float
    yield_strength_mpa: float = 0
    elongation_pct: float = 0
    thermal_conductivity: float = 0
    density_gcm3: float = 7.85
    specific_heat_capacity_j_kgk: float = 460
    elastic_modulus_gpa: float = 210
    poisson_ratio: float = 0.3
    specific_cutting_force_kc1_1: float = 2000
    machinability_index: float = 50
    cutting_speed_range: dict[str, list[float]] = field(default_factory=dict)
    feed_range: dict[str, list[float]] = field(default_factory=dict)
    depth_of_cut_range: dict[str, list[float]] = field(default_factory=dict)
    taylor_tool_life_exponent: float = 0.25
    taylor_constant_c: float = 250
    melting_point_celsius: float = 0
    max_service_temp_celsius: float = 0
    corrosion_resistance: str = "low"

    _PHYSICAL_CONSTRAINTS = {
        "hardness_hb": (0.0, 2000.0),
        "tensile_strength_mpa": (0.0, 5000.0),
        "yield_strength_mpa": (0.0, 4500.0),
        "elongation_pct": (0.0, 100.0),
        "thermal_conductivity": (0.0, 500.0),
        "density_gcm3": (0.5, 23.0),
        "specific_heat_capacity_j_kgk": (100.0, 5000.0),
        "elastic_modulus_gpa": (0.0, 600.0),
        "poisson_ratio": (0.0, 0.5),
        "specific_cutting_force_kc1_1": (100.0, 10000.0),
        "machinability_index": (0.0, 500.0),
        "taylor_tool_life_exponent": (0.05, 1.0),
        "taylor_constant_c": (10.0, 2000.0),
        "melting_point_celsius": (0.0, 4000.0),
    }

    _VALID_CORROSION_LEVELS = frozenset({"low", "medium", "high", "excellent"})

    def __post_init__(self) -> None:
        for field_name, (low, high) in self._PHYSICAL_CONSTRAINTS.items():
            value = getattr(self, field_name, None)
            if value is None:
                continue
            if value < low or value > high:
                raise ValueError(f"MaterialEntry.{field_name}={value}超出物理约束范围[{low}, {high}]")
        if self.corrosion_resistance not in self._VALID_CORROSION_LEVELS:
            raise ValueError(
                f"corrosion_resistance='{self.corrosion_resistance}'无效，可选: {sorted(self._VALID_CORROSION_LEVELS)}"
            )
        if self.yield_strength_mpa > self.tensile_strength_mpa and self.tensile_strength_mpa > 0:
            raise ValueError(f"屈服强度({self.yield_strength_mpa}MPa)超过抗拉强度({self.tensile_strength_mpa}MPa)")

    def get_cutting_speed(self, operation: str = "roughing") -> tuple[float, float]:
        r = self.cutting_speed_range.get(operation)
        if r and len(r) >= 2:
            return (r[0], r[1])
        return (0.0, 0.0)

    def get_feed(self, operation: str = "roughing") -> tuple[float, float]:
        r = self.feed_range.get(operation)
        if r and len(r) >= 2:
            return (r[0], r[1])
        return (0.0, 0.0)

    def get_depth_of_cut(self, operation: str = "roughing") -> tuple[float, float]:
        r = self.depth_of_cut_range.get(operation)
        if r and len(r) >= 2:
            return (r[0], r[1])
        return (0.0, 0.0)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MaterialEntry:
        return cls(
            id=data["id"],
            name=data["name"],
            category=data["category"],
            hardness_hb=data.get("hardness_hb", 0),
            tensile_strength_mpa=data.get("tensile_strength_mpa", 0),
            yield_strength_mpa=data.get("yield_strength_mpa", 0),
            elongation_pct=data.get("elongation_pct", 0),
            thermal_conductivity=data.get("thermal_conductivity", 0),
            density_gcm3=data.get("density_gcm3", 7.85),
            specific_heat_capacity_j_kgk=data.get("specific_heat_capacity_j_kgk", 460),
            elastic_modulus_gpa=data.get("elastic_modulus_gpa", 210),
            poisson_ratio=data.get("poisson_ratio", 0.3),
            specific_cutting_force_kc1_1=data.get(
                "specific_cutting_force", data.get("specific_cutting_force_kc1_1", 2000)
            ),
            machinability_index=data.get("machinability_index", 50),
            cutting_speed_range=data.get("cutting_speed_range", {}),
            feed_range=data.get("feed_range", {}),
            depth_of_cut_range=data.get("depth_of_cut_range", {}),
            taylor_tool_life_exponent=data.get("taylor_exponent_n", data.get("taylor_tool_life_exponent", 0.25)),
            taylor_constant_c=data.get("taylor_constant_c", 250),
            melting_point_celsius=data.get("melting_point_celsius", 0),
            max_service_temp_celsius=data.get("max_service_temp_celsius", 0),
            corrosion_resistance=data.get("corrosion_resistance", "low"),
        )

    @property
    def taylor_exponent_n(self) -> float:
        return self.taylor_tool_life_exponent

    @property
    def specific_cutting_force(self) -> float:
        return self.specific_cutting_force_kc1_1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "hardness_hb": self.hardness_hb,
            "tensile_strength_mpa": self.tensile_strength_mpa,
            "yield_strength_mpa": self.yield_strength_mpa,
            "elongation_pct": self.elongation_pct,
            "thermal_conductivity": self.thermal_conductivity,
            "density_gcm3": self.density_gcm3,
            "specific_heat_capacity_j_kgk": self.specific_heat_capacity_j_kgk,
            "elastic_modulus_gpa": self.elastic_modulus_gpa,
            "poisson_ratio": self.poisson_ratio,
            "specific_cutting_force_kc1_1": self.specific_cutting_force_kc1_1,
            "machinability_index": self.machinability_index,
            "cutting_speed_range": self.cutting_speed_range,
            "feed_range": self.feed_range,
            "depth_of_cut_range": self.depth_of_cut_range,
            "taylor_tool_life_exponent": self.taylor_tool_life_exponent,
            "taylor_constant_c": self.taylor_constant_c,
            "melting_point_celsius": self.melting_point_celsius,
            "max_service_temp_celsius": self.max_service_temp_celsius,
            "corrosion_resistance": self.corrosion_resistance,
        }


class MaterialDatabase:
    def __init__(self, data_path: str | None = None) -> None:
        if data_path is None:
            data_dir = Path(__file__).resolve().parent / "data"
            data_path = str(data_dir / "materials.json")
        from app.database.repository import JsonRepository

        self._repo: JsonRepository[MaterialEntry] = JsonRepository(data_path, MaterialEntry.from_dict, lambda m: m.id)

    def get(self, material_id: str) -> MaterialEntry:
        return self._repo.get(material_id)

    def list_all(self) -> list[MaterialEntry]:
        return sorted(self._repo.list_all(), key=lambda m: m.name)

    def list_ids(self) -> list[str]:
        return self._repo.list_keys()

    def filter_by_category(self, category: str) -> list[MaterialEntry]:
        return self._repo.filter(lambda m: m.category == category)

    def search(self, keyword: str) -> list[MaterialEntry]:
        kw = keyword.lower()
        return self._repo.filter(lambda m: kw in m.name.lower() or kw in m.id.lower())
