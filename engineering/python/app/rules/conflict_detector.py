"""
工艺规则冲突检测模块

提供规则冲突检测算法，支持以下冲突类型：
- 矛盾冲突 (CONTRADICTION): 条件完全相同但结论不同
- 子集冲突 (SUBSET): 一条规则的条件是另一条的子集但结论不同
- 参数冲突 (PARAMETER): 多条规则对同一参数赋予不同值

性能目标: 规则数量 <= 1000 时，单次检测时间 < 1 秒
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum


from app.database.rule_db import ProcessRule, RuleCondition, RuleResult

logger = logging.getLogger(__name__)


class ConflictType(str, Enum):
    """冲突类型枚举"""

    CONTRADICTION = "CONTRADICTION"  # 矛盾冲突：条件完全相同但结论不同
    SUBSET = "SUBSET"  # 子集冲突：条件是子集关系但结论不同
    PARAMETER = "PARAMETER"  # 参数冲突：同一参数被赋予不同值


class ConflictSeverity(str, Enum):
    """冲突严重程度枚举"""

    HIGH = "HIGH"  # 高：矛盾冲突，规则会直接冲突
    MEDIUM = "MEDIUM"  # 中：子集冲突，部分场景下冲突
    LOW = "LOW"  # 低：参数冲突，可能需要人工确认


@dataclass
class ConflictReport:
    """冲突报告数据结构"""

    conflicting_rule_ids: list[int]  # 冲突规则ID列表
    conflict_type: ConflictType  # 冲突类型
    severity: ConflictSeverity  # 严重程度
    description: str  # 冲突详细描述
    conflicting_parameters: list[str] = field(default_factory=list)  # 冲突涉及的参数


def _normalize_condition(cond: RuleCondition) -> tuple[str, str, str]:
    """将条件项标准化为可比较的元组"""
    return (cond.parameter.strip().lower(), cond.operator.strip(), cond.value.strip())


def _normalize_result(result: RuleResult) -> tuple[str, str, str]:
    """将结果项标准化为可比较的元组"""
    return (result.parameter.strip().lower(), result.operator.strip(), result.value.strip())


def _get_condition_signature(rule: ProcessRule) -> frozenset:
    """
    获取规则条件的签名（忽略顺序）

    返回 frozenset 以便进行集合比较
    """
    return frozenset(_normalize_condition(c) for c in rule.conditions)


def _get_result_signature(rule: ProcessRule) -> tuple[str, str, str]:
    """获取规则结果的签名"""
    if rule.result is None:
        return ("", "", "")
    return _normalize_result(rule.result)


def _results_conflict(r1: RuleResult, r2: RuleResult) -> bool:
    """
    判断两个结果是否冲突

    冲突判定：
    1. 同一参数被赋予不同的值
    2. 同一参数的约束条件相互矛盾（如 >5 和 <3）
    """
    if r1 is None or r2 is None:
        return False

    s1 = _normalize_result(r1)
    s2 = _normalize_result(r2)

    # 同一参数，不同值或不同运算符
    if s1[0] == s2[0] and s1 != s2:
        return True

    return False


def _detect_contradiction(rules: list[ProcessRule]) -> list[ConflictReport]:
    """
    检测矛盾冲突

    矛盾冲突定义：两条规则的条件完全相同（不考虑顺序），但结论不同。
    这是最严重的冲突类型，因为相同的输入会导致不同的输出。

    算法: 使用条件签名哈希，O(n) 时间复杂度
    """
    reports = []
    # 条件签名 -> 规则列表
    sig_map: dict[frozenset, list[ProcessRule]] = {}

    for rule in rules:
        if not rule.conditions or not rule.result:
            continue
        sig = _get_condition_signature(rule)
        sig_map.setdefault(sig, []).append(rule)

    # 检查相同签名的规则是否有冲突的结论
    for sig, rule_group in sig_map.items():
        if len(rule_group) < 2:
            continue

        # 两两比较
        for i in range(len(rule_group)):
            for j in range(i + 1, len(rule_group)):
                r1, r2 = rule_group[i], rule_group[j]
                if r1.result is not None and r2.result is not None and _results_conflict(r1.result, r2.result):
                    # 获取冲突的参数名
                    conflict_params = []
                    if r1.result and r2.result:
                        n1 = _normalize_result(r1.result)
                        n2 = _normalize_result(r2.result)
                        if n1[0] == n2[0]:
                            conflict_params.append(n1[0])

                    reports.append(
                        ConflictReport(
                            conflicting_rule_ids=[r1.id or 0, r2.id or 0],
                            conflict_type=ConflictType.CONTRADICTION,
                            severity=ConflictSeverity.HIGH,
                            description=(
                                f"规则 {r1.id} 和规则 {r2.id} 存在矛盾冲突："
                                f"条件完全相同但结论不同。"
                                f"规则 {r1.id} 结论: {r1.result.parameter} {r1.result.operator} {r1.result.value}, "
                                f"规则 {r2.id} 结论: {r2.result.parameter} {r2.result.operator} {r2.result.value}"
                            ),
                            conflicting_parameters=conflict_params,
                        )
                    )

    return reports


def _is_subset(small: frozenset, large: frozenset) -> bool:
    """判断 small 是否是 large 的真子集"""
    return small < large  # 严格子集


def _detect_subset(rules: list[ProcessRule]) -> list[ConflictReport]:
    """
    检测子集冲突

    子集冲突定义：规则A的条件是规则B条件的子集（A的条件更宽松），
    但两者的结论不同。当A的条件满足时，两条规则都会被触发但给出不同结论。

    算法: 按条件数量排序后比较，O(n^2) 时间复杂度
    """
    reports = []
    # 按条件数量排序，子集的条件数量一定 <= 超集
    sorted_rules = sorted([r for r in rules if r.conditions and r.result], key=lambda r: len(r.conditions))

    for i in range(len(sorted_rules)):
        sig_i = _get_condition_signature(sorted_rules[i])
        for j in range(i + 1, len(sorted_rules)):
            sig_j = _get_condition_signature(sorted_rules[j])

            # 检查 sig_i 是否是 sig_j 的子集
            if _is_subset(sig_i, sig_j):
                r1, r2 = sorted_rules[i], sorted_rules[j]
                result_i = r1.result
                result_j = r2.result
                if result_i is not None and result_j is not None and _results_conflict(result_i, result_j):
                    conflict_params = []
                    if r1.result and r2.result:
                        n1 = _normalize_result(r1.result)
                        n2 = _normalize_result(r2.result)
                        if n1[0] == n2[0]:
                            conflict_params.append(n1[0])

                    reports.append(
                        ConflictReport(
                            conflicting_rule_ids=[r1.id or 0, r2.id or 0],
                            conflict_type=ConflictType.SUBSET,
                            severity=ConflictSeverity.MEDIUM,
                            description=(
                                f"规则 {r1.id} 和规则 {r2.id} 存在子集冲突："
                                f"规则 {r1.id} 的条件({len(r1.conditions)}个)是规则 {r2.id} 条件({len(r2.conditions)}个)的子集，"
                                f"但结论不同。"
                                f"规则 {r1.id} 结论: {result_i.parameter} {result_i.operator} {result_i.value}, "
                                f"规则 {r2.id} 结论: {result_j.parameter} {result_j.operator} {result_j.value}"
                            ),
                            conflicting_parameters=conflict_params,
                        )
                    )

    return reports


def _detect_parameter(rules: list[ProcessRule]) -> list[ConflictReport]:
    """
    检测参数冲突

    参数冲突定义：多条规则的结论针对同一个参数，但赋予的值不同。
    与矛盾冲突的区别：参数冲突的条件可以完全不同，只要结论参数相同且值不同。

    算法: 按结果参数分组后比较，O(n) 时间复杂度
    """
    reports = []
    # 结果参数 -> 规则列表
    param_map: dict[str, list[ProcessRule]] = {}

    for rule in rules:
        if not rule.result:
            continue
        result_sig = _normalize_result(rule.result)
        param_name = result_sig[0]
        param_map.setdefault(param_name, []).append(rule)

    # 检查同一参数的规则是否有冲突的值
    for param_name, rule_group in param_map.items():
        if len(rule_group) < 2:
            continue

        # 两两比较
        for i in range(len(rule_group)):
            for j in range(i + 1, len(rule_group)):
                r1, r2 = rule_group[i], rule_group[j]
                result_i = r1.result
                result_j = r2.result
                if result_i is not None and result_j is not None and _results_conflict(result_i, result_j):
                    # 跳过已经作为矛盾冲突或子集冲突报告的
                    # （参数冲突是更一般的冲突，避免重复报告）
                    reports.append(
                        ConflictReport(
                            conflicting_rule_ids=[r1.id or 0, r2.id or 0],
                            conflict_type=ConflictType.PARAMETER,
                            severity=ConflictSeverity.LOW,
                            description=(
                                f"规则 {r1.id} 和规则 {r2.id} 存在参数冲突："
                                f"对同一参数 '{param_name}' 赋予了不同的值。"
                                f"规则 {r1.id}: {result_i.parameter} {result_i.operator} {result_i.value}, "
                                f"规则 {r2.id}: {result_j.parameter} {result_j.operator} {result_j.value}"
                            ),
                            conflicting_parameters=[param_name],
                        )
                    )

    return reports


def detect_conflicts(rules: list[ProcessRule]) -> list[ConflictReport]:
    """
    检测规则列表中的所有冲突

    参数:
        rules: 待检测的工艺规则列表

    返回:
        冲突报告列表，包含所有检测到的冲突信息

    性能:
        - 矛盾冲突: O(n) 使用哈希分组
        - 子集冲突: O(n^2) 最坏情况，实际远小于 n^2
        - 参数冲突: O(n) 使用哈希分组
        - 规则数量 <= 1000 时，总检测时间 < 1 秒
    """
    if not rules or len(rules) < 2:
        return []

    logger.info("开始冲突检测，规则数量: %s", len(rules))

    all_reports = []

    # 检测三种冲突类型
    all_reports.extend(_detect_contradiction(rules))
    all_reports.extend(_detect_subset(rules))
    all_reports.extend(_detect_parameter(rules))

    # 去重：同一对规则ID不应在多种冲突类型中重复出现
    seen_pairs: set[tuple[int, int]] = set()
    unique_reports = []
    for report in all_reports:
        pair = (int(report.conflicting_rule_ids[0]), int(report.conflicting_rule_ids[1]))
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            unique_reports.append(report)

    logger.info("冲突检测完成，发现 %s 个冲突", len(unique_reports))
    return unique_reports
