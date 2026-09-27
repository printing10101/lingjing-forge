"""后处理器配置管理 mixin（从 base 拆出）。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class _ConfigMixin:
    # 宿主契约：由主类提供（mypy 需要显式声明）
    config: dict[str, Any]
    limiter: Any

    def _validate_and_complete_config(self) -> None:
        """验证配置完整性并为缺失字段填充默认值。

        检查必需字段是否存在，对缺失的可选字段使用安全默认值，
        并记录警告日志以便用户了解配置不完整的情况。
        """
        # 定义必需字段及其类型
        required_sections = {
            "spindle": dict,
            "feed": dict,
            "work_coordinate": dict,
            "tool_offset": dict,
            "fixed_cycles": dict,
            "subprogram": dict,
        }

        # 检查并补全顶层节
        for section, expected_type in required_sections.items():
            if section not in self.config:
                logger.warning("配置缺少 '%s' 节，将使用默认值。建议检查配置文件完整性。", section)
                self.config[section] = expected_type()
            elif not isinstance(self.config[section], expected_type):
                logger.warning(
                    "配置 '%s' 类型错误: 期望 %s, 实际 %s。将使用空字典作为默认值。",
                    section,
                    expected_type.__name__,
                    type(self.config[section]).__name__,
                )
                self.config[section] = expected_type()

        # 验证并补全 spindle 节
        spindle_defaults = {
            "min_rpm": 50,
            "max_rpm": 24000,
            "default_rpm": 1000,
        }
        self._ensure_keys_with_defaults(self.config["spindle"], spindle_defaults, "spindle")

        # 验证并补全 feed 节
        feed_defaults = {
            "min_rate": 10.0,
            "max_rate": 20000.0,
            "default_rate": 1000.0,
        }
        self._ensure_keys_with_defaults(self.config["feed"], feed_defaults, "feed")

        # 验证并补全 work_coordinate 节
        wcs_defaults = {
            "G54": {},
            "G55": {},
            "G56": {},
            "G57": {},
            "G58": {},
            "G59": {},
            "default_coordinate_system": "G54",
        }
        self._ensure_keys_with_defaults(self.config["work_coordinate"], wcs_defaults, "work_coordinate")

        # 验证并补全 tool_offset 节
        tool_offset_defaults = {
            "length_registers": {"start": 1, "end": 100, "default_offset": 0.0},
            "radius_registers": {
                "start": 1,
                "end": 100,
                "default_offset": 0.0,
                "compensation_types": {
                    "G41": {"register_range": [1, 100]},
                    "G42": {"register_range": [1, 100]},
                },
            },
        }
        self._ensure_keys_with_defaults(self.config["tool_offset"], tool_offset_defaults, "tool_offset")

        # 验证并补全 fixed_cycles 节
        fixed_cycles_defaults: dict[str, dict[str, Any]] = {
            "drilling": {},
            "tapping": {},
            "boring": {},
            "threading": {},
        }
        self._ensure_keys_with_defaults(self.config["fixed_cycles"], fixed_cycles_defaults, "fixed_cycles")

        # 验证并补全 subprogram 节
        subprogram_defaults = {
            "call_format": "M98 P{program_number}",
            "end_code": "M99",
            "program_number": {"minimum": 1, "maximum": 9999, "format": "O"},
            "repeat": {"default": 1, "minimum": 1, "maximum": 999},
            "macro_variables": {
                "local": {"range": [1, 33]},
                "common": {"range": [100, 199]},
                "system": {"range": [500, 599]},
            },
        }
        self._ensure_keys_with_defaults(self.config["subprogram"], subprogram_defaults, "subprogram")

        # 验证顶层基础参数
        top_level_defaults = {
            "decimal_places": 3,
            "safe_z_height": 80.0,
            "rapid_feed": 10000.0,
        }
        for key, default_val in top_level_defaults.items():
            if key not in self.config:
                logger.warning("配置缺少顶层参数 '%s'，使用默认值: %s", key, default_val)
                self.config[key] = default_val

    def _ensure_keys_with_defaults(
        self,
        target: dict,
        defaults: dict,
        section_name: str,
    ) -> None:
        """确保目标字典包含所有必需键，缺失时使用默认值。

        Args:
            target: 要检查和补全的目标字典
            defaults: 默认值字典
            section_name: 配置节名称（用于日志）
        """
        for key, default_val in defaults.items():
            if key not in target:
                logger.warning("配置节 '%s' 缺少参数 '%s'，使用默认值: %s", section_name, key, default_val)
                target[key] = default_val

    def _init_from_config(self) -> None:
        """从配置字典初始化派生参数。"""
        spindle = self.config.get("spindle", {})
        self._spindle_min_rpm = spindle.get("min_rpm", 50)
        self._spindle_max_rpm = spindle.get("max_rpm", 24000)
        self._spindle_default_rpm = spindle.get("default_rpm", 1000)

        feed = self.config.get("feed", {})
        self._feed_min_rate = feed.get("min_rate", 10.0)
        self._feed_max_rate = feed.get("max_rate", 20000.0)
        self._feed_default_rate = feed.get("default_rate", 1000.0)

        wcs = self.config.get("work_coordinate", {})
        self._work_coordinates: dict[str, dict[str, Any]] = {}
        for cs in ("G54", "G55", "G56", "G57", "G58", "G59"):
            self._work_coordinates[cs] = wcs.get(cs, {})
        self._default_coordinate_system = wcs.get("default_coordinate_system", "G54")

    def get_spindle_rpm(self, requested_rpm: float | None = None) -> float:
        """获取限制后的主轴转速。

        Args:
            requested_rpm: 请求的转速，None则返回默认值

        Returns:
            限制后的主轴转速（RPM）
        """
        if self.limiter is not None and requested_rpm is not None:
            return self.limiter.limit_spindle_rpm(requested_rpm, "spindle")
        if requested_rpm is not None:
            return requested_rpm
        if self.limiter is not None:
            return self.limiter.get_spindle_default()
        return float(self._spindle_default_rpm)

    def get_feed_rate(self, requested_feed: float | None = None) -> float:
        """获取限制后的进给速度。

        Args:
            requested_feed: 请求的进给速度，None则返回默认值

        Returns:
            限制后的进给速度（mm/min）
        """
        if self.limiter is not None and requested_feed is not None:
            return self.limiter.limit_feed_rate(requested_feed, "feed")
        if requested_feed is not None:
            return requested_feed
        if self.limiter is not None:
            return self.limiter.get_feed_default()
        return float(self._feed_default_rate)

    def get_work_coordinate(self, system: str = "G54") -> dict[str, Any]:
        """获取指定工件坐标系的配置。

        Args:
            system: 坐标系名称 (G54-G59)

        Returns:
            坐标系配置字典
        """
        cs = system.upper()
        if cs not in self._work_coordinates:
            raise ValueError(f"无效的工件坐标系: {system}，有效值: G54-G59")
        return self._work_coordinates[cs]

    def get_enabled_coordinate_systems(self) -> list:
        """获取所有已启用的工件坐标系列表。"""
        return [cs for cs, cfg in self._work_coordinates.items() if cfg.get("enabled", False)] or [
            self._default_coordinate_system
        ]

    def get_cycle_config(self, group: str, cycle: str) -> dict[str, Any]:
        """获取指定固定循环的配置参数。

        Args:
            group: 循环组名 (drilling/tapping/boring/threading)
            cycle: 循环代码 (G81/G83/G84/G86/G89/G76)

        Returns:
            循环配置字典
        """
        cycles = self.config.get("fixed_cycles", {})
        group_cfg = cycles.get(group, {})
        return group_cfg.get(cycle, {})

    def get_tool_offset_config(self) -> dict[str, Any]:
        """获取刀具补偿寄存器配置。"""
        return self.config.get("tool_offset", {})

    def get_subprogram_config(self) -> dict[str, Any]:
        """获取子程序/宏程序配置。"""
        return self.config.get("subprogram", {})
