"""特征提取路由辅助函数（从 routes.py 抽取，D5 God 模块拆分）。

承接与 HTTP 无关的纯逻辑：_spawn / _get_pipeline / _disclaimer_dict /
_resolve_upstream_calibrated。抽取后 routes.py 仅保留端点，行为零变更。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import config
from app.core.safe_errors import safe_error_message
from app.feature_extraction import (
    FeatureExtractionPipeline,
    build_feature_disclaimer,
    get_feature_store,
)

logger = logging.getLogger(__name__)

# 后台任务引用集合（asyncio.create_task 不保存引用会被 GC 回收）
_background_tasks: set = set()


def _spawn(coro):
    """启动后台任务并保存引用，避免被 Python GC 回收。"""
    t = asyncio.create_task(coro)
    _background_tasks.add(t)
    t.add_done_callback(_background_tasks.discard)
    return t


_pipeline: FeatureExtractionPipeline | None = None


def _get_pipeline() -> FeatureExtractionPipeline:
    """获取 pipeline 单例。"""
    global _pipeline
    if _pipeline is None:
        _pipeline = FeatureExtractionPipeline(
            task_store=get_feature_store(),
            cfg=config.feature_extraction,
        )
    return _pipeline


def _disclaimer_dict(
    mesh_calibrated: bool = False,
    mesh_source: str = "external_upload",
) -> dict[str, Any]:
    """构造精度告知字段。"""
    return build_feature_disclaimer(
        config.feature_extraction,
        mesh_calibrated=mesh_calibrated,
        mesh_source=mesh_source,
    ).to_dict()


def _resolve_upstream_calibrated(
    source_reconstruction_task_id: str,
) -> tuple[bool, str]:
    """从上游 image_to_3d 任务查询 mesh 标定状态。

    Returns:
        (calibrated, mesh_source)
        - 若上游任务存在且已 SUCCEEDED：返回 (task.calibrated, task_id)
        - 若上游任务不存在或未完成：返回 (False, "external_upload")，并记日志

    设计意图：避免硬依赖 image_to_3d 模块（桌面轻量档位下可能未启用），
    通过 try/except ImportError 实现软依赖。
    """
    if not source_reconstruction_task_id:
        return False, "external_upload"

    try:
        from app.image_to_3d import get_task_store as get_i2t3d_store
        from app.image_to_3d.task_store import ReconstructionTaskStatus
    except ImportError:
        logger.warning(
            "image_to_3d 模块未启用，无法追溯上游任务 calibrated 状态 source_reconstruction_task_id=%s，按未标定处理",
            source_reconstruction_task_id,
        )
        return False, "external_upload"

    try:
        upstream = get_i2t3d_store().get(source_reconstruction_task_id)
        if upstream is None:
            logger.warning(
                "上游 image_to_3d 任务不存在 task_id=%s，按未标定处理",
                source_reconstruction_task_id,
            )
            return False, "external_upload"

        if upstream.status != ReconstructionTaskStatus.SUCCEEDED.value:
            logger.warning(
                "上游 image_to_3d 任务未完成 task_id=%s status=%s，按未标定处理",
                source_reconstruction_task_id,
                upstream.status,
            )
            return False, "external_upload"

        return bool(upstream.calibrated), source_reconstruction_task_id
    except Exception as e:
        safe = safe_error_message(e, context="feature_extraction.resolve_upstream_calibrated")
        logger.warning(
            "查询上游 image_to_3d 任务异常 source=%s error_id=%s，按未标定处理",
            source_reconstruction_task_id,
            safe.get("error_id"),
        )
        return False, "external_upload"
