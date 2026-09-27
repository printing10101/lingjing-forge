"""P1-2 白盒模块：参数化几何审核状态机（纯 Python，零框架依赖）。

抽取自 `app/parametric_geometry/pipeline.py` 与 `step_store.py` 中的
「状态流转判定」逻辑（P1-1 白盒化方法论复用）：

- `review_step_feature` 的状态允许性判定 → 委托 `can_review`
- `finalize_step` 的状态允许性判定 → 委托 `can_finalize`
- `run_pipeline` 的状态允许性判定 → 委托 `can_execute`
- 全部特征审核完毕判定 → 委托 `all_features_reviewed`
- 终态判定 → 委托 `is_terminal`

纯字符串输入/输出（状态名与 `ParametricGeometryTaskStatus` /
`StepReviewStatus` 枚举逐值对齐，测试锁定防漂移），不 import 任何框架。

状态转移图（与 pipeline.py docstring 一致）：
    PENDING → RUNNING → STEP_GENERATED → REVIEWED → SUCCEEDED
                          ↘ FAILED
                          ↘ CANCELLED
"""

from __future__ import annotations

from dataclasses import dataclass

# 状态常量（与 step_store.py 枚举逐值对齐）

# 任务状态
ST_PENDING = "pending"
ST_RUNNING = "running"
ST_STEP_GENERATED = "step_generated"
ST_REVIEWED = "reviewed"
ST_SUCCEEDED = "succeeded"
ST_FAILED = "failed"
ST_CANCELLED = "cancelled"

# 特征审核状态
RV_PENDING = "pending"
RV_CONFIRMED = "confirmed"
RV_REJECTED = "rejected"
RV_EDITED = "edited"

# 全部合法任务状态
ALL_TASK_STATUSES = frozenset(
    {
        ST_PENDING,
        ST_RUNNING,
        ST_STEP_GENERATED,
        ST_REVIEWED,
        ST_SUCCEEDED,
        ST_FAILED,
        ST_CANCELLED,
    }
)

# 全部合法审核状态
ALL_REVIEW_STATUSES = frozenset({RV_PENDING, RV_CONFIRMED, RV_REJECTED, RV_EDITED})

# 终态（不可再流转）
TERMINAL_STATUSES = frozenset({ST_SUCCEEDED, ST_FAILED, ST_CANCELLED})

# 可执行流水线的状态（run_pipeline 允许）
EXECUTABLE_STATUSES = frozenset({ST_PENDING, ST_FAILED})

# 可审核的状态（review_step_feature 允许）
REVIEWABLE_STATUSES = frozenset({ST_STEP_GENERATED})

# 可最终化的状态（finalize_step 允许）
FINALIZABLE_STATUSES = frozenset({ST_REVIEWED})


@dataclass(frozen=True)
class StatusRule:
    """一条状态流转判定规则。"""

    current: str
    allowed: frozenset[str]
    error_template: str


# 审核状态机核心规则（current allowed）
TASK_TRANSITIONS: tuple[StatusRule, ...] = (
    StatusRule(
        current=ST_PENDING,
        allowed=frozenset({ST_RUNNING}),
        error_template="任务状态不允许执行: {current}（仅 pending/failed 可执行）",
    ),
    StatusRule(
        current=ST_RUNNING,
        allowed=frozenset({ST_STEP_GENERATED, ST_FAILED}),
        error_template="任务状态不允许流转: {current}",
    ),
    StatusRule(
        current=ST_STEP_GENERATED,
        allowed=frozenset({ST_REVIEWED, ST_FAILED}),
        error_template="任务状态不允许流转: {current}",
    ),
    StatusRule(
        current=ST_REVIEWED,
        allowed=frozenset({ST_SUCCEEDED, ST_FAILED}),
        error_template="任务状态不允许流转: {current}",
    ),
)


# 纯函数判定


def can_execute(status: str) -> bool:
    """run_pipeline 是否允许当前状态执行（pending/failed 可执行）。"""
    return status in EXECUTABLE_STATUSES


def can_review(status: str) -> bool:
    """review_step_feature 是否允许当前状态审核（仅 step_generated）。"""
    return status in REVIEWABLE_STATUSES


def can_finalize(status: str) -> bool:
    """finalize_step 是否允许当前状态最终化（仅 reviewed）。"""
    return status in FINALIZABLE_STATUSES


def can_transition(current: str, target: str) -> bool:
    """按状态机规则判断 current → target 是否合法。"""
    for rule in TASK_TRANSITIONS:
        if rule.current == current:
            return target in rule.allowed
    return False


def is_terminal(status: str) -> bool:
    """是否终态（succeeded/failed/cancelled）。"""
    return status in TERMINAL_STATUSES


def all_features_reviewed(review_statuses: list[str]) -> bool:
    """是否全部特征均已审核（无 pending）。

    Args:
        review_statuses: 各特征的审核状态列表。

    Returns:
        True 当列表非空且无 pending。
    """
    return bool(review_statuses) and all(s != RV_PENDING for s in review_statuses)


def is_valid_review_status(status: str) -> bool:
    """review_status 是否合法。"""
    return status in ALL_REVIEW_STATUSES


def is_valid_task_status(status: str) -> bool:
    """任务状态字符串是否合法。"""
    return status in ALL_TASK_STATUSES


def next_status_after_review(all_reviewed: bool, current: str) -> str:
    """审核单个特征后的新状态。

    全部审核完毕 → reviewed；否则保持当前状态（step_generated）。
    """
    if all_reviewed:
        return ST_REVIEWED
    return current


def assert_transition_allowed(current: str, target: str) -> None:
    """断言状态流转合法，非法则抛 ValueError（错误消息带当前状态）。"""
    if not can_transition(current, target):
        raise ValueError(f"非法状态流转: {current} → {target}。建议操作：检查任务状态机转移图。")


def assert_review_status_valid(status: str) -> None:
    """断言审核状态合法，非法则抛 ValueError。"""
    if not is_valid_review_status(status):
        raise ValueError(
            f"非法 review_status: {status}（合法值: {sorted(ALL_REVIEW_STATUSES)}）。"
            "建议操作：检查 StepReviewStatus 枚举。"
        )


__all__ = [
    "ST_PENDING",
    "ST_RUNNING",
    "ST_STEP_GENERATED",
    "ST_REVIEWED",
    "ST_SUCCEEDED",
    "ST_FAILED",
    "ST_CANCELLED",
    "RV_PENDING",
    "RV_CONFIRMED",
    "RV_REJECTED",
    "RV_EDITED",
    "ALL_TASK_STATUSES",
    "ALL_REVIEW_STATUSES",
    "TERMINAL_STATUSES",
    "EXECUTABLE_STATUSES",
    "REVIEWABLE_STATUSES",
    "FINALIZABLE_STATUSES",
    "TASK_TRANSITIONS",
    "can_execute",
    "can_review",
    "can_finalize",
    "can_transition",
    "is_terminal",
    "all_features_reviewed",
    "is_valid_review_status",
    "is_valid_task_status",
    "next_status_after_review",
    "assert_transition_allowed",
    "assert_review_status_valid",
]
