"""规则草稿沙箱验证器。

规则在应用前必须先经沙箱验证：
    - 在应用规则前，对规则进行沙箱验证
    - 验证内容包括：硬约束合规性、语法可解析性、边界情况、模拟执行
    - 验证失败的规则标记为 rejected，不可应用
    - 验证通过的规则标记为 validated，进入应用队列

设计原则：
    - 沙箱验证不触发任何真实生产操作
    - 模拟执行使用合成数据，不接触真实 Memory Store
    - 硬约束校验是 fail-fast：违反任一硬约束立即拒绝
    - 验证结果可追溯（记录到审计日志）

硬约束对齐：
    - 拒绝任何 skip_cam_validation 动作
    - 拒绝任何 unlock_succeeded / delete_succeeded 动作
    - 拒绝任何 force_pass 动作
    - 拒绝修改 cam_validation_required 默认值
    - 拒绝降低 HRC52 pending_calibration 的安全阈值

用法：
    validator = RuleValidator()
    result = validator.validate(rule_draft)
    if result.passed:
        applicator = RuleApplicator()
        applicator.apply(rule_draft)
    else:
        logger.warning(f"规则验证失败：{result.errors}")

本模块为门面：实现已拆分至 _validator_models / _checks_mixin / _test_mixin。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone


from app.dreaming.rule_synthesizer import RuleDraft
from app.dreaming._checks_mixin import _ChecksMixin
from app.dreaming._test_mixin import _TestMixin
from app.dreaming._validator_models import (  # noqa: F401
    FORBIDDEN_ACTIONS,
    FORBIDDEN_CONDITIONS,
    REQUIRED_HARD_CONSTRAINTS,
    ValidationResult,
    ValidationTestCase,
)

logger = logging.getLogger(__name__)


class RuleValidator(_ChecksMixin, _TestMixin):
    """规则草稿沙箱验证器。

    执行四阶段验证：
        1. 硬约束校验（fail-fast）
        2. 语法可解析性检查
        3. 边界情况测试
        4. 模拟执行（合成数据）

    验证流程不会触发任何真实生产操作，所有测试在内存中完成。
    """

    def __init__(self) -> None:
        """初始化验证器。"""
        self._validator_version = "0.1.0"

    def validate(self, rule: RuleDraft) -> ValidationResult:
        """验证规则草稿。

        Args:
            rule: 待验证的规则草稿。

        Returns:
            ValidationResult 实例。
        """
        errors: list[str] = []
        warnings: list[str] = []
        test_cases: list[ValidationTestCase] = []

        # 阶段 1：硬约束校验（fail-fast）
        hard_constraint_errors = self._check_hard_constraints(rule)
        errors.extend(hard_constraint_errors)

        # 阶段 2：语法可解析性检查
        syntax_errors, syntax_warnings = self._check_syntax(rule)
        errors.extend(syntax_errors)
        warnings.extend(syntax_warnings)

        # 阶段 3：边界情况测试
        boundary_cases = self._test_boundary_cases(rule)
        test_cases.extend(boundary_cases)
        for tc in boundary_cases:
            if not tc.actual_passed and tc.expected_passed:
                errors.append(f"边界测试失败 [{tc.name}]：{tc.error_message or '未通过'}")

        # 阶段 4：模拟执行
        sim_cases = self._simulate_execution(rule)
        test_cases.extend(sim_cases)
        for tc in sim_cases:
            if not tc.actual_passed and tc.expected_passed:
                errors.append(f"模拟执行失败 [{tc.name}]：{tc.error_message or '未通过'}")

        passed = len(errors) == 0

        return ValidationResult(
            passed=passed,
            errors=errors,
            warnings=warnings,
            test_cases=test_cases,
            validated_at=datetime.now(timezone.utc).isoformat(),
            validator_version=self._validator_version,
        )


# 阶段 1：硬约束校验


def validate_rule(rule: RuleDraft) -> ValidationResult:
    """便捷函数：验证规则草稿。

    Args:
        rule: 待验证的规则草稿。

    Returns:
        ValidationResult 实例。
    """
    validator = RuleValidator()
    return validator.validate(rule)
