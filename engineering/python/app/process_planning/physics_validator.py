"""Physical constraints validator for process planning.

Validates cutting parameters against machine tool capabilities
including cutting force, power, torque, and stability limits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MachineCapability:
    """Machine tool capability limits.

    Attributes:
        max_power_kw: Maximum spindle power (kW)
        max_torque_nm: Maximum spindle torque (N·m)
        max_force_n: Maximum cutting force (N)
        max_spindle_speed_rpm: Maximum spindle speed (RPM)
        min_spindle_speed_rpm: Minimum spindle speed (RPM)
        max_feed_rate_mm_min: Maximum feed rate (mm/min)
        max_depth_of_cut_mm: Maximum depth of cut (mm)
    """

    max_power_kw: float = 15.0
    max_torque_nm: float = 100.0
    max_force_n: float = 5000.0
    max_spindle_speed_rpm: float = 12000.0
    min_spindle_speed_rpm: float = 50.0
    max_feed_rate_mm_min: float = 10000.0
    max_depth_of_cut_mm: float = 10.0


@dataclass
class CuttingForceResult:
    """Cutting force calculation result.

    Attributes:
        force_tangential_n: Tangential cutting force (N)
        force_feed_n: Feed force (N)
        force_radial_n: Radial force (N)
        torque_nm: Required torque (N·m)
        power_kw: Required power (kW)
        within_limits: Whether forces are within machine limits
        warnings: List of warning messages
    """

    force_tangential_n: float
    force_feed_n: float
    force_radial_n: float
    torque_nm: float
    power_kw: float
    within_limits: bool
    warnings: list[str]


class PhysicsValidator:
    """Validates cutting parameters against physical constraints.

    Uses empirical cutting force models and machine capability limits
    to ensure recommended parameters are feasible and safe.
    """

    # Specific cutting force coefficients (Kc) for common materials
    # Units: N/mm²
    SPECIFIC_CUTTING_FORCE = {
        "aluminum": 800,
        "aluminum_alloy": 800,
        "steel": 2100,
        "steel_45": 2100,
        "stainless": 2500,
        "stainless_steel": 2500,
        "titanium": 1800,
        "titanium_alloy": 1800,
        "cast_iron": 1400,
        "copper": 1000,
        "brass": 900,
        "default": 2000,
    }

    # Material density for power calculations (g/cm³)
    MATERIAL_DENSITY = {
        "aluminum": 2.7,
        "steel": 7.85,
        "titanium": 4.5,
        "cast_iron": 7.2,
        "default": 7.8,
    }

    def __init__(self, machine_capability: MachineCapability | None = None) -> None:
        """Initialize physics validator.

        Args:
            machine_capability: Machine tool capability limits.
                Uses default values if not provided.
        """
        self.machine = machine_capability or MachineCapability()

    def calculate_cutting_force(
        self,
        material: str,
        cutting_speed_m_min: float,
        feed_mm_rev: float,
        depth_of_cut_mm: float,
        tool_diameter_mm: float,
        operation: str = "turning",
    ) -> CuttingForceResult:
        """Calculate cutting forces for given parameters.

        Uses empirical model: Fc = Kc * f^0.75 * ap^0.9 * vc^(-0.1)
        where Kc is specific cutting force, f is feed, ap is depth of cut,
        vc is cutting speed.

        Args:
            material: Material name
            cutting_speed_m_min: Cutting speed (m/min)
            feed_mm_rev: Feed rate (mm/rev)
            depth_of_cut_mm: Depth of cut (mm)
            tool_diameter_mm: Tool diameter (mm)
            operation: Operation type (turning/milling/drilling)

        Returns:
            CuttingForceResult with calculated forces and validation
        """
        warnings: list[str] = []

        # 非正参数防御：无法计算时直接返回不可行结果，
        # 避免 0^(-0.1) 抛 ZeroDivisionError / 负数小数幂抛 ValueError
        if feed_mm_rev <= 0 or depth_of_cut_mm <= 0 or cutting_speed_m_min <= 0 or tool_diameter_mm <= 0:
            warnings.append("切削参数必须为正数，无法计算切削力")
            return CuttingForceResult(
                force_tangential_n=0.0,
                force_feed_n=0.0,
                force_radial_n=0.0,
                torque_nm=0.0,
                power_kw=0.0,
                within_limits=False,
                warnings=warnings,
            )

        # Get specific cutting force for material
        material_lower = material.lower()
        kc = self.SPECIFIC_CUTTING_FORCE.get(material_lower, self.SPECIFIC_CUTTING_FORCE["default"])

        # Calculate tangential cutting force (empirical model)
        # Fc = Kc * f^0.75 * ap^0.9 * vc^(-0.1)
        force_tangential = kc * (feed_mm_rev**0.75) * (depth_of_cut_mm**0.9) * (cutting_speed_m_min ** (-0.1))

        # Estimate feed and radial forces based on operation type
        if operation == "turning":
            force_feed = force_tangential * 0.3  # Feed force ~30% of tangential
            force_radial = force_tangential * 0.4  # Radial force ~40% of tangential
        elif operation == "milling":
            force_feed = force_tangential * 0.5
            force_radial = force_tangential * 0.6
        else:  # drilling
            force_feed = force_tangential * 0.8
            force_radial = force_tangential * 0.2

        # Calculate torque: T = Fc * D/2
        torque_nm = force_tangential * (tool_diameter_mm / 2.0) / 1000.0

        # Calculate power: P = Fc * vc / 60000
        power_kw = (force_tangential * cutting_speed_m_min) / 60000.0

        # Validate against machine limits
        within_limits = True

        if force_tangential > self.machine.max_force_n:
            warnings.append(f"主切削力 {force_tangential:.1f} N 超过机床最大切削力 {self.machine.max_force_n:.1f} N")
            within_limits = False

        if torque_nm > self.machine.max_torque_nm:
            warnings.append(f"所需扭矩 {torque_nm:.2f} N·m 超过机床最大扭矩 {self.machine.max_torque_nm:.2f} N·m")
            within_limits = False

        if power_kw > self.machine.max_power_kw:
            warnings.append(f"所需功率 {power_kw:.2f} kW 超过机床最大功率 {self.machine.max_power_kw:.2f} kW")
            within_limits = False

        return CuttingForceResult(
            force_tangential_n=round(force_tangential, 2),
            force_feed_n=round(force_feed, 2),
            force_radial_n=round(force_radial, 2),
            torque_nm=round(torque_nm, 2),
            power_kw=round(power_kw, 2),
            within_limits=within_limits,
            warnings=warnings,
        )

    def validate_cutting_parameters(
        self,
        material: str,
        cutting_speed_m_min: float,
        feed_mm_rev: float,
        depth_of_cut_mm: float,
        tool_diameter_mm: float,
        operation: str = "turning",
    ) -> dict[str, Any]:
        """Comprehensive validation of cutting parameters.

        Checks cutting forces, power, torque, and operational limits.
        Provides recommendations if parameters exceed machine capabilities.

        Args:
            material: Material name
            cutting_speed_m_min: Cutting speed (m/min)
            feed_mm_rev: Feed rate (mm/rev)
            depth_of_cut_mm: Depth of cut (mm)
            tool_diameter_mm: Tool diameter (mm)
            operation: Operation type

        Returns:
            Dictionary with validation results and recommendations
        """
        result: dict[str, Any] = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "recommendations": [],
            "force_analysis": None,
        }

        # Calculate cutting forces
        force_result = self.calculate_cutting_force(
            material=material,
            cutting_speed_m_min=cutting_speed_m_min,
            feed_mm_rev=feed_mm_rev,
            depth_of_cut_mm=depth_of_cut_mm,
            tool_diameter_mm=tool_diameter_mm,
            operation=operation,
        )
        result["force_analysis"] = {
            "tangential_force_n": force_result.force_tangential_n,
            "feed_force_n": force_result.force_feed_n,
            "radial_force_n": force_result.force_radial_n,
            "torque_nm": force_result.torque_nm,
            "power_kw": force_result.power_kw,
            "within_limits": force_result.within_limits,
        }

        # Check basic parameter limits
        if cutting_speed_m_min <= 0:
            result["errors"].append("切削速度必须大于0")
            result["valid"] = False

        if feed_mm_rev <= 0:
            result["errors"].append("进给量必须大于0")
            result["valid"] = False

        if depth_of_cut_mm <= 0:
            result["errors"].append("切削深度必须大于0")
            result["valid"] = False

        if tool_diameter_mm <= 0:
            result["errors"].append("刀具直径必须大于0")
            result["valid"] = False

        # Check depth of cut limit
        if depth_of_cut_mm > self.machine.max_depth_of_cut_mm:
            result["errors"].append(
                f"切削深度 {depth_of_cut_mm:.2f} mm 超过机床最大切深 {self.machine.max_depth_of_cut_mm:.2f} mm"
            )
            result["valid"] = False
            result["recommendations"].append(f"建议将切削深度降低至 {self.machine.max_depth_of_cut_mm:.2f} mm 以下")

        # Add force warnings
        result["warnings"].extend(force_result.warnings)

        # Generate recommendations if not within limits
        if not force_result.within_limits:
            result["valid"] = False

            # Recommend parameter adjustments
            if force_result.power_kw > self.machine.max_power_kw:
                # Reduce cutting speed or depth of cut
                recommended_speed = cutting_speed_m_min * (self.machine.max_power_kw / force_result.power_kw)
                result["recommendations"].append(f"建议将切削速度降低至 {recommended_speed:.1f} m/min")

            if force_result.torque_nm > self.machine.max_torque_nm:
                # Reduce feed or depth of cut
                recommended_feed = feed_mm_rev * (self.machine.max_torque_nm / force_result.torque_nm)
                result["recommendations"].append(f"建议将进给量降低至 {recommended_feed:.3f} mm/rev")

        # Check for unstable cutting conditions
        if cutting_speed_m_min > 500 and feed_mm_rev > 0.5:
            result["warnings"].append("高速大切屑加工可能导致振动，建议降低切削参数")

        return result

    def recommend_safe_parameters(
        self,
        material: str,
        tool_diameter_mm: float,
        operation: str = "turning",
        target_mrr_mm3_min: float | None = None,
    ) -> dict[str, float]:
        """Recommend safe cutting parameters for given constraints.

        Iteratively adjusts parameters to stay within machine limits
        while maximizing material removal rate if target is specified.

        Args:
            material: Material name
            tool_diameter_mm: Tool diameter (mm)
            operation: Operation type
            target_mrr_mm3_min: Target material removal rate (mm³/min)

        Returns:
            Dictionary with recommended cutting parameters
        """
        # Start with conservative parameters
        recommended_speed = 100.0  # m/min
        recommended_feed = 0.2  # mm/rev
        recommended_depth = 1.0  # mm

        # Adjust based on material
        material_lower = material.lower()
        if "aluminum" in material_lower:
            recommended_speed = 300.0
            recommended_feed = 0.3
            recommended_depth = 2.0
        elif "titanium" in material_lower:
            recommended_speed = 50.0
            recommended_feed = 0.1
            recommended_depth = 0.5
        elif "stainless" in material_lower:
            recommended_speed = 80.0
            recommended_feed = 0.15
            recommended_depth = 1.0

        # Iteratively reduce parameters until within limits
        max_iterations = 10
        iteration = 0

        while iteration < max_iterations:
            validation = self.validate_cutting_parameters(
                material=material,
                cutting_speed_m_min=recommended_speed,
                feed_mm_rev=recommended_feed,
                depth_of_cut_mm=recommended_depth,
                tool_diameter_mm=tool_diameter_mm,
                operation=operation,
            )

            if validation["valid"]:
                break

            # Reduce parameters
            if recommended_speed > 50:
                recommended_speed *= 0.9
            elif recommended_feed > 0.05:
                recommended_feed *= 0.9
            elif recommended_depth > 0.2:
                recommended_depth *= 0.9
            else:
                break

            iteration += 1

        return {
            "cutting_speed_m_min": round(recommended_speed, 1),
            "feed_mm_rev": round(recommended_feed, 3),
            "depth_of_cut_mm": round(recommended_depth, 2),
            "tool_diameter_mm": tool_diameter_mm,
        }
