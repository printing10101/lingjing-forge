"""Feature Flag 机制：渐进式启用研究模块。

使用场景：
- alpha：只对白名单用户开放
- beta：对一定比例的用户开放
- ga：全量开放
- disabled：未启用
- shadow：影子模式（不返回结果，只记录 diff）
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import dataclass, field
from enum import Enum


logger = logging.getLogger(__name__)


class FeatureStatus(str, Enum):
    DISABLED = "disabled"
    SHADOW = "shadow"  # 影子模式：研究模块运行但不影响用户
    ALPHA = "alpha"  # 只对白名单用户开放
    BETA = "beta"  # 对一定比例的用户开放
    GA = "ga"  # 全量开放


class ResearchFeature(str, Enum):
    """研究模块功能开关枚举。"""

    IJEPA_3D_RECOGNIZER = "ijepa_3d_recognizer"
    VJEPA_MACHINING_MONITOR = "vjepa_machining_monitor"
    JEPA_WORLD_MODEL_PLANNER = "jepa_world_model_planner"
    BAYESIAN_LNN_PARAM_ESTIMATOR = "bayesian_lnn_param_estimator"
    CROSS_LAYER_FUSION = "cross_layer_fusion"
    AGENT_ORCHESTRATION = "agent_orchestration"


@dataclass
class RolloutConfig:
    """每个研究模块的灰度配置。"""

    status: FeatureStatus = FeatureStatus.DISABLED
    user_whitelist: list = field(default_factory=list)
    rollout_percent: float = 0.0  # 0.0 - 1.0
    ab_test_baseline: str | None = None  # 对照组名
    description: str = ""


# 灰度配置中心（产品轨唯一可修改的位置）
ROLLOUT_CONFIG: dict[ResearchFeature, RolloutConfig] = {
    ResearchFeature.IJEPA_3D_RECOGNIZER: RolloutConfig(
        status=FeatureStatus.DISABLED,
        user_whitelist=[],
        rollout_percent=0.0,
        ab_test_baseline="rule_based_recognizer",
        description="三视图 CV 识别（IJepa-3D 倒角/键槽识别）",
    ),
    ResearchFeature.VJEPA_MACHINING_MONITOR: RolloutConfig(
        status=FeatureStatus.DISABLED,
        user_whitelist=[],
        rollout_percent=0.0,
        ab_test_baseline="rule_based_monitor",
        description="V-JEPA 加工过程异常监测",
    ),
    ResearchFeature.JEPA_WORLD_MODEL_PLANNER: RolloutConfig(
        status=FeatureStatus.DISABLED,
        user_whitelist=[],
        rollout_percent=0.0,
        ab_test_baseline="rule_based_planner",
        description="JEPA-World-Model 工艺规划预测",
    ),
    ResearchFeature.BAYESIAN_LNN_PARAM_ESTIMATOR: RolloutConfig(
        status=FeatureStatus.DISABLED,
        user_whitelist=[],
        rollout_percent=0.0,
        ab_test_baseline="deterministic_lnn",
        description="Bayesian-LNN 不确定性量化参数估计",
    ),
    ResearchFeature.CROSS_LAYER_FUSION: RolloutConfig(
        status=FeatureStatus.DISABLED,
        user_whitelist=[],
        rollout_percent=0.0,
        ab_test_baseline="single_layer",
        description="跨层级融合 LNN",
    ),
    ResearchFeature.AGENT_ORCHESTRATION: RolloutConfig(
        status=FeatureStatus.DISABLED,
        user_whitelist=[],
        rollout_percent=0.0,
        ab_test_baseline="rule_based_agent",
        description="通用智能体编排",
    ),
}

# 线程安全的配置覆盖（允许运行时热更新）
_overrides: dict[ResearchFeature, RolloutConfig] = {}
_lock = threading.RLock()

# 影子模式全局开关（用于阶段 3/4）
SHADOW_MODE_MASTER = os.getenv("RESEARCH_BRIDGE_SHADOW_MASTER", "false").lower() == "true"


def register_feature(feature: ResearchFeature, config: RolloutConfig) -> None:
    """注册或更新一个研究模块的灰度配置。

    这是产品轨操作研究模块灰度的唯一接口。
    """
    with _lock:
        _overrides[feature] = config
        logger.info(
            "research_feature_registered feature=%s status=%s rollout=%.2f%%",
            feature.value,
            config.status.value,
            config.rollout_percent * 100,
        )


def get_rollout_config(feature: ResearchFeature) -> RolloutConfig:
    """获取研究模块的当前灰度配置。"""
    with _lock:
        if feature in _overrides:
            return _overrides[feature]
        return ROLLOUT_CONFIG.get(feature, RolloutConfig())


def _hash_user_to_bucket(user_id: str) -> float:
    """把 user_id 哈希成 0.0-1.0 的桶位置。"""
    if not user_id:
        return 0.0
    # 安全修复：使用 SHA256 替代 MD5，避免用户构造 user_id 预测分桶
    h = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def is_feature_enabled(
    feature: ResearchFeature,
    user_id: str = "anonymous",
) -> bool:
    """判断某用户是否启用某个研究模块。

    决策流程：
    1. 如果状态是 DISABLED，返回 False
    2. 如果状态是 SHADOW（影子模式），返回 True（但调用方需要走影子路径）
    3. 如果状态是 GA，返回 True
    4. 如果状态是 ALPHA，user 在白名单才返回 True
    5. 如果状态是 BETA，user 哈希桶落在 rollout_percent 范围内才返回 True
    """
    cfg = get_rollout_config(feature)

    if cfg.status == FeatureStatus.DISABLED:
        return False

    if cfg.status == FeatureStatus.SHADOW:
        # 影子模式：全局开启（是否收集由调用方决定）
        return SHADOW_MODE_MASTER

    if cfg.status == FeatureStatus.GA:
        return True

    if cfg.status == FeatureStatus.ALPHA:
        return user_id in cfg.user_whitelist

    if cfg.status == FeatureStatus.BETA:
        bucket = _hash_user_to_bucket(user_id)
        return bucket < cfg.rollout_percent

    return False


def is_shadow_mode(feature: ResearchFeature) -> bool:
    """判断某个研究模块当前是否在影子模式（运行但不返回结果）。"""
    cfg = get_rollout_config(feature)
    return cfg.status == FeatureStatus.SHADOW and SHADOW_MODE_MASTER


__all__ = [
    "FeatureStatus",
    "ResearchFeature",
    "RolloutConfig",
    "ROLLOUT_CONFIG",
    "register_feature",
    "get_rollout_config",
    "is_feature_enabled",
    "is_shadow_mode",
]
