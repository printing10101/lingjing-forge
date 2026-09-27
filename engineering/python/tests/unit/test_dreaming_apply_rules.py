"""dreaming/apply_rules 覆盖率补强测试。

覆盖 RuleApplicator.apply 全路径：
- skip_validation=True 直接应用（新增节点 / 幂等更新）
- 校验失败 → rejected 分支
- RuleDraft 构造与默认字段
"""

from __future__ import annotations

import pytest

from app.dreaming.apply_rules import ApplyResult, RuleApplicator
from app.dreaming.rule_synthesizer import RULE_STATUS_APPLIED, RULE_STATUS_DRAFT, RuleDraft
from app.knowledge_graph.graph_store import GraphStore

pytestmark = pytest.mark.unit


def _make_draft(rule_id: str = "r-test-001", description: str = "测试规则") -> RuleDraft:
    return RuleDraft(
        rule_id=rule_id,
        rule_type="parameter_adjustment",
        description=description,
        condition={"param": "spindle_speed", "op": ">", "value": 8000},
        action={"action": "adjust", "target": "spindle_speed", "delta": -500},
        confidence=0.85,
    )


class TestRuleApplicator:
    def test_apply_skip_validation_creates_node(self, tmp_path):
        graph = GraphStore(auto_load=False)
        applier = RuleApplicator(output_dir=str(tmp_path), graph_store=graph)
        result = applier.apply(_make_draft(), skip_validation=True)
        assert result.success is True
        assert result.rule_id == "r-test-001"
        assert graph.has_node("rule_r-test-001")

    def test_apply_skip_validation_idempotent_update(self, tmp_path):
        graph = GraphStore(auto_load=False)
        applier = RuleApplicator(output_dir=str(tmp_path), graph_store=graph)
        applier.apply(_make_draft(), skip_validation=True)
        # 二次应用 更新而非新增
        draft2 = _make_draft(description="更新后的描述")
        result = applier.apply(draft2, skip_validation=True)
        assert result.success is True
        node = graph.get_node("rule_r-test-001")
        assert node["properties"]["description"] == "更新后的描述"

    def test_apply_with_validation_valid_rule(self, tmp_path):
        graph = GraphStore(auto_load=False)
        applier = RuleApplicator(output_dir=str(tmp_path), graph_store=graph)
        result = applier.apply(_make_draft())
        assert result.success is True
        assert graph.has_node("rule_r-test-001")

    def test_apply_with_validation_invalid_rule(self, tmp_path):
        graph = GraphStore(auto_load=False)
        applier = RuleApplicator(output_dir=str(tmp_path), graph_store=graph)
        # 空 condition / action 的规则应被验证器拒绝
        draft = RuleDraft(
            rule_id="r-bad",
            rule_type="unknown_type",
            description="坏规则",
            condition={},
            action={},
            confidence=0.0,
        )
        result = applier.apply(draft)
        assert result.success is False
        assert result.rule_id == "r-bad"
        assert not graph.has_node("rule_r-bad")

    def test_apply_result_fields(self):
        r = ApplyResult(
            success=True,
            rule_id="r-1",
            applied_at="2026-01-01T00:00:00+00:00",
            node_id="rule_r-1",
            audit_entry_seq=1,
            error="",
        )
        assert r.success is True
        assert r.rule_id == "r-1"
        assert r.node_id == "rule_r-1"
        assert r.error == ""

    def test_rule_draft_defaults(self):
        d = RuleDraft(
            rule_id="r-default",
            rule_type="confidence_threshold",
            description="默认",
            condition={"a": 1},
            action={"b": 2},
        )
        assert d.confidence == 0.5
        assert d.status == RULE_STATUS_DRAFT
        assert d.respects_cam_validation is True
        assert d.respects_succeeded_lock is True
        assert d.supporting_sessions == []

    def test_simulate_apply_delete_detection(self):
        from app.dreaming.rule_validator import RuleValidator

        v = RuleValidator()
        # dict action 含 delete SUCCEEDED 边界应失败
        delete_draft = RuleDraft(
            rule_id="r-del",
            rule_type="parameter_adjustment",
            description="删除规则",
            condition={"status": "SUCCEEDED"},
            action={"action": "delete_task", "target": "anything"},
            confidence=0.8,
        )
        assert v._simulate_apply(delete_draft, {"status": "SUCCEEDED"}) is False
        # dict action 不含 delete 通过
        safe_draft = RuleDraft(
            rule_id="r-safe",
            rule_type="parameter_adjustment",
            description="安全规则",
            condition={"param": "x"},
            action={"action": "adjust", "target": "spindle_speed"},
            confidence=0.8,
        )
        assert v._simulate_apply(safe_draft, {"status": "SUCCEEDED"}) is True
        # str action 形式仍兼容
        str_draft = RuleDraft(
            rule_id="r-str",
            rule_type="parameter_adjustment",
            description="字符串动作",
            condition={"a": 1},
            action="delete_something",  # type: ignore[arg-type]
            confidence=0.8,
        )
        assert v._simulate_apply(str_draft, {"status": "SUCCEEDED"}) is False
