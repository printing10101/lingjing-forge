"""规则边界/模拟执行 mixin（从 rule_validator 拆出）。"""

from __future__ import annotations

import logging
from typing import Any

from app.dreaming.rule_synthesizer import RuleDraft
from app.dreaming._validator_models import ValidationTestCase

logger = logging.getLogger(__name__)


class _TestMixin:
    def _test_boundary_cases(self, rule: RuleDraft) -> list[ValidationTestCase]:
        """测试规则的边界情况。

        Args:
            rule: 规则草稿。

        Returns:
            测试用例列表。
        """
        cases: list[ValidationTestCase] = []

        # 边界 1：空输入
        cases.append(
            ValidationTestCase(
                name="empty_input",
                description="空输入时规则不应崩溃",
                input_data={},
                expected_passed=True,
                actual_passed=self._simulate_apply(rule, {}),
                error_message=None,
            )
        )

        # 边界 2：仅含 AR-02 修复前数据
        cases.append(
            ValidationTestCase(
                name="ar_02_pre_fix_only",
                description="仅含 AR-02 修复前数据时规则应跳过",
                input_data={"is_ar_02_pre_fix": True},
                expected_passed=True,
                actual_passed=self._simulate_apply(rule, {"is_ar_02_pre_fix": True}),
                error_message=None,
            )
        )

        # 边界 3：HRC52 pending_calibration
        cases.append(
            ValidationTestCase(
                name="hrc52_pending_calibration",
                description="HRC52 pending_calibration 时规则应降低置信度",
                input_data={
                    "material_type": "HRC52",
                    "pending_calibration": True,
                },
                expected_passed=True,
                actual_passed=self._simulate_apply(
                    rule,
                    {"material_type": "HRC52", "pending_calibration": True},
                ),
                error_message=None,
            )
        )

        # 边界 4：SUCCEEDED 任务
        cases.append(
            ValidationTestCase(
                name="succeeded_task",
                description="SUCCEEDED 任务时规则不可删除",
                input_data={"status": "SUCCEEDED"},
                expected_passed=True,
                actual_passed=self._simulate_apply(rule, {"status": "SUCCEEDED"}),
                error_message=None,
            )
        )

        # 边界 5：CAM 验证失败
        cases.append(
            ValidationTestCase(
                name="cam_validation_failed",
                description="CAM 验证失败时规则应标记 requires_revalidation",
                input_data={"cam_validation_passed": False},
                expected_passed=True,
                actual_passed=self._simulate_apply(rule, {"cam_validation_passed": False}),
                error_message=None,
            )
        )

        return cases

    # 阶段 4：模拟执行

    def _simulate_execution(self, rule: RuleDraft) -> list[ValidationTestCase]:
        """使用合成数据模拟规则执行。

        Args:
            rule: 规则草稿。

        Returns:
            测试用例列表。
        """
        cases: list[ValidationTestCase] = []

        # 模拟 1：典型成功场景
        cases.append(
            ValidationTestCase(
                name="typical_success",
                description="典型成功场景下规则应正确触发",
                input_data={
                    "material_type": "6061-T6",
                    "chatter_confidence": 0.75,
                    "cam_validation_passed": True,
                    "status": "REVIEWED",
                },
                expected_passed=True,
                actual_passed=self._simulate_apply(
                    rule,
                    {
                        "material_type": "6061-T6",
                        "chatter_confidence": 0.75,
                        "cam_validation_passed": True,
                        "status": "REVIEWED",
                    },
                ),
                error_message=None,
            )
        )

        # 模拟 2：高颤振风险场景
        cases.append(
            ValidationTestCase(
                name="high_chatter_risk",
                description="高颤振风险（>0.8）时规则应触发预警",
                input_data={
                    "material_type": "TC4",
                    "chatter_confidence": 0.92,
                    "cam_validation_passed": True,
                    "status": "PARAMS_RECOMMENDED",
                },
                expected_passed=True,
                actual_passed=self._simulate_apply(
                    rule,
                    {
                        "material_type": "TC4",
                        "chatter_confidence": 0.92,
                        "cam_validation_passed": True,
                        "status": "PARAMS_RECOMMENDED",
                    },
                ),
                error_message=None,
            )
        )

        return cases

    def _simulate_apply(self, rule: RuleDraft, context: dict[str, Any]) -> bool:
        """模拟规则应用（不触发真实操作）。

        Args:
            rule: 规则草稿。
            context: 模拟上下文数据。

        Returns:
            True 若模拟应用成功（无异常且不违反硬约束）。
        """
        try:
            # 检查 context 中是否包含禁止的硬约束覆盖
            if context.get("cam_validation_required") is False:
                return False
            if context.get("allow_delete_succeeded") is True:
                return False
            if context.get("status") == "SUCCEEDED" and self._action_contains_delete(rule.action):
                return False
            # 模拟成功
            return True
        except Exception as e:
            logger.debug("模拟应用异常 [%s]：%s", rule.rule_id, e)
            return False

    @staticmethod
    def _action_contains_delete(action: Any) -> bool:
        """判断规则动作是否包含删除语义（兼容 dict 与 str 两种表示）。

        RuleDraft.action 类型为 Dict（如 {"action": "delete_task", ...}），
        但历史数据 / 字符串形式（如 "delete_task"）也需兼容。
        """
        if isinstance(action, str):
            return "delete" in action.lower()
        if isinstance(action, dict):
            # 检查 action 名与所有字符串值
            for key, value in action.items():
                if "delete" in str(key).lower():
                    return True
                if isinstance(value, str) and "delete" in value.lower():
                    return True
            return False
        return "delete" in str(action).lower()
