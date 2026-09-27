"""Unit tests for optimizer 白盒模块（baseline/recommender/evaluator）。"""

from __future__ import annotations

import pytest

from app.optimizer.baseline import (
    BaselineEntry,
    BaselineLibrary,
    DEFAULT_BASELINE,
    _normalize_material,
    lookup_baseline,
)
from app.optimizer.evaluator import (
    compare_parameter_sets,
    evaluate_recommendation,
)
from app.optimizer.recommender import (
    OptimizationTarget,
    ParameterRecommender,
    RecommendationStrategy,
    clamp_to_safe_bounds,
)


# baseline


class TestBaseline:
    def test_default_baseline_has_entries(self) -> None:
        assert len(DEFAULT_BASELINE) >= 15

    def test_lookup_al6061_milling(self) -> None:
        entry = lookup_baseline("AL6061", "milling")
        assert entry is not None
        assert entry.material == "AL6061"
        assert entry.depth_of_cut_mm == 2.0

    def test_lookup_alias_case_insensitive(self) -> None:
        entry = lookup_baseline("al6061", "MILLING")
        assert entry is not None
        assert entry.material == "AL6061"

    def test_lookup_chinese_alias(self) -> None:
        entry = lookup_baseline("铝合金", "milling")
        assert entry is not None

    def test_lookup_unknown_material_returns_none(self) -> None:
        assert lookup_baseline("UNKNOWN-XYZ", "milling") is None

    def test_lookup_unknown_type_returns_none(self) -> None:
        assert lookup_baseline("AL6061", "edm") is None

    def test_normalize_material(self) -> None:
        assert _normalize_material(" SS304 ") == "SS304"
        assert _normalize_material("304") == "SS304"
        assert _normalize_material("钛合金") == "Ti6Al4V"

    def test_library_add_overrides(self) -> None:
        lib = BaselineLibrary()
        lib.add(
            BaselineEntry(
                material="AL6061",
                machining_type="milling",
                depth_of_cut_mm=3.0,
                feed_mm_per_rev=0.3,
                spindle_rpm=9000,
                cutting_speed_m_min=350,
            )
        )
        entry = lib.lookup("AL6061", "milling")
        assert entry is not None
        assert entry.depth_of_cut_mm == 3.0
        # 覆盖后库大小不变（同键替换）
        assert len(lib.entries) == len(DEFAULT_BASELINE)


# recommender


class TestClamp:
    def test_clamp_within_bounds(self) -> None:
        assert clamp_to_safe_bounds(5.0, 0.0, 10.0) == (5.0, False)

    def test_clamp_below(self) -> None:
        assert clamp_to_safe_bounds(-1.0, 0.0, 10.0) == (0.0, True)

    def test_clamp_above(self) -> None:
        assert clamp_to_safe_bounds(15.0, 0.0, 10.0) == (10.0, True)


class TestParameterRecommender:
    def test_recommend_l0_baseline(self) -> None:
        rec = ParameterRecommender().recommend("AL6061", "milling")
        assert rec is not None
        assert rec.strategy == RecommendationStrategy.L0_BASELINE
        assert rec.depth_of_cut_mm == 2.0
        assert rec.feed_mm_per_rev == 0.2
        assert rec.spindle_rpm == 8000
        assert rec.basis[0]["source"] == "baseline"

    def test_recommend_unknown_returns_none(self) -> None:
        rec = ParameterRecommender().recommend("UNKNOWN-XYZ", "milling")
        assert rec is None

    def test_recommend_l1_stats_overrides(self) -> None:
        def fake_stats(material: str, tool_id: str, mtype: str) -> dict | None:
            return {
                "depth_of_cut_mm": 1.2,
                "feed_mm_per_rev": 0.25,
                "spindle_rpm": 7000,
                "cutting_speed_m_min": 250,
                "sample_count": 8,
            }

        rec = ParameterRecommender(stats_callback=fake_stats).recommend("AL6061", "milling")
        assert rec is not None
        assert rec.strategy == RecommendationStrategy.L1_STATISTICAL
        assert rec.depth_of_cut_mm == 1.2
        assert rec.feed_mm_per_rev == 0.25
        assert rec.spindle_rpm == 7000
        assert rec.confidence > 0.5  # 样本数 8 → 0.5 + 8*0.05 = 0.9

    def test_recommend_l1_without_baseline(self) -> None:
        def fake_stats(material: str, tool_id: str, mtype: str) -> dict | None:
            return {"depth_of_cut_mm": 1.0, "feed_mm_per_rev": 0.1, "spindle_rpm": 5000}

        rec = ParameterRecommender(stats_callback=fake_stats).recommend("UNKNOWN-XYZ", "milling")
        assert rec is not None
        assert rec.strategy == RecommendationStrategy.L1_STATISTICAL

    def test_stats_exception_falls_back_to_l0(self) -> None:
        def broken_stats(material: str, tool_id: str, mtype: str) -> dict | None:
            raise RuntimeError("stats unavailable")

        rec = ParameterRecommender(stats_callback=broken_stats).recommend("AL6061", "milling")
        assert rec is not None
        assert rec.strategy == RecommendationStrategy.L0_BASELINE

    def test_recommend_target_cycle_time(self) -> None:
        rec = ParameterRecommender().recommend("AL6061", "milling", target=OptimizationTarget.CYCLE_TIME)
        assert rec is not None
        assert rec.feed_mm_per_rev > 0.2  # 0.2 * 1.2 = 0.24
        assert rec.spindle_rpm > 8000  # 8000 * 1.1 = 8800

    def test_recommend_target_tool_life(self) -> None:
        rec = ParameterRecommender().recommend("AL6061", "milling", target=OptimizationTarget.TOOL_LIFE)
        assert rec is not None
        assert rec.feed_mm_per_rev < 0.2  # 0.2 * 0.8 = 0.16
        assert rec.spindle_rpm < 8000

    def test_recommend_target_surface(self) -> None:
        rec = ParameterRecommender().recommend("AL6061", "milling", target=OptimizationTarget.SURFACE)
        assert rec is not None
        assert rec.feed_mm_per_rev < 0.2  # 0.2 * 0.7 = 0.14

    def test_recommendation_to_dict(self) -> None:
        rec = ParameterRecommender().recommend("AL6061", "milling")
        assert rec is not None
        d = rec.to_dict()
        assert d["strategy"] == "L0_baseline"
        assert d["clamped"] is False
        assert "basis" in d and "confidence" in d


# evaluator


class TestEvaluator:
    def test_perfect_result_scores_high(self) -> None:
        result = evaluate_recommendation(cycle_time_s=100.0, tool_wear_percent=10.0, surface_roughness_ra=1.0)
        assert result.score > 0.9
        assert result.result_ok and result.cycle_time_ok

    def test_scrap_scores_zero(self) -> None:
        result = evaluate_recommendation(
            cycle_time_s=100.0,
            tool_wear_percent=10.0,
            surface_roughness_ra=1.0,
            result="scrap",
        )
        assert result.score <= 0.6  # 无 result 分（0.5），其余 0.5

    def test_none_metrics_do_not_crash(self) -> None:
        result = evaluate_recommendation(None, None, None)
        assert result.score <= 0.6
        assert result.cycle_time_ok is False

    def test_threshold_overrides(self) -> None:
        result = evaluate_recommendation(cycle_time_s=500.0, tool_wear_percent=10.0, surface_roughness_ra=1.0)
        assert result.cycle_time_ok is False  # 500 > 300 默认阈值


class TestCompare:
    def test_compare_a_faster(self) -> None:
        a = [{"cycle_time_s": 80.0}, {"cycle_time_s": 90.0}]
        b = [{"cycle_time_s": 120.0}, {"cycle_time_s": 130.0}]
        result = compare_parameter_sets(a, b)
        assert result.better == "a"
        assert result.improvement_pct > 25.0  # (125-85)/125 ≈ 32%
        assert result.a_samples == 2 and result.b_samples == 2

    def test_compare_b_faster(self) -> None:
        a = [{"cycle_time_s": 200.0}]
        b = [{"cycle_time_s": 100.0}]
        result = compare_parameter_sets(a, b)
        assert result.better == "b"

    def test_compare_tie(self) -> None:
        a = [{"cycle_time_s": 100.0}]
        b = [{"cycle_time_s": 100.5}]
        result = compare_parameter_sets(a, b)
        assert result.better == "tie"

    def test_compare_empty_lists(self) -> None:
        result = compare_parameter_sets([], [])
        assert result.better == "tie"
        assert result.a_avg_cycle is None

    def test_compare_ignores_missing_cycle(self) -> None:
        a = [{"cycle_time_s": 100.0}, {"cycle_time_s": None}]
        b = [{"cycle_time_s": 120.0}]
        result = compare_parameter_sets(a, b)
        assert result.a_avg_cycle == 100.0
