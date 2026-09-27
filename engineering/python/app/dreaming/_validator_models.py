"""规则验证数据类与常量（从 rule_validator 拆出）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 禁止的动作关键词（硬约束）
FORBIDDEN_ACTIONS = {
    "skip_cam_validation",
    "bypass_cam_validation",
    "force_pass",
    "force_pass_cam",
    "unlock_succeeded",
    "delete_succeeded",
    "remove_succeeded_lock",
    "disable_cam_validation",
    "set_cam_validation_false",
    "lower_hrc52_threshold",
    "override_pending_calibration",
}

# 禁止的 condition 关键词（防止条件中包含绕过逻辑）
FORBIDDEN_CONDITIONS = {
    "cam_validation_required == False",
    "cam_validation_required=False",
    "succeeded_lock == False",
    "allow_delete_succeeded == True",
    "LNN_CP_ALLOW_DELETE_SUCCEEDED",
}

# 必须保持的硬约束键值对
REQUIRED_HARD_CONSTRAINTS = {
    "cam_validation_required": True,
    "allow_delete_succeeded": False,
    "k_s_direct_transfer": True,
}


@dataclass
class ValidationTestCase:
    """沙箱验证测试用例。

    记录每个测试用例的名称、输入、预期输出、实际输出、是否通过。
    """

    name: str
    description: str
    input_data: dict[str, Any]
    expected_passed: bool
    actual_passed: bool
    error_message: str | None = None


@dataclass
class ValidationResult:
    """规则验证结果。

    Attributes:
        passed: 整体是否通过。True 表示规则可应用。
        errors: 阻断性错误列表（任一非空则 passed=False）。
        warnings: 非阻断性警告列表（不阻止应用，但需人工复核）。
        test_cases: 执行的测试用例列表。
        validated_at: 验证时间戳。
        validator_version: 验证器版本。
    """

    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    test_cases: list[ValidationTestCase] = field(default_factory=list)
    validated_at: str = ""
    validator_version: str = "0.1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "errors": self.errors,
            "warnings": self.warnings,
            "test_cases": [
                {
                    "name": tc.name,
                    "description": tc.description,
                    "input_data": tc.input_data,
                    "expected_passed": tc.expected_passed,
                    "actual_passed": tc.actual_passed,
                    "error_message": tc.error_message,
                }
                for tc in self.test_cases
            ],
            "validated_at": self.validated_at,
            "validator_version": self.validator_version,
        }
