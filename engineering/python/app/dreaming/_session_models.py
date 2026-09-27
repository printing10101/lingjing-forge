"""ProjectSession 数据类（从 session_extractor 拆出）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 单次 Dream 最多 100 个 Sessions
MAX_SESSIONS_PER_DREAM = 100


@dataclass
class ProjectSession:
    """项目级 Session：一次实验/验证/审核的完整上下文。

    数据源为本项目本地数据（MLflow / CAM / 审计 / 切削参数库）。
    """

    session_id: str  # 唯一标识
    source: str  # "mlflow" | "cam_validation" | "audit_log" | "cutting_store"
    timestamp: str  # ISO 格式时间戳
    # 工艺上下文
    material_type: str | None = None
    tool_params: dict[str, Any] = field(default_factory=dict)
    # 预测结果
    chatter_confidence: float | None = None
    predicted_chatter: bool | None = None
    # 验证结果
    cam_validation_passed: bool | None = None
    cam_validation_failure_reason: str | None = None
    # 结果分类
    outcome: str = "unknown"  # "success" | "failure" | "warning" | "unknown"
    failure_reason: str | None = None
    # 学术诚信标记
    is_ar_02_pre_fix: bool = False  # AR-02 修复前数据，论文应排除
    # 原始记录路径（供审稿人复核）
    raw_artifact_path: str | None = None
    # 附加元数据
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "source": self.source,
            "timestamp": self.timestamp,
            "material_type": self.material_type,
            "tool_params": self.tool_params,
            "chatter_confidence": self.chatter_confidence,
            "predicted_chatter": self.predicted_chatter,
            "cam_validation_passed": self.cam_validation_passed,
            "cam_validation_failure_reason": self.cam_validation_failure_reason,
            "outcome": self.outcome,
            "failure_reason": self.failure_reason,
            "is_ar_02_pre_fix": self.is_ar_02_pre_fix,
            "raw_artifact_path": self.raw_artifact_path,
            "metadata": self.metadata,
        }
