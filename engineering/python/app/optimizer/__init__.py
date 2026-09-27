"""参数优化引擎（Phase D）— 白盒核心逻辑。

本包实现「数据飞轮 → 参数推荐」闭环的纯 Python 白盒逻辑，零框架依赖
（不 import scipy/torch/sklearn），可在 torch 残缺 CI 上独立跑全量覆盖。

分层推荐策略（L0-L3，数据不足自动降级）：
- L0 规则基线：材料 × 刀具经验表（cutting_params_db 迁移来源）
- L1 统计推荐：同材料同刀具的历史实测均值（基于 cutting_experience）
- L2 模型推荐：LNN 切削力模型（预留接口，需 torch 环境）
- L3 贝叶斯优化：在线探索（预留接口）

设计原则：
1. **优雅降级**：数据不足时自动回退到低层策略，系统永不"无推荐可用"
2. **物理安全**：推荐参数必须落在安全区间（深度/转速/进给上限）
3. **可解释**：每次推荐带 strategy 标记 + 依据（basis），供审计
"""

from __future__ import annotations

from .baseline import (
    BaselineEntry,
    BaselineLibrary,
    DEFAULT_BASELINE,
    lookup_baseline,
)
from .recommender import (
    OptimizationTarget,
    Recommendation,
    ParameterRecommender,
    RecommendationStrategy,
    clamp_to_safe_bounds,
)
from .evaluator import (
    evaluate_recommendation,
    compare_parameter_sets,
)

__all__ = [
    "BaselineEntry",
    "BaselineLibrary",
    "DEFAULT_BASELINE",
    "lookup_baseline",
    "OptimizationTarget",
    "Recommendation",
    "ParameterRecommender",
    "RecommendationStrategy",
    "clamp_to_safe_bounds",
    "evaluate_recommendation",
    "compare_parameter_sets",
]
