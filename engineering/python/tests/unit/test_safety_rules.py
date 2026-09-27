"""safety_constraint_rules 覆盖率补强测试。

覆盖 app/rules/safety_constraint_rules.py：
- SafetyRule / RuleCondition / RuleAction 数据类序列化
- validate_rules 各校验检查项
- SafeMathEvaluator 安全表达式求值（合法/非法/边界）
"""

from __future__ import annotations

import pytest

from app.rules.safety_constraint_rules import (
    ActionType,
    Priority,
    RuleAction,
    RuleCategory,
    RuleCondition,
    SafetyRule,
    SafeMathEvaluator,
    validate_rules,
    safe_eval_math_expression,
)

pytestmark = pytest.mark.unit


# 枚举与基础数据类


class TestEnums:
    def test_priority_level(self):
        assert Priority.P0.level == 0
        assert Priority.P1.level == 1
        assert Priority.P2.level == 2
        assert Priority.P3.level == 3

    def test_priority_from_string(self):
        assert Priority.from_string("p0") == Priority.P0  # 大小写不敏感
        assert Priority.from_string("P2") == Priority.P2

    def test_priority_from_string_invalid_raises(self):
        with pytest.raises(ValueError):
            Priority.from_string("P9")

    def test_action_types_values(self):
        assert ActionType.E_STOP.value == "e_stop"
        assert ActionType.HOLD.value == "hold"
        assert ActionType.ALERT.value == "alert"

    def test_rule_category_values(self):
        assert RuleCategory.MACHINE.value == "M"
        assert RuleCategory.SAFETY.value == "S"


# RuleCondition / RuleAction 序列化


class TestRuleCondition:
    def test_to_dict_and_from_dict(self):
        c = RuleCondition(
            condition_type="threshold",
            field="spindle_speed",
            operator=">",
            value=12000.0,
        )
        d = c.to_dict()
        assert d["type"] == "threshold"
        assert d["field"] == "spindle_speed"
        c2 = RuleCondition.from_dict(d)
        assert c2.field == "spindle_speed"
        assert c2.value == 12000.0

    def test_from_dict_missing_optional(self):
        c = RuleCondition.from_dict({"field": "temp", "value": 100})
        assert c.condition_type == "threshold"
        assert c.operator == ">"


class TestRuleAction:
    def test_to_dict_and_from_dict(self):
        a = RuleAction(
            action_type=ActionType.ALERT,
            target="operator",
            value=1,
        )
        d = a.to_dict()
        assert d["type"] == "alert"
        assert d["duration"] == "until_condition_cleared"
        a2 = RuleAction.from_dict(d)
        assert a2.action_type == ActionType.ALERT

    def test_from_dict_with_chinese_desc_mapping(self):
        a = RuleAction.from_dict({"type": ""}, action_desc="停机检查")
        assert a.action_type == ActionType.STOP

    def test_from_dict_unknown_desc_defaults_alert(self):
        a = RuleAction.from_dict({"type": "not_a_real_type"})
        assert a.action_type == ActionType.ALERT


# SafetyRule 序列化


class TestSafetyRule:
    def _make_rule(self) -> SafetyRule:
        return SafetyRule(
            rule_id="M-001",
            name="主轴超速",
            priority=Priority.P0,
            category=RuleCategory.MACHINE,
            condition=RuleCondition(
                condition_type="threshold",
                field="spindle_speed",
                operator=">",
                value=12000.0,
            ),
            action=RuleAction(
                action_type=ActionType.E_STOP,
                target="spindle",
                value=0,
            ),
        )

    def test_to_dict(self):
        rule = self._make_rule()
        d = rule.to_dict()
        assert d["rule_id"] == "M-001"
        assert d["priority"] == "P0"
        assert d["category"] == "M"
        assert d["audit"] is True

    def test_from_dict_roundtrip(self):
        rule = self._make_rule()
        d = rule.to_dict()
        rule2 = SafetyRule.from_dict(d)
        assert rule2.rule_id == "M-001"
        assert rule2.priority == Priority.P0
        assert rule2.action.action_type == ActionType.E_STOP

    def test_from_dict_with_action_description(self):
        d = {
            "rule_id": "T-001",
            "name": "刀具磨损",
            "category": "T",
            "condition": {"field": "wear", "operator": ">", "value": 0.5},
            "action": {"target": "tool"},
            "action_description": "强制换刀",
        }
        rule = SafetyRule.from_dict(d)
        assert rule.action.action_type == ActionType.FORCE_CHANGE


# validate_rules 校验链


class TestValidateRules:
    def _make_rule(self, rule_id: str = "M-001", priority: Priority = Priority.P1) -> SafetyRule:
        return SafetyRule(
            rule_id=rule_id,
            name="测试规则",
            priority=priority,
            category=RuleCategory.MACHINE,
            condition=RuleCondition(
                condition_type="threshold",
                field="spindle_temperature",
                operator=">",
                value=100,
            ),
            action=RuleAction(action_type=ActionType.STOP, target="spindle_speed", value=1),
        )

    def test_valid_rules_no_errors(self):
        errors = validate_rules([self._make_rule()])
        assert errors == []

    def test_invalid_rule_id_reported(self):
        rule = self._make_rule(rule_id="bad-id-here")
        errors = validate_rules([rule])
        assert any(e.rule_id == "bad-id-here" for e in errors)

    def test_unknown_field_reported(self):
        rule = self._make_rule()
        rule.condition = RuleCondition(
            condition_type="threshold",
            field="not_a_real_field",
            operator=">",
            value=100,
        )
        errors = validate_rules([rule])
        assert any("not_a_real_field" in e.message for e in errors)

    def test_s_series_non_p0_reported(self):
        # S 系列规则 priority 必须 P0（防复发约束）
        rule = SafetyRule(
            rule_id="S-001",
            name="急停",
            priority=Priority.P2,
            category=RuleCategory.SAFETY,
            condition=RuleCondition(
                condition_type="threshold",
                field="e_stop",
                operator="==",
                value=1,
            ),
            action=RuleAction(action_type=ActionType.E_STOP, target="machine", value=1),
        )
        errors = validate_rules([rule])
        assert len(errors) >= 1

    def test_empty_rules_reports_empty_list_error(self):
        # 设计如此：空规则列表被视为配置缺失，返回"规则列表为空"错误
        errors = validate_rules([])
        assert len(errors) == 1
        assert errors[0].field == "rules"


# SafeMathEvaluator 安全表达式求值


class TestSafeMathEvaluator:
    def test_basic_arithmetic(self):
        assert SafeMathEvaluator("1+2").evaluate() == 3.0
        assert SafeMathEvaluator("10-4").evaluate() == 6.0
        assert SafeMathEvaluator("3*4").evaluate() == 12.0
        assert SafeMathEvaluator("10/2").evaluate() == 5.0

    def test_parentheses_and_unary(self):
        assert SafeMathEvaluator("(1+2)*3").evaluate() == 9.0
        assert SafeMathEvaluator("-5+10").evaluate() == 5.0

    def test_float_expression(self):
        assert SafeMathEvaluator("1.5*2").evaluate() == 3.0

    def test_compile_caches(self):
        ev1 = SafeMathEvaluator.compile("2+2")
        ev2 = SafeMathEvaluator.compile("2+2")
        assert ev1 is ev2  # 缓存命中同一实例
        assert ev1.evaluate() == 4.0

    def test_illegal_chars_rejected(self):
        with pytest.raises(ValueError):
            SafeMathEvaluator("1+2; import os")

    def test_name_rejected(self):
        with pytest.raises(ValueError):
            SafeMathEvaluator("x+1")

    def test_function_call_rejected(self):
        with pytest.raises(ValueError):
            SafeMathEvaluator("__import__('os')")

    def test_string_constant_rejected(self):
        with pytest.raises(ValueError):
            SafeMathEvaluator("'a'+'b'")

    def test_division_by_zero_returns_zero(self):
        assert SafeMathEvaluator("1/0").evaluate() == 0.0

    def test_syntax_error_raises_value_error(self):
        with pytest.raises(ValueError):
            SafeMathEvaluator("1++")

    def test_safe_eval_math_expression_helper(self):
        assert safe_eval_math_expression("5+5") == 10.0
        # 非法表达式返回 0.0 降级
        assert safe_eval_math_expression("boom()") == 0.0
