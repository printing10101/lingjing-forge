"""机床运动学配置（3 轴立式加工中心为主）。

数据来源：``app/database/data/machines.json``（既有机床库），
字段映射：
- ``travel_xyz_mm`` [X, Y, Z] 行程 → 轴限位 [0, travel]（机床坐标系，
  原点在行程角点；如机床原点在中心需显式传入 min/max）
- ``spindle_speed_rpm`` [min, max] → 主轴转速限幅
- ``feed_cutting_max_mmmin`` / ``feed_rapid_mmmin`` → 进给限幅
- ``tool_changer_capacity`` → 刀位容量

工件坐标系（G54）与机床坐标系的换算：machine = program + work_offset，
work_offset 由校验调用方传入（默认 (0,0,0)，即程序坐标 == 机床坐标）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MACHINES_JSON = Path(__file__).resolve().parents[2] / "database" / "data" / "machines.json"


@dataclass(frozen=True)
class AxisLimits:
    """单轴行程限位（机床坐标系，mm）。"""

    min_mm: float
    max_mm: float

    def contains(self, value: float) -> bool:
        return self.min_mm <= value <= self.max_mm


@dataclass(frozen=True)
class MachineProfile:
    """3 轴机床运动学画像（行程 / 转速 / 进给 / 刀位）。"""

    machine_id: str
    name: str = ""
    axes: dict[str, AxisLimits] = field(default_factory=dict)
    min_spindle_rpm: float = 0.0
    max_spindle_rpm: float = 8000.0
    max_cutting_feed: float = 5000.0
    max_rapid_feed: float = 24000.0
    tool_count: int = 24

    def axis_names(self) -> tuple[str, ...]:
        return tuple(self.axes.keys())

    def to_dict(self) -> dict[str, Any]:
        return {
            "machine_id": self.machine_id,
            "name": self.name,
            "axes": {k: [v.min_mm, v.max_mm] for k, v in self.axes.items()},
            "min_spindle_rpm": self.min_spindle_rpm,
            "max_spindle_rpm": self.max_spindle_rpm,
            "max_cutting_feed": self.max_cutting_feed,
            "max_rapid_feed": self.max_rapid_feed,
            "tool_count": self.tool_count,
        }


def profile_from_machine_dict(raw: dict[str, Any]) -> MachineProfile:
    """把 machines.json 的单条机床记录转为 MachineProfile。

    Raises:
        ValueError: 缺少 id / travel_xyz_mm 等必填字段，或数值非法。
    """
    machine_id = str(raw.get("id", "")).strip()
    if not machine_id:
        raise ValueError("[运动学校验] 机床记录缺少 id 字段。建议操作：检查 machines.json。")

    travel = raw.get("travel_xyz_mm")
    if not isinstance(travel, (list, tuple)) or len(travel) != 3:
        raise ValueError(
            f"[运动学校验] 机床 {machine_id} 缺少 travel_xyz_mm [X,Y,Z] 行程数据。"
            "建议操作：在 machines.json 中补全行程字段。"
        )
    axes: dict[str, AxisLimits] = {}
    for axis_name, travel_mm in zip(("x", "y", "z"), travel):
        t = float(travel_mm)
        if t <= 0:
            raise ValueError(
                f"[运动学校验] 机床 {machine_id} 的 {axis_name} 轴行程 {t} 非法。建议操作：travel_xyz_mm 应为正数行程。"
            )
        axes[axis_name.upper()] = AxisLimits(0.0, t)

    rpm_range = raw.get("spindle_speed_rpm", [0, 8000])
    rpm_min = float(rpm_range[0]) if isinstance(rpm_range, (list, tuple)) and len(rpm_range) == 2 else 0.0
    rpm_max = float(rpm_range[1]) if isinstance(rpm_range, (list, tuple)) and len(rpm_range) == 2 else 8000.0

    return MachineProfile(
        machine_id=machine_id,
        name=str(raw.get("name", machine_id)),
        axes=axes,
        min_spindle_rpm=rpm_min,
        max_spindle_rpm=rpm_max,
        max_cutting_feed=float(raw.get("feed_cutting_max_mmmin", 5000.0)),
        max_rapid_feed=float(raw.get("feed_rapid_mmmin", 24000.0)),
        tool_count=int(raw.get("tool_changer_capacity", 24)),
    )


def auto_work_offset(profile: MachineProfile, stock_top_z: float) -> tuple[float, float, float]:
    """按机床行程自动安放毛坯，给出默认工件坐标系偏移（machine = program + offset）。

    约定：把程序坐标 Z=stock_top_z（毛坯顶面）安放到机床 Z 行程的 40% 高度，
    X/Y 方向不留偏移（典型板类件程序坐标即为装夹中心附近）。这样毛坯下方
    的钻/铣深度与上方的安全高度都能落在行程内，避免把"程序负 Z 钻深"误报超程。

    Note:
        仅用于未提供真实装夹偏移时的保守默认；真实生产应以对刀数据为准。
    """
    z_travel = profile.axes.get("Z")
    z_capacity = (z_travel.max_mm if z_travel else 500.0) * 0.4
    return (0.0, 0.0, max(z_capacity - stock_top_z, 0.0))


def load_profile(machine_id: str = "vmc_850") -> MachineProfile:
    """从 machines.json 加载机床画像；文件缺失/未命中时回退内置 VMC-850 默认值。"""

    def _builtin() -> MachineProfile:
        return MachineProfile(
            machine_id="vmc_850_builtin",
            name="立式加工中心 VMC850（内置默认）",
            axes={
                "X": AxisLimits(0.0, 850.0),
                "Y": AxisLimits(0.0, 500.0),
                "Z": AxisLimits(0.0, 500.0),
            },
            min_spindle_rpm=50.0,
            max_spindle_rpm=8000.0,
            max_cutting_feed=5000.0,
            max_rapid_feed=24000.0,
            tool_count=24,
        )

    try:
        machines = json.loads(_MACHINES_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("machines.json 不可读（%s），使用内置默认机床画像", exc)
        return _builtin()
    for raw in machines:
        if raw.get("id") == machine_id:
            try:
                return profile_from_machine_dict(raw)
            except ValueError as exc:
                logger.warning("%s，回退内置默认", exc)
                return _builtin()
    logger.warning("machines.json 中未找到机床 %s，使用内置默认", machine_id)
    return _builtin()
