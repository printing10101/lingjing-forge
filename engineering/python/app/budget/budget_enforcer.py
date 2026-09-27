"""Budget Enforcement & Control Mechanism — re-export shim.

历史上本模块同时承载了预算执行与成本优化两个不相干职责（1183 行、4 个类）。
P1-2 重构将其拆分为三个子模块：

- ``app/budget/models.py``: ``EnforcementAction`` / ``EnforcementResult``
  等执行器专属数据类与枚举。
- ``app/budget/enforcer.py``: ``BudgetEnforcer`` + ``_BudgetEnforcerHolder``
  及其工厂函数（预算执行）。
- ``app/budget/cost_optimizer.py``: ``CostOptimizer`` + ``_CostOptimizerHolder``
  及其工厂函数（成本优化）。

本文件仅作为向后兼容的 re-export 入口：所有 ``from app.budget.budget_enforcer
import XXX`` 形式的旧导入仍可正常工作，类签名与运行时行为保持不变。
"""

from app.budget.models import EnforcementAction, EnforcementResult

# 修复：P1-2 拆分后静态 shim 缺失的 re-export 导入（2026-08-03 安装验证发现）
from app.budget.enforcer import (
    BudgetEnforcer,
    _BudgetEnforcerHolder,
    _budget_holder,
    get_budget_enforcer,
    init_budget_enforcer,
)
from app.budget.cost_optimizer import (
    CostOptimizer,
    _CostOptimizerHolder,
    _optimizer_holder,
    get_cost_optimizer,
    init_cost_optimizer,
)

__all__ = [
    # 数据模型
    "EnforcementAction",
    "EnforcementResult",
    # 预算执行
    "BudgetEnforcer",
    "_BudgetEnforcerHolder",
    "_budget_holder",
    "get_budget_enforcer",
    "init_budget_enforcer",
    # 成本优化
    "CostOptimizer",
    "_CostOptimizerHolder",
    "_optimizer_holder",
    "get_cost_optimizer",
    "init_cost_optimizer",
]
