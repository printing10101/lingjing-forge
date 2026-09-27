"""物理约束校验器。

对LNN模型输出的切削参数进行物理可行性校验，
确保推荐的切削速度、进给量、切深等在刀具/机床/材料的物理范围内。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.database.materials import MaterialDatabase, MaterialEntry
from app.database.tools import ToolDatabase, ToolEntry
from app.database.machines import MachineDatabase, MachineEntry

logger = logging.getLogger(__name__)

_UNBOUNDED_UPPER = 99999


@dataclass
class ConstraintViolation:
    param: str
    value: float
    min_allowed: float
    max_allowed: float
    message: str


@dataclass
class ConstraintResult:
    is_valid: bool
    violations: list[ConstraintViolation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    adjusted_params: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "violations": [
                {
                    "param": v.param,
                    "value": v.value,
                    "min_allowed": v.min_allowed,
                    "max_allowed": v.max_allowed,
                    "message": v.message,
                }
                for v in self.violations
            ],
            "warnings": self.warnings,
            "adjusted_params": self.adjusted_params,
        }


class CuttingConstraintValidator:
    def __init__(
        self,
        materials: MaterialDatabase | None = None,
        tools: ToolDatabase | None = None,
        machines: MachineDatabase | None = None,
    ) -> None:
        self.materials = materials or MaterialDatabase()
        self.tools = tools or ToolDatabase()
        self.machines = machines or MachineDatabase()

    def validate(
        self,
        material_id: str,
        tool_id: str,
        params: dict[str, Any],
        machine_id: str | None = None,
    ) -> ConstraintResult:
        violations: list[ConstraintViolation] = []
        warnings: list[str] = []
        adjusted: dict[str, float] = dict(params)

        try:
            material = self.materials.get(material_id)
        except KeyError:
            return ConstraintResult(
                is_valid=False,
                violations=[
                    ConstraintViolation(
                        param="material_id",
                        value=0,
                        min_allowed=0,
                        max_allowed=0,
                        message=f"未知材料: {material_id}",
                    )
                ],
            )

        try:
            tool = self.tools.get(tool_id)
        except KeyError:
            return ConstraintResult(
                is_valid=False,
                violations=[
                    ConstraintViolation(
                        param="tool_id",
                        value=0,
                        min_allowed=0,
                        max_allowed=0,
                        message=f"未知刀具: {tool_id}",
                    )
                ],
            )

        machine = None
        if machine_id:
            try:
                machine = self.machines.get(machine_id)
            except KeyError:
                warnings.append(f"未知机床ID '{machine_id}'，跳过机床校验")

        adjusted, vc_violations = self._check_cutting_speed(
            params,
            adjusted,
            material,
            tool,
        )
        violations.extend(vc_violations)

        adjusted, f_violations = self._check_feed(params, adjusted, material, tool)
        violations.extend(f_violations)

        adjusted, doc_violations = self._check_doc(params, adjusted, material, tool)
        violations.extend(doc_violations)

        adjusted, spindle_violations = self._check_spindle_speed(
            params,
            adjusted,
            tool,
            machine,
        )
        violations.extend(spindle_violations)

        force_warnings = self.check_cutting_force(adjusted, material, tool)
        warnings.extend(force_warnings)

        if machine:
            power_warnings = self.check_cutting_power(adjusted, material, machine)
            warnings.extend(power_warnings)

            machine_force_warnings = self._check_machine_force(
                adjusted,
                material,
                tool,
                machine,
            )
            warnings.extend(machine_force_warnings)

        roughness_warnings = self.check_surface_roughness(adjusted, tool)
        warnings.extend(roughness_warnings)

        tool_life_warnings = self.check_tool_life(adjusted, material, tool)
        warnings.extend(tool_life_warnings)

        return ConstraintResult(
            is_valid=len(violations) == 0,
            violations=violations,
            warnings=warnings,
            adjusted_params=adjusted,
        )

    def _material_category_for_tool(self, material: MaterialEntry) -> str:
        cat = material.category
        if cat in ("carbon_steel", "alloy_steel"):
            return "steel"
        if cat == "stainless_steel":
            return "stainless"
        if cat == "aluminum":
            return "aluminum"
        if cat == "titanium":
            return "titanium"
        if cat == "cast_iron":
            return "cast_iron"
        return "steel"

    def _check_cutting_speed(
        self,
        params: dict[str, Any],
        adjusted: dict[str, float],
        material: MaterialEntry,
        tool: ToolEntry,
    ) -> tuple[dict[str, float], list[ConstraintViolation]]:
        violations: list[ConstraintViolation] = []
        vc = params.get("cutting_speed", params.get("vc", 0.0))
        if vc == 0.0:
            return adjusted, violations

        key = "cutting_speed" if "cutting_speed" in params else "vc"
        mat_cat = self._material_category_for_tool(material)
        tool_range = tool.get_cutting_speed_for_material(mat_cat)
        mat_range = material.get_cutting_speed("roughing")

        mat_min = mat_range[0] if mat_range[0] > 0 else 0
        mat_max = mat_range[1] if mat_range[1] > 0 else _UNBOUNDED_UPPER
        tool_min = tool_range[0] if tool_range[0] > 0 else 0
        tool_max = tool_range[1] if tool_range[1] > 0 else _UNBOUNDED_UPPER
        lo = max(mat_min, tool_min)
        hi = min(mat_max, tool_max) if tool_max > 0 else mat_max

        if vc < lo:
            violations.append(
                ConstraintViolation(
                    param=key,
                    value=vc,
                    min_allowed=lo,
                    max_allowed=hi,
                    message=f"切削速度 {vc} m/min 低于推荐范围 [{lo}, {hi}]，已调整为 {lo}",
                )
            )
            adjusted[key] = lo
        elif vc > hi:
            violations.append(
                ConstraintViolation(
                    param=key,
                    value=vc,
                    min_allowed=lo,
                    max_allowed=hi,
                    message=f"切削速度 {vc} m/min 高于推荐范围 [{lo}, {hi}]，已调整为 {hi}",
                )
            )
            adjusted[key] = hi

        return adjusted, violations

    def _check_feed(
        self,
        params: dict[str, Any],
        adjusted: dict[str, float],
        material: MaterialEntry,
        tool: ToolEntry,
    ) -> tuple[dict[str, float], list[ConstraintViolation]]:
        violations: list[ConstraintViolation] = []
        feed = params.get("feed", params.get("f", 0.0))
        if feed == 0.0:
            return adjusted, violations

        key = "feed" if "feed" in params else "f"
        mat_range = material.get_feed("roughing")
        tool_range = tool.get_feed_per_tooth("roughing")

        mat_lo = mat_range[0] if mat_range[0] > 0 else 0
        mat_hi = mat_range[1] if mat_range[1] > 0 else _UNBOUNDED_UPPER
        tool_lo = tool_range[0] if tool_range[0] > 0 else 0
        tool_hi = tool_range[1] if tool_range[1] > 0 else _UNBOUNDED_UPPER

        if tool.flutes > 0 and tool.type != "turning" and tool.type != "tap":
            tool_lo *= tool.flutes
            tool_hi *= tool.flutes

        lo = max(mat_lo, tool_lo) if (mat_lo > 0 or tool_lo > 0) else 0
        hi = min(mat_hi, tool_hi) if (mat_hi > 0 and tool_hi > 0) else max(mat_hi, tool_hi)

        if lo > 0 and feed < lo:
            violations.append(
                ConstraintViolation(
                    param=key,
                    value=feed,
                    min_allowed=lo,
                    max_allowed=hi,
                    message=f"进给量 {feed} mm/rev 低于推荐范围 [{lo}, {hi}]，已调整为 {lo}",
                )
            )
            adjusted[key] = lo
        elif hi > 0 and feed > hi:
            violations.append(
                ConstraintViolation(
                    param=key,
                    value=feed,
                    min_allowed=lo,
                    max_allowed=hi,
                    message=f"进给量 {feed} mm/rev 高于推荐范围 [{lo}, {hi}]，已调整为 {hi}",
                )
            )
            adjusted[key] = hi

        return adjusted, violations

    def _check_doc(
        self,
        params: dict[str, Any],
        adjusted: dict[str, float],
        material: MaterialEntry,
        tool: ToolEntry,
    ) -> tuple[dict[str, float], list[ConstraintViolation]]:
        violations: list[ConstraintViolation] = []
        doc = params.get("depth_of_cut", params.get("ap", 0.0))
        if doc == 0.0:
            return adjusted, violations

        key = "depth_of_cut" if "depth_of_cut" in params else "ap"
        mat_range = material.get_depth_of_cut("roughing")
        lo = mat_range[0]
        hi = min(mat_range[1], tool.max_doc) if tool.max_doc > 0 else mat_range[1]

        if doc < lo:
            violations.append(
                ConstraintViolation(
                    param=key,
                    value=doc,
                    min_allowed=lo,
                    max_allowed=hi,
                    message=f"切深 {doc} mm 低于推荐范围 [{lo}, {hi}]，已调整为 {lo}",
                )
            )
            adjusted[key] = lo
        elif doc > hi:
            violations.append(
                ConstraintViolation(
                    param=key,
                    value=doc,
                    min_allowed=lo,
                    max_allowed=hi,
                    message=f"切深 {doc} mm 高于推荐范围 [{lo}, {hi}]，已调整为 {hi}",
                )
            )
            adjusted[key] = hi

        return adjusted, violations

    def _check_spindle_speed(
        self,
        params: dict[str, Any],
        adjusted: dict[str, float],
        tool: ToolEntry,
        machine: MachineEntry | None,
    ) -> tuple[dict[str, float], list[ConstraintViolation]]:
        violations: list[ConstraintViolation] = []
        spindle = params.get("spindle_speed", params.get("n", params.get("rpm", 0.0)))
        if spindle == 0.0:
            return adjusted, violations

        key = "spindle_speed" if "spindle_speed" in params else "n" if "n" in params else "rpm"

        if machine and machine.spindle_speed_rpm:
            lo = machine.spindle_speed_rpm[0]
            hi = machine.spindle_speed_rpm[1] if len(machine.spindle_speed_rpm) > 1 else _UNBOUNDED_UPPER
            if spindle < lo:
                violations.append(
                    ConstraintViolation(
                        param=key,
                        value=spindle,
                        min_allowed=float(lo),
                        max_allowed=float(hi),
                        message=f"主轴转速 {spindle} rpm 低于机床最低转速 {lo} rpm，已调整",
                    )
                )
                adjusted[key] = float(lo)
            elif spindle > hi:
                violations.append(
                    ConstraintViolation(
                        param=key,
                        value=spindle,
                        min_allowed=float(lo),
                        max_allowed=float(hi),
                        message=f"主轴转速 {spindle} rpm 超过机床最高转速 {hi} rpm，已调整",
                    )
                )
                adjusted[key] = float(hi)

        return adjusted, violations

    def check_cutting_force(
        self,
        params: dict[str, Any],
        material: MaterialEntry,
        tool: ToolEntry,
    ) -> list[str]:
        warnings: list[str] = []
        feed = params.get("feed", params.get("f", 0.0))
        doc = params.get("depth_of_cut", params.get("ap", 0.0))
        if doc == 0 or feed == 0:
            return warnings

        fc = material.specific_cutting_force * doc * feed
        if tool.max_cutting_force_n > 0 and fc > tool.max_cutting_force_n:
            warnings.append(f"估算切削力 {fc:.0f} N 超过刀具承受范围 ({tool.max_cutting_force_n} N)")
        return warnings

    def check_cutting_power(
        self,
        params: dict[str, Any],
        material: MaterialEntry,
        machine: MachineEntry,
    ) -> list[str]:
        warnings: list[str] = []
        vc = params.get("cutting_speed", params.get("vc", 0.0))
        feed = params.get("feed", params.get("f", 0.0))
        doc = params.get("depth_of_cut", params.get("ap", 0.0))
        if doc == 0 or feed == 0 or vc == 0:
            return warnings

        fc = material.specific_cutting_force * doc * feed
        power_kw = (fc * vc) / 60000
        if power_kw > machine.spindle_power_kw * 0.85:
            warnings.append(
                f"估算切削功率 {power_kw:.2f} kW 超过机床额定功率 85% ({machine.spindle_power_kw * 0.85:.2f} kW)"
            )
        if power_kw > machine.spindle_power_kw:
            warnings.append(f"估算切削功率 {power_kw:.2f} kW 超过机床额定功率 ({machine.spindle_power_kw} kW)")
        return warnings

    def check_surface_roughness(
        self,
        params: dict[str, Any],
        tool: ToolEntry,
    ) -> list[str]:
        warnings: list[str] = []
        feed = params.get("feed", params.get("f", 0.0))
        if feed == 0 or tool.nose_radius <= 0:
            return warnings

        ra = (feed**2) / (32 * tool.nose_radius)
        if ra > 0.0063:
            warnings.append(f"估算表面粗糙度 Ra={ra * 1000:.2f} μm，超出精加工要求 (Ra 6.3μm)")
        return warnings

    def check_tool_life(
        self,
        params: dict[str, Any],
        material: MaterialEntry,
        tool: ToolEntry,
    ) -> list[str]:
        warnings: list[str] = []
        vc = params.get("cutting_speed", params.get("vc", 0.0))
        if vc == 0 or material.taylor_exponent_n <= 0:
            return warnings

        try:
            t = (material.taylor_constant_c / vc) ** (1.0 / material.taylor_exponent_n)
        except (ZeroDivisionError, OverflowError):
            return warnings

        if t < tool.tool_life_minutes * 0.5:
            warnings.append(
                f"Taylor寿命估算 {t:.1f} min 远低于刀具额定寿命 ({tool.tool_life_minutes} min)，建议降低切削速度"
            )
        elif t < tool.tool_life_minutes:
            warnings.append(f"Taylor寿命估算 {t:.1f} min 低于刀具额定寿命 ({tool.tool_life_minutes} min)")
        return warnings

    def _check_machine_force(
        self,
        params: dict[str, Any],
        material: MaterialEntry,
        tool: ToolEntry,
        machine: MachineEntry,
    ) -> list[str]:
        warnings: list[str] = []
        feed = params.get("feed", params.get("f", 0.0))
        doc = params.get("depth_of_cut", params.get("ap", 0.0))
        if doc == 0 or feed == 0:
            return warnings

        fc = material.specific_cutting_force * doc * feed
        if machine.max_cutting_force_n > 0 and fc > machine.max_cutting_force_n:
            warnings.append(f"估算切削力 {fc:.0f} N 超过机床最大切削力 ({machine.max_cutting_force_n} N)")
        return warnings
