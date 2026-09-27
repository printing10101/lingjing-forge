"""
审计日志测试

验证每条规则触发时都有完整的审计日志记录。
日志必须包含：时间戳、规则ID、条件值、动作、结果。
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
)


@pytest.fixture
def engine_with_rules():
    """创建带规则的引擎"""
    engine = SafetyRuleEngine()
    engine.load_rules([
        SafetyRule(
            rule_id="M-001",
            name="主轴速度超限保护",
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
            audit=True,
        ),
        SafetyRule(
            rule_id="M-002",
            name="主轴温度超限保护",
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
            audit=True,
        ),
        SafetyRule(
            rule_id="M-003",
            name="振动异常保护",
            priority=Priority.P1,
            category=RuleCategory.MACHINE,
            condition=RuleCondition(
                condition_type="threshold",
                field="vibration_rms", operator=">", value=5.0,
            ),
            action=RuleAction(
                action_type=ActionType.STOP,
                target="spindle_speed", value=0,
            ),
            audit=True,
        ),
    ])
    return engine


class TestAuditLogging:
    """审计日志测试"""

    def test_audit_log_created_when_rule_triggers(self, engine_with_rules):
        """验证：规则触发时产生审计日志"""
        sensor_data = {
            "spindle_speed": 12000,
            "max_spindle_speed": 10000,
        }

        results = engine_with_rules.evaluate(sensor_data)
        assert len(results) == 1

        audit_log = engine_with_rules.get_audit_log()
        assert len(audit_log) == 1, f"期望1条审计日志，实际: {len(audit_log)}"

    def test_audit_log_contains_required_fields(self, engine_with_rules):
        """
        验证：审计日志包含所有必需字段
        必需字段：时间戳、规则ID、条件值、动作、结果
        """
        sensor_data = {
            "spindle_speed": 12000,
            "max_spindle_speed": 10000,
        }

        engine_with_rules.evaluate(sensor_data)
        audit_log = engine_with_rules.get_audit_log()

        assert len(audit_log) >= 1

        for entry in audit_log:
            # 时间戳
            assert "timestamp" in entry, "审计日志缺少timestamp字段"
            assert isinstance(entry["timestamp"], float)
            assert entry["timestamp"] > 0

            # 规则ID
            assert "rule_id" in entry, "审计日志缺少rule_id字段"
            assert entry["rule_id"] == "M-001"

            # 条件值
            assert "condition_values" in entry, "审计日志缺少condition_values字段"
            assert "spindle_speed" in entry["condition_values"]
            assert entry["condition_values"]["spindle_speed"] == 12000

            # 动作
            assert "action" in entry, "审计日志缺少action字段"
            assert "type" in entry["action"]
            assert "target" in entry["action"]
            assert "value" in entry["action"]

            # 结果
            assert "result" in entry, "审计日志缺少result字段"
            assert isinstance(entry["result"], str)
            assert len(entry["result"]) > 0

    def test_no_audit_log_when_no_rule_triggers(self, engine_with_rules):
        """验证：无规则触发时不产生审计日志"""
        sensor_data = {
            "spindle_speed": 5000,
            "max_spindle_speed": 10000,
            "spindle_temperature": 30,
        }

        results = engine_with_rules.evaluate(sensor_data)
        assert len(results) == 0

        audit_log = engine_with_rules.get_audit_log()
        assert len(audit_log) == 0, (
            f"无规则触发时不应有审计日志，实际: {len(audit_log)}"
        )

    def test_multiple_rules_create_multiple_audit_entries(self, engine_with_rules):
        """验证：多条规则触发时产生对应数量的审计日志"""
        sensor_data = {
            "spindle_speed": 12000,
            "max_spindle_speed": 10000,
            "spindle_temperature": 90,
            "vibration_rms": 10,
        }

        engine_with_rules.evaluate(sensor_data)
        audit_log = engine_with_rules.get_audit_log()

        # M-001, M-002, M-003 都应触发
        assert len(audit_log) == 3, (
            f"期望3条审计日志，实际: {len(audit_log)}"
        )

        triggered_ids = {e["rule_id"] for e in audit_log}
        assert triggered_ids == {"M-001", "M-002", "M-003"}

    def test_audit_log_timestamps_are_sequential(self, engine_with_rules):
        """验证：审计日志时间戳按触发顺序递增"""
        sensor_data = {
            "spindle_speed": 12000,
            "max_spindle_speed": 10000,
            "spindle_temperature": 90,
        }

        engine_with_rules.evaluate(sensor_data)
        audit_log = engine_with_rules.get_audit_log()

        for i in range(1, len(audit_log)):
            assert audit_log[i]["timestamp"] >= audit_log[i - 1]["timestamp"], (
                f"审计日志时间戳应递增: entry[{i-1}]={audit_log[i-1]['timestamp']}, "
                f"entry[{i}]={audit_log[i]['timestamp']}"
            )

    def test_audit_log_clear(self, engine_with_rules):
        """验证：clear_audit_log能清除所有日志"""
        sensor_data = {"spindle_speed": 12000, "max_spindle_speed": 10000}

        engine_with_rules.evaluate(sensor_data)
        assert len(engine_with_rules.get_audit_log()) == 1

        engine_with_rules.clear_audit_log()
        assert len(engine_with_rules.get_audit_log()) == 0

    def test_audit_log_condition_values_match_sensor_data(self, engine_with_rules):
        """验证：审计日志中记录的条件值与触发时的传感器数据一致"""
        sensor_data = {
            "spindle_speed": 13500,
            "max_spindle_speed": 10000,
            "spindle_temperature": 95,
        }

        engine_with_rules.evaluate(sensor_data)
        audit_log = engine_with_rules.get_audit_log()

        # M-001的审计日志应有spindle_speed=13500
        m001_entries = [e for e in audit_log if e["rule_id"] == "M-001"]
        assert len(m001_entries) == 1
        assert m001_entries[0]["condition_values"]["spindle_speed"] == 13500

        # M-002的审计日志应有spindle_temperature=95
        m002_entries = [e for e in audit_log if e["rule_id"] == "M-002"]
        assert len(m002_entries) == 1
        assert m002_entries[0]["condition_values"]["spindle_temperature"] == 95

    def test_audit_disabled_rule_no_log(self, engine_with_rules):
        """验证：audit=False的规则触发时不产生审计日志"""
        engine = SafetyRuleEngine()
        engine.load_rules([
            SafetyRule(
                rule_id="T-001",
                name="无审计规则",
                priority=Priority.P1,
                category=RuleCategory.TOOL,
                condition=RuleCondition(
                    condition_type="threshold",
                    field="tool_wear", operator=">", value=0.5,
                ),
                action=RuleAction(
                    action_type=ActionType.FORCE_CHANGE,
                    target="tool_life_used", value=0,
                ),
                audit=False,  # 禁用审计
            ),
        ])

        sensor_data = {"tool_wear": 0.8}
        results = engine.evaluate(sensor_data)

        assert len(results) == 1, "规则应被触发"
        assert len(engine.get_audit_log()) == 0, (
            "audit=False时不应有审计日志"
        )

    def test_audit_log_priority_field(self, engine_with_rules):
        """验证：审计日志包含优先级字段"""
        sensor_data = {"spindle_speed": 12000, "max_spindle_speed": 10000}

        engine_with_rules.evaluate(sensor_data)
        audit_log = engine_with_rules.get_audit_log()

        for entry in audit_log:
            assert "priority" in entry, "审计日志缺少priority字段"
            assert entry["priority"] in ("P0", "P1", "P2", "P3"), (
                f"优先级值无效: {entry['priority']}"
            )
