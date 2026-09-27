"""Mitsubishi M70/M80 系列 CNC 控制器后处理器。

Mitsubishi 简介：
    - M70 / M80 是日本三菱电机的高端 CNC 控制器
    - 与 Fanuc 0i 高度兼容（方言接近 90%）
    - 在亚太（日本、韩国、台湾、中国大陆）使用广泛

Mitsubishi 与 Fanuc 0i 的关键差异：
    1. **程序头**：Mitsubishi 用 ``%`` + ``Oxxxx``（与 Fanuc 一致），但增加
       ``(MITSUBISHI M70/M80)`` 注释明确控制器型号
    2. **回参考点指令**：M70/M80 用 ``G91 G28 Z0.`` + ``G91 G28 X0.Y0.``（与 Fanuc 一致）
    3. **圆弧指令**：M70/M80 默认支持 ``I/J/K`` 中心坐标模式，R 模式需显式 ``R``
    4. **固定循环**：M70/M80 用与 Fanuc 相同的 G73/G81/G83/G84/G86/G89 循环，
       但 G83 啄钻深度 Q 写法与 Fanuc 兼容
    5. **换刀**：M70/M80 用 ``Txx M06`` + ``G43 Hxx``（与 Fanuc 一致）
    6. **冷却液**：M70/M80 的 ``M08/M09`` 与 Fanuc 一致，但 ``M07`` 是雾冷
    7. **子程序**：用 ``M98 Pxxxx Lx``（与 Fanuc 一致）
    8. **特色指令**：M70/M80 独有 ``G300`` 高速高精度模式（AI 轮廓控制）、
       ``G05.1 Q1`` AI 先行控制（High-Speed High-Accuracy）

实现策略：继承 Fanuc 0i，只 override 跟 Mitsubishi 不同的方法。
"""

from __future__ import annotations

from typing import Any

from app.postprocessor.fanuc import FanucPostProcessor


class MitsubishiPostProcessor(FanucPostProcessor):
    """Mitsubishi M70/M80 系列 CNC 控制器后处理器。"""

    CONTROLLER_ID = "mitsubishi_m70_m80"
    CONTROLLER_NAME = "Mitsubishi M70/M80"

    def __init__(
        self,
        decimal_places: int = 3,
        safe_z_height: float = 80.0,
        rapid_feed: float = 10000,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(decimal_places, safe_z_height, rapid_feed, config)

    def format_header(self, program_number: int = 1) -> str:
        """Mitsubishi 风格程序头：M70/M80 启用 AI 先行控制 G05.1。"""
        wcs = self._default_coordinate_system
        default_rpm = int(self.get_spindle_rpm())

        lines = [
            "%",
            f"O{program_number:04d} (PROGRAM {program_number} - {self._date_string()})",
            self._paren_comment(f"POST: {self.CONTROLLER_NAME}"),
            # Mitsubishi 特色：G05.1 Q1 启动 AI 先行控制
            "G21 G17 G40 G49 G80 G90 G94",
            "G05.1 Q1",
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
        """Mitsubishi 换刀：与 Fanuc 一致，但 length_comp 应用于 H 寄存器。"""
        wcs = self._default_coordinate_system
        default_rpm = int(self.get_spindle_rpm())
        feed = self._fmt(self.get_feed_rate(self.rapid_feed))

        lines = [
            "G00 G91 G28 Z0.",
            "G00 G91 G28 X0. Y0.",
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
        """Mitsubishi 圆弧：优先 I/J/K（中心偏移），R 模式可用。"""
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
        # pecking 参数与基类签名对齐：True 用啄钻循环，False 用普通循环
        """Mitsubishi 啄钻：与 Fanuc 一致，Q 表示每次下钻深度。"""
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
        """Mitsubishi 攻丝：G84 同步进给，与 Fanuc 兼容。"""
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
        """Mitsubishi 收尾：关闭 AI 先行控制，回参考点，程序结束。"""
        lines = [
            "",
            "M09",
            "M05",
            "G00 G91 G28 Z0.",
            "G00 G91 G28 X0. Y0.",
            # Mitsubishi 特色：关闭 AI 先行控制
            "G05.1 Q0",
            "G90",
            "M30",
            "%",
        ]
        return "\n".join(lines)


__all__ = ["MitsubishiPostProcessor"]
