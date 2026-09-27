"""Heidenhain 核心格式化 mixin（从 heidenhain 拆出）。"""

from __future__ import annotations

import logging
from typing import Any
from collections.abc import Callable

logger = logging.getLogger(__name__)


class _HeidenhainCoreMixin:
    # 宿主契约：由兄弟 mixin / 基类提供（mypy 需要显式声明）
    _block_counter: int
    _last_program_number: int
    _next_block: Callable[[], int]
    _fmt: Callable[[float], str]
    _date_string: Callable[[], str]
    rapid_feed: float
    safe_z_height: float
    get_feed_rate: Callable[..., float]
    get_spindle_rpm: Callable[..., float]
    get_subprogram_config: Callable[..., Any]
    get_cycle_config: Callable[..., dict[str, Any]]

    def format_header(self, program_number: int = 1) -> str:
        self._block_counter = 0
        self._last_program_number = program_number  # 记录供 format_footer 使用
        default_rpm = int(self.get_spindle_rpm())

        lines = [
            f"0  BEGIN PGM {program_number:04d} MM",
            "1  BLK FORM 0.1 Z X+0 Y+0 Z-50",
            "2  BLK FORM 0.2 X+100 Y+100 Z+0",
            f"3  ; PROGRAM {program_number} - {self._date_string()}",
            "4  ; POST: Heidenhain TNC",
            f"5  TOOL CALL 1 Z S{default_rpm}",
            f"6  L  Z+{self._fmt(self.safe_z_height)} R0 FMAX",
            "7  L  X+0 Y+0 R0 FMAX",
            "8  M08",
            "",
        ]
        return "\n".join(lines)

    def format_tool_change(
        self,
        tool_id: int,
        length_comp: float = 0.0,
        radius_comp: float = 0.0,
    ) -> str:
        default_rpm = int(self.get_spindle_rpm())

        n = self._next_block()
        lines = [
            f"{n}  TOOL CALL {tool_id} Z S{default_rpm}",
            f"{self._next_block()}  L  Z+{self._fmt(self.safe_z_height)} R0 FMAX",
            f"{self._next_block()}  L  X+0 Y+0 R0 FMAX",
        ]
        return "\n".join(lines)

    def format_rapid_move(
        self,
        x: float,
        y: float,
        z: float,
        a: float | None = None,
        c: float | None = None,
    ) -> str:
        """生成快速定位指令（Heidenhain 会话式 L 行 + FMAX）。

        Heidenhain TNC 对话式编程不支持字地址 G 代码（G00/G01 属于 ISO
        模式，与 BEGIN PGM 会话式程序头不兼容，混用会被控制器拒绝）。
        快速定位在会话式语法下写作 ``L  X+.. Y+.. Z+.. R0 FMAX``。

        注意：基类 `_FormatMixin` 的默认实现输出 G00 行，本覆写是
        语法合规（手动锚定合规层 tests/utils/nc_dialect_checker.py）的要求。
        """
        return f"L  X+{self._fmt(x)} Y+{self._fmt(y)} Z+{self._fmt(z)} R0 FMAX"

    def format_linear_move(
        self,
        x: float,
        y: float,
        z: float,
        feed: float | None = None,
        a: float | None = None,
        c: float | None = None,
    ) -> str:
        """生成直线插补指令（Heidenhain 会话式 L 行 + 进给）。

        feed 为 None 时回退到 rapid_feed（与基类默认行为一致）。
        """
        feed_value = feed if feed is not None else self.rapid_feed
        return f"L  X+{self._fmt(x)} Y+{self._fmt(y)} Z+{self._fmt(z)} R0 F{self._fmt(feed_value)}"

    def format_arc(
        self,
        start: tuple[float, float, float],
        end: tuple[float, float, float],
        center: tuple[float, float, float],
        clockwise: bool = True,
    ) -> str:
        if clockwise:
            return f"{self._next_block()}  L  X+{self._fmt(end[0])} Y+{self._fmt(end[1])} F{self._fmt(self.rapid_feed)}"

        n = self._next_block()
        lines = [
            f"{n}  CC  X+{self._fmt(center[0])} Y+{self._fmt(center[1])}",
            f"{self._next_block()}  C  X+{self._fmt(end[0])} Y+{self._fmt(end[1])} F{self._fmt(self.rapid_feed)}",
        ]
        return "\n".join(lines)

    def format_tool_compensation(
        self,
        length_offset: int = 0,
        radius_offset: int = 0,
    ) -> str:
        if length_offset > 0 or radius_offset > 0:
            parts = ["TOOL CALL 1 Z"]
            if radius_offset > 0:
                parts.append(f"DR+{radius_offset}")
            return " ".join(parts)
        return "TOOL CALL 0 Z"

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
        rep_max = repeat_cfg.get("maximum", 999)

        program_number = max(prog_min, min(prog_max, program_number))
        repeat = max(rep_min, min(rep_max, repeat))

        call_fmt = sub_cfg.get("call_format", "LBL CALL {program_num:04d} REP{repeat}")
        try:
            formatted = call_fmt.format(program_num=program_number, repeat=repeat)
        except KeyError:
            formatted = f"LBL CALL {program_number:04d}"
            if repeat > 1:
                formatted += f" REP{repeat}"

        return f"{self._next_block()}  {formatted}"

    def format_subprogram_end(
        self,
        return_value: str | None = None,
    ) -> str:
        sub_cfg = self.get_subprogram_config()
        end_code = sub_cfg.get("end_code", "LBL 0")
        return f"{self._next_block()}  {end_code}"

    def format_footer(self) -> str:
        lines = [
            "",
            f"{self._next_block()}  M09",
            f"{self._next_block()}  M05",
            f"{self._next_block()}  L  Z+{self._fmt(self.safe_z_height)} R0 FMAX",
            f"{self._next_block()}  L  X+0 Y+0 R0 FMAX",
            f"{self._next_block()}  M30",
            f"{self._next_block()}  END PGM {self._last_program_number:04d} MM",
        ]
        return "\n".join(lines)

    def format_rtcp_on(self, tool_length: float = 0.0) -> str:
        """开启 RTCP（旋转刀具中心点）补偿模式（Heidenhain：M128/TCPM）。

        基类默认实现输出 Fanuc 风格 G43.4，与对话式程序不兼容；
        Heidenhain TNC 的 RTCP 指令为 M128（与 format_high_precision_mode 同族）。
        """
        return f"{self._next_block()}  M128"

    def format_rtcp_off(self) -> str:
        """关闭 RTCP 补偿模式（Heidenhain：M129）。"""
        return f"{self._next_block()}  M129"

    def format_high_precision_mode(self, enable: bool = True) -> str:
        """生成高精度加工模式指令（M128）。

        M128 是 Heidenhain TNC 控制器的高精度模式，
        用于提升曲面加工精度和表面质量，特别是在高速加工时。

        Args:
            enable: True 开启高精度模式，False 关闭

        Returns:
            高精度模式 NC 代码字符串
        """
        if enable:
            return f"{self._next_block()}  M128"
        else:
            return f"{self._next_block()}  M129"

    def format_probe_cycle(
        self,
        probe_number: int = 1,
        x_pos: float = 0.0,
        y_pos: float = 0.0,
        z_depth: float = -10.0,
        feed_rate: float | None = None,
    ) -> str:
        """生成测头测量循环（CYCL DEF 19）。

        CYCL DEF 19 是 Heidenhain 的测头测量固定循环，
        用于工件找正、尺寸检测和自适应加工。

        Args:
            probe_number: 测头编号（1-4）
            x_pos: 测量点 X 坐标
            y_pos: 测量点 Y 坐标
            z_depth: 测量深度 Z 坐标（负值）
            feed_rate: 测量进给速度，None 使用默认值

        Returns:
            测头循环 NC 代码字符串
        """
        if feed_rate is None:
            feed_rate_str = self._fmt(self.get_feed_rate(self.rapid_feed * 0.1))
        else:
            feed_rate_str = self._fmt(feed_rate)

        lines = [
            f"{self._next_block()}  CYCL DEF 19 PROBE",
            f"{self._next_block()}     Q260={self._fmt(self.safe_z_height)}  ;CLEARANCE HEIGHT",
            f"{self._next_block()}     Q261={self._fmt(z_depth)}  ;MEASURING DEPTH",
            f"{self._next_block()}     Q264={self._fmt(x_pos)}  ;FIRST AXIS COORDINATE",
            f"{self._next_block()}     Q265={self._fmt(y_pos)}  ;SECOND AXIS COORDINATE",
            f"{self._next_block()}     Q272={probe_number}  ;PROBE NUMBER",
            f"{self._next_block()}     Q273={feed_rate_str}  ;FEED RATE",
            f"{self._next_block()}  CYCL CALL",
        ]
        return "\n".join(lines)
