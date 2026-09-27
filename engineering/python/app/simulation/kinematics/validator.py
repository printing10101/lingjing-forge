"""机床运动学校验器：对展开后的运动序列做上机前安全检查。

检查项（对照 VERICUT/NCSIMUL 类机床仿真软件的程序级检查子集）：

====  ================================  ========
代码  检查项                              严重度
====  ================================  ========
K001  轴行程超限（含圆弧采样点）          error
K002  主轴未运转执行切削（G1/G2/G3/孔底）  error
K003  切削移动未设定进给 F                error
K004  切削进给超过机床切削进给上限         error
K005  主轴转速超过机床转速上限            error
K006  快移(G00)进入毛坯区/在切削深度横移   error
K007  未装刀执行切削                      error
K008  刀号超出刀库容量                    error
K009  程序无 M30/M02 结束                 warning
====  ================================  ========

边界：本模块是**程序级运动学检查**（秒级、无几何），不替代体素材料去除
仿真（VoxelValidator）与外部 CAM 二次校验；三者串联构成阶段 7 的
程序安全三道闸。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.simulation.kinematics.interpreter import (
    GCodeKinematicsInterpreter,
    ProgramTrace,
)
from app.simulation.kinematics.machine import MachineProfile, load_profile

_ISSUE_CODES: dict[str, tuple[str, str]] = {
    # code -> (severity, 检查项名称)
    "K001": ("error", "轴行程超限"),
    "K002": ("error", "主轴未运转执行切削"),
    "K003": ("error", "切削移动未设定进给"),
    "K004": ("error", "切削进给超上限"),
    "K005": ("error", "主轴转速超上限"),
    "K006": ("error", "快移进入毛坯区"),
    "K007": ("error", "未装刀执行切削"),
    "K008": ("error", "刀号超出刀库容量"),
    "K009": ("warning", "程序无结束指令"),
}


@dataclass
class KinematicsIssue:
    """单个运动学问题（定位到源程序行）。"""

    code: str
    line_no: int
    block: str
    message: str
    severity: str = "error"
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "line_no": self.line_no,
            "block": self.block,
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass
class KinematicsReport:
    """运动学校验聚合报告。"""

    passed: bool = True
    machine_id: str = ""
    lines_processed: int = 0
    move_count: int = 0
    error_count: int = 0
    warning_count: int = 0
    axis_extent: dict[str, list[float]] = field(default_factory=dict)
    issues: list[KinematicsIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "machine_id": self.machine_id,
            "lines_processed": self.lines_processed,
            "move_count": self.move_count,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "axis_extent": {k: [round(v[0], 4), round(v[1], 4)] for k, v in self.axis_extent.items()},
            "issues": [i.to_dict() for i in self.issues],
            "warnings": list(self.warnings),
            "duration_seconds": round(self.duration_seconds, 4),
        }


class KinematicsValidator:
    """程序级运动学校验器（3 轴语义，组合解释器）。"""

    def __init__(self, profile: MachineProfile | None = None) -> None:
        self._profile = profile or load_profile()
        self._interpreter = GCodeKinematicsInterpreter()

    @property
    def profile(self) -> MachineProfile:
        return self._profile

    def validate(
        self,
        gcode_text: str,
        work_offset: tuple[float, float, float] = (0.0, 0.0, 0.0),
        stock_top_z: float | None = None,
    ) -> KinematicsReport:
        """执行运动学校验。

        Args:
            gcode_text: NC 程序全文。
            work_offset: 工件坐标系 → 机床坐标系偏移 (dx, dy, dz)，
                machine = program + work_offset。
            stock_top_z: 毛坯顶面 Z（程序坐标）。提供后启用 K006
                （快移进毛坯区/在切削深度横移）检查；None 则跳过。

        Returns:
            KinematicsReport（passed = 无 error 级问题）。
        """
        start = time.perf_counter()
        trace: ProgramTrace = self._interpreter.run(gcode_text)

        issues: list[KinematicsIssue] = []
        issues.extend(self._trace_issues(trace))
        axis_extent = self._check_travel(trace, work_offset, issues)
        self._check_state_semantics(trace, issues)
        if stock_top_z is not None:
            self._check_rapid_below_stock(trace, stock_top_z, issues)

        error_count = sum(1 for i in issues if i.severity == "error")
        report = KinematicsReport(
            passed=error_count == 0,
            machine_id=self._profile.machine_id,
            lines_processed=trace.lines_processed,
            move_count=len(trace.records),
            error_count=error_count,
            warning_count=len(issues) - error_count,
            axis_extent=axis_extent,
            issues=issues,
            warnings=list(trace.warnings),
            duration_seconds=time.perf_counter() - start,
        )
        return report

    # ---------- 各检查项 ----------

    def _trace_issues(self, trace: ProgramTrace) -> list[KinematicsIssue]:
        """转速上限（K005）——程序级，只需看峰值。"""
        issues: list[KinematicsIssue] = []
        if trace.max_spindle_speed > self._profile.max_spindle_rpm + 1e-9:
            issues.append(
                KinematicsIssue(
                    code="K005",
                    line_no=0,
                    block="",
                    message=(
                        f"程序主轴转速峰值 {trace.max_spindle_speed:.0f} 超过机床上限 "
                        f"{self._profile.max_spindle_rpm:.0f} RPM。"
                    ),
                    suggestion="建议操作：降低 S 值或更换支持更高转速的机床。",
                )
            )
        return issues

    def _check_travel(
        self,
        trace: ProgramTrace,
        work_offset: tuple[float, float, float],
        issues: list[KinematicsIssue],
    ) -> dict[str, list[float]]:
        """K001：所有运动采样点换算到机床坐标后须在行程内。"""
        extent: dict[str, list[float]] = {a: [float("inf"), float("-inf")] for a in self._profile.axis_names()}
        for rec in trace.records:
            for px, py, pz in rec.points:
                machine_pt = (px + work_offset[0], py + work_offset[1], pz + work_offset[2])
                for axis_name, value in zip(("X", "Y", "Z"), machine_pt):
                    lo, hi = extent[axis_name]
                    extent[axis_name] = [min(lo, value), max(hi, value)]
                    limits = self._profile.axes.get(axis_name)
                    if limits is None or limits.contains(value):
                        continue
                    issues.append(
                        KinematicsIssue(
                            code="K001",
                            line_no=rec.line_no,
                            block=rec.block,
                            message=(
                                f"{axis_name} 轴坐标 {value:.3f} 超出机床行程 "
                                f"[{limits.min_mm:.1f}, {limits.max_mm:.1f}]（机床坐标）。"
                            ),
                            suggestion=("建议操作：检查工件坐标系（G54 偏移）与装夹位置，或改用行程更大的机床。"),
                        )
                    )
        # 同一行多点超限去重：按 (line_no, axis) 折叠
        seen: set[tuple[int, str]] = set()
        deduped: list[KinematicsIssue] = []
        for issue in issues:
            key = (issue.line_no, issue.message[:24])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(issue)
        issues[:] = deduped
        for axis_name in extent:
            lo, hi = extent[axis_name]
            if lo == float("inf"):
                extent[axis_name] = [0.0, 0.0]
        return extent

    def _check_state_semantics(self, trace: ProgramTrace, issues: list[KinematicsIssue]) -> None:
        """K002 主轴 / K003 进给 / K004 限幅 / K007 装刀 / K008 刀号 / K009 结束。"""
        feed_ever_set = any(r.feed is not None for r in trace.records)
        for rec in trace.records:
            if not rec.is_cutting:
                continue
            if not rec.spindle_on:
                issues.append(
                    KinematicsIssue(
                        code="K002",
                        line_no=rec.line_no,
                        block=rec.block,
                        message="主轴未运转执行切削移动。",
                        suggestion="建议操作：在切削前加 M03 S[转速]。",
                    )
                )
            if rec.feed is None and not feed_ever_set:
                issues.append(
                    KinematicsIssue(
                        code="K003",
                        line_no=rec.line_no,
                        block=rec.block,
                        message="切削移动未设定进给速度 F。",
                        suggestion="建议操作：添加 F[mm/min] 模态指令。",
                    )
                )
            if rec.feed is not None and rec.feed > self._profile.max_cutting_feed + 1e-9:
                issues.append(
                    KinematicsIssue(
                        code="K004",
                        line_no=rec.line_no,
                        block=rec.block,
                        message=(f"切削进给 {rec.feed:.0f} 超过机床上限 {self._profile.max_cutting_feed:.0f} mm/min。"),
                        suggestion="建议操作：降低 F 值。",
                    )
                )
            if rec.tool is None:
                issues.append(
                    KinematicsIssue(
                        code="K007",
                        line_no=rec.line_no,
                        block=rec.block,
                        message="未装刀执行切削移动（程序中无 T..M06）。",
                        suggestion="建议操作：在程序头加 T[刀号] M06。",
                    )
                )
        # 刀号范围：检查所有出现过的 tool
        for rec in trace.records:
            if rec.tool is not None and not 1 <= rec.tool <= self._profile.tool_count:
                issues.append(
                    KinematicsIssue(
                        code="K008",
                        line_no=rec.line_no,
                        block=rec.block,
                        message=(f"刀号 T{rec.tool} 超出刀库容量 {self._profile.tool_count}。"),
                        suggestion="建议操作：修正 T 值。",
                    )
                )
                break
        if not trace.program_ended:
            issues.append(
                KinematicsIssue(
                    code="K009",
                    line_no=trace.lines_processed,
                    block="",
                    message="程序缺少 M30/M02 结束指令。",
                    severity="warning",
                    suggestion="建议操作：程序尾补 M30。",
                )
            )

    def _check_rapid_below_stock(
        self,
        trace: ProgramTrace,
        stock_top_z: float,
        issues: list[KinematicsIssue],
    ) -> None:
        """K006：G00 终点低于毛坯顶面（快移扎刀），或在低于顶面处横向快移。"""
        for rec in trace.records:
            if rec.kind != "rapid":
                continue
            below_start = rec.start[2] < stock_top_z - 1e-9
            below_end = rec.end[2] < stock_top_z - 1e-9
            lateral = math_hypot2d(rec.start, rec.end) > 1e-9
            if not (below_start or below_end):
                continue
            if lateral and (below_start and below_end):
                issues.append(
                    KinematicsIssue(
                        code="K006",
                        line_no=rec.line_no,
                        block=rec.block,
                        message=(
                            f"快移在毛坯顶面（Z={stock_top_z:.2f}）下方横向移动 "
                            f"{math_hypot2d(rec.start, rec.end):.1f}mm，存在撞刀风险。"
                        ),
                        suggestion="建议操作：先抬刀至安全高度再快移（G00 Z[safe] 后再 X/Y 定位）。",
                    )
                )
            elif below_end and not below_start:
                issues.append(
                    KinematicsIssue(
                        code="K006",
                        line_no=rec.line_no,
                        block=rec.block,
                        message=(
                            f"快移终点 Z={rec.end[2]:.2f} 低于毛坯顶面 {stock_top_z:.2f}，快速下刀将撞击毛坯/夹具。"
                        ),
                        suggestion="建议操作：快移只在安全高度以上；下刀用 G01 进给或固定循环。",
                    )
                )


def math_hypot2d(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    """两点 XY 平面距离。"""
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
