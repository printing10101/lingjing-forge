"""
Material-Tool Cutting Parameter Database

Provides recommended cutting parameters for different material categories
and machining operations based on tool diameter.
"""

from typing import NotRequired, TypedDict


# 切削参数推荐结果的精确类型（2026-08-19：从 Dict[str, Union[...]] 收紧，
# 修复 mypy 调用方取值推断为 int|float|list[str] 联合类型的问题）
class CuttingParamsResult(TypedDict):
    """get_cutting_params 返回结构。"""

    spindle_speed: int  # RPM
    feed_rate: float  # mm/min（turning 为 mm/rev）
    depth_of_cut: float  # mm
    warnings: NotRequired[list[str]]  # 仅 validate_machine_limits=True 时存在


# Material categories with their machinability properties
MATERIAL_CATEGORIES = {
    "aluminum": {
        "machinability": 1.0,  # Reference material
        "hardness": "soft",
    },
    "steel": {
        "machinability": 0.6,
        "hardness": "medium",
    },
    "stainless": {
        "machinability": 0.5,
        "hardness": "medium-hard",
    },
    "titanium": {
        "machinability": 0.35,
        "hardness": "hard",
    },
    "cast_iron": {
        "machinability": 0.7,
        "hardness": "medium",
    },
    "brass": {
        "machinability": 0.9,
        "hardness": "soft",
    },
}

# Base cutting parameters for each operation and material
# Format: (spindle_speed_rpm, feed_rate_mm_per_min, depth_of_cut_mm)
# These are baseline values for a 10mm diameter tool
# 显式类型注解：mypy 对深层嵌套字面量推导会退化为 float（见 mypy 修复批次）
OperationParams = dict[str, tuple[float, float]]  # {"spindle_speed_range": (min, max)}
MaterialOps = dict[str, OperationParams]  # {"drilling": {...}}
MaterialParams = dict[str, MaterialOps]  # {"aluminum": {...}}
BASE_PARAMETERS: MaterialParams = {
    "aluminum": {
        "drilling": {
            "spindle_speed_range": (2000, 4000),
            "feed_rate_range": (200, 500),
            "depth_of_cut_range": (1.0, 3.0),
        },
        "milling": {
            "spindle_speed_range": (2500, 5000),
            "feed_rate_range": (300, 800),
            "depth_of_cut_range": (0.5, 2.0),
        },
        "turning": {
            "spindle_speed_range": (1800, 3500),
            "feed_rate_range": (0.1, 0.3),  # mm/rev for turning
            "depth_of_cut_range": (0.5, 2.5),
        },
    },
    "steel": {
        "drilling": {
            "spindle_speed_range": (800, 1500),
            "feed_rate_range": (80, 200),
            "depth_of_cut_range": (0.8, 2.5),
        },
        "milling": {
            "spindle_speed_range": (1200, 2500),
            "feed_rate_range": (150, 400),
            "depth_of_cut_range": (0.3, 1.5),
        },
        "turning": {
            "spindle_speed_range": (1000, 2000),
            "feed_rate_range": (0.08, 0.25),
            "depth_of_cut_range": (0.5, 2.0),
        },
    },
    "stainless": {
        "drilling": {
            "spindle_speed_range": (600, 1200),
            "feed_rate_range": (60, 150),
            "depth_of_cut_range": (0.5, 2.0),
        },
        "milling": {
            "spindle_speed_range": (1000, 2000),
            "feed_rate_range": (120, 300),
            "depth_of_cut_range": (0.2, 1.2),
        },
        "turning": {
            "spindle_speed_range": (800, 1600),
            "feed_rate_range": (0.06, 0.2),
            "depth_of_cut_range": (0.3, 1.8),
        },
    },
    "titanium": {
        "drilling": {
            "spindle_speed_range": (400, 800),
            "feed_rate_range": (40, 100),
            "depth_of_cut_range": (0.3, 1.5),
        },
        "milling": {
            "spindle_speed_range": (600, 1500),
            "feed_rate_range": (80, 200),
            "depth_of_cut_range": (0.2, 1.0),
        },
        "turning": {
            "spindle_speed_range": (500, 1200),
            "feed_rate_range": (0.05, 0.15),
            "depth_of_cut_range": (0.2, 1.2),
        },
    },
    "cast_iron": {
        "drilling": {
            "spindle_speed_range": (1000, 2000),
            "feed_rate_range": (100, 250),
            "depth_of_cut_range": (0.8, 2.5),
        },
        "milling": {
            "spindle_speed_range": (1500, 3000),
            "feed_rate_range": (200, 500),
            "depth_of_cut_range": (0.3, 1.8),
        },
        "turning": {
            "spindle_speed_range": (1200, 2500),
            "feed_rate_range": (0.1, 0.3),
            "depth_of_cut_range": (0.5, 2.2),
        },
    },
    "brass": {
        "drilling": {
            "spindle_speed_range": (1500, 3000),
            "feed_rate_range": (150, 400),
            "depth_of_cut_range": (0.8, 2.8),
        },
        "milling": {
            "spindle_speed_range": (2000, 4000),
            "feed_rate_range": (250, 600),
            "depth_of_cut_range": (0.4, 1.8),
        },
        "turning": {
            "spindle_speed_range": (1500, 3000),
            "feed_rate_range": (0.12, 0.35),
            "depth_of_cut_range": (0.5, 2.5),
        },
    },
}

# Safe defaults for unknown materials
DEFAULT_PARAMETERS: MaterialOps = {
    "drilling": {
        "spindle_speed_range": (800, 1500),
        "feed_rate_range": (80, 200),
        "depth_of_cut_range": (0.5, 2.0),
    },
    "milling": {
        "spindle_speed_range": (1200, 2500),
        "feed_rate_range": (150, 400),
        "depth_of_cut_range": (0.3, 1.5),
    },
    "turning": {
        "spindle_speed_range": (1000, 2000),
        "feed_rate_range": (0.08, 0.25),
        "depth_of_cut_range": (0.5, 2.0),
    },
}

# 机床能力限制配置（典型数控机床参数）
# 实际使用时应根据具体机床型号调整
MACHINE_CAPABILITIES = {
    "default": {
        "max_spindle_speed": 24000,  # RPM
        "min_spindle_speed": 50,  # RPM
        "max_feed_rate": 20000,  # mm/min
        "min_feed_rate": 10,  # mm/min
        "max_depth_of_cut": 10.0,  # mm
        "max_power": 15.0,  # kW
        "max_torque": 100.0,  # Nm
    },
    "high_speed": {
        "max_spindle_speed": 40000,
        "min_spindle_speed": 100,
        "max_feed_rate": 30000,
        "min_feed_rate": 10,
        "max_depth_of_cut": 8.0,
        "max_power": 22.0,
        "max_torque": 80.0,
    },
    "heavy_duty": {
        "max_spindle_speed": 12000,
        "min_spindle_speed": 30,
        "max_feed_rate": 15000,
        "min_feed_rate": 5,
        "max_depth_of_cut": 15.0,
        "max_power": 30.0,
        "max_torque": 200.0,
    },
}


def get_cutting_params(
    material: str,
    operation: str,
    tool_diameter: float,
    machine_type: str = "default",
    validate_machine_limits: bool = True,
) -> CuttingParamsResult:
    """
    Get recommended cutting parameters for a given material, operation, and tool diameter.

    Args:
        material: Material category (aluminum, steel, stainless, titanium, cast_iron, brass)
        operation: Machining operation (drilling, milling, turning)
        tool_diameter: Tool diameter in millimeters
        machine_type: Machine capability type (default/high_speed/heavy_duty)
        validate_machine_limits: Whether to validate against machine capabilities

    Returns:
        Dictionary containing:
        - spindle_speed: Recommended spindle speed in RPM
        - feed_rate: Recommended feed rate (mm/min for drilling/milling, mm/rev for turning)
        - depth_of_cut: Recommended depth of cut in mm
        - warnings: List of validation warnings (if validate_machine_limits=True)

    Raises:
        ValueError: If operation is not supported or tool_diameter is invalid
    """
    if operation not in ["drilling", "milling", "turning"]:
        raise ValueError(f"Unsupported operation: {operation}")

    if tool_diameter <= 0:
        raise ValueError(f"Tool diameter must be positive, got {tool_diameter}")

    # Normalize material name
    material = material.lower().strip()

    # Get material parameters or use defaults
    # 注意默认值层级：BASE_PARAMETERS[material] 是操作级字典，DEFAULT_PARAMETERS[operation]
    # 是参数级字典；未知材料时不能直接嵌套 .get（`params[operation]` 会 KeyError），必须显式分支。
    material_params = BASE_PARAMETERS.get(material)
    if material_params is not None:
        operation_params = material_params[operation]
    else:
        operation_params = DEFAULT_PARAMETERS[operation]

    # Get machine capabilities
    machine_caps = MACHINE_CAPABILITIES.get(machine_type, MACHINE_CAPABILITIES["default"])

    warnings = []

    # Calculate speed adjustment based on tool diameter
    # Smaller tools need higher RPM, larger tools need lower RPM
    # Base parameters are for 10mm tool
    diameter_ratio = 10.0 / tool_diameter

    # Calculate spindle speed (RPM)
    speed_min, speed_max = operation_params["spindle_speed_range"]
    base_speed = (speed_min + speed_max) / 2
    spindle_speed = base_speed * diameter_ratio
    spindle_speed = max(speed_min, min(speed_max, spindle_speed))

    # Validate against machine limits
    if validate_machine_limits:
        if spindle_speed > machine_caps["max_spindle_speed"]:
            warnings.append(
                f"计算的主轴转速 {spindle_speed:.0f} RPM 超出机床最大转速 "
                f"{machine_caps['max_spindle_speed']} RPM，已自动降低"
            )
            spindle_speed = machine_caps["max_spindle_speed"]
        elif spindle_speed < machine_caps["min_spindle_speed"]:
            warnings.append(
                f"计算的主轴转速 {spindle_speed:.0f} RPM 低于机床最小转速 "
                f"{machine_caps['min_spindle_speed']} RPM，已自动提高"
            )
            spindle_speed = machine_caps["min_spindle_speed"]

    # Calculate feed rate
    feed_min, feed_max = operation_params["feed_rate_range"]
    base_feed = (feed_min + feed_max) / 2

    # For turning, feed is in mm/rev and scales differently
    if operation == "turning":
        # Feed per revolution decreases slightly with larger diameter
        feed_rate = base_feed * (10.0 / tool_diameter) ** 0.3
        feed_rate = max(feed_min, min(feed_max, feed_rate))
    else:
        # For drilling/milling, feed increases with tool diameter
        feed_rate = base_feed * (tool_diameter / 10.0) ** 0.5
        feed_rate = max(feed_min, min(feed_max, feed_rate))

    # Validate feed rate against machine limits (only for drilling/milling)
    if validate_machine_limits and operation in ["drilling", "milling"]:
        if feed_rate > machine_caps["max_feed_rate"]:
            warnings.append(
                f"计算的进给速度 {feed_rate:.1f} mm/min 超出机床最大进给 "
                f"{machine_caps['max_feed_rate']} mm/min，已自动降低"
            )
            feed_rate = machine_caps["max_feed_rate"]
        elif feed_rate < machine_caps["min_feed_rate"]:
            warnings.append(
                f"计算的进给速度 {feed_rate:.1f} mm/min 低于机床最小进给 "
                f"{machine_caps['min_feed_rate']} mm/min，已自动提高"
            )
            feed_rate = machine_caps["min_feed_rate"]

    # Calculate depth of cut
    depth_min, depth_max = operation_params["depth_of_cut_range"]
    base_depth = (depth_min + depth_max) / 2
    # Depth scales with tool diameter
    depth_of_cut = base_depth * (tool_diameter / 10.0) ** 0.4
    depth_of_cut = max(depth_min, min(depth_max, depth_of_cut))

    # Validate depth of cut against machine limits
    if validate_machine_limits:
        if depth_of_cut > machine_caps["max_depth_of_cut"]:
            warnings.append(
                f"计算的切削深度 {depth_of_cut:.2f} mm 超出机床最大切深 "
                f"{machine_caps['max_depth_of_cut']} mm，已自动降低"
            )
            depth_of_cut = machine_caps["max_depth_of_cut"]

    result: CuttingParamsResult = {
        "spindle_speed": int(spindle_speed),
        "feed_rate": round(feed_rate, 2),
        "depth_of_cut": round(depth_of_cut, 2),
    }

    if validate_machine_limits:
        result["warnings"] = warnings

    return result


def get_material_list() -> list:
    """Get list of supported material categories."""
    return list(MATERIAL_CATEGORIES.keys())


def get_operation_list() -> list:
    """Get list of supported operations."""
    return ["drilling", "milling", "turning"]
