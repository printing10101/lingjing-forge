"""
工艺规则冲突检测模块测试

覆盖三种冲突类型：
- 矛盾冲突 (CONTRADICTION): 条件完全相同但结论不同
- 子集冲突 (SUBSET): 一条规则条件是另一条的子集但结论不同
- 参数冲突 (PARAMETER): 多条规则对同一参数赋予不同值
"""

from app.rules.conflict_detector import (
    detect_conflicts,
    ConflictType,
    ConflictSeverity,
    _detect_contradiction,
    _detect_subset,
    _detect_parameter,
)
from app.database.rule_db import ProcessRule, RuleCondition, RuleResult

from tests.utils.perf_thresholds import perf_threshold


def make_rule(
    rule_id: int,
    conditions: list,
    result: dict,
    name: str = "",
) -> ProcessRule:
    """辅助函数：快速创建规则用于测试"""
    return ProcessRule(
        id=rule_id,
        name=name or f"rule_{rule_id}",
        conditions=[RuleCondition(**c) for c in conditions],
        logic_operator="AND",
        result=RuleResult(**result),
        status="active",
        priority=0,
    )


# 矛盾冲突测试


class TestContradictionConflict:
    """矛盾冲突测试：条件完全相同但结论不同"""

    def test_exact_same_conditions_different_results(self):
        """两条规则条件完全相同，结论不同 -> 应检测到矛盾冲突"""
        r1 = make_rule(
            1,
            conditions=[
                {"parameter": "材料", "operator": "=", "value": "45钢"},
                {"parameter": "工序", "operator": "=", "value": "粗铣"},
            ],
            result={"parameter": "切深", "operator": "<=", "value": "2"},
        )
        r2 = make_rule(
            2,
            conditions=[
                {"parameter": "材料", "operator": "=", "value": "45钢"},
                {"parameter": "工序", "operator": "=", "value": "粗铣"},
            ],
            result={"parameter": "切深", "operator": "<=", "value": "5"},
        )

        reports = _detect_contradiction([r1, r2])
        assert len(reports) == 1
        assert reports[0].conflict_type == ConflictType.CONTRADICTION
        assert reports[0].severity == ConflictSeverity.HIGH
        assert set(reports[0].conflicting_rule_ids) == {1, 2}

    def test_same_conditions_different_result_operators(self):
        """两条规则条件完全相同，结论运算符不同 -> 应检测到矛盾冲突"""
        r1 = make_rule(
            1,
            conditions=[{"parameter": "材料", "operator": "=", "value": "铝合金"}],
            result={"parameter": "转速", "operator": "<=", "value": "3000"},
        )
        r2 = make_rule(
            2,
            conditions=[{"parameter": "材料", "operator": "=", "value": "铝合金"}],
            result={"parameter": "转速", "operator": ">=", "value": "3000"},
        )

        reports = _detect_contradiction([r1, r2])
        assert len(reports) == 1
        assert reports[0].conflict_type == ConflictType.CONTRADICTION
        assert "转速" in reports[0].conflicting_parameters

    def test_same_conditions_same_results_no_conflict(self):
        """两条规则条件相同，结论也相同 -> 不应检测到冲突"""
        r1 = make_rule(
            1,
            conditions=[{"parameter": "材料", "operator": "=", "value": "45钢"}],
            result={"parameter": "切深", "operator": "<=", "value": "2"},
        )
        r2 = make_rule(
            2,
            conditions=[{"parameter": "材料", "operator": "=", "value": "45钢"}],
            result={"parameter": "切深", "operator": "<=", "value": "2"},
        )

        reports = _detect_contradiction([r1, r2])
        assert len(reports) == 0

    def test_different_conditions_no_contradiction(self):
        """条件不同的规则 -> 不应检测到矛盾冲突"""
        r1 = make_rule(
            1,
            conditions=[{"parameter": "材料", "operator": "=", "value": "45钢"}],
            result={"parameter": "切深", "operator": "<=", "value": "2"},
        )
        r2 = make_rule(
            2,
            conditions=[{"parameter": "材料", "operator": "=", "value": "不锈钢"}],
            result={"parameter": "切深", "operator": "<=", "value": "5"},
        )

        reports = _detect_contradiction([r1, r2])
        assert len(reports) == 0

    def test_condition_order_independent(self):
        """条件顺序不同但内容相同 -> 应检测到矛盾冲突"""
        r1 = make_rule(
            1,
            conditions=[
                {"parameter": "材料", "operator": "=", "value": "45钢"},
                {"parameter": "工序", "operator": "=", "value": "精铣"},
            ],
            result={"parameter": "进给", "operator": "<=", "value": "100"},
        )
        r2 = make_rule(
            2,
            conditions=[
                {"parameter": "工序", "operator": "=", "value": "精铣"},
                {"parameter": "材料", "operator": "=", "value": "45钢"},
            ],
            result={"parameter": "进给", "operator": "<=", "value": "200"},
        )

        reports = _detect_contradiction([r1, r2])
        assert len(reports) == 1
        assert reports[0].conflict_type == ConflictType.CONTRADICTION

    def test_three_rules_with_same_conditions(self):
        """三条规则条件相同但结论不同 -> 应检测到3对矛盾冲突"""
        r1 = make_rule(
            1,
            conditions=[{"parameter": "材料", "operator": "=", "value": "铜"}],
            result={"parameter": "转速", "operator": "=", "value": "1000"},
        )
        r2 = make_rule(
            2,
            conditions=[{"parameter": "材料", "operator": "=", "value": "铜"}],
            result={"parameter": "转速", "operator": "=", "value": "2000"},
        )
        r3 = make_rule(
            3,
            conditions=[{"parameter": "材料", "operator": "=", "value": "铜"}],
            result={"parameter": "转速", "operator": "=", "value": "3000"},
        )

        reports = _detect_contradiction([r1, r2, r3])
        assert len(reports) == 3


# 子集冲突测试


class TestSubsetConflict:
    """子集冲突测试：一条规则条件是另一条的子集但结论不同"""

    def test_proper_subset_different_results(self):
        """规则A条件是规则B的真子集，结论不同 -> 应检测到子集冲突"""
        r1 = make_rule(
            1,
            conditions=[{"parameter": "材料", "operator": "=", "value": "45钢"}],
            result={"parameter": "切深", "operator": "<=", "value": "3"},
        )
        r2 = make_rule(
            2,
            conditions=[
                {"parameter": "材料", "operator": "=", "value": "45钢"},
                {"parameter": "工序", "operator": "=", "value": "粗铣"},
            ],
            result={"parameter": "切深", "operator": "<=", "value": "2"},
        )

        reports = _detect_subset([r1, r2])
        assert len(reports) == 1
        assert reports[0].conflict_type == ConflictType.SUBSET
        assert reports[0].severity == ConflictSeverity.MEDIUM
        assert set(reports[0].conflicting_rule_ids) == {1, 2}

    def test_subset_with_multiple_extra_conditions(self):
        """规则A条件是规则B的真子集（多2个条件），结论不同 -> 应检测到子集冲突"""
        r1 = make_rule(
            1,
            conditions=[{"parameter": "材料", "operator": "=", "value": "钛合金"}],
            result={"parameter": "转速", "operator": "<=", "value": "800"},
        )
        r2 = make_rule(
            2,
            conditions=[
                {"parameter": "材料", "operator": "=", "value": "钛合金"},
                {"parameter": "工序", "operator": "=", "value": "钻孔"},
                {"parameter": "刀具", "operator": "=", "value": "硬质合金"},
            ],
            result={"parameter": "转速", "operator": "<=", "value": "500"},
        )

        reports = _detect_subset([r1, r2])
        assert len(reports) == 1
        assert reports[0].conflict_type == ConflictType.SUBSET

    def test_same_conditions_no_subset(self):
        """条件完全相同的规则 -> 不应检测到子集冲突（需要是真子集）"""
        r1 = make_rule(
            1,
            conditions=[{"parameter": "材料", "operator": "=", "value": "45钢"}],
            result={"parameter": "切深", "operator": "<=", "value": "2"},
        )
        r2 = make_rule(
            2,
            conditions=[{"parameter": "材料", "operator": "=", "value": "45钢"}],
            result={"parameter": "切深", "operator": "<=", "value": "5"},
        )

        reports = _detect_subset([r1, r2])
        assert len(reports) == 0

    def test_subset_same_results_no_conflict(self):
        """规则A条件是规则B的子集，结论相同 -> 不应检测到冲突"""
        r1 = make_rule(
            1,
            conditions=[{"parameter": "材料", "operator": "=", "value": "45钢"}],
            result={"parameter": "切深", "operator": "<=", "value": "2"},
        )
        r2 = make_rule(
            2,
            conditions=[
                {"parameter": "材料", "operator": "=", "value": "45钢"},
                {"parameter": "工序", "operator": "=", "value": "粗铣"},
            ],
            result={"parameter": "切深", "operator": "<=", "value": "2"},
        )

        reports = _detect_subset([r1, r2])
        assert len(reports) == 0

    def test_disjoint_conditions_no_subset(self):
        """条件完全不相交 -> 不应检测到子集冲突"""
        r1 = make_rule(
            1,
            conditions=[{"parameter": "材料", "operator": "=", "value": "45钢"}],
            result={"parameter": "切深", "operator": "<=", "value": "2"},
        )
        r2 = make_rule(
            2,
            conditions=[{"parameter": "工序", "operator": "=", "value": "精铣"}],
            result={"parameter": "切深", "operator": "<=", "value": "0.5"},
        )

        reports = _detect_subset([r1, r2])
        assert len(reports) == 0

    def test_reverse_subset_order(self):
        """传入顺序不影响子集检测结果（规则多的在前）"""
        r1 = make_rule(
            1,
            conditions=[
                {"parameter": "材料", "operator": "=", "value": "铝合金"},
                {"parameter": "温度", "operator": ">", "value": "25"},
            ],
            result={"parameter": "冷却", "operator": "=", "value": "开启"},
        )
        r2 = make_rule(
            2,
            conditions=[{"parameter": "材料", "operator": "=", "value": "铝合金"}],
            result={"parameter": "冷却", "operator": "=", "value": "关闭"},
        )

        # r1条件多，r2条件少且是r1的子集
        reports = _detect_subset([r1, r2])
        assert len(reports) == 1
        assert reports[0].conflict_type == ConflictType.SUBSET


# 参数冲突测试


class TestParameterConflict:
    """参数冲突测试：多条规则对同一参数赋予不同值"""

    def test_same_parameter_different_values(self):
        """两条规则结论参数相同但值不同 -> 应检测到参数冲突"""
        r1 = make_rule(
            1,
            conditions=[{"parameter": "材料", "operator": "=", "value": "45钢"}],
            result={"parameter": "切深", "operator": "<=", "value": "2"},
        )
        r2 = make_rule(
            2,
            conditions=[{"parameter": "工序", "operator": "=", "value": "钻孔"}],
            result={"parameter": "切深", "operator": "<=", "value": "5"},
        )

        reports = _detect_parameter([r1, r2])
        assert len(reports) == 1
        assert reports[0].conflict_type == ConflictType.PARAMETER
        assert reports[0].severity == ConflictSeverity.LOW
        assert "切深" in reports[0].conflicting_parameters

    def test_same_parameter_different_operators(self):
        """两条规则结论参数相同但运算符不同 -> 应检测到参数冲突"""
        r1 = make_rule(
            1,
            conditions=[{"parameter": "工况", "operator": "=", "value": "正常"}],
            result={"parameter": "转速", "operator": "<=", "value": "3000"},
        )
        r2 = make_rule(
            2,
            conditions=[{"parameter": "工况", "operator": "=", "value": "重载"}],
            result={"parameter": "转速", "operator": ">=", "value": "3000"},
        )

        reports = _detect_parameter([r1, r2])
        assert len(reports) == 1
        assert reports[0].conflict_type == ConflictType.PARAMETER

    def test_different_parameters_no_conflict(self):
        """结论参数不同的规则 -> 不应检测到参数冲突"""
        r1 = make_rule(
            1,
            conditions=[{"parameter": "材料", "operator": "=", "value": "45钢"}],
            result={"parameter": "切深", "operator": "<=", "value": "2"},
        )
        r2 = make_rule(
            2,
            conditions=[{"parameter": "工序", "operator": "=", "value": "精铣"}],
            result={"parameter": "转速", "operator": "<=", "value": "5000"},
        )

        reports = _detect_parameter([r1, r2])
        assert len(reports) == 0

    def test_same_parameter_same_value_no_conflict(self):
        """结论参数相同且值也相同 -> 不应检测到参数冲突"""
        r1 = make_rule(
            1,
            conditions=[{"parameter": "材料", "operator": "=", "value": "45钢"}],
            result={"parameter": "切深", "operator": "<=", "value": "2"},
        )
        r2 = make_rule(
            2,
            conditions=[{"parameter": "工序", "operator": "=", "value": "粗铣"}],
            result={"parameter": "切深", "operator": "<=", "value": "2"},
        )

        reports = _detect_parameter([r1, r2])
        assert len(reports) == 0

    def test_multiple_rules_same_parameter(self):
        """多条规则对同一参数赋予不同值 -> 应检测到多对参数冲突"""
        r1 = make_rule(
            1,
            conditions=[{"parameter": "材料", "operator": "=", "value": "钢"}],
            result={"parameter": "进给", "operator": "=", "value": "100"},
        )
        r2 = make_rule(
            2,
            conditions=[{"parameter": "材料", "operator": "=", "value": "铝"}],
            result={"parameter": "进给", "operator": "=", "value": "200"},
        )
        r3 = make_rule(
            3,
            conditions=[{"parameter": "材料", "operator": "=", "value": "铜"}],
            result={"parameter": "进给", "operator": "=", "value": "150"},
        )

        reports = _detect_parameter([r1, r2, r3])
        assert len(reports) == 3


# detect_conflicts 集成测试


class TestDetectConflictsIntegration:
    """detect_conflicts 主函数集成测试"""

    def test_empty_rules(self):
        """空规则列表 -> 返回空"""
        assert detect_conflicts([]) == []

    def test_single_rule(self):
        """单条规则 -> 返回空"""
        r = make_rule(
            1,
            conditions=[{"parameter": "材料", "operator": "=", "value": "45钢"}],
            result={"parameter": "切深", "operator": "<=", "value": "2"},
        )
        assert detect_conflicts([r]) == []

    def test_no_conflicts(self):
        """无冲突的规则列表 -> 返回空"""
        r1 = make_rule(
            1,
            conditions=[{"parameter": "材料", "operator": "=", "value": "45钢"}],
            result={"parameter": "切深", "operator": "<=", "value": "2"},
        )
        r2 = make_rule(
            2,
            conditions=[{"parameter": "材料", "operator": "=", "value": "铝"}],
            result={"parameter": "转速", "operator": "<=", "value": "5000"},
        )
        assert detect_conflicts([r1, r2]) == []

    def test_deduplication_across_types(self):
        """同一对规则不应在不同冲突类型中重复出现"""
        r1 = make_rule(
            1,
            conditions=[{"parameter": "材料", "operator": "=", "value": "45钢"}],
            result={"parameter": "切深", "operator": "<=", "value": "2"},
        )
        r2 = make_rule(
            2,
            conditions=[{"parameter": "材料", "operator": "=", "value": "45钢"}],
            result={"parameter": "切深", "operator": "<=", "value": "5"},
        )

        reports = detect_conflicts([r1, r2])
        # 这对规则只应报告一次
        pairs = [tuple(sorted(r.conflicting_rule_ids)) for r in reports]
        assert len(pairs) == len(set(pairs)), "存在重复的规则对报告"

    def test_performance_100_rules(self):
        """100条规则的冲突检测应在合理时间内完成"""
        import time

        rules = []
        for i in range(100):
            rules.append(
                make_rule(
                    i + 1,
                    conditions=[{"parameter": f"材料{i % 10}", "operator": "=", "value": f"材料{i % 10}"}],
                    result={"parameter": "切深", "operator": "<=", "value": str(i + 1)},
                )
            )

        start = time.time()
        detect_conflicts(rules)
        elapsed = time.time() - start

        assert elapsed < perf_threshold(1.0), f"100条规则检测耗时 {elapsed:.3f}s，超过1秒限制"

    def test_conflict_report_structure(self):
        """冲突报告应包含所有必要字段"""
        r1 = make_rule(
            1,
            conditions=[{"parameter": "材料", "operator": "=", "value": "45钢"}],
            result={"parameter": "切深", "operator": "<=", "value": "2"},
        )
        r2 = make_rule(
            2,
            conditions=[{"parameter": "材料", "operator": "=", "value": "45钢"}],
            result={"parameter": "切深", "operator": "<=", "value": "5"},
        )

        reports = detect_conflicts([r1, r2])
        assert len(reports) >= 1

        report = reports[0]
        assert isinstance(report.conflicting_rule_ids, list)
        assert len(report.conflicting_rule_ids) == 2
        assert isinstance(report.conflict_type, ConflictType)
        assert isinstance(report.severity, ConflictSeverity)
        assert isinstance(report.description, str)
        assert len(report.description) > 0
        assert isinstance(report.conflicting_parameters, list)

    def test_mixed_conflict_types(self):
        """混合多种冲突类型应正确检测"""
        # 矛盾冲突对
        r1 = make_rule(
            1,
            conditions=[{"parameter": "材料", "operator": "=", "value": "钢"}],
            result={"parameter": "切深", "operator": "<=", "value": "2"},
        )
        r2 = make_rule(
            2,
            conditions=[{"parameter": "材料", "operator": "=", "value": "钢"}],
            result={"parameter": "切深", "operator": "<=", "value": "5"},
        )

        # 参数冲突对（条件完全不同）
        r3 = make_rule(
            3,
            conditions=[{"parameter": "工序", "operator": "=", "value": "钻孔"}],
            result={"parameter": "切深", "operator": "<=", "value": "3"},
        )

        reports = detect_conflicts([r1, r2, r3])
        assert len(reports) >= 2
