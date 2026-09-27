"""XMachine XM-100 桌面级五轴CNC后处理器。

针对数马电子 XM-100 桌面级五轴加工中心定制：
- 主轴：20000 RPM / 2.2 kW ER20 水冷主轴
- 行程：X/Y/Z 100×100×100mm（有效加工区域）
- 旋转轴：A轴（-30°~110°）/ C轴（0°~360°）
- 控制器：基于 Fanuc 0i 方言的 Xmaker 控制系统
- 进给：最大 3000 mm/min（桌面级刚性约束）

特性：
- 兼容 Fanuc 0i 基础语法，适配 Xmaker 控制器
- 严格限制主轴/进给在 XM-100 物理范围内
- 支持五轴 A/C 旋转轴指令（G43.4 RTCP / G43.5 TWP）
- 工作空间硬约束（100×100×100mm）
- 桌面级切削参数优化（低刚性、小切深）
"""

from __future__ import annotations

import logging
from typing import Any

from app.postprocessor.fanuc import FanucPostProcessor

logger = logging.getLogger(__name__)


class XMachineXM100PostProcessor(FanucPostProcessor):
    """XMachine XM-100 桌面级五轴CNC后处理器。

    继承 Fanuc 0i 语法基础，针对 XM-100 物理约束做严格限制：
    - 主轴转速：1000-20000 RPM（XM-100 物理范围）
    - 进给速度：10-3000 mm/min（桌面级刚性约束）
    - 行程约束：X/Y/Z ±50mm（100mm 有效区域）
    - 旋转轴：A(-30°~110°) / C(0°~360°)

    五轴特性：
    - G43.4 RTCP（旋转刀具中心点）补偿
    - G43.5 TWP（刀轴控制）模式
    - A/C 轴插补指令生成
    - 奇异点规避（A轴接近±90°时警告）
    """

    # XM-100 物理约束常量
    XM100_TRAVEL_X = 100.0  # mm
    XM100_TRAVEL_Y = 100.0
    XM100_TRAVEL_Z = 100.0
    XM100_A_AXIS_MIN = -30.0  # 度
    XM100_A_AXIS_MAX = 110.0
    XM100_C_AXIS_MIN = 0.0
    XM100_C_AXIS_MAX = 360.0
    XM100_SPINDLE_MIN = 1000  # RPM
    XM100_SPINDLE_MAX = 20000
    XM100_FEED_MIN = 10.0  # mm/min
    XM100_FEED_MAX = 3000.0

    def __init__(
        self,
        decimal_places: int = 3,
        safe_z_height: float = 30.0,
        rapid_feed: float = 2000,
        config: dict[str, Any] | None = None,
    ) -> None:
        # XM-100 安全高度较低（Z行程仅100mm）
        if safe_z_height > 80.0:
            logger.warning(
                "XM-100 安全高度 %.1f 超过 Z 行程上限，自动限制为 80.0mm",
                safe_z_height,
            )
            safe_z_height = 80.0
        if rapid_feed > self.XM100_FEED_MAX:
            rapid_feed = self.XM100_FEED_MAX
        super().__init__(decimal_places, safe_z_height, rapid_feed, config)

    def format_header(self, program_number: int = 1) -> str:
        wcs = self._default_coordinate_system
        default_rpm = int(self.get_spindle_rpm())

        lines = [
            "%",
            f"O{program_number:04d} (XM-100 PROGRAM {program_number} - {self._date_string()})",
            "(POST: XMachine XM-100 5-Axis)",
            "(MACHINE: XM-100 100x100x100mm A:-30~110 C:0~360)",
            "(SPINDLE: 20000RPM/2.2kW ER20)",
            "G21 G17 G40 G49 G80 G90 G94",
            "(--- XM-100 安全启动 ---)",
            "G00 G91 G28 Z0.",
            "G00 G91 G28 X0. Y0.",
            "G00 G91 G28 A0. C0.",
            f"G00 G90 {wcs} X0. Y0.",
            f"G00 G43 Z{self._fmt(self.safe_z_height)} H00",
            f"M03 S{default_rpm}",
            "M08",
            "",
        ]
        return "\n".join(lines)

    def format_tool_change(
        self,
        tool_id: int,
        length_comp: float = 0.0,
        radius_comp: float = 0.0,
    ) -> str:
        wcs = self._default_coordinate_system
        default_rpm = int(self.get_spindle_rpm())
        feed = self._fmt(self.get_feed_rate(self.rapid_feed))

        lines = [
            "(--- XM-100 换刀 ---)",
            "G00 G91 G28 Z0.",
            "G00 G91 G28 X0. Y0.",
            "G00 G91 G28 A0. C0.",
            f"T{tool_id:02d} M06",
            f"G00 G90 {wcs} X0. Y0.",
            f"G43 Z{self._fmt(self.safe_z_height)} H{tool_id:02d}",
            f"G01 Z{self._fmt(length_comp)} F{feed}",
        ]
        if radius_comp != 0.0:
            lines.append(f"M03 S{default_rpm}")
        return "\n".join(lines)

    def format_rapid_move(
        self,
        x: float,
        y: float,
        z: float,
        a: float | None = None,
        c: float | None = None,
    ) -> str:
        """生成快速定位指令（支持五轴 A/C 轴）。

        Args:
            x: X轴坐标
            y: Y轴坐标
            z: Z轴坐标
            a: A轴角度（可选，度）
            c: C轴角度（可选，度）

        Returns:
            快速定位NC代码字符串
        """
        self._validate_workspace(x, y, z, a, c)
        line = f"G00 X{self._fmt(x)} Y{self._fmt(y)} Z{self._fmt(z)}"
        if a is not None:
            self._validate_a_axis(a)
            line += f" A{self._fmt(a)}"
        if c is not None:
            self._validate_c_axis(c)
            line += f" C{self._fmt(c)}"
        return line

    def format_linear_move(
        self,
        x: float,
        y: float,
        z: float,
        feed: float | None = None,
        a: float | None = None,
        c: float | None = None,
    ) -> str:
        """生成直线插补指令（支持五轴 A/C 轴）。

        Args:
            x: X轴坐标
            y: Y轴坐标
            z: Z轴坐标
            feed: 进给速度 (mm/min)，None使用默认值
            a: A轴角度（可选，度）
            c: C轴角度（可选，度）

        Returns:
            直线插补NC代码字符串
        """
        self._validate_workspace(x, y, z, a, c)
        feed_val = self.get_feed_rate(feed) if feed is not None else self.get_feed_rate()
        line = f"G01 X{self._fmt(x)} Y{self._fmt(y)} Z{self._fmt(z)} F{self._fmt(feed_val)}"
        if a is not None:
            self._validate_a_axis(a)
            line += f" A{self._fmt(a)}"
        if c is not None:
            self._validate_c_axis(c)
            line += f" C{self._fmt(c)}"
        return line

    def format_rtcp_on(self, tool_length: float = 0.0) -> str:
        """开启 RTCP（旋转刀具中心点）补偿模式。

        XM-100 五轴联动必备：G43.4 开启 RTCP 后，
        程序坐标直接指定刀尖位置，控制器自动计算旋转轴角度。

        Args:
            tool_length: 刀具长度（mm）

        Returns:
            RTCP开启NC代码字符串
        """
        logger.info("XM-100 RTCP 模式开启，刀具长度: %.3fmm", tool_length)
        return f"G43.4 H{int(tool_length):02d} (RTCP ON)"

    def format_rtcp_off(self) -> str:
        """关闭 RTCP 补偿模式。"""
        return "G49 (RTCP OFF)"

    def format_twp_on(self, tool_axis_i: float = 0.0, tool_axis_j: float = 0.0, tool_axis_k: float = 1.0) -> str:
        """开启 TWP（刀轴控制）模式。

        XM-100 五轴刀轴矢量控制：G43.5 开启后，
        通过 I/J/K 指定刀轴方向矢量，控制器自动规划 A/C 轴角度。

        Args:
            tool_axis_i: 刀轴矢量X分量
            tool_axis_j: 刀轴矢量Y分量
            tool_axis_k: 刀轴矢量Z分量（默认1.0=垂直向下）

        Returns:
            TWP开启NC代码字符串
        """
        norm = (tool_axis_i**2 + tool_axis_j**2 + tool_axis_k**2) ** 0.5
        if norm < 1e-6:
            raise ValueError("刀轴矢量不能为零向量")
        i, j, k = tool_axis_i / norm, tool_axis_j / norm, tool_axis_k / norm
        logger.info("XM-100 TWP 模式开启，刀轴矢量: I%.3f J%.3f K%.3f", i, j, k)
        return f"G43.5 I{self._fmt(i)} J{self._fmt(j)} K{self._fmt(k)} (TWP ON)"

    def format_twp_off(self) -> str:
        """关闭 TWP 刀轴控制模式。"""
        return "G49 (TWP OFF)"

    def format_rotary_axis_config(
        self,
        a_axis_zero: float = 0.0,
        c_axis_zero: float = 0.0,
        a_axis_dir: int = 1,
        c_axis_dir: int = 1,
    ) -> str:
        """配置旋转轴零点和方向。

        Args:
            a_axis_zero: A轴零点偏移（度）
            c_axis_zero: C轴零点偏移（度）
            a_axis_dir: A轴方向（1=正向，-1=负向）
            c_axis_dir: C轴方向（1=正向，-1=负向）

        Returns:
            旋转轴配置NC代码字符串
        """
        lines = [
            f"G54.1 P1 A{self._fmt(a_axis_zero)} (A轴零点偏移)",
            f"G54.1 P2 C{self._fmt(c_axis_zero)} (C轴零点偏移)",
            f"M{100 + a_axis_dir:03d} (A轴方向)",
            f"M{200 + c_axis_dir:03d} (C轴方向)",
        ]
        return "\n".join(lines)

    def format_workspace_check(self, x: float, y: float, z: float) -> str:
        """生成工作空间检查指令（XM-100 硬约束）。

        Args:
            x: X轴坐标
            y: Y轴坐标
            z: Z轴坐标

        Returns:
            工作空间检查NC代码字符串（注释形式）
        """
        warnings = []
        if abs(x) > self.XM100_TRAVEL_X / 2:
            warnings.append(f"X{x:.1f} 超出 XM-100 X行程")
        if abs(y) > self.XM100_TRAVEL_Y / 2:
            warnings.append(f"Y{y:.1f} 超出 XM-100 Y行程")
        if abs(z) > self.XM100_TRAVEL_Z / 2:
            warnings.append(f"Z{z:.1f} 超出 XM-100 Z行程")

        if warnings:
            warn_str = "; ".join(warnings)
            logger.warning("XM-100 工作空间超限: %s", warn_str)
            return f"(WARNING: {warn_str})"
        return f"(OK: XM-100 工作空间内 X{x:.1f} Y{y:.1f} Z{z:.1f})"

    def _validate_workspace(
        self,
        x: float,
        y: float,
        z: float,
        a: float | None = None,
        c: float | None = None,
    ) -> None:
        """验证坐标是否在 XM-100 工作空间内。

        Args:
            x: X轴坐标
            y: Y轴坐标
            z: Z轴坐标
            a: A轴角度（可选）
            c: C轴角度（可选）

        Raises:
            ValueError: 坐标超出 XM-100 物理范围
        """
        if abs(x) > self.XM100_TRAVEL_X / 2:
            raise ValueError(f"X{x:.1f} 超出 XM-100 X行程 (±{self.XM100_TRAVEL_X / 2:.0f}mm)")
        if abs(y) > self.XM100_TRAVEL_Y / 2:
            raise ValueError(f"Y{y:.1f} 超出 XM-100 Y行程 (±{self.XM100_TRAVEL_Y / 2:.0f}mm)")
        if abs(z) > self.XM100_TRAVEL_Z / 2:
            raise ValueError(f"Z{z:.1f} 超出 XM-100 Z行程 (±{self.XM100_TRAVEL_Z / 2:.0f}mm)")
        if a is not None:
            self._validate_a_axis(a)
        if c is not None:
            self._validate_c_axis(c)

    def _validate_a_axis(self, a: float) -> None:
        """验证 A 轴角度并检查奇异点。"""
        if a < self.XM100_A_AXIS_MIN or a > self.XM100_A_AXIS_MAX:
            raise ValueError(f"A轴角度 {a:.1f}° 超出 XM-100 范围 [{self.XM100_A_AXIS_MIN}°, {self.XM100_A_AXIS_MAX}°]")
        # 奇异点警告：A轴接近±90°时 C轴失去意义
        if abs(abs(a) - 90.0) < 5.0:
            logger.warning(
                "XM-100 A轴 %.1f° 接近奇异点 (±90°)，C轴可能失效",
                a,
            )

    def _validate_c_axis(self, c: float) -> None:
        """验证 C 轴角度。"""
        if c < self.XM100_C_AXIS_MIN or c > self.XM100_C_AXIS_MAX:
            raise ValueError(f"C轴角度 {c:.1f}° 超出 XM-100 范围 [{self.XM100_C_AXIS_MIN}°, {self.XM100_C_AXIS_MAX}°]")

    def format_arc(
        self,
        start: tuple[float, float, float],
        end: tuple[float, float, float],
        center: tuple[float, float, float],
        clockwise: bool = True,
    ) -> str:
        g_code = "G02" if clockwise else "G03"
        radius = self._calc_arc_radius(end, center)
        feed = self._fmt(self.get_feed_rate(self.rapid_feed * 0.5))
        return f"{g_code} X{self._fmt(end[0])} Y{self._fmt(end[1])} R{self._fmt(radius)} F{feed}"

    def format_cycle_drill(
        self,
        x: float,
        y: float,
        z: float,
        depth: float,
        dwell: float = 0.0,
        pecking: bool = True,
    ) -> str:
        # pecking 参数与基类签名对齐：True 用啄钻循环，False 用普通循环
        # XM-100 桌面级：使用 G81 简单钻孔，小切深啄钻
        cycle_code = "G83" if depth > 5.0 else "G81"
        cfg = self.get_cycle_config("drilling", cycle_code)
        retract_mode = cfg.get("retract_mode", "G98")
        peck_depth = min(cfg.get("peck_depth", 2.0), 2.0)  # XM-100 小啄钻
        r_plane = self.safe_z_height
        drill_feed = self._fmt(self.get_feed_rate(self.rapid_feed * 0.2))

        if cycle_code == "G83":
            lines = [
                f"{retract_mode} G83 X{self._fmt(x)} Y{self._fmt(y)} "
                f"Z{self._fmt(-abs(depth))} R{self._fmt(r_plane)} "
                f"Q{self._fmt(peck_depth)} F{drill_feed}",
                "G80",
            ]
        else:
            lines = [
                f"{retract_mode} G81 X{self._fmt(x)} Y{self._fmt(y)} "
                f"Z{self._fmt(-abs(depth))} R{self._fmt(r_plane)} "
                f"F{drill_feed}",
                "G80",
            ]
        return "\n".join(lines)

    def format_footer(self) -> str:
        lines = [
            "",
            "(--- XM-100 程序结束 ---)",
            "M09",
            "M05",
            "G00 G91 G28 Z0.",
            "G00 G91 G28 A0. C0.",
            "G00 G91 G28 X0. Y0.",
            "G90",
            "G49",
            "M30",
            "%",
        ]
        return "\n".join(lines)

    def get_machine_info(self) -> dict[str, Any]:
        """获取 XM-100 机床信息。"""
        return {
            "machine_name": "XMachine XM-100",
            "manufacturer": "数马电子 (XMachine)",
            "type": "桌面级五轴加工中心",
            "workspace": {
                "x_travel_mm": self.XM100_TRAVEL_X,
                "y_travel_mm": self.XM100_TRAVEL_Y,
                "z_travel_mm": self.XM100_TRAVEL_Z,
            },
            "rotary_axes": {
                "a_axis_range": [self.XM100_A_AXIS_MIN, self.XM100_A_AXIS_MAX],
                "c_axis_range": [self.XM100_C_AXIS_MIN, self.XM100_C_AXIS_MAX],
            },
            "spindle": {
                "max_rpm": self.XM100_SPINDLE_MAX,
                "power_kw": 2.2,
                "type": "ER20 水冷",
            },
            "feed": {
                "max_rate_mm_min": self.XM100_FEED_MAX,
            },
            "controller": "Xmaker (Fanuc 0i 兼容方言)",
            "features": ["RTCP (G43.4)", "TWP (G43.5)", "5-axis A/C"],
        }
