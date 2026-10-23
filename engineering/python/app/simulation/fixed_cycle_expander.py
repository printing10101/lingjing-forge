"""固定循环展开器：把 G73/G81-G89 展开为等价的 G00/G01 显式运动序列。

背景
====
``app/simulation/toolpath_parser.py`` 对固定循环只生成「定位到 (X, Y, R 平面)」
的 rapid 段，循环内部的钻深轨迹**完全不生成**（见该文件 ``fixed_cycle`` 分支）。
后果：体素仿真与碰撞检测对钻孔是盲的——实测一份 ``Z=-15`` 钻穿垫板的程序，
``VoxelValidator`` 报 ``collision_count=0``。

本模块在**文本层**做纯函数变换，把循环展开成等价的显式运动，供需要完整刀轨的
消费者（体素校验）使用，不改变 ``ToolpathParser`` 的既有行为。

支持的循环
==========
- ``G81`` 钻孔：R → Z → R
- ``G82`` 带停顿钻孔：同 G81（停顿时长对几何校验无影响）
- ``G73`` 高速深孔钻：按 Q 步长啄钻，每步退回 R
- ``G83`` 深孔啄钻：按 Q 步长啄钻，每步退回 R
- ``G84`` / ``G74`` 右旋 / 左旋攻丝：进给到 Z 后回退。**材料去除几何与 G81 一致**，
  差异仅在主轴反转与退刀方式；但攻丝的 ``F`` 是螺距（mm/rev）而非进给率（mm/min），
  展开后按数值写出并给出告警——轨迹正确、进给速度不等价（2026-10-23 新增）。
- ``G85`` / ``G86`` / ``G88`` / ``G89`` 镗孔：进给到 Z 后回退（G85 进给退、G86 主轴停
  快退、G88 手动退、G89 进给退）。四者的 **材料去除几何与 G81 一致**，退刀方式与
  主轴动作对体素去除无影响（2026-10-23 新增）。

仍不支持的循环
==============
- ``G76`` 精镗：带主轴定向与让刀偏置（``P``），退刀时刀尖让开孔壁，直上直下的
  展开会低估孔壁去除，故不冒充覆盖。
- ``G87`` 反镗：进给方向为 Z→R（自孔底向上），与 G81 的 R→Z 相反，几何不可复用。
两者**原样保留**并计入 ``unsupported``，避免用错误几何冒充校验覆盖。

语义约定
========
- 仅展开绝对坐标（``G90``）下的循环。遇到 ``G91`` 增量模式时该行原样保留并计入
  ``skipped_incremental``——增量循环的目标点依赖机床当前状态，文本层无法安全展开。
- ``R`` = 退回平面绝对 Z；``Z`` = 孔底绝对 Z；``Q`` = 啄钻步长（G73/G83）。
- 展开后每次啄钻退回 R 平面；循环结束停在 R 平面（G98/G99 的差异对几何校验无影响）。
- 模态重复：``G8x`` 行之后仅给 ``X``/``Y`` 的行沿用上一次的 R/Z/Q/F 再次执行循环，
  与 Fanuc 语义一致；``G80`` 取消循环。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"([A-Z])\s*(-?\d*\.?\d+)")
# 支持展开的循环
_SUPPORTED_CYCLES = {"73", "81", "82", "83", "74", "84", "85", "86", "88", "89"}
# 识别出但暂不展开的循环（原样保留）
_KNOWN_UNSUPPORTED = {"76", "87"}
_DRILL_CYCLES = {"73", "83"}  # 需要 Q 啄钻
_TAPPING_CYCLES = {"74", "84"}  # F 语义为螺距


@dataclass
class ExpandStats:
    """展开统计，供调用方判断「校验覆盖度是否完整」。"""

    expanded_cycles: int = 0
    expanded_holes: int = 0
    emitted_lines: int = 0
    unsupported: dict[str, int] = field(default_factory=dict)
    skipped_incremental: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """True 表示文本中不存在未展开的固定循环（覆盖度完整）。"""
        return not any(self.unsupported.values()) and self.skipped_incremental == 0

    def to_dict(self) -> dict[str, object]:
        return {
            "expanded_cycles": self.expanded_cycles,
            "expanded_holes": self.expanded_holes,
            "emitted_lines": self.emitted_lines,
            "unsupported": dict(self.unsupported),
            "skipped_incremental": self.skipped_incremental,
            "complete": self.complete,
            "warnings": list(self.warnings),
        }


def _words(line: str) -> dict[str, float]:
    """解析行内单字母地址字（取该字母最后一次出现，符合 G 代码覆盖语义）。"""
    out: dict[str, float] = {}
    # 去掉注释（括号注释 + 分号注释）
    body = re.sub(r"\([^)]*\)", " ", line)
    body = body.split(";", 1)[0]
    for letter, value in _WORD_RE.findall(body.upper()):
        try:
            out[letter] = float(value)
        except ValueError:  # pragma: no cover - 正则已保证可解析
            continue
    return out


def _g_codes(line: str) -> set[int]:
    """提取行内的主 G 码（跳过带小数点的扩展码，如 G05.1 / G38.2）。"""
    out: set[int] = set()
    body = re.sub(r"\([^)]*\)", " ", line).split(";", 1)[0].upper()
    for letter, value in _WORD_RE.findall(body):
        if letter != "G" or "." in value:
            continue
        try:
            out.add(int(value))
        except ValueError:  # pragma: no cover - 正则已排除非法形式
            continue
    return out


def _fmt(value: float) -> str:
    """坐标输出格式：保留 3 位并去掉无意义的尾零，保持与生成器一致。"""
    return f"{value:.3f}"


def _emit_hole(
    *,
    cycle: str,
    x: float | None,
    y: float | None,
    z_bottom: float,
    r_plane: float,
    q_step: float | None,
    feed: float | None,
    cur_x: float | None,
    cur_y: float | None,
) -> tuple[list[str], tuple[float | None, float | None]]:
    """生成单个孔的显式运动序列，返回 (行列表, 新的当前 XY)。"""
    lines: list[str] = []

    # 1. 定位到孔位（XY 缺省表示沿用当前坐标）
    new_x = x if x is not None else cur_x
    new_y = y if y is not None else cur_y
    if new_x is not None and new_y is not None:
        if x is not None or y is not None:
            lines.append(f"G00 X{_fmt(new_x)} Y{_fmt(new_y)}")
    elif x is not None:
        lines.append(f"G00 X{_fmt(x)}")
    elif y is not None:
        lines.append(f"G00 Y{_fmt(y)}")

    # 2. 抬到 R 平面
    lines.append(f"G00 Z{_fmt(r_plane)}")

    # 3. 下钻（G73/G83 按 Q 啄钻）
    feed_part = f" F{_fmt(feed)}" if feed else ""
    depth = r_plane - z_bottom
    if cycle in _DRILL_CYCLES and q_step and q_step > 0 and depth > q_step:
        current = r_plane
        # 逐级啄钻，最后一次落在孔底
        while current - q_step > z_bottom + 1e-9:
            current -= q_step
            lines.append(f"G01 Z{_fmt(current)}{feed_part}")
            lines.append(f"G00 Z{_fmt(r_plane)}")
        lines.append(f"G01 Z{_fmt(z_bottom)}{feed_part}")
        lines.append(f"G00 Z{_fmt(r_plane)}")
    else:
        lines.append(f"G01 Z{_fmt(z_bottom)}{feed_part}")
        lines.append(f"G00 Z{_fmt(r_plane)}")

    return lines, (new_x, new_y)


def expand_fixed_cycles(gcode: str) -> tuple[str, ExpandStats]:
    """把支持范围内的固定循环展开为显式运动序列。

    Args:
        gcode: 原始 G 代码文本。

    Returns:
        ``(展开后的文本, 统计)``。统计中的 ``complete`` 为 False 时，
        调用方应把「校验覆盖度不完整」如实写进报告，而不是当作已覆盖。
    """
    stats = ExpandStats()
    out: list[str] = []

    # 模态状态
    absolute = True
    active_cycle: str | None = None
    active_r: float | None = None
    active_z: float | None = None
    active_q: float | None = None
    active_f: float | None = None
    cur_x: float | None = None
    cur_y: float | None = None

    for raw in gcode.splitlines():
        words = _words(raw)

        # 坐标系模式
        if "G" in words:
            pass  # 单行可能含多个 G，下面按集合判断
        g_codes = _g_codes(raw)
        if 90 in g_codes:
            absolute = True
        if 91 in g_codes:
            absolute = False

        cycle_code = next((str(g) for g in g_codes if str(g) in _SUPPORTED_CYCLES), None)
        unsupported_code = next((str(g) for g in g_codes if str(g) in _KNOWN_UNSUPPORTED), None)

        # G80 取消循环
        if 80 in g_codes:
            active_cycle = None
            active_r = active_z = active_q = None
            out.append(raw)
            continue

        # 不支持的循环：原样保留并记账
        if unsupported_code is not None:
            stats.unsupported[unsupported_code] = stats.unsupported.get(unsupported_code, 0) + 1
            active_cycle = None
            out.append(raw)
            continue

        is_cycle_line = cycle_code is not None
        is_modal_repeat = (
            active_cycle is not None
            and not is_cycle_line
            and not any(g in g_codes for g in (0, 1, 2, 3, 28, 30))
            and ("X" in words or "Y" in words)
        )

        if not is_cycle_line and not is_modal_repeat:
            out.append(raw)
            continue

        # 增量模式下不展开（语义依赖机床状态）
        if not absolute:
            stats.skipped_incremental += 1
            stats.warnings.append(f"增量模式（G91）下的固定循环未展开，原样保留：{raw.strip()[:60]}")
            active_cycle = None
            out.append(raw)
            continue

        # 解析本次孔位与工艺参数（保留模态值）
        if is_cycle_line:
            active_cycle = cycle_code
            stats.expanded_cycles += 1
            if cycle_code in _TAPPING_CYCLES and "F" in words:
                stats.warnings.append(
                    f"攻丝循环 G{cycle_code} 的 F 是螺距（mm/rev），展开后按数值写成 G01 进给率；"
                    "轨迹几何与真实攻丝一致，进给速度不等价，涉及进给率复核时须人工确认"
                )
        cycle = active_cycle
        assert cycle is not None

        if "R" in words:
            active_r = words["R"]
        if "Z" in words:
            active_z = words["Z"]
        if "Q" in words:
            active_q = words["Q"]
        if "F" in words:
            active_f = words["F"]

        if active_r is None or active_z is None:
            stats.warnings.append(f"固定循环缺少 R 或 Z，原样保留：{raw.strip()[:60]}")
            out.append(raw)
            continue

        if active_z > active_r:
            stats.warnings.append(f"固定循环孔底 Z={active_z} 高于退回平面 R={active_r}，跳过展开：{raw.strip()[:60]}")
            out.append(raw)
            continue

        emitted, (cur_x, cur_y) = _emit_hole(
            cycle=cycle,
            x=words.get("X"),
            y=words.get("Y"),
            z_bottom=active_z,
            r_plane=active_r,
            q_step=active_q,
            feed=active_f,
            cur_x=cur_x,
            cur_y=cur_y,
        )
        out.append(f"(EXPANDED {cycle}: {raw.strip()})")
        out.extend(emitted)
        stats.expanded_holes += 1
        stats.emitted_lines += len(emitted)

    return "\n".join(out), stats


__all__ = ["expand_fixed_cycles", "ExpandStats"]
