"""T3 补测（三）：抽取校验器、规则解析器、健康检查与报告。"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest

from app.knowledge_graph.extractor.validator import ExtractionValidator
from app.knowledge_graph.graph_store import GraphStore
from app.knowledge_graph.health.checker import (
    ContradictoryEdgeResult,
    HealthChecker,
    HealthCheckResult,
    IsolatedNodeResult,
    StaleNodeResult,
)
from app.knowledge_graph.health.report import HealthReportGenerator
from app.knowledge_graph.importer.rule_parser import RuleParser

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------- validator


def _valid_payload() -> dict:
    return {
        "entities": [
            {"id": "m1", "entity_type": "material", "name": "45钢", "confidence": 95},
            {"id": "t1", "entity_type": "tool", "name": "D10 立铣刀", "confidence": 90},
        ],
        "relations": [
            {"source_id": "t1", "target_id": "m1", "relation_type": "SUITABLE_FOR", "confidence": 88},
        ],
    }


def test_validator_accepts_clean_payload():
    report = ExtractionValidator().validate(_valid_payload())
    assert report.overall_valid is True
    assert report.accuracy_score == 100.0
    summary = report.summary()
    assert summary["total_entities"] == 2
    assert summary["overall_pass_rate"] == 1.0
    assert "优秀" in summary["recommendation"]


def test_validator_flags_missing_fields_and_confidence_range():
    validator = ExtractionValidator()
    payload = {
        "entities": [
            {"name": "无 ID"},
            {"id": "e2", "entity_type": "material", "name": "坏置信度", "confidence": 150},
        ],
        "relations": [
            {"target_id": "m1", "relation_type": "SUITABLE_FOR"},
            {"source_id": "t1", "relation_type": "SUITABLE_FOR"},
            {"source_id": "t1", "target_id": "m1"},
            {"source_id": "t1", "target_id": "m1", "relation_type": "SUITABLE_FOR", "confidence": "high"},
            {"source_id": "t1", "target_id": "m1", "relation_type": "SUITABLE_FOR", "confidence": 120},
        ],
    }
    report = validator.validate(payload)
    assert report.overall_valid is False
    relation_errors = [error for result in report.relation_results for error in result.errors]
    assert any("source_id" in error for error in relation_errors)
    assert any("target_id" in error for error in relation_errors)
    assert any("relation_type" in error for error in relation_errors)
    assert any("必须为数值" in error for error in relation_errors)
    assert any("[0, 100]" in error for error in relation_errors)
    assert report.accuracy_score < 60


def test_validator_detects_dangling_relation_and_duplicate_ids():
    payload = {
        "entities": [
            {"id": "m1", "entity_type": "material", "name": "45钢", "confidence": 90},
            {"id": "m1", "entity_type": "material", "name": "重复", "confidence": 90},
        ],
        "relations": [
            {"source_id": "ghost", "target_id": "m1", "relation_type": "SUITABLE_FOR", "confidence": 80},
        ],
    }
    report = ExtractionValidator().validate(payload)
    # 悬挂关系按一致性扣分（-2/条），不直接判废；此处断言问题被识别
    assert report.consistency_issues
    assert any("ghost" in issue for issue in report.consistency_issues)
    assert report.accuracy_score < 100


def test_validator_to_dict_serializes_report():
    validator = ExtractionValidator()
    payload = validator.to_dict(validator.validate(_valid_payload()))
    assert payload["summary"]["overall_valid"] is True
    assert len(payload["entity_results"]) == 2
    assert payload["relation_results"][0]["is_valid"] is True


# --------------------------------------------------------------- rule parser


def test_rule_parser_parses_rule_and_extracts_features():
    parser = RuleParser()
    rule = parser.parse_single_rule({
        "id": "R-001",
        "name": "钻孔前先铣基准面",
        "category": "sequence",
        "description": "先加工基准面，再钻孔；型腔随后加工",
        "details": {"rationale": "保证孔位精度，避免槽与孔干涉"},
    })
    assert rule.process_id == "R-001"
    assert rule.process_category == "sequence"
    assert rule.confidence == 0.9 and rule.source == "rule"
    names = {feature.name for feature in rule.features}
    assert {"基准面", "孔"} <= names
    assert rule.has_features() is True


def test_rule_parser_rejects_invalid_input():
    parser = RuleParser()
    with pytest.raises(TypeError):
        parser.parse_single_rule("not-a-dict")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        parser.parse_single_rule({"name": "无 id 规则"})


def test_rule_parser_file_and_no_feature_rule():
    parser = RuleParser()
    rules = parser.parse_rules_file([
        {"id": "R-002", "name": "高速钢刀具选择", "description": "按材料硬度选刀"},
        {"id": "R-003", "name": "空规则"},
    ])
    assert [rule.process_id for rule in rules] == ["R-002", "R-003"]
    assert rules[1].has_features() is False


# ------------------------------------------------------------- health check


@pytest.fixture()
def health_store() -> GraphStore:
    graph = GraphStore(auto_load=False)
    graph.add_node("material", "material-45steel", {"name": "45钢"})
    graph.add_node("tool", "tool-endmill-d10", {"name": "D10"})
    graph.add_node("tool", "tool-orphan", {"name": "孤立刀具"})
    graph.add_edge("tool-endmill-d10", "material-45steel", "SUITABLE_FOR", {"confidence": 0.9})
    graph.add_edge("material-45steel", "tool-endmill-d10", "REVERSE_TEST", {"confidence": 0.4})
    old = (datetime.now(timezone.utc) - timedelta(days=365 * 8)).isoformat()
    graph.add_node("tool", "tool-stale", {"name": "老旧刀具", "updated_at": old})
    graph.add_edge("tool-stale", "material-45steel", "SUITABLE_FOR", {"confidence": 0.5})
    return graph


def test_health_checker_isolated_nodes(health_store: GraphStore):
    results = HealthChecker(health_store).check_isolated_nodes()
    by_id = {item.node_id: item for item in results}
    assert by_id["tool-orphan"].reason == "no_edges"
    assert "tool-endmill-d10" not in by_id


def test_health_checker_contradictory_edges(health_store: GraphStore):
    results = HealthChecker(health_store).check_contradictory_edges()
    pairs = {(item.source_id, item.target_id) for item in results}
    assert ("material-45steel", "tool-endmill-d10") in pairs or \
           ("tool-endmill-d10", "material-45steel") in pairs


def test_health_checker_stale_nodes(health_store: GraphStore):
    results = HealthChecker(health_store).check_stale_nodes(threshold_years=5)
    assert any(item.node_id == "tool-stale" and item.age_days > 365 * 7 for item in results)


def test_health_checker_run_all_checks(health_store: GraphStore):
    result = HealthChecker(health_store).run_all_checks()
    assert result.total_nodes >= 4
    assert result.total_edges >= 2
    assert result.issue_count >= 2
    assert result.check_duration_seconds >= 0


def test_health_report_renders_markdown(health_store: GraphStore):
    result = HealthChecker(health_store).run_all_checks()
    report = HealthReportGenerator().generate(result)
    assert "知识图谱健康" in report
    assert "孤立" in report
    buffer = io.StringIO()
    HealthReportGenerator().write(result, buffer)
    assert buffer.getvalue() == report


def test_health_report_handles_empty_result():
    empty = HealthCheckResult()
    report = HealthReportGenerator().generate(empty)
    assert "0" in report
    assert isinstance(empty.issue_count, int)
    assert empty.issue_count == 0
    # 数据类字段可独立构造（报告生成需兼容空列表）
    custom = HealthCheckResult(
        isolated_nodes=[IsolatedNodeResult(node_id="material-x", node_type="material", reason="no_edges")],
        contradictory_edges=[ContradictoryEdgeResult(
            source_id="a-1", target_id="b-1", edge_type_forward="X", edge_type_reverse="Y")],
        stale_nodes=[StaleNodeResult(node_id="c-1", node_type="tool", last_updated=None,
                                     age_days=2000, threshold_years=5)],
        total_nodes=3, total_edges=2)
    assert custom.issue_count == 3
    assert "material-x" in HealthReportGenerator().generate(custom)
