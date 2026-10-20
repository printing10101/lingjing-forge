"""颤振拦截自动降参重试（阶段 6 闭环恢复）。

背景（基于 failure_cases.db 641 条案例实测）：
    阶段 6 生成链路的失败中 97% 以上为 unstable_features 型——颤振预检
    （解析模型，全频段最坏下界，系统性保守）判定某特征
    ``axial_depth_mm > limit_depth_mm``，fail-closed 终止生成。
    实测全部 216 条失败案例的超限比（ap/limit）落在 [1.02, 1.80]，
    均可由 Z 分层（每层切深压回极限以内）恢复，无需改工艺路线。

本模块职责：
    从 unstable 特征集合反解一个全局 ``max_stepdown``，使生成器把每个
    工序的每层切深钳制到 ``RECOVERY_MARGIN × min(limit_depth)`` 以下。
    生成器（``gcode_generator.py`` 的 ``max_stepdown`` 参数）已内置
    分层机制，本模块只做数值决策，不做几何计算。

安全边界：
    - 恢复切深 = 0.95 × limit（留 5% 余量，且仍在原 fail-closed 闸门之下）
    - 只处理 unstable 型失败；任何其他错误码（语法/碰撞/行程）不重试
    - 重试只降低每层切深，不缩减特征总加工深度（生成器契约保证）
    - 恢复结果必须带 ``recovered=True`` 标记随任务上报，不静默混入正常产物
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from app.chatter_prediction._types import FeatureChatterResult

logger = logging.getLogger(__name__)

# 恢复切深相对极限切深的安全余量（每层切深 = 0.95 × limit）
RECOVERY_MARGIN = 0.95

# 单特征最大允许层数：超出说明切深远超极限（>6 倍），不属于贴线超限，
# 强行分层会产生大量空行程层，应交回工程师处理
MAX_RECOVERY_LAYERS = 6


@dataclass(frozen=True)
class StepdownRecovery:
    """一次降参恢复决策的结果。"""

    recoverable: bool
    """是否可自动恢复。False 时调用方必须走原有 FAILED 流程。"""

    max_stepdown_mm: float | None = None
    """建议的全局最大分层切深（mm）。仅 recoverable=True 时非 None。"""

    reasons: tuple[str, ...] = ()
    """不可恢复时的原因列表（人读，随任务 errors 上报）。"""

    affected_features: tuple[str, ...] = ()
    """触发恢复决策的 unstable 特征 id 列表。"""


def compute_stepdown_recovery(
    chatter_results: list[FeatureChatterResult],
    margin: float = RECOVERY_MARGIN,
    max_layers: int = MAX_RECOVERY_LAYERS,
) -> StepdownRecovery:
    """从 unstable 特征反解全局 max_stepdown。

    规则：
        1. 无 unstable 特征 → 不可恢复（调用方本就不该在失败分支调它）
        2. 任一特征 limit_depth_mm <= 0 或非有限值 → 不可恢复（数据异常，
           不能拿异常数据反解切深）
        3. 任一特征所需层数 > max_layers（即 ap/limit > max_layers/margin）
           → 不可恢复（远超贴线超限范畴）
        4. 否则 recoverable，max_stepdown = margin × min(limit_depth)
           （取所有 unstable 特征的最严限值，一次重试全部覆盖）

    Args:
        chatter_results: 阶段 5 ChatterReport 的特征结果列表
        margin: 安全余量系数（0,1]，默认 0.95
        max_layers: 单特征最大允许层数，默认 6

    Returns:
        StepdownRecovery 决策对象
    """
    unstable = [f for f in chatter_results if not f.stable]
    if not unstable:
        return StepdownRecovery(
            recoverable=False,
            reasons=("无 unstable 特征，无需降参恢复",),
        )

    reasons: list[str] = []
    for f in unstable:
        limit = f.limit_depth_mm
        if not math.isfinite(limit) or limit <= 0:
            reasons.append(
                f"特征 {f.feature_id} 极限切深异常（limit_depth_mm={limit}），"
                "不可自动降参，需工程师审核"
            )
            continue
        layers = math.ceil(f.axial_depth_mm / (margin * limit)) if f.axial_depth_mm > 0 else 0
        if layers > max_layers:
            reasons.append(
                f"特征 {f.feature_id} 超限比过大"
                f"（axial_depth={f.axial_depth_mm:.2f}mm, limit={limit:.2f}mm, "
                f"需 {layers} 层 > 上限 {max_layers}），需人工调整工艺"
            )

    if reasons:
        return StepdownRecovery(
            recoverable=False,
            reasons=tuple(reasons),
            affected_features=tuple(f.feature_id for f in unstable),
        )

    worst_limit = min(f.limit_depth_mm for f in unstable)
    stepdown = margin * worst_limit
    logger.info(
        "降参恢复可行：unstable=%d, 最严限值=%.3fmm, 建议 max_stepdown=%.3fmm",
        len(unstable),
        worst_limit,
        stepdown,
    )
    return StepdownRecovery(
        recoverable=True,
        max_stepdown_mm=stepdown,
        affected_features=tuple(f.feature_id for f in unstable),
    )
