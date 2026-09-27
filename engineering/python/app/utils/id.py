"""统一的实体 ID 生成工具。

数据库模型层大量 ``f"{prefix}_{uuid.uuid4().hex}"`` 形式的主键生成
函数在此收敛为单一实现，格式约定：``{前缀}_{uuid4 hex32}``。
"""

from __future__ import annotations

import uuid


def new_id(prefix: str) -> str:
    """生成 ``{prefix}_{uuid4 hex}`` 形式的实体 ID。

    Args:
        prefix: 业务前缀（如 ``mrec`` / ``proj``），存量数据的前缀
            格式不能变，新增前缀需与建表约定一致。

    Returns:
        形如 ``mrec_3f2a...`` 的 ID 字符串。
    """
    return f"{prefix}_{uuid.uuid4().hex}"
