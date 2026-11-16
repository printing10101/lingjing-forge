"""T3 补测（五）：加工记录反馈回灌。"""

from __future__ import annotations

import pytest

from app.knowledge_graph.feedback_updater import FeedbackUpdater
from app.knowledge_graph.graph_store import GraphStore

pytestmark = pytest.mark.unit


@pytest.fixture()
def updater() -> tuple[FeedbackUpdater, GraphStore]:
    graph = GraphStore(auto_load=False)
    graph.add_node("tool", "tool-endmill-d10", {"name": "D10 立铣刀"})
    graph.add_node("process", "process-roughing", {"name": "粗铣", "sample_count": 0})
    return FeedbackUpdater(graph_store=graph, persist_updates=False), graph


def test_feedback_updates_process_and_creates_receipt(updater):
    feedback, graph = updater
    record = {
        "record_id": "rec-001",
        "tool_id": "tool-endmill-d10",
        "workpiece_material": "45号钢",
        "first_pass_acceptance": True,
        "surface_roughness": 1.6,
        "process_plan": {"steps": [{"process_id": "process-roughing", "name": "粗铣",
                                    "spindle_speed": 1200, "feed_rate": 300, "depth_of_cut": 2.0}]},
        "actual_dimensions": {"length": 100.02},
    }
    stats = feedback.update_from_machining_record(record)
    assert stats["process_nodes_updated"] == 1
    assert stats["confidence_adjustments"] >= 0
    process = graph.get_node("process-roughing")
    assert process["properties"]["sample_count"] == 1
    assert process["properties"]["success_count"] == 1

    receipt = graph.get_node("feedback-" + __import__("hashlib").sha256(b"rec-001").hexdigest())
    assert receipt is not None and receipt["properties"]["record_id"] == "rec-001"


def test_feedback_is_idempotent_and_rejects_content_change(updater):
    feedback, graph = updater
    record = {"record_id": "rec-002", "first_pass_acceptance": True, "surface_roughness": 2.0}
    feedback.update_from_machining_record(record)
    again = feedback.update_from_machining_record(record)
    assert again["process_nodes_updated"] == 0

    changed = dict(record, surface_roughness=1.0)
    with pytest.raises(ValueError, match="different content"):
        feedback.update_from_machining_record(changed)


@pytest.mark.parametrize(
    ("record", "message"),
    [
        ({}, "record_id"),
        ({"record_id": "  "}, "record_id"),
        ({"record_id": "r1", "first_pass_acceptance": "yes"}, "boolean"),
        ({"record_id": "r2", "surface_roughness": -1}, "non-negative"),
        ({"record_id": "r3", "surface_roughness": float("nan")}, "finite"),
    ],
)
def test_feedback_rejects_invalid_records(updater, record, message):
    feedback, _graph = updater
    with pytest.raises(ValueError, match=message):
        feedback.update_from_machining_record(record)


def test_feedback_failure_record_lowers_process_confidence(updater):
    feedback, graph = updater
    record = {
        "record_id": "rec-003",
        "first_pass_acceptance": False,
        "surface_roughness": 6.3,
        "process_plan": {"steps": [{"process_id": "process-roughing", "spindle_speed": 800,
                                    "feed_rate": 200, "depth_of_cut": 1.0}]},
    }
    stats = feedback.update_from_machining_record(record)
    assert stats["process_nodes_updated"] == 1
    process = graph.get_node("process-roughing")
    assert process["properties"]["success_count"] == 0
    assert process["properties"]["sample_count"] == 1
