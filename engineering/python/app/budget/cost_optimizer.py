"""智能成本优化建议系统.

从原 ``app/budget/budget_enforcer.py`` 拆分而来，聚焦于成本优化职责：
模型替代建议、GPU 利用率分析、训练复用建议等。

向后兼容：``app/budget/budget_enforcer.py`` 仍作为 re-export shim 暴露
本模块的全部公开符号。
"""

import logging
import threading
import time


from app.models.budget import CostOptimizationSuggestion
from app.services._shared.service_base import BaseSingletonService

logger = logging.getLogger(__name__)


class CostOptimizer(BaseSingletonService):
    """智能成本优化建议系统.

    单例管理由 ``BaseSingletonService`` 提供（``get_instance`` / ``reset_instance``）。
    需要「强制重新创建实例」时使用 :meth:`init` 类方法。
    """

    from app.budget.cost_tracker import ModelType as CTModelType

    MODEL_ALTERNATIVES = {
        CTModelType.CFC.value: [
            {
                "model": "LTC",
                "cost_factor": 0.7,
                "performance_note": "相近精度，低30%成本",
            },
            {
                "model": "Custom",
                "cost_factor": 0.5,
                "performance_note": "精简模型，适用于简单任务",
            },
        ],
        CTModelType.LTC.value: [
            {
                "model": "CFC",
                "cost_factor": 1.2,
                "performance_note": "更高精度但成本较高",
            },
            {
                "model": "Custom",
                "cost_factor": 0.6,
                "performance_note": "精简模型，适用于推理任务",
            },
        ],
        CTModelType.HYBRID_LNN.value: [
            {
                "model": "LTC",
                "cost_factor": 0.5,
                "performance_note": "单模型方案，低成本替代",
            },
            {
                "model": "CFC",
                "cost_factor": 0.8,
                "performance_note": "简化架构，适中成本",
            },
        ],
        CTModelType.TRANSFORMER.value: [
            {
                "model": "HybridLNN",
                "cost_factor": 0.3,
                "performance_note": "LNN架构，显著降本",
            },
            {"model": "Custom", "cost_factor": 0.4, "performance_note": "轻量模型替代"},
        ],
        CTModelType.CUSTOM.value: [
            {
                "model": "LTC",
                "cost_factor": 1.5,
                "performance_note": "更高性能标准模型",
            },
            {
                "model": "CFC",
                "cost_factor": 2.0,
                "performance_note": "最高精度专业模型",
            },
        ],
    }

    def __init__(self, cost_tracker=None):
        self._cost_tracker = cost_tracker

    def set_cost_tracker(self, cost_tracker) -> None:
        self._cost_tracker = cost_tracker

    def analyze_model_cost(self) -> list[CostOptimizationSuggestion]:
        from app.budget.cost_tracker import CostDimension, ModelType as CTModelType

        suggestions: list[CostOptimizationSuggestion] = []

        if self._cost_tracker is None:
            return suggestions

        summaries = self._cost_tracker.get_all_summaries(CostDimension.MODEL)

        for summary in summaries:
            model_name = summary.scope_id

            alternatives = self.MODEL_ALTERNATIVES.get(model_name)
            if not alternatives:
                alternatives = self.MODEL_ALTERNATIVES.get(CTModelType.CUSTOM.value, [])

            for alt in alternatives:
                alt_cost = summary.total_cost * alt["cost_factor"]
                savings = summary.total_cost - alt_cost

                if savings > 0:
                    suggestion = CostOptimizationSuggestion(
                        suggestion_id=f"model_{model_name}_{alt['model']}_{int(time.time())}",
                        category="model_optimization",
                        title=f"模型替代建议: {model_name} → {alt['model']}",
                        description=(
                            f"当前模型 {model_name} 总成本为 {summary.total_cost:.6f}，"
                            f"使用 {alt['model']} 预估成本 {alt_cost:.6f}。"
                            f"{alt['performance_note']}。"
                        ),
                        current_cost=summary.total_cost,
                        estimated_savings=savings,
                        savings_percentage=(savings / summary.total_cost * 100) if summary.total_cost > 0 else 0,
                        priority="high" if savings > summary.total_cost * 0.3 else "medium",
                        recommendation=f"建议将 {model_name} 相关任务迁移至 {alt['model']} 模型",
                        metrics={
                            "current_model": model_name,
                            "suggested_model": alt["model"],
                            "task_count": summary.task_count,
                            "gpu_time_cost": summary.gpu_time_cost,
                        },
                        generated_at=time.time(),
                    )
                    suggestions.append(suggestion)

        return suggestions

    def analyze_gpu_utilization(self, gpu_utilization_threshold: float = 0.5) -> list[CostOptimizationSuggestion]:
        suggestions: list[CostOptimizationSuggestion] = []

        if self._cost_tracker is None:
            return suggestions

        from app.budget.cost_tracker import CostDimension

        gpu_summary = self._cost_tracker.get_all_summaries(CostDimension.TASK)
        low_util_tasks = [
            s
            for s in gpu_summary
            if s.total_gpu_seconds > 0
            and (s.total_gpu_memory_gb_seconds / s.total_gpu_seconds if s.total_gpu_seconds > 0 else 1.0)
            < gpu_utilization_threshold
        ]

        if low_util_tasks:
            suggestion = CostOptimizationSuggestion(
                suggestion_id=f"gpu_util_{int(time.time())}",
                category="resource_optimization",
                title="GPU利用率优化建议",
                description=(
                    f"检测到 {len(low_util_tasks)} 个任务的GPU利用率低于{gpu_utilization_threshold * 100:.0f}%。"
                    f"建议采用批量推理策略，将多个低利用率任务合并执行。"
                ),
                current_cost=sum(t.total_cost for t in low_util_tasks),
                estimated_savings=sum(t.total_cost for t in low_util_tasks) * 0.3,
                savings_percentage=30.0,
                priority="medium",
                recommendation="启用批量推理模式，合并GPU低利用率任务以提升资源效率",
                metrics={
                    "low_utilization_task_count": len(low_util_tasks),
                    "threshold": gpu_utilization_threshold,
                },
                generated_at=time.time(),
            )
            suggestions.append(suggestion)

        return suggestions

    def analyze_training_efficiency(self) -> list[CostOptimizationSuggestion]:
        suggestions: list[CostOptimizationSuggestion] = []

        if self._cost_tracker is None:
            return suggestions

        from app.budget.cost_tracker import CostDimension

        model_summaries = self._cost_tracker.get_all_summaries(CostDimension.MODEL)

        for summary in model_summaries:
            if summary.task_count > 5 and summary.total_gpu_seconds > 3600:
                suggestion = CostOptimizationSuggestion(
                    suggestion_id=f"training_reuse_{summary.scope_id}_{int(time.time())}",
                    category="training_efficiency",
                    title=f"训练复用建议: {summary.scope_id}",
                    description=(
                        f"模型 {summary.scope_id} 已执行 {summary.task_count} 次训练任务，"
                        f"累计GPU时间 {summary.total_gpu_seconds:.0f}秒。"
                        f"检测到重复训练模式，建议启用模型复用机制。"
                    ),
                    current_cost=summary.total_cost,
                    estimated_savings=summary.total_cost * 0.4,
                    savings_percentage=40.0,
                    priority="high",
                    recommendation=(
                        f"为 {summary.scope_id} 启用预训练模型缓存，对相似任务复用已有模型权重，减少冗余训练"
                    ),
                    metrics={
                        "model": summary.scope_id,
                        "task_count": summary.task_count,
                        "total_gpu_seconds": summary.total_gpu_seconds,
                    },
                    generated_at=time.time(),
                )
                suggestions.append(suggestion)

        return suggestions

    def generate_all_suggestions(self) -> list[CostOptimizationSuggestion]:
        all_suggestions = []
        all_suggestions.extend(self.analyze_model_cost())
        all_suggestions.extend(self.analyze_gpu_utilization())
        all_suggestions.extend(self.analyze_training_efficiency())
        return sorted(all_suggestions, key=lambda s: s.estimated_savings, reverse=True)

    # 单例生命周期扩展

    @classmethod
    def init(cls) -> "CostOptimizer":
        """强制重新创建单例实例（用于启动时初始化的场景）。

        与 :meth:`get_instance` 的「懒初始化」不同，``init`` 总是创建新实例并
        覆盖已有的单例。行为与重构前 ``_CostOptimizerHolder.init`` 一致。
        """
        with cls._service_lock:
            cls._service_singleton = cls()
            return cls._service_singleton


class _CostOptimizerHolder:
    """[Deprecated] 已被 :class:`BaseSingletonService` 单例机制取代.

    本类仅作为占位符保留，避免破坏 ``app/budget/budget_enforcer.py`` re-export
    shim 的导入。新代码应直接使用 :meth:`CostOptimizer.get_instance` /
    :meth:`CostOptimizer.init` / :meth:`CostOptimizer.reset_instance`。
    """

    def __init__(self) -> None:
        # 保留原属性名以兼容可能的外部反射访问
        self._lock = threading.Lock()
        self._instance: CostOptimizer | None = None

    def get(self) -> CostOptimizer:
        return CostOptimizer.get_instance()  # type: ignore[return-value]

    def init(self) -> CostOptimizer:
        return CostOptimizer.init()

    def reset(self) -> None:
        CostOptimizer.reset_instance()


_optimizer_holder = _CostOptimizerHolder()


def get_cost_optimizer() -> CostOptimizer:
    """获取共享的 :class:`CostOptimizer` 单例；首次访问时懒初始化。

    .. deprecated:: V3.0 (2026-08-02)

    Returns:
        :class:`CostOptimizer` 实例（应用生命周期内同一实例）。
    """
    return CostOptimizer.get_instance()  # type: ignore[return-value]


def init_cost_optimizer() -> CostOptimizer:
    """初始化成本优化器，行为与重构前完全一致。

    内部委托给 :meth:`CostOptimizer.init`：强制重新创建单例。
    """
    return CostOptimizer.init()


__all__ = [
    "CostOptimizer",
    "_CostOptimizerHolder",
    "_optimizer_holder",
    "get_cost_optimizer",
    "init_cost_optimizer",
]
