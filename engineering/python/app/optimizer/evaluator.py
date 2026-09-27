"""推荐效果评估器（Phase D，纯白盒）。

评估推荐参数 vs 基线/历史的效果：
- evaluate_recommendation: 单次推荐 → 实测结果 打分（0-1）
- compare_parameter_sets: 两组参数（A/B）批量对比，输出提升率

指标：
- 节拍（cycle_time_s，越低越好）
- 刀具磨损（tool_wear_percent，越低越好）
- 表面粗糙度（surface_roughness_ra，越低越好）
- 结果判定（ok=1.0, rework=0.5, scrap=0.0）

设计要点：纯函数、零框架依赖、可独立测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class EvaluationResult:
    """单条推荐效果评估。"""

    score: float  # 0-1 综合得分
    cycle_time_ok: bool
    wear_ok: bool
    roughness_ok: bool
    result_ok: bool
    details: dict[str, Any]


@dataclass
class ComparisonResult:
    """两组参数（A/B）批量对比结果。"""

    better: str  # "a" / "b" / "tie"
    improvement_pct: float  # 正数表示 A 优于 B 的幅度（%）
    a_samples: int
    b_samples: int
    a_avg_cycle: float | None
    b_avg_cycle: float | None
    a_avg_wear: float | None
    b_avg_wear: float | None


# 阈值（可配置默认值）
_CYCLE_THRESHOLD_S = 300.0  # 节拍超此值视为异常慢
_WEAR_THRESHOLD_PCT = 50.0  # 磨损超此值视为异常高
_ROUGHNESS_THRESHOLD_UM = 3.2  # Ra 超此值视为不合格


def evaluate_recommendation(
    cycle_time_s: float | None,
    tool_wear_percent: float | None,
    surface_roughness_ra: float | None,
    result: str = "ok",
    cycle_threshold: float = _CYCLE_THRESHOLD_S,
    wear_threshold: float = _WEAR_THRESHOLD_PCT,
    roughness_threshold: float = _ROUGHNESS_THRESHOLD_UM,
) -> EvaluationResult:
    """评估一条加工实测结果。

    Args:
        cycle_time_s: 实际节拍（s）。
        tool_wear_percent: 刀具磨损（0-100）。
        surface_roughness_ra: 表面粗糙度 Ra（μm）。
        result: 结果判定 ok/rework/scrap。

    Returns:
        EvaluationResult（score 0-1）。
    """
    result_ok = result == "ok"
    cycle_ok = cycle_time_s is not None and cycle_time_s <= cycle_threshold
    wear_ok = tool_wear_percent is not None and tool_wear_percent <= wear_threshold
    roughness_ok = surface_roughness_ra is not None and surface_roughness_ra <= roughness_threshold

    # 得分：结果判定权重最高（0.5），其余各 0.1667
    score = 0.0
    score += 0.5 if result_ok else (0.25 if result == "rework" else 0.0)
    score += 1.0 / 6.0 if cycle_ok else 0.0
    score += 1.0 / 6.0 if wear_ok else 0.0
    score += 1.0 / 6.0 if roughness_ok else 0.0
    score = round(min(max(score, 0.0), 1.0), 4)

    return EvaluationResult(
        score=score,
        cycle_time_ok=cycle_ok,
        wear_ok=wear_ok,
        roughness_ok=roughness_ok,
        result_ok=result_ok,
        details={
            "cycle_time_s": cycle_time_s,
            "tool_wear_percent": tool_wear_percent,
            "surface_roughness_ra": surface_roughness_ra,
            "result": result,
        },
    )


def compare_parameter_sets(
    a_results: list[dict[str, Any]],
    b_results: list[dict[str, Any]],
) -> ComparisonResult:
    """对比两组（A/B）加工结果，判断哪组更优。

    Args:
        a_results: A 组记录列表，每条含 cycle_time_s / tool_wear_percent。
        b_results: B 组记录列表，同上。

    Returns:
        ComparisonResult（better: a/b/tie + 提升率%）。
    """
    a_cycle = [r["cycle_time_s"] for r in a_results if r.get("cycle_time_s")]
    b_cycle = [r["cycle_time_s"] for r in b_results if r.get("cycle_time_s")]
    a_wear = [r["tool_wear_percent"] for r in a_results if r.get("tool_wear_percent") is not None]
    b_wear = [r["tool_wear_percent"] for r in b_results if r.get("tool_wear_percent") is not None]

    a_avg_cycle = sum(a_cycle) / len(a_cycle) if a_cycle else None
    b_avg_cycle = sum(b_cycle) / len(b_cycle) if b_cycle else None
    a_avg_wear = sum(a_wear) / len(a_wear) if a_wear else None
    b_avg_wear = sum(b_wear) / len(b_wear) if b_wear else None

    # 节拍提升率：A 比 B 快多少（%）
    improvement_pct = 0.0
    better = "tie"
    if a_avg_cycle is not None and b_avg_cycle is not None and b_avg_cycle > 0:
        improvement_pct = round((b_avg_cycle - a_avg_cycle) / b_avg_cycle * 100.0, 2)
        if improvement_pct > 1.0:
            better = "a"
        elif improvement_pct < -1.0:
            better = "b"

    return ComparisonResult(
        better=better,
        improvement_pct=improvement_pct,
        a_samples=len(a_results),
        b_samples=len(b_results),
        a_avg_cycle=a_avg_cycle,
        b_avg_cycle=b_avg_cycle,
        a_avg_wear=a_avg_wear,
        b_avg_wear=b_avg_wear,
    )


__all__ = [
    "EvaluationResult",
    "ComparisonResult",
    "evaluate_recommendation",
    "compare_parameter_sets",
]
