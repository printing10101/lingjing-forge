"""Fanuc 0i/18i/31i系列CNC控制器后处理器。

实现Fanuc 0i系列控制器特有的G代码方言，包括：
- G43/G44刀具长度补偿
- G41/G42刀具半径补偿
- G02/G03圆弧插补（R半径模式）
- G73高速深孔啄钻 / G81/G83钻孔循环
- G84攻丝循环（主轴同步）
- G86/G89镗孔循环
- G76精镗/螺纹加工循环
- M98/M99子程序调用
- 宏变量 #1-#33 / #100-#199 / #1000+# 支持
- M03/M04/M05主轴控制
- M08/M09冷却液控制
"""

from __future__ import annotations

import logging
from typing import Any

from app.postprocessor.base import BasePostProcessor

logger = logging.getLogger(__name__)


class FanucPostProcessor(BasePostProcessor):
    """Fanuc 0i/18i/31i系列CNC控制器后处理器。

    生成符合Fanuc 0i/18i/31i语法规范的G代码。
    支持G54-G59工件坐标系、G76螺纹加工、M98/M99子程序。
    """

    def __init__(
        self,
        decimal_places: int = 3,
        safe_z_height: float = 80.0,
        rapid_feed: float = 10000,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(decimal_places, safe_z_height, rapid_feed, config)

    def format_header(self, program_number: int = 1) -> str:
        wcs = self._default_coordinate_system
        default_rpm = int(self.get_spindle_rpm())

        lines = [
            "%",
            f"O{program_number:04d} (PROGRAM {program_number} - {self._date_string()})",
            "(POST: Fanuc 0i-MF)",
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
        wcs = self._default_coordinate_system
        default_rpm = int(self.get_spindle_rpm())
        feed = self._fmt(self.get_feed_rate(self.rapid_feed))

        lines = [
            "G00 G91 G28 Z0.",
            "G00 G91 G28 X0. Y0.",
            f"T{tool_id:02d} M06",
            f"G00 G90 {wcs} X0. Y0.",
            f"G43 Z{self._fmt(self.safe_z_height)} H{tool_id:02d}",
            f"G01 Z{self._fmt(self.safe_z_height)} F{feed}",
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
        g_code = "G02" if clockwise else "G03"
        radius = self._calc_arc_radius(end, center)
        feed = self._fmt(self.get_feed_rate(self.rapid_feed))
        return f"{g_code} X{self._fmt(end[0])} Y{self._fmt(end[1])} R{self._fmt(radius)} F{feed}"

    def format_tool_compensation(
        self,
        length_offset: int = 0,
        radius_offset: int = 0,
    ) -> str:
        lines = []
        if length_offset > 0:
            lines.append(f"G43 H{length_offset:02d}")
        if radius_offset > 0:
            lines.append(f"G41 D{radius_offset:02d}")
        return "\n".join(lines) if lines else "G49 G40"

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
        # When a dwell is requested, use the high-speed peck drill
        # cycle G73 (small retract for chip breaking). Without a
        # dwell the deep-hole peck cycle G83 (full retract) is used.
        cycle_code = "G73" if dwell > 0 else "G83"
        cfg = self.get_cycle_config("drilling", cycle_code)
        retract_mode = cfg.get("retract_mode", "G98")
        peck_depth = cfg.get("peck_depth", 5.0)
        _retract_dist = cfg.get("retract_distance", 1.0)
        r_plane = self.safe_z_height
        drill_feed = self._fmt(self.get_feed_rate(self.rapid_feed * 0.3))

        if dwell > 0:
            dwell_ms = int(dwell * 1000)
            lines = [
                f"{retract_mode} {cycle_code} X{self._fmt(x)} Y{self._fmt(y)} "
                f"Z{self._fmt(z)} R{self._fmt(r_plane)} "
                f"Q{self._fmt(peck_depth)} P{dwell_ms} "
                f"F{drill_feed}",
                "G80",
            ]
        else:
            lines = [
                f"{retract_mode} {cycle_code} X{self._fmt(x)} Y{self._fmt(y)} "
                f"Z{self._fmt(z)} R{self._fmt(r_plane)} "
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
        cfg = self.get_cycle_config("tapping", "G84")
        rpm = self.get_spindle_rpm(spindle_rpm)
        spindle_dir = cfg.get("spindle_direction", "M03")
        r_plane = self.safe_z_height
        feed_per_rev = cfg.get("feed_per_rev", True)

        if feed_per_rev:
            tap_feed = pitch
        else:
            tap_feed = pitch * rpm

        dwell_ms = int(cfg.get("dwell_time", 0.0) * 1000)

        lines = [
            f"{spindle_dir} S{int(rpm)}",
            f"G99 G84 X{self._fmt(x)} Y{self._fmt(y)} Z{self._fmt(z)} R{self._fmt(r_plane)} F{self._fmt(tap_feed)}",
        ]
        if dwell_ms > 0:
            lines[-1] += f" P{dwell_ms}"

        lines.append("G80")
        return "\n".join(lines)

    def format_cycle_boring(
        self,
        x: float,
        y: float,
        z: float,
        depth: float,
        cycle_type: str = "G86",
        dwell: float = 0.5,
    ) -> str:
        cfg = self.get_cycle_config("boring", cycle_type)
        retract_mode = cfg.get("retract_mode", "G98")
        _retract_type = cfg.get("retract_type", "rapid")
        r_plane = self.safe_z_height
        bore_feed = self._fmt(self.get_feed_rate(self.rapid_feed * 0.15))
        dwell_ms = int(dwell * 1000)

        lines = []
        if cycle_type == "G86":
            lines.append(
                f"{retract_mode} G86 X{self._fmt(x)} Y{self._fmt(y)} Z{self._fmt(z)} R{self._fmt(r_plane)} F{bore_feed}"
            )
            if dwell_ms > 0:
                lines[-1] += f" P{dwell_ms}"
        elif cycle_type == "G89":
            lines.append(
                f"{retract_mode} G89 X{self._fmt(x)} Y{self._fmt(y)} "
                f"Z{self._fmt(z)} R{self._fmt(r_plane)} "
                f"P{dwell_ms} F{bore_feed}"
            )
        else:
            lines.append(
                f"{retract_mode} G86 X{self._fmt(x)} Y{self._fmt(y)} Z{self._fmt(z)} R{self._fmt(r_plane)} F{bore_feed}"
            )

        lines.append("G80")
        return "\n".join(lines)

    def format_cycle_threading(
        self,
        x: float,
        y: float,
        depth: float,
        lead: float = 1.0,
        passes: int | None = None,
        depth_cut_first: float | None = None,
        depth_cut_last: float | None = None,
        finishing_passes: int | None = None,
        tool_angle: float | None = None,
        taper: float | None = None,
    ) -> str:
        cfg = self.get_cycle_config("threading", "G76")

        _p = passes if passes is not None else cfg.get("passes", 5)
        d_first = depth_cut_first if depth_cut_first is not None else cfg.get("depth_cut_first", 0.2)
        d_last = depth_cut_last if depth_cut_last is not None else cfg.get("depth_cut_last", 0.05)
        _finish = finishing_passes if finishing_passes is not None else cfg.get("finishing_passes", 2)
        angle = tool_angle if tool_angle is not None else cfg.get("tool_angle", 60.0)
        taper_val = taper if taper is not None else cfg.get("taper", 0.0)
        _shift_axis = cfg.get("shift_axis", "X")
        _shift_dist = cfg.get("shift_distance", 0.1)

        r_plane = self.safe_z_height
        retract_mode = cfg.get("retract_mode", "G99")
        infeed = cfg.get("infeed_method", "compound")

        infeed_map = {"compound": 1, "radial": 2, "flank": 3}
        infeed_code = infeed_map.get(infeed, 1)

        _thread_feed = self._fmt(self.get_feed_rate(self.rapid_feed * 0.05))

        lines = [
            f"{retract_mode} G76 X{self._fmt(x)} Y{self._fmt(y)} "
            f"Z{self._fmt(-abs(depth))} R{self._fmt(r_plane)} "
            f"P{infeed_code}{int(angle):02d}{int(taper_val * 10):02d}"
            f"Q{self._fmt(d_first)} R{self._fmt(d_last)} "
            f"F{self._fmt(lead)}",
            "G80",
        ]
        return "\n".join(lines)

    def format_cycle_groove(
        self,
        x: float,
        z: float,
        depth: float,
        width: float = 3.0,
        retract: float = 0.5,
        finish_allowance: float = 0.1,
    ) -> str:
        """生成切槽循环 (G75)。

        用于外径切槽、端面切槽等加工。

        Args:
            x: 切槽直径 (X轴坐标)
            z: 切槽Z向位置
            depth: 切槽深度 (半径值)
            width: 切槽宽度 (mm)
            retract: 每次切削后退量 (mm)
            finish_allowance: 精加工余量 (mm)

        Returns:
            G75切槽循环NC代码
        """
        cfg = self.get_cycle_config("grooving", "G75")
        retract_val = cfg.get("retract_amount", retract)
        finish_allow = cfg.get("finish_allowance", finish_allowance)

        groove_feed = self._fmt(self.get_feed_rate(self.rapid_feed * 0.1))

        # G75 外径切槽循环格式
        # G75 R(e)
        # G75 X(U) Z(W) P(Δi) Q(Δk) R(Δd) F_
        lines = [
            f"G00 X{self._fmt(x)} Z{self._fmt(z)}",
            f"G75 R{self._fmt(retract_val)}",
            f"G75 X{self._fmt(x - 2 * depth)} Z{self._fmt(z - width)} "
            f"P{int(depth * 1000)} Q{int(retract_val * 1000)} "
            f"R{self._fmt(finish_allow)} F{groove_feed}",
            "G80",
        ]
        return "\n".join(lines)

    def format_cycle_thread_turning(
        self,
        x: float,
        z: float,
        depth: float,
        pitch: float = 1.0,
        passes: int = 5,
        first_depth: float = 0.2,
        last_depth: float = 0.05,
        finishing_passes: int = 2,
        tool_angle: float = 60.0,
    ) -> str:
        """生成车削螺纹循环 (G92)。

        用于公制/英制螺纹加工。

        Args:
            x: 螺纹小径 (X轴坐标)
            z: 螺纹终点Z坐标
            depth: 螺纹深度 (半径值)
            pitch: 螺距 (mm)
            passes: 切削次数
            first_depth: 第一次切深 (mm)
            last_depth: 最后一次切深 (mm)
            finishing_passes: 精加工次数
            tool_angle: 刀具角度 (度)

        Returns:
            G92螺纹循环NC代码
        """
        # G92 螺纹切削循环格式
        # G92 X(U) Z(W) F_
        # 多次切削需要多行G92指令
        lines = [
            f"G00 X{self._fmt(x + 2.0)} Z{self._fmt(z + 5.0)}",  # 快速定位到起始点上方
            f"S{int(self.get_spindle_rpm())} M03",
        ]

        # 计算每次切深
        current_x = x + 2.0 * depth  # 从外径开始

        for i in range(passes):
            if i < passes - 1:
                # 递减切深
                if i == 0:
                    cut_depth = first_depth
                else:
                    # 逐渐递减
                    cut_depth = first_depth * (1.0 - 0.15 * i)
                    cut_depth = max(cut_depth, last_depth)
                current_x = x + 2.0 * (depth - cut_depth)
            else:
                # 最后一次切削到目标深度
                current_x = x

            lines.append(f"G92 X{self._fmt(current_x)} Z{self._fmt(z)} F{self._fmt(pitch)}")

        # 精加工
        for _ in range(finishing_passes):
            lines.append(f"G92 X{self._fmt(x)} Z{self._fmt(z)} F{self._fmt(pitch)}")

        lines.append("G80")
        return "\n".join(lines)

    def format_subprogram_call(
        self,
        program_number: int,
        repeat: int = 1,
    ) -> str:
        sub_cfg = self.get_subprogram_config()
        prog_cfg = sub_cfg.get("program_number", {})
        repeat_cfg = sub_cfg.get("repeat", {})

        prog_min = prog_cfg.get("minimum", 1)
        prog_max = prog_cfg.get("maximum", 9999)
        rep_min = repeat_cfg.get("minimum", 1)
        rep_max = repeat_cfg.get("maximum", 9999)

        program_number = max(prog_min, min(prog_max, program_number))
        repeat = max(rep_min, min(rep_max, repeat))

        call_fmt = sub_cfg.get("call_format", "M98 P{program_num:04d} L{repeat}")

        try:
            formatted = call_fmt.format(program_num=program_number, repeat=repeat)
        except KeyError:
            formatted = f"M98 P{program_number:04d}"
            if repeat > 1:
                formatted += f" L{repeat}"

        return formatted

    def format_subprogram_end(
        self,
        return_value: str | None = None,
    ) -> str:
        sub_cfg = self.get_subprogram_config()
        end_code = sub_cfg.get("end_code", "M99")

        if return_value:
            return f"{end_code} P{return_value}"
        return end_code

    def format_footer(self) -> str:
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

    def format_high_precision_mode(self, enable: bool = True, mode: int = 1) -> str:
        """生成高精度加工模式指令（G05.1 Q1）。

        Fanuc 0i/18i/31i 系列控制器的 AI 轮廓控制功能，
        用于提升曲面加工精度和表面质量。

        Args:
            enable: True 开启高精度模式，False 关闭
            mode: 模式选择，1=标准 AI 轮廓控制，2=AI 轮廓控制+预读

        Returns:
            高精度模式 NC 代码字符串
        """
        if enable:
            return f"G05.1 Q{mode}"
        else:
            return "G05.1 Q0"
