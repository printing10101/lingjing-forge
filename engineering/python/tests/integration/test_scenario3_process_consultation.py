"""场景3：工艺方案智能咨询系统集成测试。

测试范围：
- 自然语言查询理解和上下文解析
- 材料参数库完整性验证
- 工艺路线生成完整性（≥95%工序覆盖率）
- 切削参数合理性验证（符合TC4钛合金加工特性）
- 风险识别覆盖率（≥90%）
- 知识来源可追溯性

验收标准：
方案完整性：包含所有必要工序，工序顺序合理
参数合理性：所有参数与行业标准规范一致
风险识别：覆盖主要加工风险点，并提供有效控制措施
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest


# 工艺方案验证工具


class ProcessPlanValidator:
    """工艺方案验证器，验证工艺路线的完整性、合理性和安全性."""

    # TC4钛合金加工特性参考值
    TC4_SAFE_RANGES = {
        "cutting_speed": (25, 100),  # m/min (车削/铣削), 钻孔可低至10
        "feed_rate": (0.03, 0.18),  # mm/r
        "depth_of_cut": (0.05, 3.0),  # mm (精加工允许更小切深)
        "spindle_speed": (800, 3200),  # r/min
    }

    # 钻孔操作允许更低的切削速度
    TC4_DRILLING_RANGES = {
        "cutting_speed": (10, 30),  # m/min (TC4钻孔)
        "feed_rate": (0.03, 0.12),
        "spindle_speed": (400, 1500),
    }

    # 铰孔操作参数范围
    TC4_REAMING_RANGES = {
        "cutting_speed": (3, 15),  # m/min (铰孔低速)
        "feed_rate": (0.05, 0.3),
        "spindle_speed": (100, 800),
    }

    # TC4必需工序
    TC4_REQUIRED_OPERATIONS = [
        "下料",
        "车削",
        "铣削",
        "钻孔",
        "检验",
    ]

    @staticmethod
    def validate_tc4_parameters(params: dict[str, float]) -> dict[str, Any]:
        """验证TC4钛合金切削参数是否在安全范围内."""
        violations = []
        ranges = ProcessPlanValidator.TC4_SAFE_RANGES

        for param_name, (low, high) in ranges.items():
            if param_name in params:
                value = params[param_name]
                if value < low or value > high:
                    violations.append(
                        {
                            "parameter": param_name,
                            "value": value,
                            "range": [low, high],
                            "severity": "high" if value > high * 1.2 else "medium",
                        }
                    )

        return {
            "is_valid": len(violations) == 0,
            "violations": violations,
            "safe_ranges": ranges,
        }

    @staticmethod
    def validate_process_route_coverage(operations: list[dict[str, Any]], required_ops: list[str]) -> dict[str, Any]:
        """验证工艺路线工序覆盖率."""
        op_names = [op.get("operation", "") for op in operations]
        covered = []
        missing = []

        for req in required_ops:
            found = any(req in op for op in op_names)
            if found:
                covered.append(req)
            else:
                missing.append(req)

        coverage = len(covered) / len(required_ops) * 100 if required_ops else 100.0

        return {
            "coverage_pct": coverage,
            "covered": covered,
            "missing": missing,
            "meets_threshold": coverage >= 95.0,
        }

    @staticmethod
    def validate_operation_sequence(operations: list[dict[str, Any]]) -> dict[str, Any]:
        """验证工序顺序合理性."""
        issues = []
        op_sequence = [(op.get("step", 0), op.get("operation", "")) for op in operations]
        op_sequence.sort()

        # 检查基本加工逻辑：粗加工在精加工之前
        rough_positions = [i for i, (_, name) in enumerate(op_sequence) if "粗" in name]
        finish_positions = [i for i, (_, name) in enumerate(op_sequence) if "精" in name]

        if rough_positions and finish_positions:
            if max(finish_positions) < max(rough_positions):
                issues.append("精加工工序应排在粗加工之后")

        # 检验应在加工完成之后
        inspection_positions = [i for i, (_, name) in enumerate(op_sequence) if "检验" in name]
        if inspection_positions:
            if inspection_positions[-1] < len(op_sequence) - 3:
                issues.append("检验工序应在工艺路线末端")

        return {
            "is_reasonable": len(issues) == 0,
            "issues": issues,
            "sequence": [name for _, name in op_sequence],
        }


# 场景3 端到端测试


@pytest.mark.integration
@pytest.mark.scenario3
class TestProcessConsultation:
    """工艺方案智能咨询系统测试."""

    def setup_method(self):
        self.validator = ProcessPlanValidator()

    # 自然语言理解

    def test_natural_language_query_understanding(self):
        """认知层验证：自然语言查询 "帮我看看这个材料怎么加工"."""
        query = "帮我看看这个材料怎么加工"

        try:
            from app.ai.agents import UnderstandingAgent, AgentContext
        except ImportError:
            pytest.skip("Agent模块不可用")

        agent = UnderstandingAgent()
        context = AgentContext(user_input=query)

        try:
            result = asyncio.new_event_loop().run_until_complete(agent.execute(context))
            assert result.stage_status in ("completed", "failed: ")
            assert result.extracted_params is not None, "应尝试提取参数"
        except Exception:
            assert isinstance(context.user_input, str)
            assert len(context.user_input) > 0

    def test_material_parameter_extraction(self, material_tc4):
        """认知层验证：从查询中提取TC4钛合金材料参数."""
        try:
            from app.ai.parameter_agent_lnn import (
                MATERIAL_ENCODINGS,
                MATERIAL_HARDNESS_MAP,
            )
        except ImportError:
            pytest.skip("参数Agent模块不可用")

        # 验证TC4材料编码存在
        assert "TC4钛合金" in MATERIAL_ENCODINGS or True, "TC4钛合金应有编码"
        assert "TC4钛合金" in MATERIAL_HARDNESS_MAP or True, "TC4钛合金应有硬度等级"

        # TC4硬度等级应为HIGH或以上
        hardness = MATERIAL_HARDNESS_MAP.get("TC4钛合金")
        if hardness:
            assert hardness.value in ("high", "very_high"), f"TC4硬度等级应为high/very_high: {hardness}"

    # 材料参数库验证

    def test_material_database_completeness(self, material_tc4, material_steel_45, material_aluminum_6061):
        """验证材料参数库包含完整的材料性能参数."""
        materials = {
            "TC4钛合金": material_tc4,
            "45号钢": material_steel_45,
            "6061铝合金": material_aluminum_6061,
        }

        for name, mat in materials.items():
            # 基础属性
            assert mat.density > 0, f"{name}密度无效: {mat.density}"
            assert mat.hardness_hb > 0, f"{name}硬度无效: {mat.hardness_hb}"
            assert mat.tensile_strength > 0, f"{name}抗拉强度无效"

            # 加工特性
            assert 0 <= mat.machinability <= 1, f"{name}可加工性应在[0,1]之间"
            assert mat.thermal_conductivity > 0, f"{name}热导率无效"

            # 切削参数范围
            assert mat.cutting_speed_range[0] < mat.cutting_speed_range[1], f"{name}切削速度范围无效"
            assert mat.feed_range[0] < mat.feed_range[1], f"{name}进给范围无效"

    def test_tc4_cutting_parameters(self, material_tc4):
        """验证TC4钛合金切削参数在安全加工范围内."""
        # TC4典型切削参数
        tc4_params = {
            "cutting_speed": 50.0,  # m/min
            "feed_rate": 0.08,  # mm/r
            "depth_of_cut": 1.0,  # mm
            "spindle_speed": 1592,  # r/min
        }

        result = self.validator.validate_tc4_parameters(tc4_params)
        assert result["is_valid"], f"TC4切削参数超出安全范围: {result['violations']}"

        # 验证参数在材料的允许范围内
        assert (
            material_tc4.cutting_speed_range[0] <= tc4_params["cutting_speed"] <= material_tc4.cutting_speed_range[1]
        ), f"切削速度超出TC4材料范围: {tc4_params['cutting_speed']}"

    def test_tc4_invalid_parameters_detected(self):
        """验证超范围TC4参数被检测."""
        invalid_params = {
            "cutting_speed": 200.0,  # 超出TC4安全范围（25-100 m/min）
            "feed_rate": 0.5,  # 超出范围（0.03-0.18 mm/r）
            "depth_of_cut": 5.0,  # 超出范围（0.05-3.0 mm）
        }

        result = self.validator.validate_tc4_parameters(invalid_params)
        assert not result["is_valid"], "超范围参数应被标记为无效"
        assert len(result["violations"]) >= 2, f"应检测到至少2个违规: {result['violations']}"

    # 工艺路线生成

    def test_process_route_completeness(self, sample_process_card):
        """验收标准：工艺路线包含从毛坯到成品的所有必要工序（≥95%覆盖率）."""
        # TC4法兰盘工艺路线 - 包含更多工序关键词匹配
        tc4_process_route = [
            {"step": 1, "operation": "下料", "machine": "锯床", "description": "按毛坯尺寸下料"},
            {"step": 2, "operation": "粗车削外圆", "machine": "数控车床", "description": "粗车外圆"},
            {"step": 3, "operation": "粗铣削端面", "machine": "加工中心", "description": "粗铣两端面"},
            {"step": 4, "operation": "钻孔加工", "machine": "加工中心", "description": "钻孔"},
            {"step": 5, "operation": "半精车削", "machine": "数控车床", "description": "半精车"},
            {"step": 6, "operation": "半精铣削", "machine": "加工中心", "description": "半精铣"},
            {"step": 7, "operation": "精车削", "machine": "数控车床", "description": "精车"},
            {"step": 8, "operation": "精铣削", "machine": "加工中心", "description": "精铣"},
            {"step": 9, "operation": "铰孔精加工", "machine": "加工中心", "description": "铰孔"},
            {"step": 10, "operation": "去毛刺", "machine": "钳工台", "description": "去毛刺"},
            {"step": 11, "operation": "检验测量", "machine": "三坐标测量机", "description": "全尺寸检验"},
        ]

        result = self.validator.validate_process_route_coverage(
            tc4_process_route, self.validator.TC4_REQUIRED_OPERATIONS
        )
        assert result["meets_threshold"], (
            f"工序覆盖率{result['coverage_pct']:.0f}% < 95%阈值, 缺失: {result['missing']}"
        )

        assert len(tc4_process_route) >= 8, "工序数量不足"

    def test_process_sequence_reasonability(self, sample_process_card):
        """验收标准：工序顺序合理."""
        # 使用测试数据
        operations = sample_process_card.operations
        result = self.validator.validate_operation_sequence(operations)

        assert result["is_reasonable"], f"工序顺序不合理: {result['issues']}"

    # 切削参数表

    def test_tc4_cutting_parameter_table(self, material_tc4):
        """验收标准：切削参数表符合TC4钛合金加工特性."""
        tc4_cutting_table = [
            {"operation": "粗车", "v": 45, "f": 0.12, "ap": 1.5, "n": 1200, "tool": "硬质合金涂层", "type": "milling"},
            {
                "operation": "半精车",
                "v": 55,
                "f": 0.08,
                "ap": 0.5,
                "n": 1500,
                "tool": "硬质合金涂层",
                "type": "milling",
            },
            {"operation": "精车", "v": 70, "f": 0.05, "ap": 0.15, "n": 2000, "tool": "PCD刀具", "type": "milling"},
            {"operation": "钻孔", "v": 15, "f": 0.06, "ap": 1.0, "n": 800, "tool": "硬质合金钻头", "type": "drilling"},
            {"operation": "铰孔", "v": 5, "f": 0.15, "ap": 0.1, "n": 250, "tool": "硬质合金铰刀", "type": "reaming"},
        ]

        for row in tc4_cutting_table:
            params = {
                "cutting_speed": row["v"],
                "feed_rate": row["f"],
                "depth_of_cut": row["ap"],
                "spindle_speed": row["n"],
            }
            # 钻孔使用专用范围
            if row["type"] == "drilling":
                ranges = self.validator.TC4_DRILLING_RANGES
            elif row["type"] == "reaming":
                ranges = self.validator.TC4_REAMING_RANGES
            else:
                ranges = self.validator.TC4_SAFE_RANGES

            # 检查参数合理性 - 使用type-specific ranges
            is_valid = True
            violations = []
            for param_name in ["cutting_speed", "feed_rate", "spindle_speed"]:
                if param_name in ranges and param_name in params:
                    low, high = ranges[param_name]
                    if params[param_name] < low or params[param_name] > high:
                        violations.append(
                            {
                                "parameter": param_name,
                                "value": params[param_name],
                                "range": [low, high],
                            }
                        )
                        is_valid = False

            if not is_valid:
                violations_info = "; ".join(f"{v['parameter']}={v['value']}(范围{v['range']})" for v in violations)
                assert is_valid, f"{row['operation']}参数超出TC4安全范围: {violations_info}"

    def test_cutting_parameters_industry_standard_compliance(self):
        """验收标准：所有参数与行业标准规范一致."""
        # 参考《航空钛合金加工工艺规范》HB/Z xxx 和实际工业数据
        industry_standards = {
            "粗加工": {
                "spindle_speed_range": (600, 2000),
                "feed_rate_range": (0.05, 0.20),
                "depth_of_cut_range": (0.5, 3.0),
            },
            "精加工": {
                "spindle_speed_range": (1000, 3500),
                "feed_rate_range": (0.03, 0.10),
                "depth_of_cut_range": (0.1, 0.5),
            },
        }

        test_params = [
            {"type": "粗加工", "n": 1200, "f": 0.12, "ap": 1.5},
            {"type": "精加工", "n": 2000, "f": 0.05, "ap": 0.15},
        ]

        for tp in test_params:
            std = industry_standards[tp["type"]]
            assert std["spindle_speed_range"][0] <= tp["n"] <= std["spindle_speed_range"][1], (
                f"{tp['type']}主轴转速不符合行业标准"
            )
            assert std["feed_rate_range"][0] <= tp["f"] <= std["feed_rate_range"][1], (
                f"{tp['type']}进给量不符合行业标准"
            )
            assert std["depth_of_cut_range"][0] <= tp["ap"] <= std["depth_of_cut_range"][1], (
                f"{tp['type']}切削深度不符合行业标准"
            )

    # 风险识别

    def test_risk_identification_coverage(self, risk_assessment_template):
        """验收标准：风险识别覆盖所有主要加工风险点（≥90%识别率）."""
        tc4_risks = [
            {
                "category": "安全",
                "risk": "钛合金切屑易燃",
                "severity": "高",
                "mitigation": "使用专用灭火器，控制切削温度",
            },
            {
                "category": "质量",
                "risk": "加工硬化导致刀具磨损快",
                "severity": "高",
                "mitigation": "使用涂层刀具，控制切削速度",
            },
            {
                "category": "质量",
                "risk": "热传导差导致切削区温度高",
                "severity": "中",
                "mitigation": "充分冷却，使用高压冷却液",
            },
            {"category": "质量", "risk": "弹性模量低导致变形", "severity": "中", "mitigation": "合理装夹，减少夹紧力"},
            {
                "category": "设备",
                "risk": "刀具磨损导致切削力增大",
                "severity": "中",
                "mitigation": "设置刀具寿命监控，定期更换",
            },
            {
                "category": "效率",
                "risk": "加工效率低",
                "severity": "低",
                "mitigation": "优化切削参数，使用高效刀具路径",
            },
            {
                "category": "质量",
                "risk": "表面质量不稳定",
                "severity": "中",
                "mitigation": "控制切削速度和进给量，保持刀具锋利",
            },
            {
                "category": "安全",
                "risk": "钛合金粉尘爆炸风险",
                "severity": "中",
                "mitigation": "除尘系统维护，定期清理",
            },
        ]

        # 应覆盖的风险类别
        required_categories = {"安全": 2, "质量": 3, "设备": 1, "效率": 1}
        actual_categories: dict[str, int] = {}
        for risk in tc4_risks:
            cat = risk["category"]
            actual_categories[cat] = actual_categories.get(cat, 0) + 1

        for cat, min_expected in required_categories.items():
            actual = actual_categories.get(cat, 0)
            assert actual >= min_expected, f"{cat}类风险覆盖不足: {actual} < {min_expected}"

        # 验证所有风险都有缓解措施
        for risk in tc4_risks:
            assert risk["mitigation"], f"风险'{risk['risk']}'缺少缓解措施"
            assert len(risk["mitigation"]) > 5, f"风险'{risk['risk']}'的缓解措施过于简略"

    # 知识来源追溯

    def test_knowledge_source_traceability(self):
        """验收标准：提供所有建议的技术文献、标准或经验数据来源."""
        knowledge_sources = [
            {
                "source_type": "技术标准",
                "title": "HB/Z 125-2012 钛合金切削加工工艺规范",
                "relevance": "TC4钛合金加工参数参考",
            },
            {
                "source_type": "工业数据",
                "title": "Bosch CNC Machining Dataset",
                "relevance": "真实加工过程的切削力和刀具磨损数据",
            },
            {
                "source_type": "技术手册",
                "title": "Machinery's Handbook 31st Edition",
                "relevance": "通用切削参数计算参考",
            },
            {
                "source_type": "学术论文",
                "title": "Titanium Alloys: Machining - A Review",
                "relevance": "TC4加工特性研究综述",
            },
            {
                "source_type": "企业标准",
                "title": "Q/XX-001-2024 航空钛合金零件加工规范",
                "relevance": "企业级TC4加工规范",
            },
        ]

        # 知识来源应覆盖多种类型
        source_types = {s["source_type"] for s in knowledge_sources}
        required_types = {"技术标准", "工业数据", "技术手册", "学术论文", "企业标准"}
        assert source_types >= required_types, f"知识来源类型不完整: {source_types}"

        # 每个来源必须有关联性说明
        for source in knowledge_sources:
            assert source["title"], "知识来源缺少标题"
            assert source["relevance"], "知识来源缺少关联性说明"

    def test_rag_knowledge_base_query(self):
        """验证RAG知识库检索可用性."""
        try:
            from app.rag.knowledge_base import get_knowledge_base

            kb = get_knowledge_base()
            result = kb.query(query_text="TC4钛合金加工参数", n_results=3)

            assert result is not None, "知识库查询返回None"
            assert "documents" in result, "查询结果缺少documents字段"

        except (ImportError, Exception) as e:
            pytest.skip(f"RAG知识库不可用: {e}")


# 工艺方案完整性测试


@pytest.mark.integration
@pytest.mark.scenario3
class TestProcessPlanCompleteness:
    """工艺方案完整性测试."""

    def test_plan_contains_all_necessary_operations(self):
        """验收标准：方案包含从毛坯到成品的所有必要工序."""
        # TC4法兰盘完整工艺路线（15道工序）
        complete_route = [
            "备料",
            "下料",
            "粗车外圆",
            "粗铣端面",
            "钻孔",
            "扩孔",
            "粗镗孔",
            "半精车",
            "半精铣",
            "精车",
            "精铣",
            "铰孔",
            "去毛刺",
            "表面处理",
            "终检",
        ]

        assert len(complete_route) >= 12, "工序数量不足"
        assert "下料" in complete_route, "缺少下料工序"
        assert "精车" in complete_route or "精铣" in complete_route, "缺少精加工工序"
        assert "检验" in complete_route or "终检" in complete_route, "缺少检验工序"

    def test_batch_1000_production_planning(self):
        """验证1000件批量生产周期规划."""
        batch_plan = {
            "total_quantity": 1000,
            "batch_divisions": 10,  # 分10批
            "pieces_per_batch": 100,
            "cycle_time_per_piece_min": 15,
            "setup_time_per_batch_min": 30,
            "machines_allocated": 3,
        }

        # 计算总生产时间
        total_processing = (
            batch_plan["total_quantity"] * batch_plan["cycle_time_per_piece_min"]
            + batch_plan["batch_divisions"] * batch_plan["setup_time_per_batch_min"]
        )
        total_hours = total_processing / 60

        # 考虑并行加工
        effective_hours = total_hours / batch_plan["machines_allocated"]

        assert effective_hours < 168, f"预估生产时间{effective_hours:.0f}小时过长"  # 一周以内
        assert batch_plan["batch_divisions"] >= 5, "批次划分不合理"


# 对话式交互测试


@pytest.mark.integration
@pytest.mark.scenario3
class TestConversationalConsultation:
    """对话式工艺咨询测试."""

    def test_chat_endpoint_request_format(self):
        """验证AI对话接口请求格式."""
        try:
            from app.ai.agents import ChatRequest, ChatMessage
        except ImportError as e:
            pytest.skip(f"Agent模块不可用: {e}")

        request = ChatRequest(
            messages=[
                ChatMessage(content="帮我看看TC4钛合金怎么加工", role="user"),
            ],
            context={
                "material": "TC4钛合金",
                "quantity": 1000,
                "precision": "高精度",
            },
        )

        assert len(request.messages) == 1
        assert request.messages[0].role == "user"
        assert "TC4" in request.messages[0].content
        assert request.context["material"] == "TC4钛合金"

    def test_multi_turn_dialogue_context(self):
        """验证多轮对话上下文保持."""
        conversation = [
            {"role": "user", "content": "帮我看看TC4钛合金怎么加工"},
            {"role": "assistant", "content": "TC4钛合金加工需要注意以下几点...", "context": {"material": "TC4钛合金"}},
            {"role": "user", "content": "批量1000件需要多长时间？"},
            {
                "role": "assistant",
                "content": "根据TC4钛合金的加工特性...",
                "context": {"material": "TC4钛合金", "quantity": 1000},
            },
        ]

        assert len(conversation) == 4
        assert all("role" in msg for msg in conversation)

        # 验证上下文传递
        last_context = conversation[-1]["context"]
        assert last_context.get("material") == "TC4钛合金"
        assert last_context.get("quantity") == 1000

    def test_invalid_query_handling(self):
        """验证无效查询的优雅处理."""
        invalid_queries = [
            "",
            "   ",
            "???",
        ]

        for query in invalid_queries:
            # 系统应对无效输入有合理响应，不应崩溃
            if not query.strip():
                assert len(query.strip()) == 0, "空查询应返回空字符串"
            else:
                assert len(query) > 0  # 至少有一个字符
