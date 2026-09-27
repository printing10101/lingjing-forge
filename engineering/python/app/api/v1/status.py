"""系统状态端点。

包含：
    - GET /api/v1/status
        系统健康、模块版本、研究/产品开关、桥接层日志大小
    - GET /api/v1/status/postprocessors
        列出已注册的后处理器（覆盖 GSK / HNC / KND）
    - GET /api/v1/status/knowledge-graph
        知识图谱规模
    - GET /api/v1/status/research-bridge
        桥接层日志与 feature flag
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter

from app.core.safe_errors import safe_error_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/status", tags=["status"])


@router.get("")
def get_status() -> dict[str, Any]:
    """总体系统状态。"""
    out: dict[str, Any] = {
        "service": "lingjing-factory",
        "components": {},
    }
    # 1. 桥接层
    try:
        from app.research_bridge import UsageDataCollector

        out["components"]["research_bridge"] = UsageDataCollector.get_instance().health_check()
    except (ImportError, AttributeError, RuntimeError) as e:
        logger.warning("research_bridge health check failed: %s", e)
        out["components"]["research_bridge"] = safe_error_message(
            e, fallback="research_bridge 健康检查失败", context="status.research_bridge"
        )

    # 2. feature flags
    try:
        from app.research_bridge.feature_flags import (
            is_shadow_mode,
            ROLLOUT_CONFIG,
        )

        out["components"]["feature_flags"] = {
            "shadow_mode_master": any(is_shadow_mode(name) for name in ROLLOUT_CONFIG),
            "rollout": {
                name.value: {
                    "status": cfg.status.value,
                    "whitelist": list(cfg.user_whitelist),
                    "rollout_pct": cfg.rollout_percent,
                }
                for name, cfg in ROLLOUT_CONFIG.items()
            },
        }
    except (ImportError, AttributeError, KeyError) as e:
        logger.warning("feature_flags check failed: %s", e)
        out["components"]["feature_flags"] = safe_error_message(
            e, fallback="feature_flags 检查失败", context="status.feature_flags"
        )

    # 3. postprocessors
    try:
        from app.postprocessor.registry import PostProcessorRegistry

        regs = PostProcessorRegistry()
        out["components"]["postprocessors"] = {
            "registered": regs.list_controllers(),
        }
    except (ImportError, AttributeError, RuntimeError) as e:
        logger.warning("postprocessors check failed: %s", e)
        out["components"]["postprocessors"] = safe_error_message(
            e, fallback="postprocessors 检查失败", context="status.postprocessors"
        )

    # 4. knowledge graph
    try:
        from app.knowledge_graph import KnowledgeGraphQueryAPI, GraphStore

        api = KnowledgeGraphQueryAPI(GraphStore(auto_load=False))
        out["components"]["knowledge_graph"] = api.stats()
    except (ImportError, AttributeError, RuntimeError, OSError) as e:
        logger.warning("knowledge_graph check failed: %s", e)
        out["components"]["knowledge_graph"] = safe_error_message(
            e, fallback="knowledge_graph 检查失败", context="status.knowledge_graph"
        )

    # 5. environment flags
    out["env"] = {
        "shadow_mode_master": os.environ.get("SHADOW_MODE_MASTER", "0"),
        "research_flag": os.environ.get("ENABLE_RESEARCH", "0"),
    }
    return out


@router.get("/postprocessors")
def get_postprocessors() -> dict[str, Any]:
    """列出已注册的后处理器。"""
    try:
        from app.postprocessor.registry import PostProcessorRegistry

        regs = PostProcessorRegistry()
        return {
            "count": len(regs.list_controllers()),
            "controllers": regs.list_controllers(),
        }
    except (ImportError, AttributeError, RuntimeError) as e:
        logger.warning("get_postprocessors failed: %s", e)
        return safe_error_message(e, fallback="postprocessors 列表查询失败", context="status.get_postprocessors")


@router.get("/research-bridge")
def get_bridge() -> dict[str, Any]:
    """桥接层详情。"""
    try:
        from app.research_bridge import UsageDataCollector

        c = UsageDataCollector.get_instance()
        return {"health": c.health_check(), "summary": c.summary()}
    except (ImportError, AttributeError, RuntimeError) as e:
        logger.warning("get_bridge failed: %s", e)
        return safe_error_message(e, fallback="research_bridge 详情查询失败", context="status.get_bridge")


__all__ = ["router"]
