"""cost_budget 端点线程池安全结构测试。

成本/预算端点内部执行同步 SQLite 查询（CostTracker/BudgetEnforcer），
必须声明为普通 ``def``（FastAPI 自动放入线程池）；写成 ``async def``
会阻塞事件循环。本测试锁定该结构约束。
"""

import inspect

from app.api.v1 import cost_budget


def test_cost_budget_endpoints_are_not_coroutines():
    routes = [r for r in cost_budget.router.routes if hasattr(r, "endpoint")]
    assert routes, "cost_budget 路由为空，测试失去意义"

    offenders = [(r.path, r.endpoint.__name__) for r in routes if inspect.iscoroutinefunction(r.endpoint)]
    assert offenders == [], f"同步 SQLite 端点不允许写成 async def（会阻塞事件循环）: {offenders}"
