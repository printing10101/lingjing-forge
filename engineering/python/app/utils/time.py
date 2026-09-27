"""统一的时间获取工具。

历史代码中大量直接调用 ``datetime.utcnow()``，该函数在 Python 3.12+
已被标记为 ``DeprecationWarning``（返回 naive datetime 易引发时区混淆
类 bug）。本模块提供行为等价的集中入口，便于：

1. 消除弃用警告（无需修改 25+ 处调用站点的调用形式）；
2. 后续如需迁移到 timezone-aware datetime，只需修改本模块一处即可
   全局生效；
3. 为文件名时间戳、ISO 8601 序列化等高频用法提供专用辅助函数，
   避免 ``.strftime("%Y%m%d_%H%M%S")`` / ``.isoformat() + "Z"`` 等
   重复样板代码。

.. note::
    当前 ``utcnow()`` 仍返回 **naive** UTC datetime，与历史
    ``datetime.utcnow()`` 行为完全一致，确保与现有 SQLAlchemy
    ``Column(DateTime)`` 列（naive）兼容。未来若全量迁移到
    ``Column(DateTime(timezone=True))``，可在此处切换为 aware 实现。
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """返回当前 UTC 时间（naive datetime）。

    与 ``datetime.utcnow()`` 行为完全一致：返回不带 ``tzinfo`` 的
    UTC datetime。供 SQLAlchemy ``Column(DateTime)`` 列赋值、
    时间戳比较等场景使用。

    Returns:
        Naive UTC datetime（与 ``datetime.utcnow()`` 等价）。

    Note:
        本函数刻意保留 naive 行为以兼容现有数据库列定义。新代码如需
        timezone-aware datetime，请直接使用
        ``datetime.now(timezone.utc)``。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utcnow_iso_z() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串（带 ``Z`` 后缀）。

    用于 JSON 序列化场景，例如::

        {"exported_at": utcnow_iso_z()}  # "2026-07-19T12:34:56.789Z"

    Returns:
        形如 ``"YYYY-MM-DDTHH:MM:SS.ffffffZ"`` 的字符串。
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def utcnow_seconds_iso_z() -> str:
    """返回当前 UTC 时间的秒级精度 ISO 8601 字符串（``Z`` 后缀）。

    格式 ``YYYY-MM-DDTHH:MM:SSZ``。用于与秒级精度的存储约定对齐的
    场景（如 SQLite DDL 的 ``strftime('%Y-%m-%dT%H:%M:%SZ', 'now')``
    默认值、CAM 校验报告时间戳）。

    Returns:
        形如 ``"2026-09-05T12:34:56Z"`` 的字符串。
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utcnow_filename_suffix() -> str:
    """返回适用于文件名的时间戳后缀。

    格式：``YYYYMMDD_HHMMSS``，例如 ``"20260719_123456"``。

    用于导出文件、日志切片等需要可排序的文件名场景。

    Returns:
        形如 ``"20260719_123456"`` 的字符串。
    """
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
