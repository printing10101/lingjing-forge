"""Postprocessor config submodule (split from config_loader)."""

from __future__ import annotations
from typing import Any

import logging

logger = logging.getLogger(__name__)


class ConfigValidationError(Exception):
    """配置验证异常。"""


class ConfigValidator:
    """配置验证器。

    负责校验配置完整性、参数类型正确性及数值范围限制。
    """

    def __init__(self) -> None:
        self._errors: list[str] = []
        self._warnings: list[str] = []

    @property
    def errors(self) -> list[str]:
        return self._errors

    @property
    def warnings(self) -> list[str]:
        return self._warnings

    def _add_error(self, path: str, message: str) -> None:
        self._errors.append(f"[{path}] {message}")

    def _add_warning(self, path: str, message: str) -> None:
        self._warnings.append(f"[{path}] {message}")

    def _check_type(
        self,
        path: str,
        value: Any,
        expected_type: type | tuple[type, ...],
        allow_none: bool = False,
    ) -> bool:
        if allow_none and value is None:
            return True
        if not isinstance(value, expected_type):
            self._add_error(
                path,
                f"类型错误: 期望 {getattr(expected_type, '__name__', str(expected_type))}, 实际 {type(value).__name__}",
            )
            return False
        return True

    def _check_positive_int(self, path: str, value: Any) -> bool:
        if not self._check_type(path, value, int):
            return False
        if value < 0:
            self._add_error(path, f"值必须 >= 0, 当前: {value}")
            return False
        return True

    def _check_positive_float(self, path: str, value: Any) -> bool:
        if isinstance(value, int):
            value = float(value)
        if not self._check_type(path, value, float):
            return False
        if value < 0.0:
            self._add_error(path, f"值必须 >= 0, 当前: {value}")
            return False
        return True

    def _check_range(
        self,
        path: str,
        value: Any,
        min_val: float,
        max_val: float,
    ) -> bool:
        if isinstance(value, (int, float)):
            if value < min_val or value > max_val:
                self._add_error(
                    path,
                    f"值 {value} 超出范围 [{min_val}, {max_val}]",
                )
                return False
        return True

    def validate(self, config: dict) -> bool:
        """执行完整配置验证。

        Args:
            config: 合并后的最终配置字典

        Returns:
            True 表示验证通过，False 表示存在错误
        """
        self._errors.clear()
        self._warnings.clear()

        self._validate_top_level(config)
        self._validate_spindle(config.get("spindle", {}))
        self._validate_feed(config.get("feed", {}))
        self._validate_work_coordinate(config.get("work_coordinate", {}))
        self._validate_tool_offset(config.get("tool_offset", {}))
        self._validate_fixed_cycles(config.get("fixed_cycles", {}))
        self._validate_subprogram(config.get("subprogram", {}))

        if self._errors:
            logger.error(
                "配置验证失败 (%d 错误, %d 警告):\n%s",
                len(self._errors),
                len(self._warnings),
                "\n".join(self._errors),
            )
            return False

        if self._warnings:
            logger.warning(
                "配置验证通过 (%d 警告):\n%s",
                len(self._warnings),
                "\n".join(self._warnings),
            )
        return True

    def _validate_top_level(self, config: dict) -> None:
        path = "config"

        self._check_type(f"{path}.decimal_places", config.get("decimal_places"), int)
        dp = config.get("decimal_places", 3)
        if isinstance(dp, int) and dp < 0:
            self._add_error(f"{path}.decimal_places", f"不能为负数: {dp}")

        self._check_positive_float(f"{path}.safe_z_height", config.get("safe_z_height"))
        sh = config.get("safe_z_height", 50.0)
        if isinstance(sh, (int, float)) and sh <= 0:
            self._add_error(f"{path}.safe_z_height", f"必须大于0: {sh}")

        self._check_positive_float(f"{path}.rapid_feed", config.get("rapid_feed"))

    def _validate_spindle(self, spindle: dict) -> None:
        path = "spindle"
        for key in REQUIRED_SPINDLE_KEYS:
            if key not in spindle:
                self._add_error(path, f"缺少必需参数: {key}")
        for key in REQUIRED_SPINDLE_KEYS:
            if key in spindle:
                self._check_positive_int(f"{path}.{key}", spindle[key])

        min_rpm = spindle.get("min_rpm", 0)
        max_rpm = spindle.get("max_rpm", 0)
        default_rpm = spindle.get("default_rpm", 0)

        if isinstance(min_rpm, int) and isinstance(max_rpm, int):
            if min_rpm >= max_rpm:
                self._add_error(
                    path,
                    f"min_rpm ({min_rpm}) 必须小于 max_rpm ({max_rpm})",
                )
        if isinstance(default_rpm, int) and isinstance(min_rpm, int) and isinstance(max_rpm, int):
            if not (min_rpm <= default_rpm <= max_rpm):
                self._add_warning(
                    path,
                    f"default_rpm ({default_rpm}) 不在 [{min_rpm}, {max_rpm}] 范围内",
                )

    def _validate_feed(self, feed: dict) -> None:
        path = "feed"
        for key in REQUIRED_FEED_KEYS:
            if key not in feed:
                self._add_error(path, f"缺少必需参数: {key}")

        min_rate = feed.get("min_rate", 0)
        max_rate = feed.get("max_rate", 0)
        default_rate = feed.get("default_rate", 0)

        self._check_positive_float(f"{path}.min_rate", min_rate)
        self._check_positive_float(f"{path}.max_rate", max_rate)
        self._check_positive_float(f"{path}.default_rate", default_rate)

        if isinstance(min_rate, (int, float)) and isinstance(max_rate, (int, float)):
            if min_rate >= max_rate:
                self._add_error(
                    path,
                    f"min_rate ({min_rate}) 必须小于 max_rate ({max_rate})",
                )

    def _validate_work_coordinate(self, wcs: dict) -> None:
        path = "work_coordinate"
        for key in COORD_SYSTEMS:
            if key not in wcs:
                self._add_error(path, f"缺少坐标系: {key}")
                continue
            coord = wcs[key]
            if not isinstance(coord, dict):
                self._add_error(f"{path}.{key}", "必须为字典类型")
                continue
            for axis in ("x_offset", "y_offset", "z_offset"):
                if axis in coord:
                    self._check_type(f"{path}.{key}.{axis}", coord[axis], (int, float))
            self._check_type(f"{path}.{key}.enabled", coord.get("enabled"), bool)

        default_cs = wcs.get("default_coordinate_system")
        if default_cs is None:
            self._add_error(path, "缺少 default_coordinate_system")
        elif default_cs not in COORD_SYSTEMS:
            self._add_error(
                path,
                f"default_coordinate_system 无效: {default_cs}, 有效值: {COORD_SYSTEMS}",
            )

    def _validate_tool_offset(self, tool_offset: dict) -> None:
        path = "tool_offset"
        for key in REQUIRED_TOOL_OFFSET_KEYS:
            if key not in tool_offset:
                self._add_error(path, f"缺少必需参数: {key}")
                continue

            reg = tool_offset[key]
            if not isinstance(reg, dict):
                self._add_error(f"{path}.{key}", "必须为字典类型")
                continue

            self._check_positive_int(f"{path}.{key}.start", reg.get("start"))
            self._check_positive_int(f"{path}.{key}.end", reg.get("end"))

            if isinstance(reg.get("start"), int) and isinstance(reg.get("end"), int) and reg["start"] > reg["end"]:
                self._add_error(
                    f"{path}.{key}",
                    f"start ({reg['start']}) 不能大于 end ({reg['end']})",
                )

            self._check_positive_float(f"{path}.{key}.default_offset", reg.get("default_offset"))

        self._validate_radius_compensation_types(tool_offset)

    def _validate_radius_compensation_types(self, tool_offset: dict) -> None:
        path = "tool_offset.radius_registers.compensation_types"
        radius_regs = tool_offset.get("radius_registers", {})
        if not isinstance(radius_regs, dict):
            return

        comp_types = radius_regs.get("compensation_types", {})
        if not isinstance(comp_types, dict):
            self._add_error(path, "必须为字典类型")
            return

        for comp_name in ("G41", "G42"):
            if comp_name not in comp_types:
                self._add_error(path, f"缺少补偿类型: {comp_name}")
                continue
            ct = comp_types[comp_name]
            if not isinstance(ct, dict):
                self._add_error(f"{path}.{comp_name}", "必须为字典类型")
                continue
            reg_range = ct.get("register_range")
            if reg_range is None:
                self._add_error(f"{path}.{comp_name}", "缺少 register_range")
            elif not isinstance(reg_range, list) or len(reg_range) != 2:
                self._add_error(
                    f"{path}.{comp_name}.register_range",
                    "必须为长度为2的列表 [min, max]",
                )
            else:
                if not all(isinstance(v, int) for v in reg_range):
                    self._add_error(
                        f"{path}.{comp_name}.register_range",
                        "元素必须为整数",
                    )
                elif reg_range[0] > reg_range[1]:
                    self._add_error(
                        f"{path}.{comp_name}.register_range",
                        f"起始值 {reg_range[0]} 不能大于结束值 {reg_range[1]}",
                    )

    def _validate_fixed_cycles(self, fixed_cycles: dict) -> None:
        path = "fixed_cycles"

        for group in REQUIRED_FIXED_CYCLE_GROUPS:
            if group not in fixed_cycles:
                self._add_error(path, f"缺少循环组: {group}")
            elif not isinstance(fixed_cycles[group], dict):
                self._add_error(f"{path}.{group}", "必须为字典类型")

        if isinstance(fixed_cycles.get("drilling"), dict):
            dr = fixed_cycles["drilling"]
            for cyc in REQUIRED_DRILLING_CYCLES:
                if cyc not in dr:
                    self._add_error(f"{path}.drilling", f"缺少钻孔循环: {cyc}")
                elif isinstance(dr[cyc], dict):
                    self._validate_drilling_cycle(f"{path}.drilling.{cyc}", dr[cyc])

        if isinstance(fixed_cycles.get("tapping"), dict):
            tp = fixed_cycles["tapping"]
            if REQUIRED_TAPPING_CYCLES not in tp:
                self._add_error(f"{path}.tapping", f"缺少攻丝循环: {REQUIRED_TAPPING_CYCLES}")
            elif isinstance(tp[REQUIRED_TAPPING_CYCLES], dict):
                self._validate_tapping_cycle(
                    f"{path}.tapping.{REQUIRED_TAPPING_CYCLES}",
                    tp[REQUIRED_TAPPING_CYCLES],
                )

        if isinstance(fixed_cycles.get("boring"), dict):
            br = fixed_cycles["boring"]
            for cyc in REQUIRED_BORING_CYCLES:
                if cyc not in br:
                    self._add_error(f"{path}.boring", f"缺少镗孔循环: {cyc}")
                elif isinstance(br[cyc], dict):
                    self._validate_boring_cycle(f"{path}.boring.{cyc}", br[cyc])

        if isinstance(fixed_cycles.get("threading"), dict):
            th = fixed_cycles["threading"]
            if REQUIRED_THREADING_CYCLES not in th:
                self._add_error(
                    f"{path}.threading",
                    f"缺少螺纹加工循环: {REQUIRED_THREADING_CYCLES}",
                )
            elif isinstance(th[REQUIRED_THREADING_CYCLES], dict):
                self._validate_threading_cycle(
                    f"{path}.threading.{REQUIRED_THREADING_CYCLES}",
                    th[REQUIRED_THREADING_CYCLES],
                )

    def _validate_drilling_cycle(self, path: str, cycle: dict) -> None:
        self._check_type(f"{path}.retract_mode", cycle.get("retract_mode"), str)
        if cycle.get("retract_mode") not in VALID_RETRACT_MODES:
            self._add_error(
                path,
                f"retract_mode 无效: {cycle.get('retract_mode')}, 有效值: {VALID_RETRACT_MODES}",
            )
        self._check_positive_float(f"{path}.peck_depth", cycle.get("peck_depth"))
        self._check_positive_float(f"{path}.retract_distance", cycle.get("retract_distance"))
        if "dwell_time" in cycle:
            self._check_positive_float(f"{path}.dwell_time", cycle.get("dwell_time"))
        if "decrement_type" in cycle:
            if cycle.get("decrement_type") not in VALID_DECREMENT_TYPES:
                self._add_error(
                    path,
                    f"decrement_type 无效: {cycle.get('decrement_type')}, 有效值: {VALID_DECREMENT_TYPES}",
                )

    def _validate_tapping_cycle(self, path: str, cycle: dict) -> None:
        if cycle.get("spindle_direction") not in VALID_SPINDLE_DIRECTIONS:
            self._add_error(
                path,
                f"spindle_direction 无效: {cycle.get('spindle_direction')}, 有效值: {VALID_SPINDLE_DIRECTIONS}",
            )
        self._check_type(f"{path}.feed_per_rev", cycle.get("feed_per_rev"), bool)
        self._check_positive_float(f"{path}.dwell_time", cycle.get("dwell_time"))
        if cycle.get("retract_spindle_direction") not in VALID_SPINDLE_DIRECTIONS:
            self._add_error(
                path,
                f"retract_spindle_direction 无效: {cycle.get('retract_spindle_direction')}",
            )

    def _validate_boring_cycle(self, path: str, cycle: dict) -> None:
        if cycle.get("retract_mode") not in VALID_RETRACT_MODES:
            self._add_error(
                path,
                f"retract_mode 无效: {cycle.get('retract_mode')}",
            )
        self._check_positive_float(f"{path}.dwell_time", cycle.get("dwell_time"))
        if cycle.get("retract_type") not in VALID_RETRACT_TYPES:
            self._add_error(
                path,
                f"retract_type 无效: {cycle.get('retract_type')}",
            )
        self._check_type(f"{path}.orient_spindle", cycle.get("orient_spindle"), bool)
        if cycle.get("shift_axis") not in VALID_SHIFT_AXES:
            self._add_error(
                path,
                f"shift_axis 无效: {cycle.get('shift_axis')}",
            )
        self._check_positive_float(f"{path}.shift_distance", cycle.get("shift_distance"))

    def _validate_threading_cycle(self, path: str, cycle: dict) -> None:
        if cycle.get("retract_mode") not in VALID_RETRACT_MODES:
            self._add_error(
                path,
                f"retract_mode 无效: {cycle.get('retract_mode')}",
            )
        self._check_positive_float(f"{path}.lead", cycle.get("lead"))
        self._check_positive_int(f"{path}.passes", cycle.get("passes"))
        self._check_positive_float(f"{path}.depth_cut_first", cycle.get("depth_cut_first"))
        self._check_positive_float(f"{path}.depth_cut_last", cycle.get("depth_cut_last"))
        self._check_positive_int(f"{path}.finishing_passes", cycle.get("finishing_passes"))
        self._check_positive_float(f"{path}.taper", cycle.get("taper"))
        self._check_positive_float(f"{path}.tool_angle", cycle.get("tool_angle"))
        if cycle.get("retract_type") not in VALID_RETRACT_TYPES:
            self._add_error(
                path,
                f"retract_type 无效: {cycle.get('retract_type')}",
            )
        if cycle.get("shift_axis") not in VALID_SHIFT_AXES:
            self._add_error(
                path,
                f"shift_axis 无效: {cycle.get('shift_axis')}",
            )
        self._check_positive_float(f"{path}.shift_distance", cycle.get("shift_distance"))
        if cycle.get("infeed_method") not in VALID_INFEED_METHODS:
            self._add_error(
                path,
                f"infeed_method 无效: {cycle.get('infeed_method')}, 有效值: {VALID_INFEED_METHODS}",
            )

    def _validate_subprogram(self, sub: dict) -> None:
        path = "subprogram"
        for key in ("call_format", "end_code"):
            if key not in sub:
                self._add_error(path, f"缺少必需参数: {key}")
            else:
                self._check_type(f"{path}.{key}", sub[key], str)

        prog_num = sub.get("program_number", {})
        if isinstance(prog_num, dict):
            self._check_positive_int(f"{path}.program_number.minimum", prog_num.get("minimum"))
            self._check_positive_int(f"{path}.program_number.maximum", prog_num.get("maximum"))
            self._check_type(f"{path}.program_number.format", prog_num.get("format"), str)

        repeat = sub.get("repeat", {})
        if isinstance(repeat, dict):
            self._check_positive_int(f"{path}.repeat.default", repeat.get("default"))
            self._check_positive_int(f"{path}.repeat.minimum", repeat.get("minimum"))
            self._check_positive_int(f"{path}.repeat.maximum", repeat.get("maximum"))

        macro = sub.get("macro_variables", {})
        if isinstance(macro, dict):
            for var_type in ("local", "common", "system"):
                if var_type not in macro:
                    self._add_error(path, f"缺少宏变量类型: {var_type}")
                elif isinstance(macro[var_type], dict):
                    rng = macro[var_type].get("range")
                    if not isinstance(rng, list) or len(rng) != 2:
                        self._add_error(
                            f"{path}.macro_variables.{var_type}",
                            "range 必须为长度为2的列表",
                        )
                    elif not all(isinstance(v, int) for v in rng):
                        self._add_error(
                            f"{path}.macro_variables.{var_type}.range",
                            "元素必须为整数",
                        )


# 常量（自 config_loader 迁移）
REQUIRED_TOP_KEYS = ("base", "controllers")
REQUIRED_BASE_KEYS = (
    "decimal_places",
    "safe_z_height",
    "rapid_feed",
    "spindle",
    "feed",
    "work_coordinate",
    "tool_offset",
    "fixed_cycles",
    "subprogram",
)
REQUIRED_SPINDLE_KEYS = ("min_rpm", "max_rpm", "default_rpm")
REQUIRED_FEED_KEYS = ("min_rate", "max_rate", "default_rate")
REQUIRED_WORK_COORD_KEYS = tuple(f"G{n}" for n in range(54, 60)) + ("default_coordinate_system",)
REQUIRED_TOOL_OFFSET_KEYS = ("length_registers", "radius_registers")
REQUIRED_FIXED_CYCLE_GROUPS = ("drilling", "tapping", "boring", "threading")
REQUIRED_DRILLING_CYCLES = ("G81", "G83")
REQUIRED_TAPPING_CYCLES = "G84"
REQUIRED_BORING_CYCLES = ("G86", "G89")
REQUIRED_THREADING_CYCLES = "G76"
REQUIRED_SUBPROGRAM_KEYS = (
    "call_format",
    "end_code",
    "program_number",
    "repeat",
    "macro_variables",
)
COORD_SYSTEMS = tuple(f"G{n}" for n in range(54, 60))
VALID_RETRACT_MODES = ("G98", "G99")
VALID_RETRACT_TYPES = ("rapid", "feed", "oriented")
VALID_SPINDLE_DIRECTIONS = ("M03", "M04")
VALID_DECREMENT_TYPES = ("constant", "variable")
VALID_SHIFT_AXES = (None, "X", "Y")
VALID_INFEED_METHODS = ("compound", "radial", "flank")
