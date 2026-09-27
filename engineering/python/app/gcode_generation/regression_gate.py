"""提示词迭代回归门控（Phase 0 自进化 · M4b）。

任何对生成链路提示词/参数推荐逻辑的改动，重放失败案例库基线后，
用本模块对比改动前后的 ``stats()`` 报表，判定是否允许合入：

- 规则 1（一次通过率退化）：one_pass_rate 降幅超过阈值 → FAIL；
- 规则 2（错误码恶化）：某错误码在全部失败中的占比增幅超过阈值
  （百分点）→ FAIL——通过率持平但某类失败集中恶化同样不放行；
- 规则 3（样本不足）：任一侧样本量 < min_samples → 判定不充分
  （passed=True 但 inconclusive=True，提示继续积累样本，不武断放行/拦截）。

纯函数设计：``evaluate_regression`` 只依赖两份 stats() 字典，不触库、
不依赖 LLM——谁迭代提示词，谁在灰度前后各取一次 ``store.stats()``
调用本函数即可。

stats() 报表结构（见 failure_case_store.FailureCaseStore.stats）：
    {total, failures, successes, one_pass_rate, by_source, by_code}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "GateThresholds",
    "GateResult",
    "DEFAULT_THRESHOLDS",
    "evaluate_regression",
]


@dataclass(frozen=True)
class GateThresholds:
    """回归门控阈值。"""

    one_pass_rate_min_drop: float = 0.05  # 一次通过率降幅阈值（百分点）
    error_code_share_max_increase: float = 0.10  # 错误码失败占比增幅阈值（百分点）
    min_samples: int = 10  # 最小样本量（total，任一侧不足即 inconclusive）


#: 默认阈值：一次通过率降幅 > 5 个百分点判退化；错误码失败占比增幅
#: > 10 个百分点判恶化；样本 < 10 不武断。
DEFAULT_THRESHOLDS = GateThresholds()


@dataclass
class GateResult:
    """门控判定结果。

    Attributes:
        passed: 是否放行（inconclusive 时也为 True）。
        inconclusive: 样本不足、无法作出可信判定。
        reasons: 触发的判定说明（FAIL 原因 / inconclusive 说明 / PASS 摘要）。
        baseline_summary / current_summary: 两侧关键指标快照，便于日志留痕。
    """

    passed: bool
    inconclusive: bool = False
    reasons: list[str] = field(default_factory=list)
    baseline_summary: dict[str, Any] = field(default_factory=dict)
    current_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "inconclusive": self.inconclusive,
            "reasons": list(self.reasons),
            "baseline_summary": dict(self.baseline_summary),
            "current_summary": dict(self.current_summary),
        }


def _summarize(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "total": stats.get("total", 0),
        "failures": stats.get("failures", 0),
        "successes": stats.get("successes", 0),
        "one_pass_rate": stats.get("one_pass_rate"),
    }


def _error_code_shares(stats: dict[str, Any]) -> dict[str, float]:
    """各错误码在全部失败中的占比（0-1）。failures==0 时返回空表。"""
    failures = stats.get("failures", 0)
    if failures <= 0:
        return {}
    by_code = stats.get("by_code") or {}
    total_codes = sum(by_code.values())
    if total_codes <= 0:
        return {}
    return {code: n / total_codes for code, n in by_code.items()}


def evaluate_regression(
    baseline: dict[str, Any],
    current: dict[str, Any],
    thresholds: GateThresholds | None = None,
) -> GateResult:
    """对比提示词/参数逻辑改动前后的失败案例库报表，判定是否放行。

    Args:
        baseline: 改动前 ``FailureCaseStore.stats()`` 报表。
        current: 改动后 ``FailureCaseStore.stats()`` 报表。
        thresholds: 阈值（None 用默认）。

    Returns:
        GateResult：passed=False 表示门控拦截（不允许合入提示词改动）。
    """
    th = thresholds or DEFAULT_THRESHOLDS
    reasons: list[str] = []
    baseline_summary = _summarize(baseline)
    current_summary = _summarize(current)

    # 规则 3：样本不足 → 不武断
    if baseline.get("total", 0) < th.min_samples or current.get("total", 0) < th.min_samples:
        return GateResult(
            passed=True,
            inconclusive=True,
            reasons=[
                f"样本不足（baseline={baseline.get('total', 0)}，"
                f"current={current.get('total', 0)}，阈值={th.min_samples}），"
                "无法作出可信判定；请继续积累失败案例后重放。"
            ],
            baseline_summary=baseline_summary,
            current_summary=current_summary,
        )

    failed = False

    # 规则 1：一次通过率退化
    base_rate = baseline.get("one_pass_rate")
    cur_rate = current.get("one_pass_rate")
    if base_rate is not None and cur_rate is not None:
        drop = base_rate - cur_rate  # 正值 = 退化
        if drop > th.one_pass_rate_min_drop:
            failed = True
            reasons.append(
                f"一次通过率退化：{base_rate:.4f} → {cur_rate:.4f}"
                f"（降幅 {drop * 100:.2f} 个百分点，阈值 {th.one_pass_rate_min_drop * 100:.2f}）"
            )
        else:
            reasons.append(f"一次通过率 {base_rate:.4f} → {cur_rate:.4f}（未超阈值，通过）")

    # 规则 2：错误码占比恶化
    base_shares = _error_code_shares(baseline)
    cur_shares = _error_code_shares(current)
    for code, cur_share in cur_shares.items():
        base_share = base_shares.get(code, 0.0)
        increase = cur_share - base_share
        if increase > th.error_code_share_max_increase:
            failed = True
            reasons.append(
                f"错误码 {code} 失败占比恶化：{base_share * 100:.1f}% → "
                f"{cur_share * 100:.1f}%（增幅 {increase * 100:.2f} 个百分点，"
                f"阈值 {th.error_code_share_max_increase * 100:.1f}）"
            )

    if failed:
        return GateResult(
            passed=False,
            inconclusive=False,
            reasons=reasons,
            baseline_summary=baseline_summary,
            current_summary=current_summary,
        )

    if not reasons:
        reasons.append("两期均无失败案例，无可比对项，默认放行。")
    return GateResult(
        passed=True,
        inconclusive=False,
        reasons=reasons,
        baseline_summary=baseline_summary,
        current_summary=current_summary,
    )
