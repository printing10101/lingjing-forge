"""Fagor 8055 系列 CNC 控制器后处理器。

Fagor Automation 简介：
    - 总部位于西班牙，是欧洲第二大 CNC 控制系统供应商
    - 8055 / 8060 / 8065 / 8070 系列覆盖中高端市场
    - 在欧洲（西班牙、葡萄牙、意大利、法国、德国）使用广泛
    - 语言：西班牙语原版 + 英语版本

Fagor 8055 与 Fanuc 0i 的关键差异：
    1. **程序号**：Fagor 用 ``%xxxxx`` 数字前缀（不是 ``Oxxxx``）
    2. **子程序调用**：Fagor 用 ``CALL Pxxxx``（不是 ``M98 Pxxxx``）
    3. **子程序结束**：Fagor 用 ``RET``（不是 ``M99``）
    4. **圆弧指令**：Fagor 默认 ``I/J/K`` 中心偏移模式
    5. **回参考点**：Fagor 用 ``G75``（固定返回参考点，区别于 Fanuc 的 G28）
    6. **固定循环**：Fagor 用 G81-G89，与 Fanuc 类似
    7. **螺纹循环**：Fagor 8055 用 G76（与 Fanuc 一致；G86 是镗削循环，不是螺纹循环）
    8. **工件坐标系**：Fagor 用 G54-G59（与 Fanuc 兼容）

实现策略：继承 Fanuc 0i，只 override 跟 Fagor 不同的方法。
"""

from __future__ import annotations

from typing import Any

from app.postprocessor.fanuc import FanucPostProcessor


class FagorPostProcessor(FanucPostProcessor):
    """Fagor 8055/8060/8065/8070 系列 CNC 控制器后处理器。"""

    CONTROLLER_ID = "fagor_8055"
    CONTROLLER_NAME = "Fagor 8055 (Fagor Automation)"

    def __init__(
        self,
        decimal_places: int = 3,
        safe_z_height: float = 80.0,
        rapid_feed: float = 10000,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(decimal_places, safe_z_height, rapid_feed, config)

    def format_header(self, program_number: int = 1) -> str:
        """Fagor 风格程序头：用 %xxxxx 数字程序号，G75 替代 G28。"""
        wcs = self._default_coordinate_system
        default_rpm = int(self.get_spindle_rpm())

        lines = [
            # Fagor 特色：% + 5位数字 程序号（必须单独一行）
            f"%{program_number:05d}",
            f"(PROGRAM {program_number} - {self._date_string()})",
            self._paren_comment(f"POST: {self.CONTROLLER_NAME}"),
            "G21 G17 G40 G49 G80 G90 G94",
            # Fagor 特色：G75 = 固定返回参考点
            "G75 Z0.",
            "G75 X0. Y0.",
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
        """Fagor 换刀：Txx + M06（与 Fanuc 类似），但用 G75 替代 G28。"""
        wcs = self._default_coordinate_system
        default_rpm = int(self.get_spindle_rpm())
        feed = self._fmt(self.get_feed_rate(self.rapid_feed))

        lines = [
            "G75 Z0.",
            "G75 X0. Y0.",
            f"T{tool_id:02d} M06",
            f"G00 G90 {wcs} X0. Y0.",
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
        """Fagor 圆弧：I/J/K 中心偏移（与 Fanuc 一致）。"""
        g_code = "G02" if clockwise else "G03"
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
        """Fagor 钻孔循环：G81 简单钻 / G83 啄钻 / G82 沉孔钻。"""
        cycle_code = "G83" if pecking else "G81"
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
        """Fagor 攻丝：G84 同步进给。"""
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

    def format_subprogram_call(
        self,
        program_number: int,
        repeat: int = 1,
    ) -> str:
        """Fagor 子程序调用：CALL Pxxxx (与 Fanuc M98 Pxxxx 不同)。"""
        if repeat <= 1:
            return f"CALL P{program_number:05d}"
        return f"CALL P{program_number:05d}, R{repeat}"

    def format_subprogram_end(
        self,
        return_value: str | None = None,
    ) -> str:
        """Fagor 子程序结束：RET (与 Fanuc M99 不同)。"""
        if return_value:
            return f"RET {return_value}"
        return "RET"

    def format_footer(self) -> str:
        """Fagor 收尾：G75 替代 G28，RET/M30 程序结束。"""
        lines = [
            "",
            "M09",
            "M05",
            "G75 Z0.",
            "G75 X0. Y0.",
            "G90",
            "M30",
        ]
        return "\n".join(lines)


__all__ = ["FagorPostProcessor"]
