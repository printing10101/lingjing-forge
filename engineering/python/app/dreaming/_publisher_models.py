"""灰度发布数据类（从 progressive_publisher 拆出）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.dreaming.rule_validator import ValidationResult

logger = logging.getLogger(__name__)

# 灰度发布状态


class PublicationStage(str, Enum):
    """规则灰度发布阶段。

    阶段顺序：SHADOW → CANARY → ROLLING_10 → ROLLING_50 → FULL
    降级方向：FULL → ROLLING_50 → ROLLING_10 → CANARY → SHADOW → DEPRECATED
    """

    SHADOW = "shadow"  # 影子模式：0% 流量，仅记录
    CANARY = "canary"  # 金丝雀：1% 流量
    ROLLING_10 = "rolling_10"  # 滚动 10%
    ROLLING_50 = "rolling_50"  # 滚动 50%
    FULL = "full"  # 全量 100%
    DEPRECATED = "deprecated"  # 已废弃（等价于 rollback）

    @property
    def traffic_percentage(self) -> float:
        """该阶段对应的流量百分比。"""
        return _STAGE_TRAFFIC_PERCENTAGE[self]

    @property
    def next_stage(self) -> "PublicationStage" | None:
        """下一阶段（晋级方向）。FULL 已是最高，返回 None。"""
        idx = _STAGE_ORDER.index(self)
        if idx + 1 >= len(_STAGE_ORDER):
            return None
        return _STAGE_ORDER[idx + 1]

    @property
    def previous_stage(self) -> "PublicationStage" | None:
        """上一阶段（降级方向）。SHADOW 已是最低，返回 None。"""
        idx = _STAGE_ORDER.index(self)
        if idx == 0:
            return None
        return _STAGE_ORDER[idx - 1]


@dataclass
class PublicationRecord:
    """单条规则的灰度发布记录。

    记录规则在哪个灰度阶段、何时进入、效果指标快照。

    Attributes:
        rule_id: 规则 ID。
        current_stage: 当前灰度阶段。
        entered_at: 进入当前阶段的时间戳。
        promoted_count: 累计晋级次数。
        demoted_count: 累计降级次数。
        last_metrics: 最近一次效果指标快照。
        stage_history: 阶段变更历史。
        promoted_to_full: 是否已全量发布。
        auto_rollback_triggered: 是否触发了自动回滚。
    """

    rule_id: str
    current_stage: PublicationStage = PublicationStage.SHADOW
    entered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    promoted_count: int = 0
    demoted_count: int = 0
    last_metrics: dict[str, Any] = field(default_factory=dict)
    stage_history: list[dict[str, Any]] = field(default_factory=list)
    promoted_to_full: bool = False
    auto_rollback_triggered: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["current_stage"] = self.current_stage.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PublicationRecord":
        stage_str = data.get("current_stage", "shadow")
        try:
            stage = PublicationStage(stage_str)
        except ValueError:
            logger.warning("未知灰度阶段 '%s'，回退到 SHADOW", stage_str)
            stage = PublicationStage.SHADOW
        return cls(
            rule_id=data["rule_id"],
            current_stage=stage,
            entered_at=data.get("entered_at", datetime.now(timezone.utc).isoformat()),
            promoted_count=int(data.get("promoted_count", 0)),
            demoted_count=int(data.get("demoted_count", 0)),
            last_metrics=data.get("last_metrics", {}),
            stage_history=data.get("stage_history", []),
            promoted_to_full=bool(data.get("promoted_to_full", False)),
            auto_rollback_triggered=bool(data.get("auto_rollback_triggered", False)),
        )


@dataclass
class PublicationResult:
    """灰度发布操作结果。

    Attributes:
        success: 操作是否成功。
        rule_id: 规则 ID。
        stage: 进入的灰度阶段。
        traffic_percentage: 该阶段的流量百分比。
        operated_at: 操作时间戳。
        validation_result: 晋级前的沙箱校验结果。
        audit_entry_seq: 审计日志条目序号（若写入成功）。
        error: 失败时的错误信息。
    """

    success: bool
    rule_id: str
    stage: PublicationStage
    traffic_percentage: float = 0.0
    operated_at: str = ""
    validation_result: ValidationResult | None = None
    audit_entry_seq: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["stage"] = self.stage.value
        if self.validation_result is not None:
            d["validation_result"] = self.validation_result.to_dict()
        return d


# 灰度阶段常量（自 progressive_publisher 迁移，置于类定义之后）
_STAGE_ORDER: list[PublicationStage] = [
    PublicationStage.SHADOW,
    PublicationStage.CANARY,
    PublicationStage.ROLLING_10,
    PublicationStage.ROLLING_50,
    PublicationStage.FULL,
]

_STAGE_TRAFFIC_PERCENTAGE: dict[PublicationStage, float] = {
    PublicationStage.SHADOW: 0.0,
    PublicationStage.CANARY: 0.01,
    PublicationStage.ROLLING_10: 0.10,
    PublicationStage.ROLLING_50: 0.50,
    PublicationStage.FULL: 1.00,
    PublicationStage.DEPRECATED: 0.0,
}
