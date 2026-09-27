"""北京凯恩帝（KND）/ 南京肯纳 KND1000/2000/3000 国产 CNC 控制器后处理器。

KND 简介：
    - KND1000 是普及型 3 轴铣床
    - KND2000 是 5 轴加工中心
    - KND3000 是车铣复合
    - 整体是 Fanuc 0i 兼容方言

KND 与 Fanuc 0i 的关键差异：
    1. **程序头**：KND 风格注释更紧凑
    2. **回参考点**：用 G91 G28 标准
    3. **圆弧指令**：支持 I/J/K 和 R 两种模式（默认 R 模式，跟 Fanuc 一致）
    4. **固定循环**：G73/G83/G81 标准 Fanuc
    5. **换刀**：Txx M06 + G43 Hxx
    6. **冷却液**：M08/M09，但 ``M07`` 是切削液+冷却液联动
    7. **子程序**：M98 Pxxxx Lx + M99

实现策略：继承 Fanuc 0i，只 override 跟 KND 不同的方法。
"""

from __future__ import annotations

from typing import Any

from app.postprocessor.fanuc import FanucPostProcessor


class KNDPostProcessor(FanucPostProcessor):
    """北京凯恩帝 KND1000/2000/3000 后处理器。"""

    CONTROLLER_ID = "knd_1000_2000_3000"
    CONTROLLER_NAME = "KND1000/2000/3000 (Kainde CNC)"

    def __init__(
        self,
        decimal_places: int = 3,
        safe_z_height: float = 80.0,
        rapid_feed: float = 10000,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(decimal_places, safe_z_height, rapid_feed, config)

    def format_header(self, program_number: int = 1) -> str:
        """KND 风格程序头：标准 G28 风格。"""
        wcs = self._default_coordinate_system
        default_rpm = int(self.get_spindle_rpm())

        lines = [
            "%",
            f"O{program_number:04d} "
            + self._paren_comment(f"{self.CONTROLLER_NAME} PROGRAM {program_number} - {self._date_string()}"),
            "G21 G17 G40 G49 G80 G90 G94",
            "G00 G91 G28 Z0.",
            "G00 G91 G28 X0. Y0.",
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
        """KND 换刀：标准 G28 风格。"""
        wcs = self._default_coordinate_system
        default_rpm = int(self.get_spindle_rpm())
        feed = self._fmt(self.get_feed_rate(self.rapid_feed))

        lines = [
            "G00 G91 G28 Z0.",
            "G00 G91 G28 X0. Y0.",
            f"T{tool_id:02d} M06",
            f"G00 G90 {wcs} X0. Y0.",
            f"G43 Z{self._fmt(self.safe_z_height)} H{tool_id:02d}",
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
        """KND 圆弧：默认 R 模式（跟 Fanuc 一致）。"""
        g_code = "G02" if clockwise else "G03"
        radius = self._calc_arc_radius(end, center)
        feed = self._fmt(self.get_feed_rate(self.rapid_feed))
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
        """KND 啄钻：与 Fanuc G73/G83 完全一致。"""
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
        """KND 攻丝：与 Fanuc G84 一致。"""
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
        """KND 收尾：标准 G28 风格。"""
        lines = [
            "",
            "M09",
            "M05",
            "G00 G91 G28 Z0.",
            "G00 G91 G28 X0. Y0.",
            "G90",
            "M30",
            "%",
        ]
        return "\n".join(lines)


__all__ = ["KNDPostProcessor"]
