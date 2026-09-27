"""
规则覆盖率测试

验证所有已知危险场景都有对应的安全规则覆盖。
要求：每种危险场景都有对应规则。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest  # noqa: E402

from app.rules.safety_constraint_rules import (  # noqa: E402
    SafetyRuleEngine,
    RuleCategory,
    Priority,
)

# 已知危险场景清单（必须被规则覆盖）

KNOWN_HAZARD_SCENARIOS = [
    # 机床场景
    {
        "scenario": "主轴转速超过安全限速",
        "category": "M",
        "expected_rule_ids": ["M-001"],
        "min_priority": "P0",
    },
    {
        "scenario": "主轴温度超过80°C",
        "category": "M",
        "expected_rule_ids": ["M-002"],
        "min_priority": "P1",
    },
    {
        "scenario": "振动RMS超过阈值2倍",
        "category": "M",
        "expected_rule_ids": ["M-003"],
        "min_priority": "P1",
    },
    {
        "scenario": "进给速率超过机床最大允许值",
        "category": "M",
        "expected_rule_ids": ["M-004"],
        "min_priority": "P1",
    },
    # 刀具场景
    {
        "scenario": "刀具磨损量超过允许值",
        "category": "T",
        "expected_rule_ids": ["T-001"],
        "min_priority": "P1",
    },
    {
        "scenario": "声发射突增+振动异常(断刀)",
        "category": "T",
        "expected_rule_ids": ["T-002"],
        "min_priority": "P0",
    },
    {
        "scenario": "刀具使用时间超过额定寿命",
        "category": "T",
        "expected_rule_ids": ["T-003"],
        "min_priority": "P1",
    },
    # 工艺场景
    {
        "scenario": "切削力超过材料极限",
        "category": "P",
        "expected_rule_ids": ["P-001"],
        "min_priority": "P2",
    },
    {
        "scenario": "尺寸偏差超过公差带",
        "category": "P",
        "expected_rule_ids": ["P-002"],
        "min_priority": "P2",
    },
    {
        "scenario": "检测到过切特征",
        "category": "P",
        "expected_rule_ids": ["P-003"],
        "min_priority": "P0",
    },
]


@pytest.fixture(scope="module")
def engine():
    """加载安全规则引擎"""
    yaml_path = Path(__file__).resolve().parent.parent.parent.parent / "config" / "safety_rules.yaml"
    if not yaml_path.exists():
        pytest.skip(f"规则文件不存在: {yaml_path}")
    return SafetyRuleEngine.from_yaml(str(yaml_path))


class TestRuleCoverage:
    """规则覆盖率测试"""

    def test_all_scenarios_have_rule_coverage(self, engine):
        """验证：每种已知危险场景至少有一条规则覆盖"""
        rule_ids = {r.rule_id for r in engine.rules}

        uncovered = []
        for scenario in KNOWN_HAZARD_SCENARIOS:
            matched = set(scenario["expected_rule_ids"]) & rule_ids
            if not matched:
                uncovered.append(scenario["scenario"])

        assert len(uncovered) == 0, f"以下危险场景缺少规则覆盖: {uncovered}"

    def test_scenario_count_matches_rule_count(self, engine):
        """验证：已知场景数量不超规则总数（所有场景都应有规则）"""
        scenario_rules = set()
        for s in KNOWN_HAZARD_SCENARIOS:
            scenario_rules.update(s["expected_rule_ids"])

        rule_ids = {r.rule_id for r in engine.rules}
        missing = scenario_rules - rule_ids

        assert len(missing) == 0, f"以下规则在场景定义中存在但未加载: {missing}"

    def test_machine_category_coverage(self, engine):
        """验证：机床安全场景全部覆盖"""
        machine_scenarios = [s for s in KNOWN_HAZARD_SCENARIOS if s["category"] == "M"]
        machine_rules = engine.get_rules_by_category(RuleCategory.MACHINE)

        for scenario in machine_scenarios:
            found = any(r.rule_id in scenario["expected_rule_ids"] for r in machine_rules)
            assert found, f"机床场景未覆盖: {scenario['scenario']}"

    def test_tool_category_coverage(self, engine):
        """验证：刀具安全场景全部覆盖"""
        tool_scenarios = [s for s in KNOWN_HAZARD_SCENARIOS if s["category"] == "T"]
        tool_rules = engine.get_rules_by_category(RuleCategory.TOOL)

        for scenario in tool_scenarios:
            found = any(r.rule_id in scenario["expected_rule_ids"] for r in tool_rules)
            assert found, f"刀具场景未覆盖: {scenario['scenario']}"

    def test_process_category_coverage(self, engine):
        """验证：工艺场景全部覆盖"""
        process_scenarios = [s for s in KNOWN_HAZARD_SCENARIOS if s["category"] == "P"]
        process_rules = engine.get_rules_by_category(RuleCategory.PROCESS)

        for scenario in process_scenarios:
            found = any(r.rule_id in scenario["expected_rule_ids"] for r in process_rules)
            assert found, f"工艺场景未覆盖: {scenario['scenario']}"

    def test_priority_requirements_met(self, engine):
        """验证：每条场景对应的规则优先级不低于要求"""
        rule_map = {r.rule_id: r for r in engine.rules}

        for scenario in KNOWN_HAZARD_SCENARIOS:
            min_priority = Priority(scenario["min_priority"])
            for rule_id in scenario["expected_rule_ids"]:
                if rule_id in rule_map:
                    rule = rule_map[rule_id]
                    assert rule.priority.level <= min_priority.level, (
                        f"场景'{scenario['scenario']}'要求优先级≥{min_priority.value}, "
                        f"但规则{rule_id}优先级为{rule.priority.value}"
                    )

    def test_p0_rules_exist(self, engine):
        """验证：P0(人员安全)规则确实存在"""
        p0_rules = engine.get_rules_by_priority(Priority.P0)
        p0_ids = {r.rule_id for r in p0_rules}
        expected_p0 = {s["expected_rule_ids"][0] for s in KNOWN_HAZARD_SCENARIOS if s["min_priority"] == "P0"}
        # M-001, T-002, P-003 should all be P0
        for rid in expected_p0:
            assert rid in p0_ids, f"规则 {rid} 应为P0但未找到或优先级不对"


class TestRuleCount:
    """规则数量验证"""

    def test_minimum_rule_count(self, engine):
        """验证：至少包含10条基础安全规则"""
        assert engine.rule_count >= 10, f"期望至少10条安全规则，实际: {engine.rule_count}"

    def test_all_categories_present(self, engine):
        """验证：M/T/P三类规则都存在"""
        categories = {r.category for r in engine.rules}
        assert RuleCategory.MACHINE in categories, "缺少机床(M)类规则"
        assert RuleCategory.TOOL in categories, "缺少刀具(T)类规则"
        assert RuleCategory.PROCESS in categories, "缺少工艺(P)类规则"

    def test_all_priorities_present(self, engine):
        """验证：P0/P1/P2三级优先级都有规则"""
        priorities = {r.priority for r in engine.rules}
        assert Priority.P0 in priorities, "缺少P0级规则"
        assert Priority.P1 in priorities, "缺少P1级规则"
        assert Priority.P2 in priorities, "缺少P2级规则"
