"""华中数控 HNC-848 / HNC-22 国产 CNC 控制器后处理器。

HNC 简介：
    - 848 是华中数控高端 5 轴铣床 / 复合加工中心系统
    - 22 是普及型 3 轴铣床
    - 整体走 ISO 标准 + 部分 Siemens 风格

HNC 与 Fanuc 0i 的关键差异：
    1. **程序头**：HNC 用 ``%`` + ``Oxxxx``（兼容 Fanuc），但增加 ``(HNC)`` 注释
    2. **回参考点**：HNC 用 ``G91 G74 Z0`` 风格（注意是 G74 不是 G28）
    3. **坐标系**：HNC 的 G54-G59 与 Fanuc 一样，但支持 G54.1 扩展坐标系
    4. **圆弧指令**：HNC 默认用 I/J/K（中心坐标），R 模式要 ``R+/-`` 显式
    5. **固定循环**：
        - HNC 用 G73 啄钻（高速浅钻），G83 深孔啄钻（与 Fanuc 一致）
        - **G74/G84**：HNC G74 端面切槽，G84 攻丝（与 Fanuc 一样）
    6. **换刀**：HNC 用 ``Txx M06`` + ``G43 Hxx``，但要显式 ``G90 G54``
    7. **冷却液**：HNC 的 ``M08/M09`` + ``M07`` 雾冷
    8. **子程序**：HNC 用 ``M98 Pxxxx Lx``，但 ``M99`` 必须独占一行
    9. **复合循环**：HNC 支持 ``G71/G72/G73`` 粗车循环（车削类），但铣床不用
    10. **中文注释**：HNC 控制器支持中文（含 GBK 编码），可输出中文说明

实现策略：继承 Fanuc 0i，只 override 跟 HNC 不同的方法。
"""

from __future__ import annotations

from typing import Any

from app.postprocessor.fanuc import FanucPostProcessor


class HNCPostProcessor(FanucPostProcessor):
    """华中数控 HNC-848 / HNC-22 后处理器。"""

    CONTROLLER_ID = "hnc_848_22"
    CONTROLLER_NAME = "HNC-848/22 (Huazhong CNC)"

    def __init__(
        self,
        decimal_places: int = 3,
        safe_z_height: float = 80.0,
        rapid_feed: float = 10000,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(decimal_places, safe_z_height, rapid_feed, config)

    def format_header(self, program_number: int = 1) -> str:
        """HNC 风格程序头：包含 G74 回零指令。"""
        wcs = self._default_coordinate_system
        default_rpm = int(self.get_spindle_rpm())

        lines = [
            "%",
            f"O{program_number:04d} (PROGRAM {program_number} - {self._date_string()})",
            self._paren_comment(f"POST: {self.CONTROLLER_NAME}"),
            "G21 G17 G40 G49 G80 G90 G94",
            # HNC 特色：G74 Z0（不是 G28）
            "G00 G91 G74 Z0.",
            "G00 G91 G74 X0. Y0.",
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
        """HNC 换刀：G74 回零。"""
        wcs = self._default_coordinate_system
        default_rpm = int(self.get_spindle_rpm())
        feed = self._fmt(self.get_feed_rate(self.rapid_feed))

        lines = [
            "G00 G91 G74 Z0.",
            "G00 G91 G74 X0. Y0.",
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
        """HNC 圆弧：I/J/K 中心坐标模式。"""
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
        """HNC 啄钻：与 Fanuc G73/G83 一致。"""
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
        """HNC 攻丝：G84 标准。"""
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

    def format_subprogram_end(
        self,
        return_value: str | None = None,
    ) -> str:
        """HNC 子程序结束：M99 必须独占一行（不接 P）。"""
        if return_value:
            return f"M99\nP{return_value}"
        return "M99"

    def format_footer(self) -> str:
        """HNC 收尾：G74 风格回零。"""
        lines = [
            "",
            "M09",
            "M05",
            "G00 G91 G74 Z0.",
            "G00 G91 G74 X0. Y0.",
            "G90",
            "M30",
            "%",
        ]
        return "\n".join(lines)


__all__ = ["HNCPostProcessor"]
