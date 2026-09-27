"""规则硬约束/语法校验 mixin（从 rule_validator 拆出）。"""

from __future__ import annotations

import logging


from app.dreaming.rule_synthesizer import RuleDraft
from app.dreaming._validator_models import FORBIDDEN_ACTIONS, FORBIDDEN_CONDITIONS, REQUIRED_HARD_CONSTRAINTS

logger = logging.getLogger(__name__)


class _ChecksMixin:
    def _check_hard_constraints(self, rule: RuleDraft) -> list[str]:
        """检查规则是否违反硬约束。

        Args:
            rule: 规则草稿。

        Returns:
            错误列表（空列表表示通过）。
        """
        errors: list[str] = []

        # C3 bug 修复：rule.action / rule.condition 是 Dict[str, Any]，
        # 不能直接调用 .lower()。改用 json 序列化后做字符串匹配。
        import json

        action_str = json.dumps(rule.action, ensure_ascii=False).lower()
        for forbidden in FORBIDDEN_ACTIONS:
            if forbidden in action_str:
                errors.append(f"硬约束违反：action 包含禁止关键词 '{forbidden}'")

        condition_str = json.dumps(rule.condition, ensure_ascii=False).lower()
        for forbidden in FORBIDDEN_CONDITIONS:
            if forbidden.lower() in condition_str:
                errors.append(f"硬约束违反：condition 包含禁止的逻辑 '{forbidden}'")

        # 检查 respects_cam_validation 标志
        if not rule.respects_cam_validation:
            errors.append("硬约束违反：respects_cam_validation=False（必须为 True）")

        # 检查 respects_succeeded_lock 标志
        if not rule.respects_succeeded_lock:
            errors.append("硬约束违反：respects_succeeded_lock=False（必须为 True）")

        # 检查 metadata 中的硬约束键值对
        meta = rule.metadata or {}
        for key, expected_value in REQUIRED_HARD_CONSTRAINTS.items():
            if key in meta and meta[key] != expected_value:
                errors.append(f"硬约束违反：metadata.{key}={meta[key]}（期望 {expected_value}）")

        return errors

    def _check_syntax(self, rule: RuleDraft) -> tuple[list[str], list[str]]:
        """检查规则的语法可解析性。

        Args:
            rule: 规则草稿。

        Returns:
            (errors, warnings) 元组。
        """
        import json
        import re

        errors: list[str] = []
        warnings: list[str] = []

        # C3 同类 bug 修复：condition/action 是 Dict，不能调 .strip() / len()。
        # 改用 json 序列化字符串做非空与长度检查。
        condition_str = json.dumps(rule.condition, ensure_ascii=False)
        action_str = json.dumps(rule.action, ensure_ascii=False)

        # condition 不为空
        if not rule.condition or condition_str == "{}":
            errors.append("condition 为空")
        elif len(condition_str) > 500:
            warnings.append("condition 过长（>500 字符），可能影响可读性")

        # action 不为空
        if not rule.action or action_str == "{}":
            errors.append("action 为空")
        elif len(action_str) > 500:
            warnings.append("action 过长（>500 字符），可能影响可读性")

        # description 不为空
        if not rule.description or not rule.description.strip():
            errors.append("description 为空")

        # rule_id 格式校验（与 GraphStore 节点 ID 一致）
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_.\-]{0,127}$", rule.rule_id):
            errors.append(f"rule_id 格式非法：'{rule.rule_id}'（须匹配 ^[a-zA-Z_][a-zA-Z0-9_.\\-]{0, 127}$）")

        # confidence 范围校验
        if not 0.0 <= rule.confidence <= 1.0:
            errors.append(f"confidence 超出范围 [0, 1]：{rule.confidence}")

        # C7 bug 修复：rule_type 白名单必须与 RuleSynthesizer 实际生成的
        # 类型一致（parameter_adjustment / confidence_threshold /
        # validation_requirement / warning_rule），否则所有合成规则都会
        # 被判为"未知类型"而拒绝。
        valid_types = {
            "parameter_adjustment",
            "confidence_threshold",
            "validation_requirement",
            "warning_rule",
        }
        if rule.rule_type not in valid_types:
            warnings.append(f"rule_type '{rule.rule_type}' 不在标准类型 {valid_types} 中")

        return errors, warnings


# 阶段 3：边界情况测试
