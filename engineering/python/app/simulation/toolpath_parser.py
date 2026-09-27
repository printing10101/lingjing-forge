"""NC code parser for G-code toolpath extraction.

Parses Fanuc, Siemens, and Heidenhain format G-code, extracting toolpath
point sequences while maintaining modal command state. Supports G00/G01/G02/G03
motion commands, coordinate systems (G90/G91), and tool/feed/spindle parameters.

Supported G-codes:
    - G00: Rapid positioning
    - G01: Linear interpolation
    - G02: Clockwise circular interpolation
    - G03: Counterclockwise circular interpolation
    - G04: Dwell
    - G17/G18/G19: Plane selection (XY/XZ/YZ)
    - G21: Metric units
    - G90/G91: Absolute/incremental positioning

Example:
    >>> parser = ToolpathParser(controller_type="fanuc")
    >>> segments = parser.parse_gcode("N1 G00 X0 Y0 Z5\\nN2 G01 X10 Y0 Z-2 F500")
    >>> for seg in segments:
    ...     print(f"{seg.type}: {seg.start_point} -> {seg.end_point}")
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolpathSegment:
    """A single segment of a CNC toolpath.

    Represents one motion command from the parsed G-code, including start
    and end positions and associated machining parameters.

    Attributes:
        type: Motion type - "rapid", "linear", "arc", or "dwell".
        start_point: (x, y, z) start coordinates in mm.
        end_point: (x, y, z) end coordinates in mm.
        feed_rate: Feed rate in mm/min (None for rapid moves).
        spindle_speed: Spindle speed in RPM (None if not specified).
        tool_id: Tool number (None if not specified).
        block_number: NC block/line number.
        g_code: Original G-code string (e.g., "G01").
    """

    type: str
    start_point: tuple[float, float, float]
    end_point: tuple[float, float, float]
    feed_rate: float | None = None
    spindle_speed: int | None = None
    tool_id: int | None = None
    block_number: int = 0
    g_code: str = ""
    # 圆弧参数 (用于 G02/G03)
    arc_center: tuple[float, float, float] | None = None  # 圆心坐标 (I, J, K)
    arc_radius: float | None = None  # 圆弧半径 (R 格式)
    # 五轴角度 (用于 5axis 碰撞检测)
    a_angle: float | None = None  # A 轴角度（度）
    c_angle: float | None = None  # C 轴角度（度）
    clockwise: bool = True  # True=G02 顺时针, False=G03 逆时针

    def to_dict(self) -> dict[str, Any]:
        """Convert the segment to a dictionary.

        Returns:
            Dictionary with all segment fields.
        """
        return {
            "type": self.type,
            "start_point": list(self.start_point),
            "end_point": list(self.end_point),
            "feed_rate": self.feed_rate,
            "spindle_speed": self.spindle_speed,
            "tool_id": self.tool_id,
            "block_number": self.block_number,
            "g_code": self.g_code,
            "arc_center": list(self.arc_center) if self.arc_center else None,
            "arc_radius": self.arc_radius,
            "clockwise": self.clockwise,
        }

    @property
    def start(self) -> tuple[float, float, float]:
        """Alias for start_point.

        Returns:
            Start coordinates (x, y, z).
        """
        return self.start_point

    @property
    def end(self) -> tuple[float, float, float]:
        """Alias for end_point.

        Returns:
            End coordinates (x, y, z).
        """
        return self.end_point


class ToolpathParser:
    """G-code parser that extracts toolpath segments from NC programs.

    Maintains modal state (current position, feed rate, spindle speed,
    motion mode, coordinate system) across lines. Supports Fanuc, Siemens,
    and Heidenhain controller formats.

    Attributes:
        controller_type: Controller dialect ("fanuc", "siemens", "heidenhain").

    Example:
        >>> parser = ToolpathParser(controller_type="fanuc")
        >>> gcode = "N1 G00 X0 Y0 Z5\\nN2 G01 X50 Y0 Z-2 F1000"
        >>> segments = parser.parse_gcode(gcode)
        >>> print(f"Parsed {len(segments)} segments")
    """

    _RE_WORD = re.compile(r"([A-Z])\s*([\-+]?\d+\.?\d*)")

    def __init__(self, controller_type: str = "fanuc") -> None:
        """Initialize the parser with a controller dialect.

        Args:
            controller_type: Controller type ("fanuc", "siemens", "heidenhain").
        """
        self.controller_type = controller_type
        self._reset_state()

    def _reset_state(self) -> None:
        """Reset all modal state variables to their default values."""
        self._x = 0.0
        self._y = 0.0
        self._z = 100.0
        self._i = 0.0
        self._j = 0.0
        self._k = 0.0
        self._r = 0.0
        self._feed = 500.0
        self._spindle = 0
        self._tool = 1
        self._motion = "G00"
        self._plane = "G17"
        self._units = "G21"
        self._absolute = True
        self._line_num = 0
        self._arc_center: tuple[float, float, float] = (0.0, 0.0, 0.0)

    def parse_gcode(
        self,
        gcode_text: str,
    ) -> list[ToolpathSegment]:
        """Parse G-code text into a list of toolpath segments.

        Processes the input line by line, skipping comments (lines starting
        with ';' or '(') and program header lines ('%' or 'O'). Maintains
        modal state throughout the parsing process.

        Args:
            gcode_text: The complete G-code program as a string.

        Returns:
            List of ToolpathSegment objects representing the toolpath.
        """
        self._reset_state()
        segments: list[ToolpathSegment] = []
        prev_point = (self._x, self._y, self._z)
        segment_start = prev_point

        for line in gcode_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("(") or stripped.startswith(";"):
                continue
            if stripped.startswith("%") or stripped.startswith("O"):
                continue

            # Heidenhain 特殊指令过滤：跳过非运动指令行
            if self.controller_type == "heidenhain":
                upper = stripped.upper()
                # 去掉行号前缀（如 "1 BLK FORM" -> "BLK FORM"）
                parts = upper.split(None, 1)
                content = parts[1] if len(parts) > 1 else upper

                # 跳过 Heidenhain 非运动指令（检查原始行和去掉行号后的内容）
                if any(
                    upper.startswith(prefix) or content.startswith(prefix)
                    for prefix in (
                        "BEGIN PGM",
                        "END PGM",  # 程序开始/结束
                        "BLK FORM",  # 毛坯定义
                        "TOOL CALL",  # 刀具调用
                        "CYCL DEF",
                        "CYCL CALL",  # 固定循环定义/调用
                        "LBL CALL",
                        "LBL 0",  # 子程序调用/结束
                    )
                ):
                    continue
                # 跳过辅助功能（M代码）
                if upper.startswith("M") and len(upper) > 1 and upper[1:2].isdigit():
                    continue

            self._line_num += 1
            # block_number 优先使用 N 代码编号（供按 N 号索引改写），
            # 无 N 号时退化为物理行号
            n_match = re.search(r"\bN(\d+)\b", stripped.upper())
            if n_match:
                self._line_num = int(n_match.group(1))
            words = self._parse_words(stripped)
            if not words:
                continue

            # Heidenhain L 指令处理：L 表示线性移动，FMAX 表示快速移动
            if self.controller_type == "heidenhain":
                upper = stripped.upper()
                # 检查是否是 L 指令（线性移动）
                if upper.startswith("L ") or upper.startswith("L\t"):
                    # 判断是快速移动还是切削移动
                    if "FMAX" in upper:
                        move_type: str | None = "rapid"
                        self._motion = "G00"
                    else:
                        move_type = "linear"
                        self._motion = "G01"
                    # 更新坐标
                    if "X" in words:
                        self._x = words["X"]
                    if "Y" in words:
                        self._y = words["Y"]
                    if "Z" in words:
                        self._z = words["Z"]
                    # 更新进给率（如果不是 FMAX）
                    if "F" in words:
                        self._feed = words["F"]
                    new_point = (self._x, self._y, self._z)
                    if new_point != prev_point:
                        seg = ToolpathSegment(
                            type=move_type or "rapid",
                            start_point=segment_start,
                            end_point=new_point,
                            feed_rate=self._feed if move_type == "linear" else None,
                            spindle_speed=self._spindle if self._spindle > 0 else None,
                            tool_id=self._tool,
                            block_number=self._line_num,
                            g_code=(move_type or "").upper(),
                        )
                        segments.append(seg)
                        segment_start = new_point
                        prev_point = new_point
                    continue

            move_type = self._process_words(words, prev_point)
            new_point = (self._x, self._y, self._z)

            if move_type and (new_point != prev_point or move_type == "dwell"):
                seg = ToolpathSegment(
                    type=move_type,
                    start_point=segment_start,
                    end_point=new_point,
                    feed_rate=self._feed if move_type in ("linear", "arc") else None,
                    spindle_speed=self._spindle if self._spindle > 0 else None,
                    tool_id=self._tool,
                    block_number=self._line_num,
                    g_code=move_type.upper(),
                    arc_center=self._arc_center if move_type == "arc" else None,
                    arc_radius=self._r if move_type == "arc" and self._r > 0 else None,
                    clockwise=(self._motion == "G02") if move_type == "arc" else True,
                )
                segments.append(seg)
                segment_start = new_point
                prev_point = new_point

        return segments

    def _parse_words(self, line: str) -> dict[str, float]:
        """Extract G-code words (letter+number pairs) from a line.

        Args:
            line: A single G-code line (comments already stripped).

        Returns:
            Dictionary mapping word letters to their numeric values.
        """
        words: dict[str, float] = {}
        for m in self._RE_WORD.finditer(line.upper()):
            key = m.group(1)
            val = float(m.group(2))
            words[key] = val
        return words

    def _process_words(
        self,
        words: dict[str, float],
        prev_point: tuple[float, float, float],
    ) -> str | None:
        """Process parsed G-code words, updating modal state.

        Handles G-codes (motion, plane, positioning mode), axis coordinates
        (X, Y, Z), arc center parameters (I, J, K, R), and machining
        parameters (F, S, T).

        Args:
            words: Dictionary of parsed G-code words.
            prev_point: Previous tool position (x, y, z).

        Returns:
            The detected motion type ("rapid", "linear", "arc", "dwell")
            or None if no motion occurred on this line.
        """
        move_type = None
        # 标记非运动G代码（回参考点/坐标系选择等），避免被模态运动逻辑误判
        non_motion = False
        # 固定循环标志：Z 参数是切削深度而非定位目标，R 参数是退回平面
        fixed_cycle = False

        if "G" in words:
            g = int(words["G"])
            if g in (0,):
                move_type = "rapid"
                self._motion = "G00"
            elif g in (1,):
                move_type = "linear"
                self._motion = "G01"
            elif g in (2, 3):
                move_type = "arc"
                self._motion = f"G0{g}"
            elif g == 4:
                move_type = "dwell"
                self._motion = "G04"
            elif g == 80:
                self._motion = "G00"
            elif g in (17, 18, 19):
                self._plane = f"G{g}"
            elif g == 90:
                self._absolute = True
            elif g == 91:
                self._absolute = False
            elif g in (28, 30):
                # 回参考点指令：X/Y/Z 是中间点而非目标点
                # 不更新坐标，不生成运动段
                non_motion = True
            elif g in (53, 54, 55, 56, 57, 58, 59):
                # 坐标系选择：非运动指令
                non_motion = True
            elif g in (73, 74, 76, 81, 82, 83, 84, 85, 86, 87, 88, 89):
                # 固定循环：Z 是切削深度，R 是退回平面高度
                # 解析为定位到 (X, Y, R_plane) 的 rapid 移动
                fixed_cycle = True
                self._motion = f"G{g}"

        # 模态运动逻辑：仅在非 non_motion 且非 fixed_cycle 时触发
        if not move_type and not non_motion and not fixed_cycle:
            if self._motion in ("G00",):
                if any(k in words for k in ("X", "Y", "Z")):
                    move_type = "rapid"
            elif self._motion == "G01":
                if any(k in words for k in ("X", "Y", "Z")):
                    move_type = "linear"

        # 坐标更新保护：
        # - non_motion（G28/G30/G53-59）：X/Y/Z 是中间点或坐标系选择，不更新模态坐标
        # - fixed_cycle（G73/G81-89）：Z 是切削深度，R 是退回平面；Z 坐标应更新为 R 平面
        if non_motion:
            # 回参考点/坐标系选择：不更新任何坐标，避免后续指令起点错误
            pass
        elif fixed_cycle:
            # 固定循环：更新 X/Y（钻孔位置），Z 更新为 R 退回平面（而非切削深度）
            if "X" in words:
                self._x = words["X"] if self._absolute else self._x + words["X"]
            if "Y" in words:
                self._y = words["Y"] if self._absolute else self._y + words["Y"]
            if "R" in words:
                self._z = words["R"]  # 退回平面高度
            # 生成从当前位置到 (X, Y, R_plane) 的 rapid 定位段
            if any(k in words for k in ("X", "Y", "R")):
                move_type = "rapid"
        else:
            # 正常运动指令：更新 X/Y/Z
            if "X" in words:
                if self._absolute:
                    self._x = words["X"]
                else:
                    self._x += words["X"]
            if "Y" in words:
                if self._absolute:
                    self._y = words["Y"]
                else:
                    self._y += words["Y"]
            if "Z" in words:
                if self._absolute:
                    self._z = words["Z"]
                else:
                    self._z += words["Z"]

        if "I" in words:
            self._i = words["I"]
        if "J" in words:
            self._j = words["J"]
        if "K" in words:
            self._k = words["K"]
        if "R" in words:
            self._r = words["R"]

        if move_type == "arc":
            if "R" in words and "I" not in words and "J" not in words:
                r_val = self._r
                px, py = prev_point[0], prev_point[1]
                dx = self._x - px
                dy = self._y - py
                chord_sq = dx * dx + dy * dy
                if chord_sq > 4 * r_val * r_val:
                    r_val = max(abs(r_val), (chord_sq**0.5) / 2 + 0.001)
                h = max((r_val * r_val - chord_sq / 4) ** 0.5, 0)
                mx = (px + self._x) / 2
                my = (py + self._y) / 2
                if self._motion == "G02":
                    self._arc_center = (
                        mx - dy * h / (chord_sq**0.5) if chord_sq > 0 else mx,
                        my + dx * h / (chord_sq**0.5) if chord_sq > 0 else my,
                        prev_point[2],
                    )
                else:
                    self._arc_center = (
                        mx + dy * h / (chord_sq**0.5) if chord_sq > 0 else mx,
                        my - dx * h / (chord_sq**0.5) if chord_sq > 0 else my,
                        prev_point[2],
                    )
            else:
                self._arc_center = (
                    prev_point[0] + self._i,
                    prev_point[1] + self._j,
                    prev_point[2] + self._k,
                )

        if "F" in words:
            self._feed = words["F"]
        if "S" in words:
            self._spindle = int(words["S"])
        if "T" in words:
            self._tool = int(words["T"])

        return move_type
