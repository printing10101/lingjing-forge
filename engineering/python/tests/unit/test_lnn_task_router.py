"""TaskRouter 直接单元测试（W9.5 遗留项补齐）。

此前该模块（规则+ML 混合路由，384 行）仅被 lnn/engine 间接触达，
无直接测试。覆盖：权重校验、类别亲和路由、在线学习翻转、
备选方案开关、确定性置信度。
"""

from __future__ import annotations

import pytest

from app.ai.lnn.core import EngineType, TaskCategory, TaskInput
from app.ai.lnn.router.task_router import TaskRouter

pytestmark = [pytest.mark.unit]


def _task(category: TaskCategory | None = TaskCategory.TIME_SERIES) -> TaskInput:
    return TaskInput(
        task_description="测试任务",
        input_data=[0.1, 0.2, 0.3],
        task_category=category,
    )


class TestValidation:
    def test_rejects_overweight_sum(self):
        with pytest.raises(ValueError, match="must not exceed"):
            TaskRouter(rule_weight=0.8, ml_weight=0.8)

    def test_rejects_negative_weight(self):
        with pytest.raises(ValueError, match="rule_weight"):
            TaskRouter(rule_weight=-0.1)

    def test_rejects_nonpositive_history(self):
        with pytest.raises(ValueError, match="history_size"):
            TaskRouter(history_size=0)


class TestRuleRouting:
    def test_time_series_routes_to_lnn(self):
        router = TaskRouter(rule_weight=1.0, ml_weight=0.0)
        decision = router.route(_task(TaskCategory.TIME_SERIES))
        assert decision.selected_engine == EngineType.LNN
        # 纯规则模式：置信度 = 类别亲和分（TIME_SERIES → LNN 0.65）
        assert decision.confidence == pytest.approx(0.65)

    def test_rule_based_routes_to_rule_engine(self):
        router = TaskRouter(rule_weight=1.0, ml_weight=0.0)
        decision = router.route(_task(TaskCategory.RULE_BASED))
        assert decision.selected_engine == EngineType.RULE

    def test_nlp_routes_to_llm(self):
        router = TaskRouter(rule_weight=1.0, ml_weight=0.0)
        decision = router.route(_task(TaskCategory.NLP))
        assert decision.selected_engine == EngineType.LLM

    def test_auto_detect_category(self):
        """task_category=None 时走自动检测路径，决策仍合法。"""
        router = TaskRouter()
        decision = router.route(_task(category=None))
        assert isinstance(decision.selected_engine, EngineType)
        assert 0.0 <= decision.confidence <= 1.0


class TestOnlineLearning:
    def test_repeated_failures_shift_routing_away(self):
        """在线 ML 信号：LNN 连续失败后，CLASSIFICATION 任务应改选其他引擎。"""
        router = TaskRouter(rule_weight=0.4, ml_weight=0.6)
        fresh = router.route(_task(TaskCategory.CLASSIFICATION))
        assert fresh.selected_engine == EngineType.LNN

        for _ in range(30):
            router.update_outcome(EngineType.LNN, success=False)

        shifted = router.route(_task(TaskCategory.CLASSIFICATION))
        assert shifted.selected_engine != EngineType.LNN

    def test_success_keeps_preference(self):
        router = TaskRouter(rule_weight=0.4, ml_weight=0.6)
        for _ in range(10):
            router.update_outcome(EngineType.LNN, success=True, confidence=0.9)
        decision = router.route(_task(TaskCategory.CLASSIFICATION))
        assert decision.selected_engine == EngineType.LNN


class TestFallbackAlternatives:
    def test_alternatives_populated_when_enabled(self):
        router = TaskRouter(enable_fallback=True)
        decision = router.route(_task(TaskCategory.TIME_SERIES))
        assert decision.alternatives is not None

    def test_alternatives_empty_when_disabled(self):
        router = TaskRouter(enable_fallback=False)
        decision = router.route(_task(TaskCategory.TIME_SERIES))
        assert not decision.alternatives
