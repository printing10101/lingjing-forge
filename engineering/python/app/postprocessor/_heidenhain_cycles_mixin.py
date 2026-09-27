"""Heidenhain 固定循环 mixin（从 heidenhain 拆出）。"""

from __future__ import annotations

import logging
from typing import Any
from collections.abc import Callable

logger = logging.getLogger(__name__)


class _HeidenhainCyclesMixin:
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

    def format_cycle_drill(
        self,
        x: float,
        y: float,
        z: float,
        depth: float,
        dwell: float = 0.0,
        pecking: bool = True,
    ) -> str:
        # pecking 参数与基类签名对齐：Heidenhain 用 Q202（PLUNGING DEPTH）表达啄钻，
        # 循环选择由 dwell 决定（CYCL DEF 200 普通钻孔 / 203 万能钻孔），pecking 不参与分支。
        cfg = self.get_cycle_config("drilling", "G83" if dwell > 0 else "G81")
        r_plane = self.safe_z_height
        peck_depth = cfg.get("peck_depth", 5.0)
        drill_feed = self._fmt(self.get_feed_rate(self.rapid_feed * 0.3))

        if dwell > 0:
            lines = [
                f"{self._next_block()}  CYCL DEF 200 DRILLING",
                f"{self._next_block()}     Q200={self._fmt(r_plane)}  ;SET-UP CLEARANCE",
                f"{self._next_block()}     Q201={self._fmt(-abs(depth))}  ;DEPTH",
                f"{self._next_block()}     Q206={drill_feed}  ;FEED RATE",
                f"{self._next_block()}     Q202={self._fmt(peck_depth)}  ;PLUNGING DEPTH",
                f"{self._next_block()}     Q210={self._fmt(dwell)}  ;DWELL TIME AT TOP",
                f"{self._next_block()}     Q211={self._fmt(dwell or 0.0)}  ;DWELL TIME AT DEPTH",
                f"{self._next_block()}     Q203={self._fmt(0.0)}  ;SURFACE COORDINATE",
                f"{self._next_block()}     Q204={self._fmt(r_plane)}  ;2ND SET-UP CLEARANCE",
                f"{self._next_block()}  CYCL CALL",
            ]
        else:
            lines = [
                f"{self._next_block()}  CYCL DEF 203 UNIVERSAL DRILLING",
                f"{self._next_block()}     Q200={self._fmt(r_plane)}  ;SET-UP CLEARANCE",
                f"{self._next_block()}     Q201={self._fmt(-abs(depth))}  ;DEPTH",
                f"{self._next_block()}     Q206={drill_feed}  ;FEED RATE",
                f"{self._next_block()}     Q202={self._fmt(peck_depth)}  ;PLUNGING DEPTH",
                f"{self._next_block()}     Q203={self._fmt(0.0)}  ;SURFACE COORDINATE",
                f"{self._next_block()}     Q204={self._fmt(r_plane)}  ;2ND SET-UP CLEARANCE",
                f"{self._next_block()}  CYCL CALL",
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
        r_plane = self.safe_z_height
        dwell = cfg.get("dwell_time", 0.0)

        lines = [
            f"{self._next_block()}  CYCL DEF 206 TAPPING NEW",
            f"{self._next_block()}     Q200={self._fmt(r_plane)}  ;SET-UP CLEARANCE",
            f"{self._next_block()}     Q201={self._fmt(-abs(depth))}  ;DEPTH",
            f"{self._next_block()}     Q206={self._fmt(self.get_feed_rate(self.rapid_feed * 0.2))}  ;FEED RATE",
            f"{self._next_block()}     Q202={self._fmt(pitch)}  ;PITCH",
            f"{self._next_block()}     Q203={self._fmt(0.0)}  ;SURFACE COORDINATE",
            f"{self._next_block()}     Q204={self._fmt(r_plane)}  ;2ND SET-UP CLEARANCE",
            f"{self._next_block()}     Q211={self._fmt(dwell)}  ;DWELL TIME AT DEPTH",
            f"{self._next_block()}     Q220={int(rpm)}  ;SPINDLE SPEED",
            f"{self._next_block()}  CYCL CALL",
        ]
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
        r_plane = self.safe_z_height
        orient = 1 if cfg.get("orient_spindle", False) else 0
        _shift_dist = cfg.get("shift_distance", 0.0)
        bore_feed = self._fmt(self.get_feed_rate(self.rapid_feed * 0.15))

        if cycle_type == "G86":
            lines = [
                f"{self._next_block()}  CYCL DEF 202 BORING",
                f"{self._next_block()}     Q200={self._fmt(r_plane)}  ;SET-UP CLEARANCE",
                f"{self._next_block()}     Q201={self._fmt(-abs(depth))}  ;DEPTH",
                f"{self._next_block()}     Q206={bore_feed}  ;FEED RATE",
                f"{self._next_block()}     Q202={self._fmt(2.0)}  ;PLUNGING DEPTH",
                f"{self._next_block()}     Q210={self._fmt(0.0)}  ;DWELL TIME AT TOP",
                f"{self._next_block()}     Q203={self._fmt(0.0)}  ;SURFACE COORDINATE",
                f"{self._next_block()}     Q204={self._fmt(r_plane)}  ;2ND SET-UP CLEARANCE",
                f"{self._next_block()}     Q211={self._fmt(dwell or 0.0)}  ;DWELL TIME AT DEPTH",
                f"{self._next_block()}     Q214={orient}  ;SPINDLE ORIENTATION",
                f"{self._next_block()}  CYCL CALL",
            ]
        elif cycle_type == "G89":
            lines = [
                f"{self._next_block()}  CYCL DEF 209 BORING SPINDLE ORIENTED",
                f"{self._next_block()}     Q200={self._fmt(r_plane)}  ;SET-UP CLEARANCE",
                f"{self._next_block()}     Q201={self._fmt(-abs(depth))}  ;DEPTH",
                f"{self._next_block()}     Q206={bore_feed}  ;FEED RATE",
                f"{self._next_block()}     Q202={self._fmt(2.0)}  ;PLUNGING DEPTH",
                f"{self._next_block()}     Q210={self._fmt(0.0)}  ;DWELL TIME AT TOP",
                f"{self._next_block()}     Q203={self._fmt(0.0)}  ;SURFACE COORDINATE",
                f"{self._next_block()}     Q204={self._fmt(r_plane)}  ;2ND SET-UP CLEARANCE",
                f"{self._next_block()}     Q211={self._fmt(dwell or 0.0)}  ;DWELL TIME AT DEPTH",
                f"{self._next_block()}     Q214={orient}  ;SPINDLE ORIENTATION",
                f"{self._next_block()}  CYCL CALL",
            ]
        else:
            lines = [
                f"{self._next_block()}  CYCL DEF 202 BORING",
                f"{self._next_block()}     Q200={self._fmt(r_plane)}",
                f"{self._next_block()}     Q201={self._fmt(-abs(depth))}",
                f"{self._next_block()}     Q206={bore_feed}",
                f"{self._next_block()}     Q202={self._fmt(2.0)}",
                f"{self._next_block()}     Q210={self._fmt(0.0)}",
                f"{self._next_block()}     Q203={self._fmt(0.0)}",
                f"{self._next_block()}     Q204={self._fmt(r_plane)}",
                f"{self._next_block()}     Q211={self._fmt(dwell)}",
                f"{self._next_block()}  CYCL CALL",
            ]

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

        p = passes if passes is not None else cfg.get("passes", 5)
        d_first = depth_cut_first if depth_cut_first is not None else cfg.get("depth_cut_first", 0.2)
        d_last = depth_cut_last if depth_cut_last is not None else cfg.get("depth_cut_last", 0.05)
        finish = finishing_passes if finishing_passes is not None else cfg.get("finishing_passes", 2)
        angle = tool_angle if tool_angle is not None else cfg.get("tool_angle", 60.0)
        _taper_val = taper if taper is not None else cfg.get("taper", 0.0)
        _shift_axis = cfg.get("shift_axis")
        _shift_dist = cfg.get("shift_distance", 0.0)

        r_plane = self.safe_z_height
        _retract_type = cfg.get("retract_type", "feed")
        infeed = cfg.get("infeed_method", "radial")
        infeed_map = {"compound": 0, "radial": 1, "flank": 2}
        infeed_code = infeed_map.get(infeed, 1)

        lines = [
            f"{self._next_block()}  CYCL DEF 264 THREAD DRILLING/MILLING",
            f"{self._next_block()}     Q200={self._fmt(r_plane)}  ;SET-UP CLEARANCE",
            f"{self._next_block()}     Q201={self._fmt(-abs(depth))}  ;DEPTH OF THREAD",
            f"{self._next_block()}     Q206={self._fmt(self.get_feed_rate(self.rapid_feed * 0.05))}  ;FEED RATE",
            f"{self._next_block()}     Q202={self._fmt(lead)}  ;PITCH",
            f"{self._next_block()}     Q203={self._fmt(0.0)}  ;SURFACE COORDINATE",
            f"{self._next_block()}     Q204={self._fmt(r_plane)}  ;2ND SET-UP CLEARANCE",
            f"{self._next_block()}     Q211={self._fmt(0.0)}  ;DWELL TIME AT DEPTH",
            f"{self._next_block()}     Q239={self._fmt(d_first)}  ;FIRST PASS DEPTH",
            f"{self._next_block()}     Q240={self._fmt(d_last)}  ;LAST PASS DEPTH",
            f"{self._next_block()}     Q241={infeed_code}  ;INFEED METHOD",
            f"{self._next_block()}     Q242={p}  ;NUMBER OF PASSES",
            f"{self._next_block()}     Q243={finish}  ;FINISHING PASSES",
            f"{self._next_block()}     Q244={self._fmt(angle)}  ;TOOL ANGLE",
            f"{self._next_block()}  CYCL CALL",
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
        """生成切槽循环 (CYCL DEF 266)。

        Heidenhain TNC使用CYCL DEF 266进行切槽加工。

        Args:
            x: 切槽直径 (X轴坐标)
            z: 切槽Z向位置
            depth: 切槽深度 (半径值)
            width: 切槽宽度 (mm)
            retract: 每次切削后退量 (mm)
            finish_allowance: 精加工余量 (mm)

        Returns:
            CYCL DEF 266切槽循环NC代码
        """
        cfg = self.get_cycle_config("grooving", "CYCL DEF 266")
        retract_val = cfg.get("retract_amount", retract)
        finish_allow = cfg.get("finish_allowance", finish_allowance)

        r_plane = self.safe_z_height
        groove_feed = self._fmt(self.get_feed_rate(self.rapid_feed * 0.1))

        # CYCL DEF 266 切槽循环格式
        # CYCL DEF 266 GROOVING
        # Q200=SET-UP CLEARANCE
        # Q201=DEPTH
        # Q202=PLUNGING DEPTH
        # Q203=SURFACE COORDINATE
        # Q204=2ND SET-UP CLEARANCE
        # Q206=FEED RATE
        # Q210=DWELL TIME AT TOP
        # Q211=DWELL TIME AT DEPTH
        # Q214=SET-UP CLEARANCE IN TOOL AXIS
        # Q226=RETRACT AMOUNT
        # Q227=FINISHING ALLOWANCE
        lines = [
            f"{self._next_block()}  CYCL DEF 266 GROOVING",
            f"{self._next_block()}     Q200={self._fmt(r_plane)}  ;SET-UP CLEARANCE",
            f"{self._next_block()}     Q201={self._fmt(-abs(depth))}  ;DEPTH",
            f"{self._next_block()}     Q202={self._fmt(retract_val)}  ;PLUNGING DEPTH",
            f"{self._next_block()}     Q203={self._fmt(0.0)}  ;SURFACE COORDINATE",
            f"{self._next_block()}     Q204={self._fmt(r_plane)}  ;2ND SET-UP CLEARANCE",
            f"{self._next_block()}     Q206={groove_feed}  ;FEED RATE",
            f"{self._next_block()}     Q210={self._fmt(0.0)}  ;DWELL TIME AT TOP",
            f"{self._next_block()}     Q211={self._fmt(0.0)}  ;DWELL TIME AT DEPTH",
            f"{self._next_block()}     Q214={self._fmt(2.0)}  ;SET-UP CLEARANCE IN TOOL AXIS",
            f"{self._next_block()}     Q226={self._fmt(retract_val)}  ;RETRACT AMOUNT",
            f"{self._next_block()}     Q227={self._fmt(finish_allow)}  ;FINISHING ALLOWANCE",
            f"{self._next_block()}  L  X+{self._fmt(x)} Z+{self._fmt(z)} R0 FMAX",
            f"{self._next_block()}  CYCL CALL",
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
        """生成车削螺纹循环 (CYCL DEF 263/264)。

        Heidenhain TNC使用CYCL DEF 263进行外螺纹车削。

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
            CYCL DEF 263螺纹循环NC代码
        """

        # CYCL DEF 263 外螺纹车削循环格式
        # CYCL DEF 263 THREAD
        # Q200=SET-UP CLEARANCE
        # Q201=DEPTH
        # Q202=PLUNGING DEPTH
        # Q203=SURFACE COORDINATE
        # Q204=2ND SET-UP CLEARANCE
        # Q206=FEED RATE
        # Q239=PITCH
        # Q243=NUMBER OF PASSES
        # Q244=THREAD ANGLE
        r_plane = self.safe_z_height
        lines = [
            f"{self._next_block()}  L  X+{self._fmt(x + 2.0)} Z+{self._fmt(z + 5.0)} R0 FMAX",
            f"{self._next_block()}  S{int(self.get_spindle_rpm())} M03",
            f"{self._next_block()}  CYCL DEF 263 THREAD",
            f"{self._next_block()}     Q200={self._fmt(r_plane)}  ;SET-UP CLEARANCE",
            f"{self._next_block()}     Q201={self._fmt(-abs(depth))}  ;DEPTH",
            f"{self._next_block()}     Q202={self._fmt(first_depth)}  ;PLUNGING DEPTH",
            f"{self._next_block()}     Q203={self._fmt(0.0)}  ;SURFACE COORDINATE",
            f"{self._next_block()}     Q204={self._fmt(r_plane)}  ;2ND SET-UP CLEARANCE",
            f"{self._next_block()}     Q206={self._fmt(self.get_feed_rate(self.rapid_feed * 0.05))}  ;FEED RATE",
            f"{self._next_block()}     Q239={self._fmt(pitch)}  ;PITCH",
            f"{self._next_block()}     Q243={passes}  ;NUMBER OF PASSES",
            f"{self._next_block()}     Q244={self._fmt(tool_angle)}  ;THREAD ANGLE",
            f"{self._next_block()}  L  X+{self._fmt(x)} Z+{self._fmt(z)} R0 FMAX",
            f"{self._next_block()}  CYCL CALL",
        ]
        return "\n".join(lines)
