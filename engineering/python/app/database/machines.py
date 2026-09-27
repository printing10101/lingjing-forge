"""机床数据库模块。

提供符合ISO 841/230标准的机床参数查询和管理功能。
覆盖数控铣床、车床、加工中心等常见设备类型。

物理约束属性来源:
- spindle_torque_nm: 根据电机功率和额定转速计算
- max_workpiece_weight_kg: 工作台最大承载重量
- rapid_traverse_xy/z: G00快速横移速度，独立于切削进给
- repeatability_mm: 定位重复精度(ISO 230-2)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MachineEntry:
    id: str
    name: str
    type: str
    spindle_power_kw: float = 0
    spindle_torque_nm: float = 0
    spindle_speed_rpm_range: list[float] = field(default_factory=lambda: [0, 0])
    rapid_traverse_xy_mm_min: float = 0
    rapid_traverse_z_mm_min: float = 0
    feed_cutting_max_mmmin: float = 0
    max_cutting_force_n: float = 0
    max_turning_diameter_mm: float = 0
    max_turning_length_mm: float = 0
    table_size_mm: list[float] = field(default_factory=lambda: [0, 0])
    travel_xyz_mm: list[float] = field(default_factory=lambda: [0, 0, 0])
    max_workpiece_weight_kg: float = 0
    tool_changer_capacity: int = 0
    coolant_pressure_mpa: float = 0
    positioning_accuracy_mm: float = 0
    repeatability_mm: float = 0
    axis_count: int = 3
    max_spindle_speed_rpm: float = 0
    control_system: str = ""

    _PHYSICAL_CONSTRAINTS = {
        "spindle_power_kw": (0.1, 200.0),
        "spindle_torque_nm": (0.0, 2000.0),
        "max_spindle_speed_rpm": (0.0, 200000.0),
        "rapid_traverse_xy_mm_min": (0.0, 120000.0),
        "rapid_traverse_z_mm_min": (0.0, 80000.0),
        "feed_cutting_max_mmmin": (0.0, 50000.0),
        "max_workpiece_weight_kg": (0.0, 50000.0),
        "positioning_accuracy_mm": (0.0, 10.0),
        "repeatability_mm": (0.0, 1.0),
    }

    def __post_init__(self) -> None:
        for field_name, (low, high) in self._PHYSICAL_CONSTRAINTS.items():
            value = getattr(self, field_name)
            if value < low or value > high:
                raise ValueError(f"MachineEntry.{field_name}={value}超出物理约束范围[{low}, {high}]")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MachineEntry:
        spindle_range = data.get("spindle_speed_rpm", data.get("spindle_speed_rpm_range", [0, 0]))
        return cls(
            id=data["id"],
            name=data["name"],
            type=data["type"],
            spindle_power_kw=data.get("spindle_power_kw", 0),
            spindle_torque_nm=data.get("spindle_torque_nm", 0),
            spindle_speed_rpm_range=spindle_range,
            rapid_traverse_xy_mm_min=data.get("rapid_traverse_xy_mm_min", data.get("feed_rapid_mmmin", 0)),
            rapid_traverse_z_mm_min=data.get("rapid_traverse_z_mm_min", data.get("feed_rapid_mmmin", 0)),
            feed_cutting_max_mmmin=data.get("feed_cutting_max_mmmin", 0),
            max_cutting_force_n=data.get("max_cutting_force_n", 0),
            max_turning_diameter_mm=data.get("max_turning_diameter_mm", 0),
            max_turning_length_mm=data.get("max_turning_length_mm", 0),
            table_size_mm=data.get("table_size_mm", [0, 0]),
            travel_xyz_mm=data.get("travel_xyz_mm", [0, 0, 0]),
            max_workpiece_weight_kg=data.get("max_workpiece_weight_kg", 0),
            tool_changer_capacity=data.get("tool_changer_capacity", 0),
            coolant_pressure_mpa=data.get("coolant_pressure_mpa", 0),
            positioning_accuracy_mm=data.get("positioning_accuracy_mm", 0),
            repeatability_mm=data.get("repeatability_mm", 0),
            axis_count=data.get("axis_count", 3),
            max_spindle_speed_rpm=data.get("max_spindle_speed_rpm", 0),
            control_system=data.get("control_system", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "spindle_power_kw": self.spindle_power_kw,
            "spindle_torque_nm": self.spindle_torque_nm,
            "spindle_speed_rpm_range": self.spindle_speed_rpm_range,
            "rapid_traverse_xy_mm_min": self.rapid_traverse_xy_mm_min,
            "rapid_traverse_z_mm_min": self.rapid_traverse_z_mm_min,
            "feed_cutting_max_mmmin": self.feed_cutting_max_mmmin,
            "max_cutting_force_n": self.max_cutting_force_n,
            "max_turning_diameter_mm": self.max_turning_diameter_mm,
            "max_turning_length_mm": self.max_turning_length_mm,
            "table_size_mm": self.table_size_mm,
            "travel_xyz_mm": self.travel_xyz_mm,
            "max_workpiece_weight_kg": self.max_workpiece_weight_kg,
            "tool_changer_capacity": self.tool_changer_capacity,
            "coolant_pressure_mpa": self.coolant_pressure_mpa,
            "positioning_accuracy_mm": self.positioning_accuracy_mm,
            "repeatability_mm": self.repeatability_mm,
            "axis_count": self.axis_count,
            "max_spindle_speed_rpm": self.max_spindle_speed_rpm,
            "control_system": self.control_system,
        }

    @property
    def spindle_speed_rpm(self) -> list[float]:
        return self.spindle_speed_rpm_range

    def validate_cutting_parameters(
        self, spindle_speed: float, feed_rate: float, depth_of_cut: float
    ) -> tuple[bool, str]:
        if spindle_speed < self.spindle_speed_rpm_range[0] or spindle_speed > self.spindle_speed_rpm_range[1]:
            return (
                False,
                f"主轴转速{spindle_speed}RPM超出[{self.spindle_speed_rpm_range[0]}, {self.spindle_speed_rpm_range[1]}]",
            )
        if feed_rate < 0 or feed_rate > self.feed_cutting_max_mmmin:
            return False, f"进给率{feed_rate}mm/min超出最大值{self.feed_cutting_max_mmmin}"
        return True, "OK"


class MachineDatabase:
    def __init__(self, data_path: str | None = None) -> None:
        if data_path is None:
            data_dir = Path(__file__).resolve().parent / "data"
            data_path = str(data_dir / "machines.json")
        from app.database.repository import JsonRepository

        self._repo: JsonRepository[MachineEntry] = JsonRepository(data_path, MachineEntry.from_dict, lambda m: m.id)

    def get(self, machine_id: str) -> MachineEntry:
        return self._repo.get(machine_id)

    def list_ids(self) -> list[str]:
        return self._repo.list_keys()

    def list_all(self) -> list[MachineEntry]:
        return sorted(self._repo.list_all(), key=lambda m: m.name)

    def filter_by_type(self, machine_type: str) -> list[MachineEntry]:
        return self._repo.filter(lambda m: m.type == machine_type)
