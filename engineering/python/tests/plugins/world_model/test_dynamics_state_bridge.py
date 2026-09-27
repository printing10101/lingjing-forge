"""``DynamicsStateBridge`` 单元测试.

对应 ADR-020 思路 1 P0 数据解锁工具.

验收标准：
- 6 个字段一一映射正确（值传递不混淆）
- 完整 current_state → ``is_complete=True`` / ``completeness_ratio=1.0``
- 部分缺失 → ``defaulted_fields`` 准确记录 / 降级判断正确
- 严格模式任一字段缺失抛 ``ValueError``
- ``StateField.WIDTH_OF_CUT`` / ``CHATTER_PROBABILITY`` 不参与映射
- ``to_dict`` 序列化往返一致
"""

from __future__ import annotations

import pytest

from app.contracts.world_model import StateField
from app.plugins.world_model.dynamics_state_bridge import (
    BridgeResult,
    DynamicsStateBridge,
    FIELD_MAPPING,
    REQUIRED_FIELDS,
)
from app.plugins.world_model.unified_state import DynamicsState


@pytest.mark.unit
@pytest.mark.plugins
class TestFieldMappingConstants:
    """字段映射常量校验."""

    def test_field_mapping_has_six_entries(self):
        """映射表恰好包含 6 个字段（DynamicsState 全部字段）."""
        assert len(FIELD_MAPPING) == 6

    def test_required_fields_matches_mapping_keys(self):
        """``REQUIRED_FIELDS`` 与 ``FIELD_MAPPING.keys()`` 一致."""
        assert set(REQUIRED_FIELDS) == set(FIELD_MAPPING.keys())

    def test_width_of_cut_not_in_mapping(self):
        """``WIDTH_OF_CUT`` 不在映射中（DynamicsState v1 未包含此字段）."""
        assert StateField.WIDTH_OF_CUT not in FIELD_MAPPING

    def test_chatter_probability_not_in_mapping(self):
        """``CHATTER_PROBABILITY`` 不在映射中（是预测输出，非动力学输入）."""
        assert StateField.CHATTER_PROBABILITY not in FIELD_MAPPING

    def test_all_dynamics_fields_covered(self):
        """DynamicsState 全部 6 个字段都在映射值集合中."""
        dynamics_fields = {dynamics_field for dynamics_field in FIELD_MAPPING.values()}
        expected = {
            "spindle_speed",
            "feed_rate",
            "depth_of_cut",
            "tool_wear",
            "vibration_rms",
            "temperature",
        }
        assert dynamics_fields == expected


@pytest.mark.unit
@pytest.mark.plugins
class TestFromCurrentStateComplete:
    """完整 current_state 的桥接行为."""

    @pytest.fixture
    def complete_current_state(self) -> dict[str, float]:
        """完整 current_state（6 个动力学字段全部存在）."""
        return {
            StateField.SPINDLE_SPEED: 8000.0,
            StateField.FEED_RATE: 1200.0,
            StateField.DEPTH_OF_CUT: 0.5,
            StateField.TOOL_WEAR: 0.12,
            StateField.VIBRATION_RMS: 0.8,
            StateField.TEMPERATURE: 45.0,
            # 以下字段不应参与映射
            StateField.WIDTH_OF_CUT: 6.0,
            StateField.CHATTER_PROBABILITY: 0.1,
        }

    def test_returns_bridge_result(self, complete_current_state):
        result = DynamicsStateBridge.from_current_state(complete_current_state)
        assert isinstance(result, BridgeResult)

    def test_dynamics_instance(self, complete_current_state):
        result = DynamicsStateBridge.from_current_state(complete_current_state)
        assert isinstance(result.dynamics, DynamicsState)

    def test_field_values_correct(self, complete_current_state):
        """6 个字段值逐一正确传递（不混淆）."""
        result = DynamicsStateBridge.from_current_state(complete_current_state)
        d = result.dynamics
        assert d.spindle_speed == 8000.0
        assert d.feed_rate == 1200.0
        assert d.depth_of_cut == 0.5
        assert d.tool_wear == 0.12
        assert d.vibration_rms == 0.8
        assert d.temperature == 45.0

    def test_is_complete_true(self, complete_current_state):
        result = DynamicsStateBridge.from_current_state(complete_current_state)
        assert result.is_complete is True

    def test_no_missing_fields(self, complete_current_state):
        result = DynamicsStateBridge.from_current_state(complete_current_state)
        assert result.missing_fields == []
        assert result.defaulted_fields == []

    def test_completeness_ratio_one(self, complete_current_state):
        result = DynamicsStateBridge.from_current_state(complete_current_state)
        assert result.completeness_ratio == 1.0

    def test_should_degrade_false(self, complete_current_state):
        result = DynamicsStateBridge.from_current_state(complete_current_state)
        assert DynamicsStateBridge.should_degrade(result) is False

    def test_source_tag(self, complete_current_state):
        result = DynamicsStateBridge.from_current_state(complete_current_state)
        assert result.source == "legacy_current_state"


@pytest.mark.unit
@pytest.mark.plugins
class TestFromCurrentStatePartialMissing:
    """部分字段缺失的桥接行为."""

    def test_one_field_missing(self):
        """缺失 1 个字段：is_complete=False, should_degrade=False."""
        current_state = {
            StateField.SPINDLE_SPEED: 8000.0,
            StateField.FEED_RATE: 1200.0,
            StateField.DEPTH_OF_CUT: 0.5,
            StateField.TOOL_WEAR: 0.12,
            StateField.VIBRATION_RMS: 0.8,
            # TEMPERATURE 缺失
        }
        result = DynamicsStateBridge.from_current_state(current_state)

        assert result.is_complete is False
        assert StateField.TEMPERATURE in result.missing_fields
        assert StateField.TEMPERATURE in result.defaulted_fields
        assert len(result.defaulted_fields) == 1
        assert result.completeness_ratio == pytest.approx(5 / 6)
        assert DynamicsStateBridge.should_degrade(result) is False
        # 缺失字段用 0.0 填充
        assert result.dynamics.temperature == 0.0

    def test_two_fields_missing_below_threshold(self):
        """缺失 2 个字段：should_degrade=False（阈值 3）."""
        current_state = {
            StateField.SPINDLE_SPEED: 8000.0,
            StateField.FEED_RATE: 1200.0,
            StateField.DEPTH_OF_CUT: 0.5,
            StateField.TOOL_WEAR: 0.12,
            # VIBRATION_RMS 与 TEMPERATURE 缺失
        }
        result = DynamicsStateBridge.from_current_state(current_state)

        assert len(result.defaulted_fields) == 2
        assert result.completeness_ratio == pytest.approx(4 / 6)
        assert DynamicsStateBridge.should_degrade(result) is False

    def test_three_fields_missing_at_threshold(self):
        """缺失 3 个字段：should_degrade=True（>= 阈值 3）."""
        current_state = {
            StateField.SPINDLE_SPEED: 8000.0,
            StateField.FEED_RATE: 1200.0,
            StateField.DEPTH_OF_CUT: 0.5,
            # TOOL_WEAR / VIBRATION_RMS / TEMPERATURE 缺失
        }
        result = DynamicsStateBridge.from_current_state(current_state)

        assert len(result.defaulted_fields) == 3
        assert result.completeness_ratio == 0.5
        assert DynamicsStateBridge.should_degrade(result) is True

    def test_all_fields_missing(self):
        """空 current_state：6 个字段全缺失, should_degrade=True."""
        result = DynamicsStateBridge.from_current_state({})

        assert len(result.missing_fields) == 6
        assert len(result.defaulted_fields) == 6
        assert result.is_complete is False
        assert result.completeness_ratio == 0.0
        assert DynamicsStateBridge.should_degrade(result) is True
        # 全部用 0.0 填充
        d = result.dynamics
        assert d.spindle_speed == 0.0
        assert d.feed_rate == 0.0
        assert d.depth_of_cut == 0.0
        assert d.tool_wear == 0.0
        assert d.vibration_rms == 0.0
        assert d.temperature == 0.0

    def test_extra_fields_ignored(self):
        """current_state 包含非映射字段时被忽略（不报错）."""
        current_state = {
            StateField.SPINDLE_SPEED: 8000.0,
            StateField.FEED_RATE: 1200.0,
            StateField.DEPTH_OF_CUT: 0.5,
            StateField.TOOL_WEAR: 0.12,
            StateField.VIBRATION_RMS: 0.8,
            StateField.TEMPERATURE: 45.0,
            "unknown_field": 999.0,  # 应被忽略
            StateField.WIDTH_OF_CUT: 6.0,  # 应被忽略
        }
        result = DynamicsStateBridge.from_current_state(current_state)
        assert result.is_complete is True

    def test_custom_default_value(self):
        """自定义 default 填充值（用于特殊场景）."""
        current_state = {
            StateField.SPINDLE_SPEED: 8000.0,
            # 其余 5 个字段缺失
        }
        result = DynamicsStateBridge.from_current_state(current_state, default=-1.0)
        assert result.dynamics.feed_rate == -1.0
        assert result.dynamics.temperature == -1.0
        assert len(result.defaulted_fields) == 5


@pytest.mark.unit
@pytest.mark.plugins
class TestStrictMode:
    """``from_current_state_strict`` 严格模式."""

    def test_complete_state_returns_dynamics(self):
        """完整 current_state 返回 DynamicsState（非 BridgeResult）."""
        current_state = {
            StateField.SPINDLE_SPEED: 8000.0,
            StateField.FEED_RATE: 1200.0,
            StateField.DEPTH_OF_CUT: 0.5,
            StateField.TOOL_WEAR: 0.12,
            StateField.VIBRATION_RMS: 0.8,
            StateField.TEMPERATURE: 45.0,
        }
        dynamics = DynamicsStateBridge.from_current_state_strict(current_state)
        assert isinstance(dynamics, DynamicsState)
        assert dynamics.spindle_speed == 8000.0

    def test_missing_field_raises_value_error(self):
        """任一字段缺失抛 ValueError."""
        current_state = {
            StateField.SPINDLE_SPEED: 8000.0,
            # 其余 5 个字段缺失
        }
        with pytest.raises(ValueError, match="缺失动力学字段"):
            DynamicsStateBridge.from_current_state_strict(current_state)

    def test_empty_state_raises_value_error(self):
        """空 current_state 抛 ValueError."""
        with pytest.raises(ValueError, match="缺失动力学字段"):
            DynamicsStateBridge.from_current_state_strict({})

    def test_error_message_lists_all_missing_fields(self):
        """错误消息列出全部缺失字段."""
        current_state = {
            StateField.SPINDLE_SPEED: 8000.0,
            StateField.TEMPERATURE: 45.0,
        }
        with pytest.raises(ValueError) as exc_info:
            DynamicsStateBridge.from_current_state_strict(current_state)
        msg = str(exc_info.value)
        # 4 个缺失字段都应在错误消息中
        assert StateField.FEED_RATE in msg
        assert StateField.DEPTH_OF_CUT in msg
        assert StateField.TOOL_WEAR in msg
        assert StateField.VIBRATION_RMS in msg


@pytest.mark.unit
@pytest.mark.plugins
class TestShouldDegrade:
    """``should_degrade`` 降级判断."""

    def test_custom_threshold(self):
        """自定义阈值生效."""
        current_state = {
            StateField.SPINDLE_SPEED: 8000.0,
            StateField.FEED_RATE: 1200.0,
            StateField.DEPTH_OF_CUT: 0.5,
            StateField.TOOL_WEAR: 0.12,
            # 2 个字段缺失
        }
        result = DynamicsStateBridge.from_current_state(current_state)

        # 默认阈值 3 不降级
        assert DynamicsStateBridge.should_degrade(result) is False
        # 自定义阈值 2 降级
        assert DynamicsStateBridge.should_degrade(result, threshold=2) is True

    def test_threshold_boundary_at_three(self):
        """边界：defaulted_fields=3, threshold=3 → 降级 (>=)."""
        current_state = {
            StateField.SPINDLE_SPEED: 8000.0,
            StateField.FEED_RATE: 1200.0,
            StateField.DEPTH_OF_CUT: 0.5,
            # 3 个字段缺失
        }
        result = DynamicsStateBridge.from_current_state(current_state)
        assert len(result.defaulted_fields) == 3
        assert DynamicsStateBridge.should_degrade(result, threshold=3) is True


@pytest.mark.unit
@pytest.mark.plugins
class TestBridgeResultSerialization:
    """``BridgeResult.to_dict`` 序列化."""

    def test_to_dict_keys(self):
        result = DynamicsStateBridge.from_current_state(
            {
                StateField.SPINDLE_SPEED: 8000.0,
                StateField.FEED_RATE: 1200.0,
                StateField.DEPTH_OF_CUT: 0.5,
                StateField.TOOL_WEAR: 0.12,
                StateField.VIBRATION_RMS: 0.8,
                StateField.TEMPERATURE: 45.0,
            }
        )
        d = result.to_dict()
        assert set(d.keys()) == {
            "dynamics",
            "missing_fields",
            "defaulted_fields",
            "source",
            "is_complete",
            "completeness_ratio",
        }

    def test_to_dict_dynamics_subkeys(self):
        result = DynamicsStateBridge.from_current_state(
            {
                StateField.SPINDLE_SPEED: 8000.0,
                StateField.FEED_RATE: 1200.0,
                StateField.DEPTH_OF_CUT: 0.5,
                StateField.TOOL_WEAR: 0.12,
                StateField.VIBRATION_RMS: 0.8,
                StateField.TEMPERATURE: 45.0,
            }
        )
        d = result.to_dict()
        assert set(d["dynamics"].keys()) == {
            "spindle_speed",
            "feed_rate",
            "depth_of_cut",
            "tool_wear",
            "vibration_rms",
            "temperature",
        }

    def test_to_dict_reflects_missing(self):
        """序列化结果反映缺失字段."""
        result = DynamicsStateBridge.from_current_state({StateField.SPINDLE_SPEED: 8000.0})
        d = result.to_dict()
        assert d["is_complete"] is False
        assert len(d["defaulted_fields"]) == 5
        assert d["completeness_ratio"] == pytest.approx(1 / 6)


@pytest.mark.unit
@pytest.mark.plugins
class TestValuePassingIntegrity:
    """值传递完整性（防止字段混淆的回归测试）."""

    def test_distinct_values_not_swapped(self):
        """使用差异明显的值，验证字段不混淆."""
        current_state = {
            StateField.SPINDLE_SPEED: 1.0,
            StateField.FEED_RATE: 2.0,
            StateField.DEPTH_OF_CUT: 3.0,
            StateField.TOOL_WEAR: 4.0,
            StateField.VIBRATION_RMS: 5.0,
            StateField.TEMPERATURE: 6.0,
        }
        d = DynamicsStateBridge.from_current_state(current_state).dynamics
        assert d.spindle_speed == 1.0
        assert d.feed_rate == 2.0
        assert d.depth_of_cut == 3.0
        assert d.tool_wear == 4.0
        assert d.vibration_rms == 5.0
        assert d.temperature == 6.0

    def test_to_tensor_input_matches_dynamics(self):
        """桥接后的 DynamicsState.to_tensor_input 与字段值一致."""
        current_state = {
            StateField.SPINDLE_SPEED: 8000.0,
            StateField.FEED_RATE: 1200.0,
            StateField.DEPTH_OF_CUT: 0.5,
            StateField.TOOL_WEAR: 0.12,
            StateField.VIBRATION_RMS: 0.8,
            StateField.TEMPERATURE: 45.0,
        }
        result = DynamicsStateBridge.from_current_state(current_state)
        tensor_input = result.dynamics.to_tensor_input()
        assert tensor_input == [8000.0, 1200.0, 0.5, 0.12, 0.8, 45.0]

    def test_float_coercion(self):
        """int 输入被强制转为 float（防止 numpy 类型泄漏）."""
        current_state = {
            StateField.SPINDLE_SPEED: 8000,  # int
            StateField.FEED_RATE: 1200,  # int
            StateField.DEPTH_OF_CUT: 0.5,
            StateField.TOOL_WEAR: 0.12,
            StateField.VIBRATION_RMS: 0.8,
            StateField.TEMPERATURE: 45,
        }
        d = DynamicsStateBridge.from_current_state(current_state).dynamics
        assert isinstance(d.spindle_speed, float)
        assert isinstance(d.temperature, float)
