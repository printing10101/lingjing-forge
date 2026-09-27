"""提示词迭代回归门控（M4b）单元测试。"""

from __future__ import annotations

import pytest

from app.gcode_generation.regression_gate import (
    DEFAULT_THRESHOLDS,
    GateThresholds,
    evaluate_regression,
)


def _stats(
    total: int,
    failures: int,
    one_pass_rate: float | None,
    by_code: dict[str, int] | None = None,
) -> dict:
    return {
        "total": total,
        "failures": failures,
        "successes": total - failures,
        "one_pass_rate": one_pass_rate,
        "by_source": {},
        "by_code": by_code or {},
    }


class TestThresholdsDefaults:
    def test_default_thresholds(self) -> None:
        assert DEFAULT_THRESHOLDS.one_pass_rate_min_drop == 0.05
        assert DEFAULT_THRESHOLDS.error_code_share_max_increase == 0.10
        assert DEFAULT_THRESHOLDS.min_samples == 10


class TestPassCases:
    def test_rate_improved(self) -> None:
        base = _stats(100, 30, 0.70)
        cur = _stats(100, 20, 0.80)
        result = evaluate_regression(base, cur)
        assert result.passed
        assert not result.inconclusive
        assert any("0.7000 → 0.8000" in r for r in result.reasons)

    def test_rate_flat_within_threshold(self) -> None:
        base = _stats(100, 30, 0.70)
        cur = _stats(100, 33, 0.67)  # 降 3 个百分点，阈值 5
        result = evaluate_regression(base, cur)
        assert result.passed
        assert not result.inconclusive

    def test_rate_drop_exactly_at_threshold_passes(self) -> None:
        base = _stats(100, 30, 0.70)
        cur = _stats(100, 35, 0.65)  # 恰好 5 个百分点 = 阈值（不含），放行
        result = evaluate_regression(base, cur)
        assert result.passed

    def test_no_failures_both_sides(self) -> None:
        result = evaluate_regression(
            _stats(50, 0, None), _stats(50, 0, None)
        )
        assert result.passed
        assert not result.inconclusive


class TestFailCases:
    def test_rate_regression(self) -> None:
        base = _stats(100, 30, 0.70)
        cur = _stats(100, 50, 0.50)  # 降 20 个百分点
        result = evaluate_regression(base, cur)
        assert not result.passed
        assert not result.inconclusive
        assert any("一次通过率退化" in r for r in result.reasons)

    def test_error_code_share_worsens(self) -> None:
        # 通过率持平，但 L2（软限位）失败集中恶化：25% → 60%
        base = _stats(100, 40, 0.60, by_code={"L1": 20, "L2": 10, "L3": 10})
        cur = _stats(100, 40, 0.60, by_code={"L1": 6, "L2": 24, "L3": 10})
        result = evaluate_regression(base, cur)
        assert not result.passed
        assert any("L2" in r and "失败占比恶化" in r for r in result.reasons)

    def test_new_error_code_appears_worsens(self) -> None:
        # 新错误码从 0 涨到 30% 占比
        base = _stats(100, 40, 0.60, by_code={"L1": 40})
        cur = _stats(100, 40, 0.60, by_code={"L1": 28, "L5": 12})
        result = evaluate_regression(base, cur)
        assert not result.passed
        assert any("L5" in r for r in result.reasons)

    def test_rate_and_code_both_fail_reports_all_reasons(self) -> None:
        base = _stats(100, 40, 0.60, by_code={"L1": 10, "L2": 30})
        cur = _stats(100, 60, 0.40, by_code={"L1": 5, "L2": 55})
        result = evaluate_regression(base, cur)
        assert not result.passed
        assert len([r for r in result.reasons if "恶化" in r or "退化" in r]) >= 2


class TestInconclusive:
    def test_baseline_too_small(self) -> None:
        result = evaluate_regression(
            _stats(5, 2, 0.60), _stats(100, 60, 0.40)
        )
        assert result.passed
        assert result.inconclusive
        assert any("样本不足" in r for r in result.reasons)

    def test_current_too_small(self) -> None:
        result = evaluate_regression(
            _stats(100, 30, 0.70), _stats(3, 3, 0.0)
        )
        assert result.inconclusive


class TestCustomThresholds:
    def test_stricter_threshold_blocks(self) -> None:
        th = GateThresholds(one_pass_rate_min_drop=0.01)
        base = _stats(100, 30, 0.70)
        cur = _stats(100, 33, 0.67)  # 降 3 个百分点：默认放行、严格拦截
        assert evaluate_regression(base, cur).passed
        assert not evaluate_regression(base, cur, thresholds=th).passed

    def test_result_to_dict_roundtrip(self) -> None:
        result = evaluate_regression(_stats(100, 30, 0.70), _stats(100, 50, 0.50))
        d = result.to_dict()
        assert d["passed"] is False
        assert d["baseline_summary"]["one_pass_rate"] == 0.70
        assert d["current_summary"]["one_pass_rate"] == 0.50
