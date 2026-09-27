"""SQL 构建安全工具。

提供动态 SQL 中列名/表名的安全白名单校验机制，
防止通过 f-string 直接拼接用户可控字符串导致的 SQL 注入。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# 成本追踪模块允许的列名白名单

_COST_DIMENSION_COLUMNS: frozenset[str] = frozenset(
    {
        "agent_id",
        "project_id",
        "goal_id",
        "task_id",
        "provider",
        "model",
    }
)

_COST_EVENTS_COLUMNS: frozenset[str] = frozenset(
    {
        "cost_type",
        "resource_value",
        "cost_value",
        "task_id",
        "recorded_at",
        "agent_id",
        "project_id",
        "goal_id",
        "provider",
        "model",
    }
)

_BUDGET_EVENTS_COLUMNS: frozenset[str] = frozenset(
    {
        "event_type",
        "dimension",
        "scope_id",
        "limit_amount",
        "current_usage",
        "recorded_at",
        "created_at",
        "updated_at",
    }
)

# 允许在 UPDATE SET 子句中出现的列名
_BUDGET_UPDATE_ALLOWED_COLUMNS: frozenset[str] = frozenset(
    {
        "limit_amount",
        "current_usage",
        "updated_at",
    }
)

# 允许出现在 ORDER BY 后的列名
_SORT_ALLOWED_COLUMNS: frozenset[str] = frozenset(
    {
        "total_cost",
        "total_resource",
        "task_count",
        "recorded_at",
        "created_at",
        "scope_id",
    }
)


def validate_column(
    column: str,
    allowed_set: frozenset[str],
    context: str = "SQL",
) -> str:
    """校验列名是否在允许的白名单内。

    Args:
        column: 待校验的列名。
        allowed_set: 允许的列名白名单。
        context: 校验上下文（用于错误消息）。

    Returns:
        校验通过的列名（原样返回）。

    Raises:
        ValueError: 列名不在白名单内。
    """
    if column not in allowed_set:
        logger.error(
            "SQL column injection attempt blocked | column=%s | context=%s",
            column,
            context,
        )
        raise ValueError(f"非法的列名: {column!r}")
    return column


def validate_cost_dimension_column(column: str) -> str:
    """校验成本维度列名。"""
    return validate_column(column, _COST_DIMENSION_COLUMNS, context="CostDimension")


def validate_budget_update_column(column: str) -> str:
    """校验预算更新 SET 子句中的列名。"""
    return validate_column(column, _BUDGET_UPDATE_ALLOWED_COLUMNS, context="BudgetUpdate")


def validate_sort_column(column: str) -> str:
    """校验排序字段。"""
    return validate_column(column, _SORT_ALLOWED_COLUMNS, context="SortColumn")


def safe_order_clause(
    order_by: str | None,
    default: str = "total_cost DESC",
) -> str:
    """安全地构建 ORDER BY 子句。

    Args:
        order_by: 用户请求的排序字段（仅列名，不含 ASC/DESC）。
        default: 默认排序子句。

    Returns:
        安全的 ORDER BY 子句。
    """
    if not order_by:
        return default
    col = validate_sort_column(order_by)
    return f"{col} DESC"
