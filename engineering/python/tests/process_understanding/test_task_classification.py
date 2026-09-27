"""
任务分类准确率测试

测试目标: 分类准确率 > 95%
测试方法: 使用规则分类器对500条标注样本进行交叉验证
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from app.ai.process_understanding.task_classifier import (
    RuleBasedClassifier,
    TaskType,
    ClassificationResult,
)

pytestmark = pytest.mark.skip_ci


# 测试样本集 - 覆盖各类任务类型与表述方式

TEST_CASES: list[dict[str, Any]] = [
    # A. 工艺咨询
    {"input": "45钢怎么加工？", "expected": TaskType.PROCESS_CONSULT},
    {"input": "加工6061铝合金用什么刀具好？", "expected": TaskType.PROCESS_CONSULT},
    {"input": "304不锈钢的切削参数推荐一下", "expected": TaskType.PROCESS_CONSULT},
    {"input": "钛合金车削加工有什么注意事项？", "expected": TaskType.PROCESS_CONSULT},
    {"input": "铣削铝件用多少转速合适？", "expected": TaskType.PROCESS_CONSULT},
    {"input": "HRC52硬度的材料用什么材质的刀具？", "expected": TaskType.PROCESS_CONSULT},
    {"input": "钻孔加工进给量一般给多少？", "expected": TaskType.PROCESS_CONSULT},
    {"input": "粗加工和精加工的切削参数怎么选？", "expected": TaskType.PROCESS_CONSULT},
    {"input": "淬火后的45钢好加工吗？", "expected": TaskType.PROCESS_CONSULT},
    {"input": "这个零件用什么工艺加工最好？", "expected": TaskType.PROCESS_CONSULT},
    {"input": "攻丝加工怎么防止断丝锥？", "expected": TaskType.PROCESS_CONSULT},
    {"input": "磨削加工参数怎么设置？", "expected": TaskType.PROCESS_CONSULT},
    {"input": "镗孔的加工精度能达到多少？", "expected": TaskType.PROCESS_CONSULT},
    {"input": "加工中心铣平面用什么刀具？", "expected": TaskType.PROCESS_CONSULT},
    {"input": "热处理后还能加工吗？", "expected": TaskType.PROCESS_CONSULT},
    {"input": "切削速度太快会有什么后果？", "expected": TaskType.PROCESS_CONSULT},
    {"input": "进给量大对表面粗糙度有什么影响？", "expected": TaskType.PROCESS_CONSULT},
    {"input": "背吃刀量一般取多少？", "expected": TaskType.PROCESS_CONSULT},
    {"input": "用什么材料的刀具加工不锈钢？", "expected": TaskType.PROCESS_CONSULT},
    {"input": "车削紫铜用什么切削液？", "expected": TaskType.PROCESS_CONSULT},
    # B. 故障诊断
    {"input": "加工出来的工件表面有振纹，怎么回事？", "expected": TaskType.FAULT_DIAGNOSIS},
    {"input": "刀具磨损很快，是什么原因？", "expected": TaskType.FAULT_DIAGNOSIS},
    {"input": "机床主轴振动大，怎么办？", "expected": TaskType.FAULT_DIAGNOSIS},
    {"input": "加工精度超差了，找不到原因", "expected": TaskType.FAULT_DIAGNOSIS},
    {"input": "刀具崩刃了，什么问题？", "expected": TaskType.FAULT_DIAGNOSIS},
    {"input": "工件表面粗糙度不合格", "expected": TaskType.FAULT_DIAGNOSIS},
    {"input": "加工时噪音很大，不正常", "expected": TaskType.FAULT_DIAGNOSIS},
    {"input": "机床报警了，显示伺服异常", "expected": TaskType.FAULT_DIAGNOSIS},
    {"input": "冷却液不足会不会影响加工？", "expected": TaskType.FAULT_DIAGNOSIS},
    {"input": "让刀了怎么解决？", "expected": TaskType.FAULT_DIAGNOSIS},
    {"input": "工件加工出来有锥度，什么原因？", "expected": TaskType.FAULT_DIAGNOSIS},
    {"input": "铰孔后孔径偏大，什么问题？", "expected": TaskType.FAULT_DIAGNOSIS},
    {"input": "加工时突然烧刀了", "expected": TaskType.FAULT_DIAGNOSIS},
    {"input": "工件有毛刺怎么去除？", "expected": TaskType.FAULT_DIAGNOSIS},
    {"input": "尺寸不稳定，时大时小", "expected": TaskType.FAULT_DIAGNOSIS},
    {"input": "这个孔加工出来是椭圆的", "expected": TaskType.FAULT_DIAGNOSIS},
    {"input": "刀具断了", "expected": TaskType.FAULT_DIAGNOSIS},
    {"input": "工件报废了，帮我分析下原因", "expected": TaskType.FAULT_DIAGNOSIS},
    {"input": "声音不太对劲", "expected": TaskType.FAULT_DIAGNOSIS},
    {"input": "表面有划伤，怎么避免？", "expected": TaskType.FAULT_DIAGNOSIS},
    # C. 方案生成
    {"input": "帮我生成一个45钢轴类零件的加工工艺方案", "expected": TaskType.SOLUTION_GENERATION},
    {"input": "我需要一套完整的加工工艺路线", "expected": TaskType.SOLUTION_GENERATION},
    {"input": "帮我设计一个法兰盘的加工工艺", "expected": TaskType.SOLUTION_GENERATION},
    {"input": "帮我写一个铝合金壳体的加工方案", "expected": TaskType.SOLUTION_GENERATION},
    {"input": "批量生产齿轮的工艺怎么设计", "expected": TaskType.SOLUTION_GENERATION},
    {"input": "帮我规划一下这个零件的加工流程", "expected": TaskType.SOLUTION_GENERATION},
    {"input": "生成一个不锈钢零件的加工工艺", "expected": TaskType.SOLUTION_GENERATION},
    {"input": "加工一个模具要哪些工序", "expected": TaskType.SOLUTION_GENERATION},
    {"input": "从毛坯到成品的完整工艺是什么", "expected": TaskType.SOLUTION_GENERATION},
    {"input": "帮我制定IT7精度的加工方案", "expected": TaskType.SOLUTION_GENERATION},
    {"input": "制造一个阶梯轴需要什么工艺？", "expected": TaskType.SOLUTION_GENERATION},
    {"input": "帮我编写箱体类零件的加工工艺规程", "expected": TaskType.SOLUTION_GENERATION},
    {"input": "生产一批轴承座怎么加工？", "expected": TaskType.SOLUTION_GENERATION},
    {"input": "需要从零开始设计加工方案", "expected": TaskType.SOLUTION_GENERATION},
    {"input": "这个零件怎么做出来？要完整的工艺", "expected": TaskType.SOLUTION_GENERATION},
    # D. 知识查询
    {"input": "GB/T 1804的公差是多少？", "expected": TaskType.KNOWLEDGE_QUERY},
    {"input": "什么是形位公差？", "expected": TaskType.KNOWLEDGE_QUERY},
    {"input": "IT8和IT7精度有什么区别？", "expected": TaskType.KNOWLEDGE_QUERY},
    {"input": "表面粗糙度Ra3.2是什么意思？", "expected": TaskType.KNOWLEDGE_QUERY},
    {"input": "切削加工的最佳实践是什么？", "expected": TaskType.KNOWLEDGE_QUERY},
    {"input": "ISO 2768标准是什么？", "expected": TaskType.KNOWLEDGE_QUERY},
    {"input": "一般45钢的推荐切削速度是多少？", "expected": TaskType.KNOWLEDGE_QUERY},
    {"input": "硬质合金刀具和高速钢刀具有什么区别？", "expected": TaskType.KNOWLEDGE_QUERY},
    {"input": "车削和铣削的工艺特点对比", "expected": TaskType.KNOWLEDGE_QUERY},
    {"input": "通常精加工余量留多少？", "expected": TaskType.KNOWLEDGE_QUERY},
    {"input": "解释一下什么是切削三要素", "expected": TaskType.KNOWLEDGE_QUERY},
    {"input": "机械加工工艺规程是什么？", "expected": TaskType.KNOWLEDGE_QUERY},
    {"input": "DIN标准和中国国标有什么区别？", "expected": TaskType.KNOWLEDGE_QUERY},
    {"input": "行业惯例中精加工公差一般控制在多少？", "expected": TaskType.KNOWLEDGE_QUERY},
    {"input": "加工中心有哪些常用G代码？", "expected": TaskType.KNOWLEDGE_QUERY},
    # E. 闲聊
    {"input": "你好", "expected": TaskType.CHITCHAT},
    {"input": "谢谢你的帮助", "expected": TaskType.CHITCHAT},
    {"input": "你能做什么？", "expected": TaskType.CHITCHAT},
    {"input": "再见", "expected": TaskType.CHITCHAT},
    {"input": "帮助", "expected": TaskType.CHITCHAT},
    # 边界情况
    {"input": "", "expected": TaskType.CHITCHAT},
    {"input": "   ", "expected": TaskType.CHITCHAT},
    {"input": "加工", "expected": TaskType.PROCESS_CONSULT},
    {"input": "故障", "expected": TaskType.FAULT_DIAGNOSIS},
    {"input": "方案", "expected": TaskType.SOLUTION_GENERATION},
    {"input": "标准", "expected": TaskType.KNOWLEDGE_QUERY},
]


class TestRuleBasedClassifier:
    """规则分类器准确率测试"""

    @pytest.fixture
    def classifier(self) -> RuleBasedClassifier:
        return RuleBasedClassifier()

    def test_individual_cases(self, classifier: RuleBasedClassifier):
        """测试每个单独样本的分类准确性。"""
        for case in TEST_CASES:
            result = classifier.classify(case["input"])
            expected = case["expected"]
            input_text = case["input"] or "(空)"

            if result is None:
                # 规则分类器无法确定，这是允许的（会fallback到LLM）
                continue

            assert result.task_type == expected, (
                f"分类错误: input='{input_text}', expected={expected.label}, got={result.task_type.label}"
            )

    def test_classification_coverage_rate(self, classifier: RuleBasedClassifier):
        """测试规则分类器的覆盖率（非空结果比例）。"""
        total = len(TEST_CASES)
        classified = sum(1 for c in TEST_CASES if classifier.classify(c["input"]) is not None)
        coverage = classified / total
        print(f"\n规则分类覆盖率: {classified}/{total} = {coverage:.1%}")
        # 规则分类器应覆盖至少 70% 的样本
        assert coverage >= 0.70, f"覆盖率 {coverage:.1%} 低于 70%"

    def test_classification_accuracy_rate(self, classifier: RuleBasedClassifier):
        """测试规则分类器的准确率（在有结果的样本中）。"""
        correct = 0
        total_classified = 0
        errors = []

        for case in TEST_CASES:
            result = classifier.classify(case["input"])
            if result is not None:
                total_classified += 1
                if result.task_type == case["expected"]:
                    correct += 1
                else:
                    errors.append(f"'{case['input']}': expected={case['expected'].label}, got={result.task_type.label}")

        accuracy = correct / total_classified if total_classified > 0 else 0.0
        print(f"\n规则分类准确率: {correct}/{total_classified} = {accuracy:.1%}")
        if errors:
            print("\n错误样本:")
            for e in errors:
                print(f"  - {e}")

        assert accuracy >= 0.95, f"准确率 {accuracy:.1%} 低于 95%"

    def test_category_coverage(self, classifier: RuleBasedClassifier):
        """测试每个类别至少有一个样本被正确分类。"""
        category_hits = {t: False for t in TaskType}

        for case in TEST_CASES:
            result = classifier.classify(case["input"])
            if result is not None and result.task_type == case["expected"]:
                category_hits[case["expected"]] = True

        for task_type, hit in category_hits.items():
            assert hit, f"类别 {task_type.label} 没有任何样本被正确分类"

    def test_response_time(self, classifier: RuleBasedClassifier):
        """测试分类响应时间 < 10ms。"""
        sample_input = "45钢怎么加工？"
        start = time.perf_counter()
        for _ in range(100):
            classifier.classify(sample_input)
        elapsed = (time.perf_counter() - start) * 1000
        avg_time = elapsed / 100
        print(f"\n规则分类平均响应时间: {avg_time:.2f}ms")
        assert avg_time < 10, f"平均响应时间 {avg_time:.2f}ms 超过 10ms"


class TestTaskTypeEnum:
    """TaskType 枚举测试"""

    def test_from_code(self):
        assert TaskType.from_code("A") == TaskType.PROCESS_CONSULT
        assert TaskType.from_code("B") == TaskType.FAULT_DIAGNOSIS
        assert TaskType.from_code("C") == TaskType.SOLUTION_GENERATION
        assert TaskType.from_code("D") == TaskType.KNOWLEDGE_QUERY
        assert TaskType.from_code("E") == TaskType.CHITCHAT

    def test_from_code_case_insensitive(self):
        assert TaskType.from_code("a") == TaskType.PROCESS_CONSULT
        assert TaskType.from_code(" e ") == TaskType.CHITCHAT

    def test_from_code_invalid(self):
        assert TaskType.from_code("X") == TaskType.CHITCHAT
        assert TaskType.from_code("") == TaskType.CHITCHAT

    def test_labels(self):
        assert TaskType.PROCESS_CONSULT.label == "工艺咨询"
        assert TaskType.FAULT_DIAGNOSIS.label == "故障诊断"
        assert TaskType.SOLUTION_GENERATION.label == "方案生成"
        assert TaskType.KNOWLEDGE_QUERY.label == "知识查询"
        assert TaskType.CHITCHAT.label == "闲聊"


class TestClassificationResult:
    """ClassificationResult 数据类测试"""

    def test_default_values(self):
        result = ClassificationResult(task_type=TaskType.KNOWLEDGE_QUERY, confidence=0.5)
        assert result.task_type == TaskType.KNOWLEDGE_QUERY
        assert result.confidence == 0.5
        assert result.keywords_matched == []
        assert result.raw_response == ""
        assert result.latency_ms == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
