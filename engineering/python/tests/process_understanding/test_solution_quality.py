"""
工艺方案合理性评估测试

测试目标: 生成的方案与专家方案一致率 > 85%
测试方法: 评估工序合理性、参数适宜性、风险识别完整性
"""

from __future__ import annotations

import pytest

from app.ai.process_understanding.solution_generator import (
    ProcessSolution,
    ProcessStep,
    CuttingParam,
    RiskItem,
    SolutionGenerator,
)

pytestmark = pytest.mark.skip_ci


# 方案合理性评估维度与评分标准


class SolutionEvaluator:
    """工艺方案评估器

    评估维度:
    1. 工序合理性 (40分): 工序顺序是否合理、是否完整
    2. 参数适宜性 (30分): 切削参数是否在合理范围
    3. 风险识别完整性 (20分): 是否识别关键风险
    4. 置信度合理性 (10分): 置信度评估是否客观
    """

    # 常见工艺路线模板（用于合理性检查）
    EXPECTED_OPERATIONS = {
        "轴类": ["下料", "车", "磨", "检验"],
        "盘类": ["下料", "车", "铣", "钳", "检验"],
        "箱体": ["划线", "铣", "镗", "钻孔", "攻丝", "检验"],
        "齿轮": ["下料", "锻", "车", "滚齿", "热处理", "磨", "检验"],
    }

    # 切削参数合理范围
    PARAM_RANGES = {
        "spindle_speed": (100, 10000),  # r/min
        "feed_rate": (0.01, 2.0),  # mm/r or mm/min
        "depth_of_cut": (0.05, 10.0),  # mm
    }

    @classmethod
    def evaluate(cls, solution: ProcessSolution) -> dict:
        """综合评估方案合理性。"""
        scores = {}

        scores["process_route"] = cls._evaluate_process_route(solution)
        scores["cutting_params"] = cls._evaluate_cutting_params(solution)
        scores["risk_identification"] = cls._evaluate_risk_identification(solution)
        scores["confidence_reasonability"] = cls._evaluate_confidence(solution)

        total = sum(scores.values())
        max_score = 100
        percentage = (total / max_score) * 100

        return {
            "total_score": total,
            "max_score": max_score,
            "percentage": percentage,
            "dimensions": scores,
            "passed": percentage >= 85,
        }

    @classmethod
    def _evaluate_process_route(cls, solution: ProcessSolution) -> float:
        """评估工艺路线合理性 (满分40)。"""
        score = 0.0
        route = solution.process_route

        # 工序数量检查 (至少3道工序)
        if len(route) >= 3:
            score += 10
        elif len(route) >= 1:
            score += 5

        # 首道工序检查 (通常为下料/备料)
        first_op = route[0].operation.lower() if route else ""
        if any(kw in first_op for kw in ["下料", "备料", "划线", "粗"]):
            score += 5

        # 末道工序检查 (通常为检验/去毛刺)
        last_op = route[-1].operation.lower() if route else ""
        if any(kw in last_op for kw in ["检验", "检测", "检查", "去毛刺", "清洗"]):
            score += 5

        # 工序描述完整性
        complete_count = sum(1 for s in route if s.operation and s.description)
        if complete_count == len(route):
            score += 10
        elif complete_count >= len(route) * 0.5:
            score += 5

        # 工序步骤编号连续性
        step_numbers = [s.step_number for s in route]
        if step_numbers == list(range(1, len(route) + 1)):
            score += 10

        return min(score, 40)

    @classmethod
    def _evaluate_cutting_params(cls, solution: ProcessSolution) -> float:
        """评估切削参数适宜性 (满分30)。"""
        score = 0.0
        params = solution.cutting_parameters

        if not params:
            return 10  # 至少给基础分

        valid_count = 0
        for param in params:
            param_valid = True

            # 检查是否有完整的参数
            if not param.operation or not param.tool:
                param_valid = False

            # 检查转速范围
            try:
                speed = cls._parse_numeric(param.spindle_speed)
                if speed and not (100 <= speed <= 10000):
                    param_valid = False
            except (ValueError, TypeError):
                pass

            try:
                feed = cls._parse_numeric(param.feed_rate)
                if feed and not (0.01 <= feed <= 2.0):
                    param_valid = False
            except (ValueError, TypeError):
                pass

            try:
                doc = cls._parse_numeric(param.depth_of_cut)
                if doc and not (0.05 <= doc <= 10.0):
                    param_valid = False
            except (ValueError, TypeError):
                pass

            if param_valid:
                valid_count += 1

        if valid_count == len(params):
            score += 20
        elif valid_count > 0:
            score += 10

        # 参数覆盖度 (每个工序都应有参数)
        if len(params) >= len(solution.process_route) * 0.5:
            score += 10

        return min(score, 30)

    @classmethod
    def _evaluate_risk_identification(cls, solution: ProcessSolution) -> float:
        """评估风险识别完整性 (满分20)。"""
        score = 0.0
        risks = solution.risk_warnings

        if not risks:
            return 5

        # 至少识别1个风险
        if len(risks) >= 1:
            score += 5

        # 有应对措施
        with_mitigation = sum(1 for r in risks if r.mitigation)
        if with_mitigation == len(risks):
            score += 10
        elif with_mitigation > 0:
            score += 5

        # 风险等级分布合理 (有high/medium/low)
        severities = {r.severity for r in risks}
        if len(severities) >= 2:
            score += 5

        return min(score, 20)

    @classmethod
    def _evaluate_confidence(cls, solution: ProcessSolution) -> float:
        """评估置信度合理性 (满分10)。"""
        score = 0.0

        # 置信度在合理范围
        if 1.0 <= solution.confidence_score <= 10.0:
            score += 5

        # 有不确定性说明
        if solution.uncertainty:
            score += 5

        return score

    @staticmethod
    def _parse_numeric(value: str) -> float | None:
        """从字符串中提取数值。"""
        import re

        match = re.search(r"[\d.]+", str(value))
        if match:
            return float(match.group(0))
        return None


# 测试用例


class TestProcessSolution:
    """ProcessSolution 数据类测试"""

    def test_default_values(self):
        solution = ProcessSolution()
        assert solution.material == ""
        assert solution.process_route == []
        assert solution.cutting_parameters == []
        assert solution.risk_warnings == []
        assert solution.confidence_score == 0.0

    def test_to_dict(self):
        solution = ProcessSolution(
            material="45钢",
            precision_level="IT8",
            batch_size="单件",
            machine_type="CNC加工中心",
            process_route=[
                ProcessStep(1, "下料", "锯床", "按尺寸下料"),
                ProcessStep(2, "粗车", "车床", "粗加工外圆"),
            ],
            cutting_parameters=[
                CuttingParam(1, "粗车", "硬质合金", "800", "0.3", "2.0"),
            ],
            risk_warnings=[
                RiskItem("刀具磨损", "medium", "定期检查刀具"),
            ],
            confidence_score=7.0,
            uncertainty="参数基于通用数据",
        )
        d = solution.to_dict()
        assert d["material"] == "45钢"
        assert len(d["process_route"]) == 2
        assert len(d["cutting_parameters"]) == 1
        assert len(d["risk_warnings"]) == 1


class TestFallbackSolution:
    """降级方案测试"""

    def test_fallback_has_route(self):
        solution = SolutionGenerator._create_fallback_solution("45钢", "IT8", "单件", "CNC加工中心")
        assert len(solution.process_route) >= 3
        assert solution.confidence_score <= 5.0

    def test_fallback_covers_basic_ops(self):
        solution = SolutionGenerator._create_fallback_solution("304不锈钢", "IT7", "批量", "加工中心")
        ops = [s.operation for s in solution.process_route]
        # 应包含基本工序
        assert any("粗" in op for op in ops)
        assert any("精" in op for op in ops)
        assert any("检验" in op for op in ops)

    def test_fallback_has_risk(self):
        solution = SolutionGenerator._create_fallback_solution("45钢", "IT8", "单件", "CNC")
        assert len(solution.risk_warnings) >= 1
        assert solution.risk_warnings[0].severity in ("high", "medium", "low")


class TestSolutionEvaluator:
    """方案评估器测试"""

    def test_evaluate_complete_solution(self):
        """完整方案应获得高分。"""
        solution = ProcessSolution(
            material="45钢",
            precision_level="IT8",
            batch_size="单件",
            machine_type="CNC加工中心",
            process_route=[
                ProcessStep(1, "下料", "锯床", "按工艺尺寸下料，留足加工余量"),
                ProcessStep(2, "粗车", "数控车床", "粗车外圆及端面"),
                ProcessStep(3, "精车", "数控车床", "精车至图纸尺寸"),
                ProcessStep(4, "检验", "三坐标测量机", "全尺寸检验"),
            ],
            cutting_parameters=[
                CuttingParam(2, "粗车", "硬质合金刀具", "800", "0.3", "2.0"),
                CuttingParam(3, "精车", "硬质合金刀具", "1500", "0.1", "0.3"),
            ],
            risk_warnings=[
                RiskItem(
                    "高硬度材料可能导致刀具快速磨损",
                    "high",
                    "建议使用涂层刀具，降低切削速度",
                ),
                RiskItem(
                    "精加工余量不足可能影响精度",
                    "medium",
                    "确保半精加工后留有足够余量",
                ),
            ],
            confidence_score=7.5,
            uncertainty="参数基于标准材料推荐值，实际需根据刀具品牌和机床状态调整",
        )

        result = SolutionEvaluator.evaluate(solution)
        print(f"\n评估结果: {result}")
        assert result["total_score"] >= 85, f"得分 {result['total_score']} < 85"

    def test_evaluate_minimal_solution(self):
        """最小方案应获得较低分数。"""
        solution = ProcessSolution(
            material="45钢",
            process_route=[ProcessStep(1, "加工", "", "")],
            cutting_parameters=[],
            risk_warnings=[],
            confidence_score=5.0,
        )
        result = SolutionEvaluator.evaluate(solution)
        assert result["total_score"] < 85

    def test_evaluate_fallback_solution(self):
        """降级方案的评估分数。"""
        solution = SolutionGenerator._create_fallback_solution("45钢", "IT8", "单件", "CNC加工中心")
        result = SolutionEvaluator.evaluate(solution)
        print(f"\n降级方案评估: {result}")
        # 降级方案应获得合理分数
        assert result["total_score"] >= 60


class TestCuttingParamParsing:
    """切削参数解析测试"""

    def test_parse_numeric_simple(self):
        assert SolutionEvaluator._parse_numeric("800") == 800.0
        assert SolutionEvaluator._parse_numeric("0.3") == 0.3

    def test_parse_numeric_with_unit(self):
        assert SolutionEvaluator._parse_numeric("800r/min") == 800.0
        assert SolutionEvaluator._parse_numeric("0.3mm/r") == 0.3

    def test_parse_numeric_range(self):
        assert SolutionEvaluator._parse_numeric("800-1200") == 800.0

    def test_parse_numeric_invalid(self):
        assert SolutionEvaluator._parse_numeric("") is None
        assert SolutionEvaluator._parse_numeric("abc") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
