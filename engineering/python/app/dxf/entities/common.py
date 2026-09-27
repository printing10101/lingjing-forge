"""DXF 实体通用工具（safe_color 等）。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def safe_color(entity) -> int:
    """安全获取实体颜色索引。"""
    try:
        return int(entity.dxf.color)
    except (AttributeError, TypeError, ValueError) as exc:
        # 修复：原代码用裸 except Exception 静默吞掉所有错误。
        logger.warning(
            "_safe_color 降级到 256 (handle=%s): %s",
            getattr(entity.dxf, "handle", "?"),
            exc,
            exc_info=True,
        )
        return 256
