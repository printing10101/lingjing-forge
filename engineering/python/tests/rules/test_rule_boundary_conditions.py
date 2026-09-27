"""
边界条件测试

测试规则在边界值情况下的行为：
- 刚好等于阈值
- 刚好超过阈值
- 极端值
- 缺失字段
- 布尔条件

要求：行为一致可预测
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest  # noqa: E402

from app.rules.safety_constraint_rules import (  # noqa: E402
    SafetyRuleEngine,
    SafetyRule,
    Priority,
    RuleCategory,
    RuleCondition,
    RuleAction,
    ActionType,
    validate_rules,
)


@pytest.fixture
def engine():
    """创建带规则的引擎"""
    engine = SafetyRuleEngine()
    engine.load_rules([
        SafetyRule(
            rule_id="M-001",
            name="速度限制",
            priority=Priority.P0,
            category=RuleCategory.MACHINE,
            condition=RuleCondition(
                condition_type="threshold",
                field="spindle_speed", operator=">", value="max_spindle_speed",
            ),
            action=RuleAction(
                action_type=ActionType.OVERRIDE,
                target="spindle_speed", value="max_spindle_speed * 0.9",
            ),
        ),
        SafetyRule(
            rule_id="M-002",
            name="温度限制",
            priority=Priority.P1,
            category=RuleCategory.MACHINE,
            condition=RuleCondition(
                condition_type="threshold",
                field="spindle_temperature", operator=">", value=80,
            ),
            action=RuleAction(
                action_type=ActionType.ALERT_AND_OVERRIDE,
                target="spindle_speed", value=0.5,
            ),
        ),
        SafetyRule(
            rule_id="P-003",
            name="过切检测",
            priority=Priority.P0,
            category=RuleCategory.PROCESS,
            condition=RuleCondition(
                condition_type="threshold",
                field="overcut_detected", operator="==", value=1,
            ),
            action=RuleAction(
                action_type=ActionType.STOP,
                target="spindle_speed", value=0,
            ),
        ),
        SafetyRule(
            rule_id="T-001",
            name="磨损限制",
            priority=Priority.P1,
            category=RuleCategory.TOOL,
            condition=RuleCondition(
                condition_type="threshold",
                field="tool_wear", operator=">", value="tool_wear_limit",
            ),
            action=RuleAction(
                action_type=ActionType.FORCE_CHANGE,
                target="tool_life_used", value=0,
            ),
        ),
    ])
    return engine


class TestExactThreshold:
    """刚好等于阈值的测试"""

    def test_exact_equals_threshold_does_not_trigger_greater_than(self, engine):
        """验证：刚好等于阈值不触发 '>' 条件"""
        sensor_data = {
            "spindle_speed": 10000,
            "max_spindle_speed": 10000,  # 等于阈值
        }

        results = engine.evaluate(sensor_data, collect_audit=False)
        triggered_ids = {r["rule_id"] for r in results}
        assert "M-001" not in triggered_ids, (
            "刚好等于阈值不应触发 '>' 条件"
        )

    def test_exact_equals_threshold_does_not_trigger_greater_than_temperature(self, engine):
        """验证：温度刚好80°C不触发 '>80' 条件"""
        sensor_data = {"spindle_temperature": 80}

        results = engine.evaluate(sensor_data, collect_audit=False)
        triggered_ids = {r["rule_id"] for r in results}
        assert "M-002" not in triggered_ids, (
            "温度=80不应触发 '>80' 条件"
        )

    def test_exact_equals_triggers_equality_condition(self, engine):
        """验证：刚好等于触发 '==' 条件（过切检测）"""
        sensor_data = {"overcut_detected": 1}

        results = engine.evaluate(sensor_data, collect_audit=False)
        triggered_ids = {r["rule_id"] for r in results}
        assert "P-003" in triggered_ids, (
            "overcut_detected==1应触发过切检测"
        )

    def test_exact_equals_does_not_trigger_equality_wrong_value(self, engine):
        """验证：值不相等时不触发 '==' 条件"""
        sensor_data = {"overcut_detected": 0}

        results = engine.evaluate(sensor_data, collect_audit=False)
        triggered_ids = {r["rule_id"] for r in results}
        assert "P-003" not in triggered_ids, (
            "overcut_detected=0不应触发过切检测"
        )


class TestJustAboveThreshold:
    """刚好超过阈值的测试"""

    def test_just_above_threshold_triggers(self, engine):
        """验证：刚好超过阈值触发条件"""
        sensor_data = {
            "spindle_speed": 10001,
            "max_spindle_speed": 10000,
        }

        results = engine.evaluate(sensor_data, collect_audit=False)
        triggered_ids = {r["rule_id"] for r in results}
        assert "M-001" in triggered_ids, (
            "spindle_speed=10001 > max_spindle_speed=10000应触发"
        )

    def test_just_above_temperature_threshold(self, engine):
        """验证：温度80.1触发条件"""
        sensor_data = {"spindle_temperature": 80.1}

        results = engine.evaluate(sensor_data, collect_audit=False)
        triggered_ids = {r["rule_id"] for r in results}
        assert "M-002" in triggered_ids, (
            "spindle_temperature=80.1 > 80应触发"
        )


class TestExtremeValues:
    """极端值测试"""

    def test_very_high_spindle_speed(self, engine):
        """验证：极高转速正常触发"""
        sensor_data = {
            "spindle_speed": 999999,
            "max_spindle_speed": 10000,
        }

        results = engine.evaluate(sensor_data, collect_audit=False)
        assert len(results) >= 1
        assert any(r["rule_id"] == "M-001" for r in results)

    def test_zero_spindle_speed(self, engine):
        """验证：转速为0时不触发超限规则"""
        sensor_data = {
            "spindle_speed": 0,
            "max_spindle_speed": 10000,
        }

        results = engine.evaluate(sensor_data, collect_audit=False)
        triggered_ids = {r["rule_id"] for r in results}
        assert "M-001" not in triggered_ids

    def test_negative_spindle_speed(self, engine):
        """验证：负数转速不触发（异常但不应崩溃）"""
        sensor_data = {
            "spindle_speed": -500,
            "max_spindle_speed": 10000,
        }

        results = engine.evaluate(sensor_data, collect_audit=False)
        triggered_ids = {r["rule_id"] for r in results}
        assert "M-001" not in triggered_ids

    def test_very_large_temperature(self, engine):
        """验证：极端高温正常触发"""
        sensor_data = {"spindle_temperature": 9999}

        results = engine.evaluate(sensor_data, collect_audit=False)
        assert any(r["rule_id"] == "M-002" for r in results)

    def test_extreme_tool_wear(self, engine):
        """验证：磨损值>1正常触发"""
        sensor_data = {
            "tool_wear": 1.5,
            "tool_wear_limit": 0.7,
        }

        results = engine.evaluate(sensor_data, collect_audit=False)
        assert any(r["rule_id"] == "T-001" for r in results)


class TestMissingFields:
    """缺失字段测试"""

    def test_missing_field_does_not_trigger(self, engine):
        """验证：缺失条件字段不触发规则"""
        sensor_data = {"max_spindle_speed": 10000}  # 缺少spindle_speed

        results = engine.evaluate(sensor_data, collect_audit=False)
        triggered_ids = {r["rule_id"] for r in results}
        assert "M-001" not in triggered_ids, (
            "缺少spindle_speed字段不应触发M-001"
        )

    def test_empty_sensor_data(self, engine):
        """验证：空传感器数据不触发任何规则"""
        results = engine.evaluate({}, collect_audit=False)
        assert len(results) == 0, f"空数据不应触发规则，实际: {len(results)}"

    def test_partial_sensor_data(self, engine):
        """验证：部分传感器数据只触发对应规则"""
        sensor_data = {
            "spindle_speed": 12000,
            "max_spindle_speed": 10000,
            # 没有 temperature, vibration, 等
        }

        results = engine.evaluate(sensor_data, collect_audit=False)
        triggered_ids = {r["rule_id"] for r in results}

        assert "M-001" in triggered_ids, "转速超限应触发"
        assert "M-002" not in triggered_ids, "无温度数据不应触发温度规则"


class TestBooleanConditions:
    """布尔条件测试"""

    def test_overcut_detected_true(self, engine):
        """验证：overcut_detected=1触发"""
        results = engine.evaluate({"overcut_detected": 1}, collect_audit=False)
        assert any(r["rule_id"] == "P-003" for r in results)

    def test_overcut_detected_false(self, engine):
        """验证：overcut_detected=0不触发"""
        results = engine.evaluate({"overcut_detected": 0}, collect_audit=False)
        assert not any(r["rule_id"] == "P-003" for r in results)

    def test_overcut_detected_string(self, engine):
        """验证：overcut_detected='1'字符串触发（类型兼容）"""
        results = engine.evaluate({"overcut_detected": "1"}, collect_audit=False)
        assert any(r["rule_id"] == "P-003" for r in results)


class TestConsistency:
    """行为一致性测试"""

    def test_same_input_same_output(self, engine):
        """验证：相同输入多次评估结果一致"""
        sensor_data = {
            "spindle_speed": 12000,
            "max_spindle_speed": 10000,
            "spindle_temperature": 90,
        }

        previous_result = None
        for _ in range(10):
            results = engine.evaluate(sensor_data, collect_audit=False)
            result_ids = tuple(sorted(r["rule_id"] for r in results))

            if previous_result is None:
                previous_result = result_ids
            else:
                assert result_ids == previous_result, (
                    f"结果不一致: {result_ids} != {previous_result}"
                )

    def test_deterministic_priority_ordering(self, engine):
        """验证：优先级排序每次一致"""
        sensor_data = {
            "spindle_speed": 12000,
            "max_spindle_speed": 10000,
            "spindle_temperature": 90,
        }

        previous_order = None
        for _ in range(10):
            results = engine.evaluate(sensor_data, collect_audit=False)
            priority_order = tuple(r["priority"] for r in results)

            if previous_order is None:
                previous_order = priority_order
            else:
                assert priority_order == previous_order, (
                    f"优先级顺序不一致: {priority_order} != {previous_order}"
                )


class TestValidationBoundary:
    """验证边界测试"""

    def test_empty_rules_list(self):
        """验证：空规则列表返回适当错误"""
        errors = validate_rules([])
        assert len(errors) == 1
        assert errors[0].field == "rules"

    def test_invalid_rule_id_format(self):
        """验证：无效规则ID格式检测"""
        bad_rules = [
            SafetyRule(
                rule_id="X1",  # 缺少横杠和数字不足
                name="无效规则",
                priority=Priority.P3,
                category=RuleCategory.MACHINE,
                condition=RuleCondition(
                    condition_type="threshold",
                    field="spindle_speed", operator=">", value=100,
                ),
                action=RuleAction(
                    action_type=ActionType.ALERT,
                    target="spindle_speed", value=100,
                ),
            ),
        ]
        errors = validate_rules(bad_rules)
        assert any("格式无效" in e.message for e in errors)

    def test_duplicate_rule_id(self):
        """验证：重复规则ID检测"""
        rules = [
            SafetyRule(
                rule_id="M-001", name="A", priority=Priority.P1,
                category=RuleCategory.MACHINE,
                condition=RuleCondition(
                    condition_type="threshold",
                    field="spindle_speed", operator=">", value=100,
                ),
                action=RuleAction(
                    action_type=ActionType.ALERT,
                    target="spindle_speed", value=100,
                ),
            ),
            SafetyRule(
                rule_id="M-001", name="B", priority=Priority.P1,
                category=RuleCategory.MACHINE,
                condition=RuleCondition(
                    condition_type="threshold",
                    field="spindle_temperature", operator=">", value=80,
                ),
                action=RuleAction(
                    action_type=ActionType.ALERT,
                    target="spindle_speed", value=80,
                ),
            ),
        ]
        errors = validate_rules(rules)
        assert any("重复" in e.message for e in errors)
