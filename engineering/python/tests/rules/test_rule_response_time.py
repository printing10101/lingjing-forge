"""
规则响应时间测试

验证从条件触发到动作执行的时间 < 5ms。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest  # noqa: E402

from app.rules.safety_constraint_rules import (  # noqa: E402
    SafetyRuleEngine,
    Priority,
    RuleCategory,
    RuleCondition,
    RuleAction,
    ActionType,
    SafetyRule,
)

RESPONSE_TIME_LIMIT_MS = 5.0  # 响应时间上限


@pytest.fixture
def engine():
    """创建带全部10条规则的引擎"""
    yaml_path = Path(__file__).resolve().parent.parent.parent.parent / "config" / "safety_rules.yaml"
    if not yaml_path.exists():
        pytest.skip(f"规则文件不存在: {yaml_path}")
    return SafetyRuleEngine.from_yaml(str(yaml_path))


@pytest.fixture
def small_engine():
    """创建仅1条规则的引擎"""
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
                target="spindle_speed",
                value="max_spindle_speed * 0.9",
            ),
        ),
    ])
    return engine


class TestResponseTime:
    """响应时间测试"""

    def test_single_rule_response_time(self, small_engine):
        """
        验证：单条规则从触发到执行 < 5ms
        """
        sensor_data = {"spindle_speed": 12000, "max_spindle_speed": 10000}

        # 预热
        small_engine.evaluate(sensor_data, collect_audit=False)

        times = []
        for _ in range(100):
            start = time.perf_counter()
            _ = small_engine.evaluate(sensor_data, collect_audit=False)
            elapsed = (time.perf_counter() - start) * 1000
            times.append(elapsed)

        avg_time = sum(times) / len(times)
        max_time = max(times)

        assert avg_time < RESPONSE_TIME_LIMIT_MS, (
            f"平均响应时间{avg_time:.3f}ms超过上限{RESPONSE_TIME_LIMIT_MS}ms"
        )
        assert max_time < RESPONSE_TIME_LIMIT_MS * 3, (
            f"最大响应时间{max_time:.3f}ms超过3倍上限"
        )

    def test_all_10_rules_response_time(self, engine):
        """
        验证：全部10条规则从触发到执行 < 5ms
        """
        sensor_data = {
            "spindle_speed": 20000,
            "max_spindle_speed": 15000,
            "spindle_temperature": 85,
            "vibration_rms": 15,
            "vibration_threshold": 5,
            "feed_rate": 600,
            "max_feed_rate": 500,
            "tool_wear": 0.9,
            "tool_wear_limit": 0.7,
            "acoustic_emission": 120,
            "acoustic_emission_threshold": 80,
            "tool_life_used": 1200,
            "tool_rated_life": 1000,
            "cutting_force": 800,
            "material_force_limit": 600,
            "dimension_deviation": 0.05,
            "tolerance_band": 0.02,
            "overcut_detected": 1,
        }

        # 预热
        engine.evaluate(sensor_data, collect_audit=False)

        times = []
        for _ in range(100):
            start = time.perf_counter()
            engine.evaluate(sensor_data, collect_audit=False)
            elapsed = (time.perf_counter() - start) * 1000
            times.append(elapsed)

        avg_time = sum(times) / len(times)
        max_time = max(times)

        assert avg_time < RESPONSE_TIME_LIMIT_MS, (
            f"10条规则平均响应时间{avg_time:.3f}ms超过上限{RESPONSE_TIME_LIMIT_MS}ms"
        )
        assert max_time < RESPONSE_TIME_LIMIT_MS * 5, (
            f"10条规则最大响应时间{max_time:.3f}ms超过5倍上限"
        )

    def test_no_trigger_response_time(self, engine):
        """
        验证：无规则触发时的评估速度也应该很快
        """
        sensor_data = {
            "spindle_speed": 1000,
            "max_spindle_speed": 15000,
            "spindle_temperature": 30,
            "vibration_rms": 1,
            "feed_rate": 200,
            "max_feed_rate": 500,
            "tool_wear": 0.1,
            "tool_life_used": 100,
            "tool_rated_life": 1000,
            "cutting_force": 100,
            "material_force_limit": 600,
            "dimension_deviation": 0.005,
            "tolerance_band": 0.02,
            "overcut_detected": 0,
        }

        engine.evaluate(sensor_data, collect_audit=False)  # 预热

        times = []
        for _ in range(100):
            start = time.perf_counter()
            results = engine.evaluate(sensor_data, collect_audit=False)
            elapsed = (time.perf_counter() - start) * 1000
            times.append(elapsed)
            assert len(results) == 0, "无规则应被触发"

        avg_time = sum(times) / len(times)
        assert avg_time < RESPONSE_TIME_LIMIT_MS, (
            f"无规则触发时平均评估时间{avg_time:.3f}ms应<{RESPONSE_TIME_LIMIT_MS}ms"
        )

    def test_response_time_under_varying_load(self, engine):
        """验证：不同触发规则数量下响应时间仍满足要求"""
        scenarios = [
            # 触发M-001
            {"spindle_speed": 20000, "max_spindle_speed": 15000},
            # 触发M-001 + M-002
            {"spindle_speed": 20000, "max_spindle_speed": 15000,
             "spindle_temperature": 90},
            # 触发多条规则
            {
                "spindle_speed": 20000, "max_spindle_speed": 15000,
                "spindle_temperature": 90, "vibration_rms": 20,
                "vibration_threshold": 5, "feed_rate": 1000,
                "max_feed_rate": 500, "tool_wear": 0.9,
                "tool_wear_limit": 0.7, "tool_life_used": 1500,
                "tool_rated_life": 1000,
            },
        ]

        for scenario in scenarios:
            engine.evaluate(scenario, collect_audit=False)  # 预热
            times = []
            for _ in range(50):
                start = time.perf_counter()
                engine.evaluate(scenario, collect_audit=False)
                elapsed = (time.perf_counter() - start) * 1000
                times.append(elapsed)

            avg = sum(times) / len(times)
            assert avg < RESPONSE_TIME_LIMIT_MS, (
                f"场景{scenario}平均响应时间{avg:.3f}ms超过{RESPONSE_TIME_LIMIT_MS}ms"
            )
