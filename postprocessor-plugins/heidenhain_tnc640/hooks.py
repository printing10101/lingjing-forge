"""Heidenhain TNC640 hooks — 对话式编程（CYCL DEF / TOOL CALL）。

Heidenhain TNC640 与 Fanuc/Siemens 的差异：
1. 编程风格是对话式：CYCL DEF 200 DRILLING / CYCL DEF 240 TAPPING；
2. 换刀用 TOOL CALL（刀具名 + Z/S 主轴参数），而非 T 指令；
3. 程序头用 BEGIN PGM ... MM 结尾用 END PGM ...；
4. 深孔钻用 CYCL DEF 200（DRILLING）与 241（DRILLING DEEP）；
5. 坐标用绝对/增量混合，半径用 R 而非 I/J/K。

hooks 方法以方言实例为 self，可访问 _fmt / get_spindle_rpm /
get_feed_rate / get_cycle_config / rapid_feed / safe_z_height。
"""

from __future__ import annotations


class HeidenhainTNC640Hooks:
    """Heidenhain TNC640 对话式循环 hooks。"""

    def format_cycle_drill(
        self,
        x: float,
        y: float,
        z: float,
        depth: float,
        dwell: float = 0.0,
        pecking: bool = True,
    ) -> str:
        """钻孔：CYCL DEF 200 DRILLING（普通）/ 241 DEEP HOLE（啄钻）。"""
        cfg = self.get_cycle_config("drilling", "200")
        feed = self._fmt(self.get_feed_rate(self.rapid_feed * 0.3))
        z_end = self._fmt(-abs(depth))
        if pecking:
            peck_depth = cfg.get("peck_depth", 5.0)
            return (
                f"CYCL DEF 241 DRILLING DEEP\n"
                f"Q200=2.0 ;SET-UP CLEARANCE\n"
                f"Q201={z_end} ;DEPTH\n"
                f"Q206={feed} ;FEED RATE\n"
                f"Q202={self._fmt(peck_depth)} ;PLNGNG DEPTH"
            )
        return f"CYCL DEF 200 DRILLING\nQ200=2.0 ;SET-UP CLEARANCE\nQ201={z_end} ;DEPTH\nQ206={feed} ;FEED RATE"

    def format_cycle_tapping(
        self,
        x: float,
        y: float,
        z: float,
        depth: float,
        pitch: float = 1.0,
        spindle_rpm: float | None = None,
    ) -> str:
        """刚性攻丝：CYCL DEF 240 TAPPING（带 Q239 螺距）。

        参数名与基类 format_cycle_tapping 调用约定对齐（pitch / spindle_rpm）。
        """
        rpm = spindle_rpm if spindle_rpm is not None else self.get_spindle_rpm()
        feed = self._fmt(self.get_feed_rate(rpm))
        z_end = self._fmt(-abs(depth))
        return (
            f"CYCL DEF 240 TAPPING\n"
            f"Q200=2.0 ;SET-UP CLEARANCE\n"
            f"Q201={z_end} ;DEPTH\n"
            f"Q206={feed} ;FEED RATE\n"
            f"Q239={self._fmt(pitch)} ;PITCH"
        )

    def format_tool_change(
        self,
        tool_id: int,
        length_comp: float = 0.0,
        radius_comp: float = 0.0,
    ) -> str:
        """Heidenhain 换刀：TOOL CALL（刀具号 + Z 安全高度 + 主轴转速）。

        参数名与基类 format_tool_change 调用约定对齐（tool_id / length_comp /
        radius_comp），hooks 方法以方言实例为 self 被调用。
        """
        rpm = int(self.get_spindle_rpm())
        return f"TOOL CALL {int(tool_id):02d} Z S{rpm}"

    def format_probe(self, probe_number: int = 1, x_pos: float = 0.0) -> str:
        """测头循环：TCH PROBE（Heidenhain 特色）。"""
        return f"TCH PROBE {probe_number} X{self._fmt(x_pos)}"


class HeidenhainHeaderHooks:
    """Heidenhain TNC640 程序头/尾 hooks。"""

    def format_header(self, program_number: int = 1) -> str:
        """程序头：BEGIN PGM ... MM（签名与基类约定对齐）。

        程序号记录到 ``_last_program_number``（与内置后处理器约定一致），
        供 format_footer 复用。
        """
        self._last_program_number = int(program_number)
        return f"BEGIN PGM {int(program_number):04d} MM"

    def format_footer(self) -> str:
        """程序尾：END PGM ... MM（复用 format_header 记录的程序号）。"""
        num = int(getattr(self, "_last_program_number", 1))
        return f"END PGM {num:04d} MM"


__all__ = ["HeidenhainTNC640Hooks", "HeidenhainHeaderHooks"]
