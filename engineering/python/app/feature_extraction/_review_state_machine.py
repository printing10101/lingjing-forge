"""特征审核状态机（纯 Python 白盒逻辑）。

背景
====
来自「自主代码重构」路线图 Phase 1 P1-1：把工程师审核的状态流转语义
从 FeatureExtractionPipeline 的 inline 逻辑中解耦为独立、无框架依赖的
纯状态机。

覆盖的流转语义（与既有 pipeline 行为逐字节一致，防回归）
========================================================
任务状态机：
    PENDING → RUNNING → FEATURES_EXTRACTED → REVIEWED → SUCCEEDED
              │            │                   │
              ├ FAILED     ├ FAILED            ├ FAILED(审核不通过兜底)
              └ CANCELLED

可审核状态：     仅 FEATURES_EXTRACTED
审核动作：       per-feature confirmed / rejected / edited
全部审核完毕：   FEATURES_EXTRACTED → REVIEWED（自动）
可导出的状态：   FEATURES_EXTRACTED / REVIEWED（导出后 → SUCCEEDED）

本模块只提供「判定/决策」函数（是否允许、下一状态、是否全部审核完毕），
不触碰存储 / 文件 / 框架。所有输入为普通字符串 / 列表，便于单测。
"""

from __future__ import annotations

from collections.abc import Iterable

from app.feature_extraction._feature_classifier import (
    ACTION_CONFIRMED,
    ACTION_EDITED,
    ACTION_REJECTED,
    is_valid_review_action,
)

# 任务状态常量（与 feature_store.FeatureExtractionTaskStatus 对齐）
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_FEATURES_EXTRACTED = "features_extracted"
STATUS_REVIEWED = "reviewed"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"


class ReviewStateMachineError(ValueError):
    """审核状态机非法操作。"""


class FeatureReviewStateMachine:
    """特征审核状态机（无框架依赖，面向任务状态字符串）。"""

    # 允许审核的任务状态集合
    REVIEWABLE_STATES = frozenset({STATUS_FEATURES_EXTRACTED})
    # 审核动作集合
    VALID_ACTIONS = frozenset({ACTION_CONFIRMED, ACTION_REJECTED, ACTION_EDITED})
    # 未决审核状态（单条特征）
    _PENDING_FEATURE_STATUS = frozenset({"pending"})
    # 允许导出的任务状态
    EXPORTABLE_STATES = frozenset({STATUS_FEATURES_EXTRACTED, STATUS_REVIEWED})

    # 任务级判定

    @staticmethod
    def can_review(task_status: str) -> bool:
        """任务状态是否允许工程师审核。"""
        return task_status in FeatureReviewStateMachine.REVIEWABLE_STATES

    @staticmethod
    def reviewable_state_hint() -> str:
        """返回「可审核状态」的人类可读提示（用于错误消息）。"""
        return ", ".join(sorted(FeatureReviewStateMachine.REVIEWABLE_STATES))

    @staticmethod
    def assert_reviewable(task_status: str) -> None:
        """校验任务可审核，否则抛 ReviewStateMachineError。

        Raises:
            ReviewStateMachineError: 状态不允许审核。
        """
        if not FeatureReviewStateMachine.can_review(task_status):
            raise ReviewStateMachineError(
                f"任务状态 {task_status} 不允许审核，仅 {FeatureReviewStateMachine.reviewable_state_hint()} 状态可审核"
            )

    @staticmethod
    def assert_valid_action(action: str) -> None:
        """校验审核动作合法。

        Raises:
            ReviewStateMachineError: action 非法。
        """
        if not is_valid_review_action(action):
            raise ReviewStateMachineError(
                f"非法 action: {action}，应为 {sorted(FeatureReviewStateMachine.VALID_ACTIONS)}"
            )

    @staticmethod
    def action_requires_edited_params(action: str) -> bool:
        """审核动作是否必须在 edited_params 上强制校验（仅 edited 需要）。"""
        return action == ACTION_EDITED

    # 特征级判定

    @staticmethod
    def is_feature_pending(review_status: str) -> bool:
        """单条特征是否仍处于「未审核」状态。"""
        return review_status in FeatureReviewStateMachine._PENDING_FEATURE_STATUS

    @staticmethod
    def all_features_reviewed(review_statuses: Iterable[str]) -> bool:
        """所有特征是否都已审核完毕（无任何 pending 状态）。"""
        for status in review_statuses:
            if FeatureReviewStateMachine.is_feature_pending(status):
                return False
        return True

    @staticmethod
    def next_state_after_all_reviewed(has_features: bool) -> str:
        """全部审核完毕后任务应转移到的状态。

        - 无特征（has_features=False）→ 直接 REVIEWED
        - 有特征且全部审核 → REVIEWED
        """
        return STATUS_REVIEWED

    @staticmethod
    def can_export(task_status: str) -> bool:
        """任务状态是否允许导出已确认特征集。"""
        return task_status in FeatureReviewStateMachine.EXPORTABLE_STATES

    @staticmethod
    def exportable_state_hint() -> str:
        return ", ".join(sorted(FeatureReviewStateMachine.EXPORTABLE_STATES))

    @staticmethod
    def assert_exportable(task_status: str) -> None:
        """校验任务可导出，否则抛 ReviewStateMachineError。"""
        if not FeatureReviewStateMachine.can_export(task_status):
            raise ReviewStateMachineError(
                f"任务状态 {task_status} 不允许导出，仅 {FeatureReviewStateMachine.exportable_state_hint()} 状态可导出"
            )

    @staticmethod
    def next_state_after_export() -> str:
        """导出成功后任务转移到的状态。"""
        return STATUS_SUCCEEDED


__all__ = [
    "STATUS_PENDING",
    "STATUS_RUNNING",
    "STATUS_FEATURES_EXTRACTED",
    "STATUS_REVIEWED",
    "STATUS_SUCCEEDED",
    "STATUS_FAILED",
    "STATUS_CANCELLED",
    "ReviewStateMachineError",
    "FeatureReviewStateMachine",
]
