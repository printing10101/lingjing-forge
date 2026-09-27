"""路由策略/请求/结果数据类（从 router 拆出）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.ai.llm.provider_base import LLMProvider, ProviderCapability, ProviderConfig


class RoutingStrategy:
    """路由策略标识。"""

    ACTIVE_ONLY = "active_only"  # 仅使用激活的 Provider
    PRIORITY_FALLBACK = "priority_fallback"  # 按优先级降级
    CAPABILITY_MATCH = "capability_match"  # 按能力匹配
    LATENCY_FIRST = "latency_first"  # 延迟优先
    LOCAL_FIRST = "local_first"  # 本地优先（成本最低）
    CLOUD_FIRST = "cloud_first"  # 云端优先（质量最高）


@dataclass
class RoutingRequest:
    """路由请求描述。"""

    # 必需能力列表（Provider 必须具备所有列出的能力）
    required_capabilities: list[ProviderCapability] | None = None
    # 显式指定的 Provider ID（优先级最高）
    provider_id: str | None = None
    # 路由策略
    strategy: str = RoutingStrategy.PRIORITY_FALLBACK
    # 是否跳过激活的 Provider（用于健康检查/调试）
    skip_active: bool = False
    # 最大候选数（默认 5）
    max_candidates: int = 5


@dataclass
class RoutingResult:
    """路由结果。"""

    provider: LLMProvider | None
    config: ProviderConfig | None
    candidates: list[ProviderConfig]  # 候选列表（按优先级）
    selected_id: str | None
    reason: str  # 选择原因（用于日志/调试）

    @property
    def success(self) -> bool:
        return self.provider is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "selected_id": self.selected_id,
            "selected_name": self.config.name if self.config else None,
            "selected_type": (self.config.provider_type.value if self.config else None),
            "reason": self.reason,
            "candidates": [
                {
                    "provider_id": c.provider_id,
                    "name": c.name,
                    "provider_type": c.provider_type.value,
                    "priority": c.priority,
                    "is_active": c.is_active,
                }
                for c in self.candidates
            ],
        }


# 延迟缓存（轻量级，进程内）
