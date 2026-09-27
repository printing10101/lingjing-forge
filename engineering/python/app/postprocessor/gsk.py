"""广州数控 GSK980 / GSK25i 国产 CNC 控制器后处理器。

GSK 简介：
    - 980 / 988 系列是国产 3 轴铣床普及型
    - 25i 是带 5 轴功能的高端型号
    - 整体是 Fanuc 0i 兼容方言 + 国产特色

GSK 与 Fanuc 0i 的关键差异：
    1. **程序头**：GSK 用 ``%`` + ``Oxxxx`` 兼容 Fanuc，但增加 ``(GSK)`` 注释
    2. **回参考点指令**：GSK 用 ``G91 G28 Z0.`` + ``G91 G30 X0.Y0.``（更标准的 G30）
    3. **圆弧指令**：GSK 默认支持 I/J/K 模式（中心坐标），R 模式要 ``R`` 前置符
    4. **固定循环**：GSK 980 用 ``Q`` 模式表示啄钻深度（与 Fanuc 一致），但
       GSK 25i 允许 ``G98/G99`` 之外使用 ``G98.x`` 系列中间返回高度
    5. **换刀**：GSK 用 ``Txx M06`` + ``G43 Hxx``，但要显式 ``G90 G54``
    6. **冷却液**：GSK 的 ``M08/M09`` 与 Fanuc 一致，但支持 ``M07`` 雾冷
    7. **子程序**：用 ``M98 Pxxxx Lx``（与 Fanuc 一致）
    8. **宏程序**：使用 ``#1`` 风格（与 Fanuc 一致），GSK 25i 还支持 ``IF/THEN``

实现策略：继承 Fanuc 0i，只 override 跟 GSK 不同的方法。
"""

from __future__ import annotations

from typing import Any

from app.postprocessor.fanuc import FanucPostProcessor


class GSKPostProcessor(FanucPostProcessor):
    """广州数控 GSK 980 / 25i 系列后处理器。"""

    CONTROLLER_ID = "gsk_980_25i"
    CONTROLLER_NAME = "GSK 980/25i (Guangzhou CNC)"

    def __init__(
        self,
        decimal_places: int = 3,
        safe_z_height: float = 80.0,
        rapid_feed: float = 10000,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(decimal_places, safe_z_height, rapid_feed, config)

    def format_header(self, program_number: int = 1) -> str:
        """GSK 风格程序头：包含 GSK 特定的安全启动顺序。"""
        wcs = self._default_coordinate_system
        default_rpm = int(self.get_spindle_rpm())

        lines = [
            "%",
            f"O{program_number:04d} (PROGRAM {program_number} - {self._date_string()})",
            self._paren_comment(f"POST: {self.CONTROLLER_NAME}"),
            "G21 G17 G40 G49 G80 G90 G94",
            # GSK 特色：先回 Z 再回 G30 平面
            "G00 G91 G28 Z0.",
            "G00 G91 G30 X0. Y0.",
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
        """GSK 换刀：必须显式声明 G90 G54。"""
        wcs = self._default_coordinate_system
        default_rpm = int(self.get_spindle_rpm())
        feed = self._fmt(self.get_feed_rate(self.rapid_feed))

        lines = [
            "G00 G91 G28 Z0.",
            "G00 G91 G30 X0. Y0.",
            f"T{tool_id:02d} M06",
            f"G00 G90 {wcs}",
            "X0. Y0.",
            f"G00 G43 Z{self._fmt(self.safe_z_height)} H{tool_id:02d}",
            f"G01 Z{self._fmt(length_comp)} F{feed}",
        ]
        if radius_comp != 0.0:
            lines.append(f"M03 S{default_rpm}")
        return "\n".join(lines)

    def format_arc(
        self,
        start: tuple[float, float, float],
        end: tuple[float, float, float],
        center: tuple[float, float, float],
        clockwise: bool = True,
    ) -> str:
        """GSK 圆弧优先 I/J/K 模式（中心坐标），R 模式需显式标注。"""
        g_code = "G02" if clockwise else "G03"
        # I/J/K 是相对当前位置的偏移
        i = center[0] - start[0]
        j = center[1] - start[1]
        feed = self._fmt(self.get_feed_rate(self.rapid_feed))
        return f"{g_code} X{self._fmt(end[0])} Y{self._fmt(end[1])} I{self._fmt(i)} J{self._fmt(j)} F{feed}"

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
        """GSK 啄钻：Q 表示每次下钻深度。"""
        cycle_code = "G73" if dwell > 0 else "G83"
        cfg = self.get_cycle_config("drilling", cycle_code)
        retract_mode = cfg.get("retract_mode", "G98")
        peck_depth = cfg.get("peck_depth", 5.0)
        r_plane = self.safe_z_height
        drill_feed = self._fmt(self.get_feed_rate(self.rapid_feed * 0.3))

        if dwell > 0:
            dwell_ms = int(dwell * 1000)
            lines = [
                f"{retract_mode} {cycle_code} X{self._fmt(x)} Y{self._fmt(y)} "
                f"Z{self._fmt(-abs(depth))} R{self._fmt(r_plane)} "
                f"Q{self._fmt(peck_depth)} P{dwell_ms} "
                f"F{drill_feed}",
                "G80",
            ]
        else:
            lines = [
                f"{retract_mode} {cycle_code} X{self._fmt(x)} Y{self._fmt(y)} "
                f"Z{self._fmt(-abs(depth))} R{self._fmt(r_plane)} "
                f"Q{self._fmt(peck_depth)} "
                f"F{drill_feed}",
                "G80",
            ]
        return "\n".join(lines)

    def format_cycle_tapping(
        self,
        x: float,
        y: float,
        z: float,
        depth: float,
        pitch: float = 1.0,
        spindle_rpm: float | None = None,
    ) -> str:
        """GSK 攻丝：与 Fanuc 类似但固定用 G99。"""
        cfg = self.get_cycle_config("tapping", "G84")
        rpm = self.get_spindle_rpm(spindle_rpm)
        spindle_dir = cfg.get("spindle_direction", "M03")
        r_plane = self.safe_z_height
        feed_per_rev = cfg.get("feed_per_rev", True)
        tap_feed = pitch if feed_per_rev else pitch * rpm
        dwell_ms = int(cfg.get("dwell_time", 0.0) * 1000)

        lines = [
            f"{spindle_dir} S{int(rpm)}",
            f"G99 G84 X{self._fmt(x)} Y{self._fmt(y)} "
            f"Z{self._fmt(-abs(depth))} R{self._fmt(r_plane)} "
            f"F{self._fmt(tap_feed)}",
        ]
        if dwell_ms > 0:
            lines[-1] += f" P{dwell_ms}"
        lines.append("G80")
        return "\n".join(lines)

    def format_footer(self) -> str:
        """GSK 收尾：与 Fanuc 类似但 G30 替代 G28。"""
        lines = [
            "",
            "M09",
            "M05",
            "G00 G91 G28 Z0.",
            "G00 G91 G30 X0. Y0.",
            "G90",
            "M30",
            "%",
        ]
        return "\n".join(lines)


__all__ = ["GSKPostProcessor"]
