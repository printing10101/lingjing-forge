"""W6b 主权保守缩放单元测试（行为矩阵）。

「等级越低，护盾介入越深」的机制锚点：物理安全盾过滤后的动作，
再按自主等级做保守缩放。默认档（2 推荐）不干预，行为与历史一致。
"""

from __future__ import annotations

import pytest

from app.services.sovereignty import (
    CONSERVATISM_FACTORS,
    apply_conservatism,
    conservatism_for_level,
)

pytestmark = [pytest.mark.unit]

SAMPLE_ACTION = {
    "spindle_speed_delta": -0.4,
    "feed_rate_delta": 0.2,
    "depth_of_cut_delta": -0.1,
    "width_of_cut_delta": 0.0,
}


class TestConservatismMatrix:
    @pytest.mark.parametrize(
        ("level", "factor"), [(0, 0.5), (1, 0.7), (2, 1.0), (3, 1.0), (4, 1.0)]
    )
    def test_factor_per_level(self, level, factor):
        assert conservatism_for_level(level) == factor
        assert CONSERVATISM_FACTORS[0] < CONSERVATISM_FACTORS[1] <= CONSERVATISM_FACTORS[2]

    def test_level0_halves_deltas(self):
        scaled, info = apply_conservatism(SAMPLE_ACTION, 0)
        assert info == {"level": 0, "factor": 0.5, "scaled": True}
        assert scaled["spindle_speed_delta"] == -0.2
        assert scaled["feed_rate_delta"] == 0.1

    def test_level1_scales_by_seven_tenths(self):
        scaled, info = apply_conservatism(SAMPLE_ACTION, 1)
        assert info["scaled"] is True
        assert scaled["spindle_speed_delta"] == -0.28

    def test_default_levels_pass_through(self):
        for level in (2, 3, 4):
            scaled, info = apply_conservatism(SAMPLE_ACTION, level)
            assert info["scaled"] is False
            assert scaled == SAMPLE_ACTION

    def test_scaling_preserves_sign_and_zero(self):
        scaled, _ = apply_conservatism(SAMPLE_ACTION, 0)
        # 符号保持：降速仍是降速、升进给仍是升进给
        assert scaled["spindle_speed_delta"] < 0
        assert scaled["feed_rate_delta"] > 0
        assert scaled["width_of_cut_delta"] == 0.0

    def test_rejects_unknown_level(self):
        with pytest.raises(ValueError, match="自主等级"):
            apply_conservatism(SAMPLE_ACTION, 5)
