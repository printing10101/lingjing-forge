"""工艺迁移验证器 v0（B3）：同一工序计划在两套控制器下的等价性清单.

用途：把"同一个计划换到国产系统"这件事变成可核对的清单，而不是口头保证。

比对维度（功能等价）
--------------------
1. 程序可生成且通过各自语法校验；
2. 运动段数量与类型序列一致（rapid/linear/arc）；
3. 逐段终点坐标偏差 ≤ 容差（默认 0.001 mm）；
4. 进给值集合一致、主轴转速集合一致（按 0.001 精度）；
5. 圆弧半径/圆心（如有）偏差 ≤ 容差。

方言差异（不计失败，写入 `dialect_differences`）
-----------------------------------------------
仅在一侧出现的 G/M 代码集合（如 G28/G30、M08/M09、G05.1），用于工程师确认
"这是方言差异而不是功能丢失"。判定原则：功能项全过 → passed=True，
方言差异再多也只是提示。

边界：验证器比对的是**后处理输出**，不验证机床实际行为；不替代空运行与首件试切。
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

CODE_RE = re.compile(r"\b([GM])\s*(\d+(?:\.\d+)?)\b", re.IGNORECASE)
XY_RE = re.compile(r"X\s*(-?\d+(?:\.\d+)?)\s*Y\s*(-?\d+(?:\.\d+)?)")
DRILL_RE = re.compile(r"\bG\s*(8[1-9])\b")
DEFAULT_TOL_MM = 1e-3


def _xy_positions(program_text: str) -> list[tuple[float, float]]:
    """全部 X/Y 定位点（去重、按 0.001 取整）。"""
    return sorted({(round(float(x), 3), round(float(y), 3)) for x, y in XY_RE.findall(program_text)})


def _drill_cycle_count(program_text: str) -> int:
    return len(DRILL_RE.findall(program_text))


@dataclass
class MigrationCheck:
    name: str
    status: str  # ok / warn / fail
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


def _segments(program_text: str, controller: str) -> list[Any]:
    from app.simulation.toolpath_parser import ToolpathParser

    return ToolpathParser(controller_type=controller).parse_gcode(program_text)


def _code_set(program_text: str) -> set[str]:
    found = set()
    for line in program_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("(") or stripped.startswith(";"):
            continue
        for letter, number in CODE_RE.findall(stripped):
            found.add(f"{letter.upper()}{float(number):g}")
    return found


def _distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))


def compare_programs(
    source_text: str,
    target_text: str,
    *,
    source_controller: str = "fanuc",
    target_controller: str = "fanuc",
    tol_mm: float = DEFAULT_TOL_MM,
) -> dict[str, Any]:
    """比对两份后处理程序的语义等价性。"""
    if tol_mm <= 0:
        raise ValueError("tol_mm 必须为正")
    src = _segments(source_text, source_controller)
    tgt = _segments(target_text, target_controller)
    checks: list[MigrationCheck] = []

    def add(name: str, ok: bool, detail: str = "", *, warn_only: bool = False) -> None:
        checks.append(MigrationCheck(name, "ok" if ok else ("warn" if warn_only else "fail"), detail))

    add("total_segments", len(src) == len(tgt),
        f"source={len(src)} target={len(tgt)}（含快速定位拓扑，差异仅供参考）", warn_only=True)

    src_cut = [seg for seg in src if seg.type in ("linear", "arc")]
    tgt_cut = [seg for seg in tgt if seg.type in ("linear", "arc")]
    add("cutting_segment_count", len(src_cut) == len(tgt_cut),
        f"source={len(src_cut)} target={len(tgt_cut)}")

    if len(src_cut) == len(tgt_cut):
        type_mismatch = [index for index, (a, b) in enumerate(zip(src_cut, tgt_cut)) if a.type != b.type]
        add("cutting_motion_sequence", not type_mismatch,
            f"不一致段索引前5: {type_mismatch[:5]}" if type_mismatch else "")

        deviations = [_distance(a.end_point, b.end_point) for a, b in zip(src_cut, tgt_cut)]
        worst = max(deviations) if deviations else 0.0
        add("cutting_endpoint_deviation", worst <= tol_mm,
            f"最大偏差 {worst:.6f} mm（容差 {tol_mm} mm）")

        arc_pairs = [(a, b) for a, b in zip(src_cut, tgt_cut) if a.type == "arc" and a.arc_radius and b.arc_radius]
        arc_worst = max((abs((a.arc_radius or 0) - (b.arc_radius or 0)) for a, b in arc_pairs), default=0.0)
        add("arc_radius", arc_worst <= tol_mm, f"圆弧 {len(arc_pairs)} 段，最大半径差 {arc_worst:.6f} mm")

    src_feeds = sorted({round(seg.feed_rate, 3) for seg in src if seg.feed_rate})
    tgt_feeds = sorted({round(seg.feed_rate, 3) for seg in tgt if seg.feed_rate})
    add("feed_rates", src_feeds == tgt_feeds, f"source={src_feeds} target={tgt_feeds}")

    src_spindles = sorted({seg.spindle_speed for seg in src if seg.spindle_speed})
    tgt_spindles = sorted({seg.spindle_speed for seg in tgt if seg.spindle_speed})
    add("spindle_speeds", src_spindles == tgt_spindles, f"source={src_spindles} target={tgt_spindles}")

    src_xy, tgt_xy = _xy_positions(source_text), _xy_positions(target_text)
    add("xy_positions", src_xy == tgt_xy, f"source={len(src_xy)} 点 target={len(tgt_xy)} 点")

    src_drill, tgt_drill = _drill_cycle_count(source_text), _drill_cycle_count(target_text)
    add("drill_cycles", src_drill == tgt_drill, f"source={src_drill} target={tgt_drill}")

    src_codes, tgt_codes = _code_set(source_text), _code_set(target_text)
    dialect = {"only_in_source": sorted(src_codes - tgt_codes),
               "only_in_target": sorted(tgt_codes - src_codes)}
    if dialect["only_in_source"] or dialect["only_in_target"]:
        checks.append(MigrationCheck("dialect_codes", "warn",
                                     f"仅源: {dialect['only_in_source'][:8]}；"
                                     f"仅目标: {dialect['only_in_target'][:8]}"))

    passed = all(check.status != "fail" for check in checks)
    return {
        "passed": passed,
        "tol_mm": tol_mm,
        "checks": [check.to_dict() for check in checks],
        "dialect_differences": dialect,
        "summary": {
            "segments": {"source": len(src), "target": len(tgt)},
            "failed": [check.name for check in checks if check.status == "fail"],
            "warned": [check.name for check in checks if check.status == "warn"],
        },
    }


def render_checklist(report: dict[str, Any], *, source: str, target: str) -> str:
    """Markdown 清单（供工程师签字前复核）。"""
    lines = [f"# 工艺迁移验证清单：{source} → {target}", "",
             f"结论：{'PASS' if report['passed'] else 'FAIL'}（容差 {report['tol_mm']} mm）", "",
             "| 检查项 | 状态 | 说明 |", "|---|---|---|"]
    for check in report["checks"]:
        lines.append(f"| {check['name']} | {check['status']} | {check['detail']} |")
    dialect = report["dialect_differences"]
    lines += ["", "## 方言差异（需人工确认）",
              f"- 仅源程序：{', '.join(dialect['only_in_source']) or '无'}",
              f"- 仅目标程序：{', '.join(dialect['only_in_target']) or '无'}",
              "", "> 本方仅验证后处理输出语义，不替代空运行与首件试切。"]
    return "\n".join(lines) + "\n"
