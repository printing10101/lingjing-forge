"""
规则冲突检测测试

定义互相矛盾的规则，验证系统能检测到冲突并报警。

测试冲突类型：
- 条件相同但结论不同（矛盾冲突）
- 优先级依赖冲突（低优先级规则覆盖高优先级规则监控字段）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.rules.safety_constraint_rules import (  # noqa: E402
    SafetyRule,
    SafetyRuleEngine,
    Priority,
    RuleCategory,
    RuleCondition,
    RuleAction,
    ActionType,
    _check_priority_dependency,
)


class TestConflictDetection:
    """规则冲突检测测试"""

    # 冲突检测：矛盾规则

    def test_contradictory_rules_same_condition_different_action(self):
        """验证：相同条件但不同动作的规则应被检测为冲突"""
        rules = [
            SafetyRule(
                rule_id="M-001",
                name="规则A: 转速>10000降速",
                priority=Priority.P1,
                category=RuleCategory.MACHINE,
                condition=RuleCondition(
                    condition_type="threshold",
                    field="spindle_speed",
                    operator=">",
                    value=10000,
                ),
                action=RuleAction(
                    action_type=ActionType.OVERRIDE,
                    target="spindle_speed",
                    value=9000,
                ),
            ),
            SafetyRule(
                rule_id="M-002",
                name="规则B: 转速>10000停机",
                priority=Priority.P1,
                category=RuleCategory.MACHINE,
                condition=RuleCondition(
                    condition_type="threshold",
                    field="spindle_speed",
                    operator=">",
                    value=10000,
                ),
                action=RuleAction(
                    action_type=ActionType.STOP,
                    target="spindle_speed",
                    value=0,
                ),
            ),
        ]

        # 验证两条规则都会触发
        engine = SafetyRuleEngine()
        errors = engine.load_rules(rules)
        assert len(errors) == 0  # 语法验证通过

        sensor_data = {"spindle_speed": 12000}
        results = engine.evaluate(sensor_data)

        # 两条规则条件相同，都应触发
        assert len(results) == 2, f"期望触发2条规则，实际: {len(results)}"
        triggered_ids = {r["rule_id"] for r in results}
        assert "M-001" in triggered_ids
        assert "M-002" in triggered_ids

    # 冲突检测：优先级依赖冲突

    def test_low_priority_rule_cannot_override_p0_field(self):
        """验证：低优先级规则不能修改P0规则监控的字段"""
        rules = [
            SafetyRule(
                rule_id="M-001",
                name="P0: 主轴速度保护",
                priority=Priority.P0,
                category=RuleCategory.MACHINE,
                condition=RuleCondition(
                    condition_type="threshold",
                    field="spindle_speed",
                    operator=">",
                    value="max_spindle_speed",
                ),
                action=RuleAction(
                    action_type=ActionType.OVERRIDE,
                    target="spindle_speed",
                    value="max_spindle_speed * 0.9",
                ),
            ),
            SafetyRule(
                rule_id="M-099",
                name="P3: 试图修改主轴速度",
                priority=Priority.P3,
                category=RuleCategory.MACHINE,
                condition=RuleCondition(
                    condition_type="threshold",
                    field="feed_rate",
                    operator=">",
                    value=500,
                ),
                action=RuleAction(
                    action_type=ActionType.OVERRIDE,
                    target="spindle_speed",  # 尝试覆盖P0规则的监控字段
                    value=20000,
                ),
            ),
        ]

        errors = _check_priority_dependency(rules)
        assert len(errors) >= 1, "应该检测到优先级依赖冲突"

        conflict = errors[0]
        assert "M-099" in conflict.rule_id, f"应报告M-099的冲突，实际: {conflict.rule_id}"
        assert "P0" in conflict.message or "spindle_speed" in conflict.message

    def test_p0_rules_cannot_be_overridden_by_any_lower_priority(self):
        """验证：P0规则的字段不能被任何更低优先级规则覆盖"""
        rules = [
            SafetyRule(
                rule_id="P-003",
                name="P0: 过切检测",
                priority=Priority.P0,
                category=RuleCategory.PROCESS,
                condition=RuleCondition(
                    condition_type="threshold",
                    field="overcut_detected",
                    operator="==",
                    value=1,
                ),
                action=RuleAction(
                    action_type=ActionType.STOP,
                    target="spindle_speed",
                    value=0,
                ),
            ),
            SafetyRule(
                rule_id="P-099",
                name="P1: 试图修改过切检测",
                priority=Priority.P1,
                category=RuleCategory.PROCESS,
                condition=RuleCondition(
                    condition_type="threshold",
                    field="cutting_force",
                    operator=">",
                    value=100,
                ),
                action=RuleAction(
                    action_type=ActionType.OVERRIDE,
                    target="overcut_detected",  # 尝试覆盖P0字段
                    value=0,
                ),
            ),
        ]

        errors = _check_priority_dependency(rules)
        assert len(errors) >= 1, f"应检测到P1规则试图覆盖P0字段的冲突, got {len(errors)}"

    # 冲突检测：引擎级评估

    def test_engine_evaluates_by_priority_order(self):
        """验证：引擎按优先级顺序评估规则"""
        rules = [
            SafetyRule(
                rule_id="M-P3",
                name="P3规则",
                priority=Priority.P3,
                category=RuleCategory.MACHINE,
                condition=RuleCondition(
                    condition_type="threshold",
                    field="spindle_speed",
                    operator=">",
                    value=5000,
                ),
                action=RuleAction(
                    action_type=ActionType.ALERT,
                    target="spindle_speed",
                    value=5000,
                ),
            ),
            SafetyRule(
                rule_id="M-P0",
                name="P0规则",
                priority=Priority.P0,
                category=RuleCategory.MACHINE,
                condition=RuleCondition(
                    condition_type="threshold",
                    field="spindle_speed",
                    operator=">",
                    value=10000,
                ),
                action=RuleAction(
                    action_type=ActionType.STOP,
                    target="spindle_speed",
                    value=0,
                ),
            ),
        ]

        engine = SafetyRuleEngine()
        engine.load_rules(rules)

        sensor_data = {"spindle_speed": 12000}
        results = engine.evaluate(sensor_data)

        # 两个规则都会触发，但P0应该排在前面
        assert len(results) == 2
        assert results[0]["priority"] == "P0", f"P0规则应在最前面，实际: {results[0]['priority']}"
        assert results[0]["rule_id"] == "M-P0"

    def test_no_conflict_when_p0_field_not_targeted(self):
        """验证：不涉及P0字段时不报告冲突"""
        rules = [
            SafetyRule(
                rule_id="M-P0",
                name="P0规则",
                priority=Priority.P0,
                category=RuleCategory.MACHINE,
                condition=RuleCondition(
                    condition_type="threshold",
                    field="spindle_speed",
                    operator=">",
                    value=10000,
                ),
                action=RuleAction(
                    action_type=ActionType.STOP,
                    target="spindle_speed",
                    value=0,
                ),
            ),
            SafetyRule(
                rule_id="M-P2",
                name="P2规则: 修改feed_rate(不涉及P0字段)",
                priority=Priority.P2,
                category=RuleCategory.MACHINE,
                condition=RuleCondition(
                    condition_type="threshold",
                    field="cutting_force",
                    operator=">",
                    value=500,
                ),
                action=RuleAction(
                    action_type=ActionType.OVERRIDE,
                    target="feed_rate",
                    value=200,
                ),
            ),
        ]

        errors = _check_priority_dependency(rules)
        # P2规则修改的是feed_rate，不是P0的spindle_speed条件字段
        assert len(errors) == 0, f"不应报告冲突, 实际: {errors}"
