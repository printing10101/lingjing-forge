"""G 代码运动学解释器：把 NC 程序展开为机床运动序列。

职责与边界
==========
- 跟踪模态状态（运动模式 G0-G3 / 距离模式 G90-G91 / 单位 G20-G21 /
  平面 G17-G19 / 主轴 M3-M5 / 刀具 T+M6 / 进给 F / 转速 S），
  产出逐段 :class:`MotionRecord` 运动序列，供运动学/行程校验消费。
- 固定循环（G81/G83/G73/G85 + G98/G99）展开为基本运动
  （快速定位 → R 面下钻 → 退回），循环参数按当前距离模式解释。
- 圆弧（G2/G3，I/J 圆心或 R 半径，G17 平面）按弦差采样为折线点列。
- **不做**几何碰撞检测（那是 CollisionDetector / VoxelValidator 的职责）、
  不做多轴 RTCP 逆解（3 轴语义）、不做控制器方言的完整语法校验——
  未识别的 G/M 代码按"无运动"跳过，不产生误报。

坐标语义：解释器内部全部使用**程序坐标系**；机床坐标 = 程序坐标 +
work_offset，由校验器在行程判定时换算。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Literal

MotionKind = Literal["rapid", "linear", "arc_cw", "arc_ccw", "drill"]

_RE_WORD = re.compile(r"([A-Z])\s*([-+]?\d*\.?\d+)")
_ARC_SAMPLE_DEG = 10.0  # 圆弧采样角步距


@dataclass
class MotionRecord:
    """单条机床运动（已展开为基本运动）。

    Attributes:
        line_no: 源程序物理行号（1 起始，便于定位）。
        block: 源程序行文本（截断到 80 字符）。
        kind: rapid/linear/arc_cw/arc_ccw/drill（drill=固定循环孔底进给段）。
        start/end: 程序坐标起点/终点 (x, y, z)。
        points: 运动路径采样点（含起止；直线为 [start, end]）。
        feed: 进给速度（mm/min，当前模态；快速为 None）。
        spindle_on: 该运动发生时主轴是否运转。
        tool: 当前刀号（未装刀为 None）。
    """

    line_no: int
    block: str
    kind: MotionKind
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    points: list[tuple[float, float, float]] = field(default_factory=list)
    feed: float | None = None
    spindle_on: bool = False
    tool: int | None = None

    @property
    def is_cutting(self) -> bool:
        return self.kind in ("linear", "arc_cw", "arc_ccw", "drill")


@dataclass
class ProgramTrace:
    """一次解释产出的完整运动序列与程序级状态。"""

    records: list[MotionRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    lines_processed: int = 0
    program_ended: bool = False
    max_spindle_speed: float = 0.0
    units_inch: bool = False


class GCodeKinematicsInterpreter:
    """3 轴 G 代码运动学解释器（有状态实例，每次 run() 重置状态）。"""

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self._pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._motion = "G00"
        self._absolute = True
        self._inch = False
        self._plane = "G17"
        self._feed: float | None = None
        self._spindle_on = False
        self._spindle_speed = 0.0
        self._tool: int | None = None
        self._pending_tool: int | None = None
        self._cycle: str | None = None  # "G81"/"G83"/"G73"/"G85"
        self._cycle_r: float | None = None
        self._cycle_z: float | None = None
        self._cycle_q: float | None = None
        self._cycle_return_initial = True  # G98
        self._initial_z: float = 0.0
        self.trace = ProgramTrace()

    # ---------- 公共入口 ----------

    def run(self, gcode_text: str) -> ProgramTrace:
        """解释整个程序，返回运动序列。"""
        self._reset()
        for line_no, raw_line in enumerate(gcode_text.splitlines(), start=1):
            self.trace.lines_processed = line_no
            self._process_line(line_no, raw_line)
        return self.trace

    # ---------- 逐行处理 ----------

    def _process_line(self, line_no: int, raw_line: str) -> None:
        text = raw_line.strip()
        # 去注释：Fanuc 括号注释 / 分号注释 / 程序头
        text = re.sub(r"\([^)]*\)", " ", text)
        if ";" in text:
            text = text.split(";", 1)[0]
        text = text.strip()
        if not text or text.startswith("%"):
            return
        upper = text.upper()
        if upper.startswith("O") and len(upper) > 1 and upper[1].isdigit():
            return  # 程序号
        if upper.startswith("N") and len(upper) > 1 and upper[1].isdigit():
            pass  # 行号前缀：交给词解析（N 词被忽略）

        # G/M 是可重复的代码词（如 "G90 G81"），必须收集为列表；
        # 其余字母（轴/进给/转速等）同字母取最后一个
        g_values: list[int] = []
        m_values: list[int] = []
        words: dict[str, float] = {}
        for letter, value in _RE_WORD.findall(upper):
            v = float(value)
            if letter == "G":
                if abs(v - round(v)) <= 1e-9:
                    g_values.append(int(round(v)))
            elif letter == "M":
                m_values.append(int(round(v)))
            else:
                words[letter] = v

        if not words and not g_values and not m_values:
            return

        for g in g_values:
            self._apply_modal_g(g, line_no)

        # 主轴 / 刀具 / 程序结束
        if "S" in words:
            self._spindle_speed = words["S"]
            self.trace.max_spindle_speed = max(self.trace.max_spindle_speed, words["S"])
        if "T" in words:
            self._pending_tool = int(words["T"])
        if "F" in words:
            self._feed = words["F"] * (25.4 if self._inch else 1.0)
        for m_code in m_values:
            if m_code in (3, 4):
                self._spindle_on = True
            elif m_code == 5:
                self._spindle_on = False
            elif m_code == 6:
                self._tool = self._pending_tool
                self._pending_tool = None
            elif m_code in (30, 2):
                self.trace.program_ended = True

        # 运动产出
        if self._cycle is not None:
            # 固定循环激活态：采集 R/Z/Q 参数；X/Y 坐标词触发一次孔循环
            if "R" in words:
                self._cycle_r = self._to_abs("Z", words["R"])
            if "Z" in words:
                self._cycle_z = self._to_abs("Z", words["Z"])
            if "Q" in words:
                self._cycle_q = words["Q"] * (25.4 if self._inch else 1.0)
            if any(a in words for a in "XY") and self._cycle_z is not None:
                self._emit_cycle(line_no, upper)
            return

        self._emit_motion(line_no, upper, words, g_values)

    def _apply_modal_g(self, g: int, line_no: int) -> None:
        """应用模态 G 代码（运动组/平面/单位/循环/参考返回/工件坐标系）。"""
        if g in (0, 1, 2, 3):
            self._motion = f"G{g:02d}"
            self._cycle = None
        elif g == 17:
            self._plane = "G17"
        elif g in (18, 19):
            self._plane = f"G{g}"
            self.trace.warnings.append(f"行 {line_no}: 圆弧平面 {self._plane} 仅按端点检查（中间过行程不采样）")
        elif g == 20:
            self._inch = True
            self.trace.units_inch = True
        elif g == 21:
            self._inch = False
        elif g == 90:
            self._absolute = True
        elif g == 91:
            self._absolute = False
        elif g == 80:
            self._cycle = None
        elif g in (81, 83, 73, 85):
            self._cycle = f"G{g:02d}"
            self._motion = f"G{g:02d}"
        elif g == 98:
            self._cycle_return_initial = True
        elif g == 99:
            self._cycle_return_initial = False
        elif g in (54, 55, 56, 57, 58, 59):
            if g != 54:
                self.trace.warnings.append(f"行 {line_no}: G{g} 工件坐标系按 G54 处理（本模块仅支持单一 work_offset）")
        # G28 回参考点等由运动词缺省处理（无坐标词时不产运动）

    # ---------- 运动构造 ----------

    def _to_abs(self, axis: str, value: float) -> float:
        """按当前距离模式把坐标词转为绝对程序坐标（含英制换算）。"""
        v = value * (25.4 if self._inch else 1.0)
        idx = {"X": 0, "Y": 1, "Z": 2}[axis]
        if self._absolute:
            return v
        return self._pos[idx] + v

    def _emit_motion(self, line_no: int, upper: str, words: dict[str, float], g_codes: list[int]) -> None:
        has_axis_word = any(a in words for a in "XYZ")
        if not has_axis_word:
            return
        if self._motion in ("G02", "G03"):
            self._emit_arc(line_no, upper, words)
            return

        # 未写出的轴保持模态位置（G90/G91 同理：增量未写轴 = 不动）
        def axis_target(axis: str, idx: int) -> float:
            if axis in words:
                return self._to_abs(axis, words[axis])
            return self._pos[idx]

        target = (axis_target("X", 0), axis_target("Y", 1), axis_target("Z", 2))
        kind: MotionKind = "rapid" if self._motion == "G00" else "linear"
        self._append_record(line_no, upper, kind, target, feed=None if kind == "rapid" else self._feed)

    def _emit_cycle(self, line_no: int, upper: str) -> None:
        """固定循环展开：快速定位 → R 面 → 孔底进给 → 退回（G98 初始面 / G99 R 面）。"""
        assert self._cycle_z is not None
        words = {m[0]: float(m[1]) for m in _RE_WORD.findall(upper)}
        x = (
            self._to_abs("X", words.get("X", 0.0))
            if self._absolute
            else self._pos[0] + words.get("X", 0.0) * (25.4 if self._inch else 1.0)
        )
        y = (
            self._to_abs("Y", words.get("Y", 0.0))
            if self._absolute
            else self._pos[1] + words.get("Y", 0.0) * (25.4 if self._inch else 1.0)
        )
        r_plane = self._cycle_r if self._cycle_r is not None else self._pos[2]
        z_depth = self._cycle_z
        retract_z = self._initial_z if self._cycle_return_initial else r_plane

        start = self._pos
        # 1) 快速定位 XY（保持当前 Z）
        self._append_record(line_no, upper, "rapid", (x, y, start[2]), feed=None)
        # 2) 快速下到 R 面
        self._append_record(line_no, upper, "rapid", (x, y, r_plane), feed=None)
        # 3) 进给至孔底（G83/G73 啄式：中间 Q 步进按同一进给段简化，
        #    行程校验只关心孔底最低点，中间抬/落不改变包络）
        self._append_record(
            line_no, upper, "drill", (x, y, z_depth), feed=self._feed if self._feed is not None else None
        )
        # 4) 退回
        self._append_record(line_no, upper, "rapid", (x, y, retract_z), feed=None)
        self._pos = (x, y, retract_z)
        self._initial_z = max(self._initial_z, retract_z)

    def _emit_arc(self, line_no: int, upper: str, words: dict[str, float]) -> None:
        """G2/G3 圆弧采样（G17 平面，I/J 圆心增量或 R 半径）。"""
        if self._plane != "G17":
            # 非 XY 平面圆弧退化为直线到端点（已在 _apply_modal_g 提示）
            target = (
                self._to_abs("X", words.get("X", 0.0)),
                self._to_abs("Y", words.get("Y", 0.0)),
                self._to_abs("Z", words.get("Z", 0.0)),
            )
            self._append_record(line_no, upper, "linear", target, feed=self._feed)
            return

        sx, sy, sz = self._pos
        ex = self._to_abs("X", words.get("X", 0.0))
        ey = self._to_abs("Y", words.get("Y", 0.0))
        ez = self._to_abs("Z", words.get("Z", 0.0))
        cw = self._motion == "G02"

        if "I" in words or "J" in words:
            cx = sx + words.get("I", 0.0) * (25.4 if self._inch else 1.0)
            cy = sy + words.get("J", 0.0) * (25.4 if self._inch else 1.0)
            radius = math.hypot(sx - cx, sy - cy)
        elif "R" in words:
            radius = abs(words["R"]) * (25.4 if self._inch else 1.0)
            chord = math.hypot(ex - sx, ey - sy)
            if radius < chord / 2 - 1e-9 or chord < 1e-9:
                self.trace.warnings.append(
                    f"行 {line_no}: R 圆弧弦长与半径矛盾（chord={chord:.3f}, R={radius:.3f}），按直线处理"
                )
                self._append_record(line_no, upper, "linear", (ex, ey, ez), feed=self._feed)
                return
            # 圆心在弦的垂直平分线上；R 正取劣弧圆心，R 负取优弧圆心
            mid_x, mid_y = (sx + ex) / 2.0, (sy + ey) / 2.0
            d = math.sqrt(max(radius * radius - (chord / 2) ** 2, 0.0))
            ux, uy = (ey - sy) / chord, -(ex - sx) / chord
            sign = 1.0 if (words["R"] >= 0) != cw else -1.0
            cx, cy = mid_x + sign * d * ux, mid_y + sign * d * uy
        else:
            self.trace.warnings.append(f"行 {line_no}: 圆弧缺少 I/J/K 或 R，按直线处理")
            self._append_record(line_no, upper, "linear", (ex, ey, ez), feed=self._feed)
            return

        a0 = math.atan2(sy - cy, sx - cx)
        a1 = math.atan2(ey - cy, ex - cx)
        if cw:
            sweep = a0 - a1
            while sweep <= 1e-9:
                sweep += 2 * math.pi
        else:
            sweep = a1 - a0
            while sweep <= 1e-9:
                sweep += 2 * math.pi

        steps = max(2, int(math.ceil(math.degrees(sweep) / _ARC_SAMPLE_DEG)))
        points: list[tuple[float, float, float]] = []
        for i in range(steps + 1):
            t = i / steps
            ang = a0 - sweep * t if cw else a0 + sweep * t
            points.append((cx + radius * math.cos(ang), cy + radius * math.sin(ang), sz + (ez - sz) * t))
        points[-1] = (ex, ey, ez)
        self._append_record(line_no, upper, "arc_cw" if cw else "arc_ccw", (ex, ey, ez), feed=self._feed, points=points)

    def _append_record(
        self,
        line_no: int,
        upper: str,
        kind: MotionKind,
        target: tuple[float, float, float],
        feed: float | None,
        points: list[tuple[float, float, float]] | None = None,
    ) -> None:
        rec = MotionRecord(
            line_no=line_no,
            block=upper[:80],
            kind=kind,
            start=self._pos,
            end=target,
            points=points if points is not None else [self._pos, target],
            feed=feed,
            spindle_on=self._spindle_on,
            tool=self._tool,
        )
        self.trace.records.append(rec)
        self._pos = target
        self._initial_z = max(self._initial_z, target[2])
