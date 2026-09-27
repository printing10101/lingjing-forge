"""
系统一致性测试

测试目标: 相同问题重复3次，输出基本一致（允许措辞差异）
测试方法: 选取代表性工艺问题，检查规则分类器的确定性
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.ai.process_understanding.task_classifier import (
    RuleBasedClassifier,
    TaskType,
)
from app.ai.process_understanding.solution_generator import (
    SolutionGenerator,
    ProcessSolution,
)
from app.ai.process_understanding.prediction_explainer import (
    PredictionExplainer,
    PredictionData,
    PredictionExplanation,
)

pytestmark = pytest.mark.skip_ci


# 代表性测试问题集


REPRESENTATIVE_QUERIES = [
    "45钢轴类零件的车削参数怎么选？",
    "加工304不锈钢用什么刀具好？",
    "刀具磨损太快了怎么办？",
    "工件表面粗糙度不合格是什么原因？",
    "帮我生成一个铝合金壳体的加工方案",
    "IT8精度一般怎么加工？",
    "什么是切削三要素？",
    "加工中心有哪些常用G代码？",
]


# 一致性评估工具


class ConsistencyChecker:
    """输出一致性检查器"""

    @staticmethod
    def check_classification_consistency(results: list, min_match_rate: float = 1.0) -> dict:
        """检查分类结果的一致性。

        Args:
            results: 多次分类结果列表
            min_match_rate: 最低一致率要求

        Returns:
            一致性评估结果
        """
        if len(results) < 2:
            return {"consistent": True, "match_rate": 1.0}

        # 提取任务类型
        task_types = [r.task_type for r in results]

        # 检查是否所有结果相同
        all_same = all(t == task_types[0] for t in task_types)
        match_count = sum(1 for t in task_types if t == task_types[0])

        return {
            "consistent": all_same,
            "match_rate": match_count / len(task_types),
            "passed": all_same,
            "task_types": [t.label for t in task_types],
            "unique_types": len(set(task_types)),
        }

    @staticmethod
    def check_solution_consistency(
        solutions: list[ProcessSolution],
        min_dimension_match: float = 0.7,
    ) -> dict:
        """检查方案生成的一致性。

        评估维度：
        - 工序数量一致性
        - 工序名称相似度
        - 参数覆盖一致性
        - 风险识别一致性
        """
        if len(solutions) < 2:
            return {"consistent": True, "match_rate": 1.0}

        # 工序数量一致性
        route_lengths = [len(s.process_route) for s in solutions]
        route_length_same = all(length == route_lengths[0] for length in route_lengths)

        # 工序名称相似度
        all_operations = []
        for s in solutions:
            ops = [step.operation for step in s.process_route]
            all_operations.append(set(ops))

        # 计算Jaccard相似度
        similarities = []
        for i in range(len(all_operations)):
            for j in range(i + 1, len(all_operations)):
                intersection = len(all_operations[i] & all_operations[j])
                union = len(all_operations[i] | all_operations[j])
                sim = intersection / union if union > 0 else 0.0
                similarities.append(sim)

        avg_similarity = sum(similarities) / len(similarities) if similarities else 0.0

        # 置信度一致性
        confidences = [s.confidence_score for s in solutions]
        conf_range = max(confidences) - min(confidences) if confidences else 0.0
        conf_consistent = conf_range <= 3.0  # 置信度差不超过3分

        # 综合得分
        scores = []
        if route_length_same:
            scores.append(1.0)
        else:
            scores.append(0.5)
        scores.append(avg_similarity)
        if conf_consistent:
            scores.append(1.0)
        else:
            scores.append(0.5)

        overall_consistency = sum(scores) / len(scores)

        return {
            "consistent": overall_consistency >= min_dimension_match,
            "match_rate": overall_consistency,
            "route_lengths": route_lengths,
            "route_length_same": route_length_same,
            "avg_operation_similarity": round(avg_similarity, 3),
            "confidence_range": round(conf_range, 2),
            "confidence_consistent": conf_consistent,
            "passed": overall_consistency >= min_dimension_match,
        }

    @staticmethod
    def check_explanation_consistency(
        explanations: list[PredictionExplanation],
        min_match_rate: float = 0.7,
    ) -> dict:
        """检查预测解释的一致性。"""
        if len(explanations) < 2:
            return {"consistent": True, "match_rate": 1.0}

        # 风险等级一致性
        risk_levels = [e.risk_level for e in explanations]
        risk_same = all(r == risk_levels[0] for r in risk_levels)

        # 操作建议数量一致性
        action_counts = [len(e.recommended_actions) for e in explanations]
        action_count_consistent = all(abs(c - action_counts[0]) <= 1 for c in action_counts)

        # 章节标题相似度
        section_titles = [[s.title for s in e.sections] for e in explanations]
        title_sets = [set(titles) for titles in section_titles]
        similarities = []
        for i in range(len(title_sets)):
            for j in range(i + 1, len(title_sets)):
                intersection = len(title_sets[i] & title_sets[j])
                union = len(title_sets[i] | title_sets[j])
                sim = intersection / union if union > 0 else 0.0
                similarities.append(sim)

        avg_title_sim = sum(similarities) / len(similarities) if similarities else 0.0

        scores = []
        if risk_same:
            scores.append(1.0)
        else:
            scores.append(0.5)
        if action_count_consistent:
            scores.append(1.0)
        else:
            scores.append(0.5)
        scores.append(avg_title_sim)

        overall = sum(scores) / len(scores)

        return {
            "consistent": overall >= min_match_rate,
            "match_rate": overall,
            "risk_levels": risk_levels,
            "risk_same": risk_same,
            "avg_section_title_similarity": round(avg_title_sim, 3),
            "passed": overall >= min_match_rate,
        }


class TestClassifierConsistency:
    """分类器一致性测试"""

    def test_rule_classifier_is_deterministic(self):
        """规则分类器应对相同输入返回相同结果。"""
        classifier = RuleBasedClassifier()

        for query in REPRESENTATIVE_QUERIES:
            results = []
            for _ in range(3):
                result = classifier.classify(query)
                if result is not None:
                    results.append(result)

            if len(results) >= 2:
                check = ConsistencyChecker.check_classification_consistency(results)
                assert check["passed"], f"规则分类器对 '{query}' 的分类不一致: {check['task_types']}"


class TestFallbackSolutionConsistency:
    """降级方案一致性测试"""

    def test_fallback_solution_is_deterministic(self):
        """降级方案应对相同参数返回相同结构。"""
        solutions = []
        for _ in range(3):
            s = SolutionGenerator._create_fallback_solution("45钢", "IT8", "单件", "CNC加工中心")
            solutions.append(s)

        check = ConsistencyChecker.check_solution_consistency(solutions)
        print(f"\n降级方案一致性: {json.dumps(check, ensure_ascii=False)}")
        assert check["passed"], "降级方案一致性不足"


class TestExplanationConsistency:
    """预测解释一致性测试"""

    def test_fallback_explanation_is_deterministic(self):
        """降级解释应对相同输入返回一致的结论。"""
        prediction = PredictionData(
            force_pred=450.0,
            force_conf=85.0,
            wear_pred=0.35,
            wear_conf=90.0,
            visual_status="工件表面有轻微振纹",
            anomaly_prob=55.0,
        )

        explanations = []
        for _ in range(3):
            e = PredictionExplainer._create_fallback_explanation(prediction)
            explanations.append(e)

        # 检查风险等级一致性
        risk_levels = [e.risk_level for e in explanations]
        assert all(r == risk_levels[0] for r in risk_levels), f"风险等级不一致: {risk_levels}"

        # 检查操作建议非空
        for e in explanations:
            assert len(e.recommended_actions) > 0

    def test_explanation_covers_all_sections(self):
        """解释应覆盖所有必要的章节类型。"""
        prediction = PredictionData(
            force_pred=200.0,
            force_conf=90.0,
            wear_pred=0.15,
            wear_conf=85.0,
            visual_status="正常",
            anomaly_prob=10.0,
        )

        explanation = PredictionExplainer._create_fallback_explanation(prediction)
        assert len(explanation.sections) >= 2  # 至少2个章节（切削力 + 刀具磨损）
        assert any("切削力" in s.title for s in explanation.sections)
        assert any("刀具" in s.title for s in explanation.sections)


class TestJSONOutputConsistency:
    """JSON输出格式一致性测试"""

    def test_solution_to_dict_is_consistent(self):
        """ProcessSolution.to_dict() 应对相同数据返回相同结果。"""
        solution = SolutionGenerator._create_fallback_solution("45钢", "IT8", "单件", "CNC加工中心")

        outputs = [solution.to_dict() for _ in range(3)]
        hashes = [hashlib.md5(json.dumps(o, sort_keys=True).encode()).hexdigest() for o in outputs]

        assert all(h == hashes[0] for h in hashes), "to_dict() 输出不一致"

    def test_engine_output_to_dict_is_consistent(self):
        """ProcessUnderstandingOutput.to_dict() 格式一致。"""
        from app.ai.process_understanding.engine import ProcessUnderstandingOutput

        output = ProcessUnderstandingOutput(
            task_type="A",
            intent="工艺咨询",
            entities={"材料": "45钢"},
            response="测试回复",
            confidence=0.8,
            sources=["default"],
            actions=["操作1", "操作2"],
        )

        for _ in range(3):
            d = output.to_dict()
            assert "task_type" in d
            assert "intent" in d
            assert "entities" in d
            assert "response" in d
            assert "confidence" in d
            assert "sources" in d
            assert "actions" in d


class TestConsistencyChecker:
    """一致性检查器自身测试"""

    def test_identical_results(self):
        results = [
            type("Mock", (), {"task_type": TaskType.PROCESS_CONSULT})(),
            type("Mock", (), {"task_type": TaskType.PROCESS_CONSULT})(),
            type("Mock", (), {"task_type": TaskType.PROCESS_CONSULT})(),
        ]
        check = ConsistencyChecker.check_classification_consistency(results)
        assert check["passed"]

    def test_different_results(self):
        results = [
            type("Mock", (), {"task_type": TaskType.PROCESS_CONSULT})(),
            type("Mock", (), {"task_type": TaskType.FAULT_DIAGNOSIS})(),
            type("Mock", (), {"task_type": TaskType.CHITCHAT})(),
        ]
        check = ConsistencyChecker.check_classification_consistency(results)
        assert not check["passed"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
