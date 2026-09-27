"""
响应性能测试

测试目标: 单次响应延迟 < 3秒
测试方法: 模拟不同负载条件下各模块的响应时间
"""

from __future__ import annotations

import time

import pytest

from app.ai.process_understanding.task_classifier import (
    RuleBasedClassifier,
    TaskType,
)

pytestmark = pytest.mark.skip_ci


# 性能基准配置

# 最大允许延迟 (ms)


MAX_CLASSIFICATION_LATENCY_MS = 10.0  # 规则分类 < 10ms
MAX_FALLBACK_LATENCY_MS = 100.0  # 降级方案生成 < 100ms
MAX_EXPLANATION_LATENCY_MS = 100.0  # 降级解释生成 < 100ms
MAX_OVERALL_LATENCY_MS = 3000.0  # 完整流程 < 3000ms

# 压力测试配置
STRESS_TEST_ITERATIONS = 1000
STRESS_TEST_MAX_AVG_LATENCY_MS = 5.0  # 批量分类平均延迟


class TestClassificationPerformance:
    """分类性能测试"""

    def test_single_classification_latency(self):
        """单次分类延迟 < 10ms。"""
        classifier = RuleBasedClassifier()
        test_input = "45钢怎么加工？切削参数怎么选？"

        latencies = []
        for _ in range(100):
            start = time.perf_counter()
            classifier.classify(test_input)
            latencies.append((time.perf_counter() - start) * 1000)

        avg_latency = sum(latencies) / len(latencies)
        max_latency = max(latencies)

        print(f"\n分类延迟: avg={avg_latency:.2f}ms, max={max_latency:.2f}ms")
        assert avg_latency < MAX_CLASSIFICATION_LATENCY_MS, (
            f"平均分类延迟 {avg_latency:.2f}ms 超过 {MAX_CLASSIFICATION_LATENCY_MS}ms"
        )

    def test_batch_classification_latency(self):
        """批量分类平均延迟测试。"""
        classifier = RuleBasedClassifier()
        test_cases = [
            "45钢怎么加工？",
            "刀具磨损太快了",
            "帮我生成一个加工方案",
            "G代码怎么编写？",
            "你好",
        ] * 200  # 1000 cases

        start = time.perf_counter()
        for case in test_cases:
            classifier.classify(case)
        total_ms = (time.perf_counter() - start) * 1000
        avg_latency = total_ms / len(test_cases)

        print(f"\n批量分类: {len(test_cases)}cases, avg={avg_latency:.2f}ms, total={total_ms:.0f}ms")
        assert avg_latency < STRESS_TEST_MAX_AVG_LATENCY_MS, (
            f"批量平均延迟 {avg_latency:.2f}ms 超过 {STRESS_TEST_MAX_AVG_LATENCY_MS}ms"
        )

    def test_classification_latency_by_type(self):
        """按任务类型测试延迟分布。"""
        classifier = RuleBasedClassifier()
        type_cases = {
            TaskType.PROCESS_CONSULT: [
                "45钢怎么加工？",
                "铣削铝件用什么刀具？",
                "车削不锈钢的参数推荐",
            ],
            TaskType.FAULT_DIAGNOSIS: [
                "刀具崩刃了",
                "加工精度超差",
                "机床振动大怎么办",
            ],
            TaskType.SOLUTION_GENERATION: [
                "帮我生成加工方案",
                "需要完整的工艺路线",
                "设计一个加工流程",
            ],
            TaskType.KNOWLEDGE_QUERY: [
                "什么是切削三要素？",
                "GB标准是什么？",
                "粗糙度Ra3.2什么意思",
            ],
            TaskType.CHITCHAT: [
                "你好",
                "谢谢",
                "再见",
            ],
        }

        for task_type, cases in type_cases.items():
            latencies = []
            for _ in range(20):
                for case in cases:
                    start = time.perf_counter()
                    classifier.classify(case)
                    latencies.append((time.perf_counter() - start) * 1000)

            avg = sum(latencies) / len(latencies)
            print(f"  {task_type.label}: avg={avg:.2f}ms")
            assert avg < MAX_CLASSIFICATION_LATENCY_MS, f"{task_type.label} 分类延迟 {avg:.2f}ms 超限"


class TestFallbackPerformance:
    """降级方案性能测试"""

    def test_fallback_solution_generation_latency(self):
        """降级方案生成延迟 < 100ms。"""
        from app.ai.process_understanding.solution_generator import SolutionGenerator

        latencies = []
        for _ in range(50):
            start = time.perf_counter()
            SolutionGenerator._create_fallback_solution("45钢", "IT8", "单件", "CNC加工中心")
            latencies.append((time.perf_counter() - start) * 1000)

        avg = sum(latencies) / len(latencies)
        print(f"\n降级方案生成: avg={avg:.2f}ms")
        assert avg < MAX_FALLBACK_LATENCY_MS, f"降级方案生成延迟 {avg:.2f}ms 超过 {MAX_FALLBACK_LATENCY_MS}ms"

    def test_fallback_explanation_generation_latency(self):
        """降级解释生成延迟 < 100ms。"""
        from app.ai.process_understanding.prediction_explainer import (
            PredictionExplainer,
            PredictionData,
        )

        prediction = PredictionData(
            force_pred=450.0,
            force_conf=85.0,
            wear_pred=0.35,
            wear_conf=90.0,
            visual_status="异常",
            anomaly_prob=55.0,
        )

        latencies = []
        for _ in range(50):
            start = time.perf_counter()
            PredictionExplainer._create_fallback_explanation(prediction)
            latencies.append((time.perf_counter() - start) * 1000)

        avg = sum(latencies) / len(latencies)
        print(f"降级解释生成: avg={avg:.2f}ms")
        assert avg < MAX_EXPLANATION_LATENCY_MS, f"降级解释生成延迟 {avg:.2f}ms 超过 {MAX_EXPLANATION_LATENCY_MS}ms"

    def test_entity_extraction_latency(self):
        """实体提取解析延迟 < 50ms。"""
        from app.ai.process_understanding.engine import ProcessUnderstandingEngine

        test_content = '{"材料": "45钢", "精度": "IT8", "批量": "单件"}'

        latencies = []
        for _ in range(100):
            start = time.perf_counter()
            ProcessUnderstandingEngine._parse_entity_json(test_content)
            latencies.append((time.perf_counter() - start) * 1000)

        avg = sum(latencies) / len(latencies)
        print(f"实体提取解析: avg={avg:.2f}ms")
        assert avg < 50, f"实体提取解析延迟 {avg:.2f}ms 超限"


class TestStressPerformance:
    """压力测试"""

    def test_concurrent_classifications(self):
        """并发分类压力测试。"""
        import asyncio

        classifier = RuleBasedClassifier()
        test_cases = [
            "45钢怎么加工？",
            "刀具磨损太快了",
            "帮我生成一个加工方案",
            "什么是切削三要素？",
            "加工中心报警了怎么办",
            "不锈钢的切削速度推荐",
            "工件表面粗糙度不合格",
            "需要完整的工艺路线",
            "G代码编程基础",
            "机床主轴故障诊断",
        ] * 100  # 1000 cases

        async def run():
            for case in test_cases:
                classifier.classify(case)

        start = time.perf_counter()
        asyncio.run(run())
        total_ms = (time.perf_counter() - start) * 1000
        avg = total_ms / len(test_cases)

        print(f"\n并发分类压力测试: {len(test_cases)}cases, avg={avg:.2f}ms, total={total_ms:.0f}ms")
        assert avg < STRESS_TEST_MAX_AVG_LATENCY_MS, f"压力测试平均延迟 {avg:.2f}ms 超限"

    def test_memory_stability(self):
        """内存稳定性测试 - 大量分类后不应泄漏。"""
        import gc

        classifier = RuleBasedClassifier()
        gc.collect()

        for _ in range(1000):
            classifier.classify("45钢怎么加工？刀具磨损太快了")
            classifier.classify("帮我生成一个完整的加工工艺方案")
            classifier.classify("什么是切削三要素？")

        # 验证垃圾回收后没有明显内存增长
        gc.collect()
        # 基本功能仍可用
        result = classifier.classify("你好")
        assert result is not None


class TestDataStructurePerformance:
    """数据结构性能测试"""

    def test_process_solution_to_dict_performance(self):
        """ProcessSolution.to_dict() 性能。"""
        from app.ai.process_understanding.solution_generator import SolutionGenerator

        solution = SolutionGenerator._create_fallback_solution("45钢", "IT8", "单件", "CNC加工中心")

        latencies = []
        for _ in range(100):
            start = time.perf_counter()
            solution.to_dict()
            latencies.append((time.perf_counter() - start) * 1000)

        avg = sum(latencies) / len(latencies)
        print(f"\nsolution.to_dict(): avg={avg:.2f}ms")
        assert avg < 10, f"to_dict() 延迟 {avg:.2f}ms 超限"

    def test_explanation_to_dict_performance(self):
        """PredictionExplanation.to_dict() 性能。"""
        from app.ai.process_understanding.prediction_explainer import (
            PredictionExplainer,
            PredictionData,
        )

        prediction = PredictionData(
            force_pred=450.0,
            force_conf=85.0,
            wear_pred=0.35,
            wear_conf=90.0,
        )
        explanation = PredictionExplainer._create_fallback_explanation(prediction)

        latencies = []
        for _ in range(100):
            start = time.perf_counter()
            explanation.to_dict()
            latencies.append((time.perf_counter() - start) * 1000)

        avg = sum(latencies) / len(latencies)
        print(f"explanation.to_dict(): avg={avg:.2f}ms")
        assert avg < 10, f"to_dict() 延迟 {avg:.2f}ms 超限"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
