"""自进化演化循环（自进化 M1 · 闭环运转）。

组件：
- ``loop.EvolutionEngine``：统计 → 提案 → 门控发布/回滚 → 报告
- ``task_handler``：workflow 任务处理器（task_type=``evolution_loop``）
  与心跳 cron 任务注册
- 提示词版本持久化：``app.ai.prompts.persistence``
"""

from app.evolution.loop import (
    EvolutionEngine,
    LoopResult,
    PromoteResult,
    ProposalResult,
    get_evolution_engine,
    reset_evolution_engine,
)

__all__ = [
    "EvolutionEngine",
    "LoopResult",
    "PromoteResult",
    "ProposalResult",
    "get_evolution_engine",
    "reset_evolution_engine",
]
