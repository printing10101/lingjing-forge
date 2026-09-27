"""P4-2 白盒模块：方言插件生命周期状态机（纯 Python，零框架依赖）。

补全 DialectRegistry 的完整生命周期语义（当前仅 discover/compile/register，
缺卸载/重建/状态查询）：

    生命周期状态：
        DISCOVERED → COMPILED → REGISTERED
                        ↘ FAILED
        REGISTERED → UNREGISTERED（卸载后回到 COMPILED 态，可重新注册）

状态转移判定（纯字符串输入，与既有 Registry 行为对齐）：
    - discover 后：声明已加载（DISCOVERED）
    - compile_all 成功后：COMPILED
    - register_to 成功后：REGISTERED
    - 卸载后：UNREGISTERED（可重新 register 或重新 discover）
    - 任一阶段失败：FAILED（需重新 discover 恢复）

设计要点：
- 不 import app 任何模块（白盒：仅判定逻辑，注册执行由调用方完成）
- 转移规则可被既有 DialectRegistry 委托（防漂移测试锁定）
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DialectLifecycleStage(str, Enum):
    """方言插件生命周期阶段。"""

    DISCOVERED = "discovered"  # 声明已发现/加载
    COMPILED = "compiled"  # 已编译为方言类
    REGISTERED = "registered"  # 已注册到 PostProcessorRegistry
    UNREGISTERED = "unregistered"  # 已卸载（可重新注册）
    FAILED = "failed"  # 某阶段失败


@dataclass(frozen=True)
class LifecycleRule:
    """一条生命周期转移规则。"""

    current: DialectLifecycleStage
    allowed: frozenset[DialectLifecycleStage]
    error_template: str


# 转移规则表
LIFECYCLE_TRANSITIONS: tuple[LifecycleRule, ...] = (
    LifecycleRule(
        DialectLifecycleStage.DISCOVERED,
        frozenset({DialectLifecycleStage.COMPILED, DialectLifecycleStage.FAILED}),
        "当前阶段 {current} 不能转移到 {target}。建议操作：先 compile_all()。",
    ),
    LifecycleRule(
        DialectLifecycleStage.COMPILED,
        frozenset({DialectLifecycleStage.REGISTERED, DialectLifecycleStage.FAILED}),
        "当前阶段 {current} 不能转移到 {target}。建议操作：先 register_to()。",
    ),
    LifecycleRule(
        DialectLifecycleStage.REGISTERED,
        frozenset({DialectLifecycleStage.UNREGISTERED, DialectLifecycleStage.FAILED}),
        "当前阶段 {current} 不能转移到 {target}。建议操作：先卸载。",
    ),
    LifecycleRule(
        DialectLifecycleStage.UNREGISTERED,
        frozenset({DialectLifecycleStage.REGISTERED, DialectLifecycleStage.FAILED}),
        "当前阶段 {current} 不能转移到 {target}。建议操作：重新注册。",
    ),
    LifecycleRule(
        DialectLifecycleStage.FAILED,
        frozenset({DialectLifecycleStage.DISCOVERED}),
        "当前阶段 {current} 不能转移到 {target}。建议操作：重新 discover()。",
    ),
)

# 终态（不可再流转，除 faileddiscovered）
TERMINAL_STAGES = frozenset({DialectLifecycleStage.REGISTERED})

# 可恢复态（可从这些状态重新 discover）
RECOVERABLE_STAGES = frozenset(
    {DialectLifecycleStage.FAILED, DialectLifecycleStage.UNREGISTERED, DialectLifecycleStage.DISCOVERED}
)


def can_transition(
    current: DialectLifecycleStage | str,
    target: DialectLifecycleStage | str,
) -> bool:
    """按规则表判断 current → target 是否合法。"""
    cur = DialectLifecycleStage(current)
    tgt = DialectLifecycleStage(target)
    for rule in LIFECYCLE_TRANSITIONS:
        if rule.current == cur:
            return tgt in rule.allowed
    return False


def assert_transition_allowed(
    current: DialectLifecycleStage | str,
    target: DialectLifecycleStage | str,
) -> None:
    """断言转移合法，非法抛 ValueError（带可操作建议）。"""
    if not can_transition(current, target):
        cur = DialectLifecycleStage(current)
        tgt = DialectLifecycleStage(target)
        raise ValueError(f"非法生命周期转移: {cur.value} → {tgt.value}。建议操作：检查方言插件生命周期状态机。")


def next_stage_after_success(
    current: DialectLifecycleStage | str,
    operation: str,
) -> DialectLifecycleStage:
    """操作成功后的下一阶段。

    Args:
        current: 当前阶段。
        operation: discover / compile / register / unregister。

    Returns:
        下一阶段（非法操作抛 ValueError）。
    """
    cur = DialectLifecycleStage(current)
    op_map: dict[str, DialectLifecycleStage] = {
        "discover": DialectLifecycleStage.DISCOVERED,
        "compile": DialectLifecycleStage.COMPILED,
        "register": DialectLifecycleStage.REGISTERED,
        "unregister": DialectLifecycleStage.UNREGISTERED,
    }
    if operation not in op_map:
        raise ValueError(f"未知操作: {operation}（合法: {sorted(op_map)}）")
    target = op_map[operation]
    assert_transition_allowed(cur, target)
    return target


def next_stage_after_failure(current: DialectLifecycleStage | str) -> DialectLifecycleStage:
    """操作失败后的阶段（一律 FAILED）。"""
    return DialectLifecycleStage.FAILED


def can_discover(current: DialectLifecycleStage | str) -> bool:
    """是否允许重新发现（FAILED/UNREGISTERED 可恢复）。"""
    return DialectLifecycleStage(current) in RECOVERABLE_STAGES or can_transition(
        current, DialectLifecycleStage.DISCOVERED
    )


def is_terminal(current: DialectLifecycleStage | str) -> bool:
    """是否终态。"""
    return DialectLifecycleStage(current) in TERMINAL_STAGES


__all__ = [
    "DialectLifecycleStage",
    "LifecycleRule",
    "LIFECYCLE_TRANSITIONS",
    "TERMINAL_STAGES",
    "RECOVERABLE_STAGES",
    "can_transition",
    "assert_transition_allowed",
    "next_stage_after_success",
    "next_stage_after_failure",
    "can_discover",
    "is_terminal",
]
