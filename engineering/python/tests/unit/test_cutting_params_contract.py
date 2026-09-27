"""切削参数数据库契约测试（cutting_params_db TypedDict 修复验证）。

覆盖 get_cutting_params 返回结构（TypedDict 精确类型）：
- 字段类型（spindle_speed: int / feed_rate: float / depth_of_cut: float）
- warnings 条件键（validate_machine_limits=True 才有）
- 未知材料回退默认
- 非法操作 / 非法直径抛 ValueError
"""

from __future__ import annotations

import pytest

from app.cutting_params_db import get_cutting_params


@pytest.mark.unit
class TestCuttingParamsContract:
    def test_return_types(self):
        r = get_cutting_params("aluminum", "drilling", 10.0)
        assert isinstance(r["spindle_speed"], int)
        assert isinstance(r["feed_rate"], float)
        assert isinstance(r["depth_of_cut"], float)
        assert r["spindle_speed"] > 0
        assert r["feed_rate"] > 0

    def test_warnings_only_when_validation(self):
        with_w = get_cutting_params("steel", "milling", 5.0)
        assert "warnings" in with_w
        assert isinstance(with_w["warnings"], list)

        without = get_cutting_params("steel", "milling", 5.0, validate_machine_limits=False)
        assert "warnings" not in without

    def test_unknown_material_falls_back_to_default(self):
        r = get_cutting_params("unknown_material", "turning", 20.0)
        assert r["spindle_speed"] > 0
        assert r["feed_rate"] > 0

    def test_invalid_operation_raises(self):
        with pytest.raises(ValueError, match="Unsupported operation"):
            get_cutting_params("aluminum", "unknown_op", 10.0)

    def test_nonpositive_diameter_raises(self):
        with pytest.raises(ValueError, match="positive"):
            get_cutting_params("aluminum", "drilling", 0.0)

    def test_small_tool_speed_clamped(self):
        """极小直径 → 转速被钳制在合法范围内（不无限升高）。"""
        r = get_cutting_params("titanium", "drilling", 0.5)
        assert r["spindle_speed"] > 0
        # 参数范围上限（钛合金高速上限）应约束转速
        assert r["spindle_speed"] <= 8000  # 保守上限：远低于 0.5mm 的理论 16000
        # warnings 是 list（条件键结构稳定）
        assert isinstance(r.get("warnings"), list)
