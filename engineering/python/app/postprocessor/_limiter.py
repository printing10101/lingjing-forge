"""Postprocessor config submodule (split from config_loader)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ConfigLimiter:
    """配置参数限幅器。

    当进给速度、主轴转速或坐标轴位置超出配置范围时，自动限制在最大/最小值，
    并记录超限日志。坐标轴软限位是安全关键功能，防止机床超程碰撞。
    """

    def __init__(self, config: dict | None = None) -> None:
        config = config or {}
        self._spindle_min = config.get("spindle", {}).get("min_rpm", 50)
        self._spindle_max = config.get("spindle", {}).get("max_rpm", 24000)
        self._spindle_default = config.get("spindle", {}).get("default_rpm", 1000)

        self._feed_min = config.get("feed", {}).get("min_rate", 10.0)
        self._feed_max = config.get("feed", {}).get("max_rate", 20000.0)
        self._feed_default = config.get("feed", {}).get("default_rate", 1000.0)

        # 坐标轴软限位配置（安全关键）
        axis_limits = config.get("axis_limits", {})
        self._axis_limits_enabled = axis_limits.get("enabled", False)
        self._x_min = axis_limits.get("x_min", -1000.0)
        self._x_max = axis_limits.get("x_max", 1000.0)
        self._y_min = axis_limits.get("y_min", -1000.0)
        self._y_max = axis_limits.get("y_max", 1000.0)
        self._z_min = axis_limits.get("z_min", -500.0)
        self._z_max = axis_limits.get("z_max", 500.0)

    def limit_spindle_rpm(self, rpm: float, context: str = "") -> float:
        """限制主轴转速在允许范围内。

        Args:
            rpm: 请求的主轴转速 (RPM)
            context: 调用上下文（用于日志记录）

        Returns:
            限制后的主轴转速
        """
        original = rpm
        if rpm < self._spindle_min:
            rpm = float(self._spindle_min)
            logger.warning(
                "主轴转速超下限 [%s]: 原始值 %.1f RPM -> 限制值 %.1f RPM (min: %d)",
                context,
                original,
                rpm,
                self._spindle_min,
            )
        elif rpm > self._spindle_max:
            rpm = float(self._spindle_max)
            logger.warning(
                "主轴转速超上限 [%s]: 原始值 %.1f RPM -> 限制值 %.1f RPM (max: %d)",
                context,
                original,
                rpm,
                self._spindle_max,
            )
        return rpm

    def limit_feed_rate(self, feed: float, context: str = "") -> float:
        """限制进给速度在允许范围内。

        Args:
            feed: 请求的进给速度 (mm/min)
            context: 调用上下文（用于日志记录）

        Returns:
            限制后的进给速度
        """
        original = feed
        if feed < self._feed_min:
            feed = self._feed_min
            logger.warning(
                "进给速度超下限 [%s]: 原始值 %.2f mm/min -> 限制值 %.2f mm/min (min: %.2f)",
                context,
                original,
                feed,
                self._feed_min,
            )
        elif feed > self._feed_max:
            feed = self._feed_max
            logger.warning(
                "进给速度超上限 [%s]: 原始值 %.2f mm/min -> 限制值 %.2f mm/min (max: %.2f)",
                context,
                original,
                feed,
                self._feed_max,
            )
        return feed

    def limit_axis_position(self, axis: str, position: float, context: str = "") -> float:
        """限制坐标轴位置在软限位范围内。

        安全关键功能：防止机床超程碰撞。当坐标轴位置超出配置的软限位范围时，
        自动限制在最大/最小值，并记录超限日志。

        Args:
            axis: 坐标轴名称 ("X", "Y", 或 "Z")
            position: 请求的坐标轴位置 (mm)
            context: 调用上下文（用于日志记录）

        Returns:
            限制后的坐标轴位置

        Raises:
            ValueError: 当坐标轴名称无效时
        """
        if not self._axis_limits_enabled:
            return position

        axis = axis.upper()
        original = position

        if axis == "X":
            min_val, max_val = self._x_min, self._x_max
        elif axis == "Y":
            min_val, max_val = self._y_min, self._y_max
        elif axis == "Z":
            min_val, max_val = self._z_min, self._z_max
        else:
            raise ValueError(f"无效的坐标轴: {axis}，有效值: X, Y, Z")

        if position < min_val:
            position = min_val
            logger.warning(
                "坐标轴软限位超下限 [%s]: %s轴 原始值 %.3f mm -> 限制值 %.3f mm (min: %.3f)",
                context,
                axis,
                original,
                position,
                min_val,
            )
        elif position > max_val:
            position = max_val
            logger.warning(
                "坐标轴软限位超上限 [%s]: %s轴 原始值 %.3f mm -> 限制值 %.3f mm (max: %.3f)",
                context,
                axis,
                original,
                position,
                max_val,
            )

        return position

    def get_spindle_default(self) -> float:
        return float(self._spindle_default)

    def get_feed_default(self) -> float:
        return float(self._feed_default)
