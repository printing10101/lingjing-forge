"""Regression contracts for I6 D02-D11; isolated graphs and review storage."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.agent.knowledge_augmenter import normalize_material
from app.knowledge_graph.feedback_updater import FeedbackUpdater
from app.knowledge_graph.graph_store import GraphStore
from app.knowledge_graph.query_api import KnowledgeGraphQueryAPI
from app.knowledge_graph.extractor.review import ReviewManager
from app.rag.document_importer import extract_entities

pytestmark = pytest.mark.unit


def record(record_id="rec-1", **kwargs):
    return {"record_id": record_id, "first_pass_acceptance": True,
            "process_plan": {"steps": [{"process_id": "process-1"}]}, **kwargs}


@pytest.fixture
def graph():
    return GraphStore(auto_load=False)


def test_process_chain_respects_ontology_hops_and_evidence(graph):
    for kind, node_id in [("material", "material-1"), ("tool", "tool-1"),
                          ("feature", "feature-1"), ("process", "process-1")]:
        graph.add_node(kind, node_id)
    graph.add_edge("process-1", "feature-1", "APPLIED_TO", {"confidence": .9, "evidence": "manual p3"})
    graph.add_edge("process-1", "tool-1", "USED", {"confidence": .8})
    graph.add_edge("tool-1", "material-1", "SUITABLE_FOR", {"confidence": .9})
    graph.add_edge("tool-1", "feature-1", "SUITABLE_FOR", {"confidence": .9})
    query = KnowledgeGraphQueryAPI(graph)
    first = query.process_chain("feature-1", max_hops=1)
    assert len(first) == 1 and first[0]["to"] == "process-1"
    assert first[0]["evidence"] == "manual p3" and first[0]["direction"] == "in"
    chain = query.process_chain("feature-1", max_hops=3)
    assert [(x["to"], x["hop"]) for x in chain] == [("process-1", 1), ("tool-1", 2)]
    assert query.process_chain("feature-1", max_hops=0) == []
    assert [x["material"]["node_type"] for x in query.materials_for_tool("tool-1")] == ["material"]


def test_feedback_retries_and_concurrent_delivery_are_idempotent(graph):
    updater = FeedbackUpdater(graph)
    item = record(surface_roughness=2.0)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: updater.update_from_machining_record(item), range(12)))
    assert graph.get_node("process-1")["properties"]["sample_count"] == 1
    assert graph.node_count("feedback_record") == 1
    # A new updater uses the same receipt rather than an instance-local cache.
    FeedbackUpdater(graph).update_from_machining_record(item)
    assert graph.get_node("process-1")["properties"]["sample_count"] == 1
    with pytest.raises(ValueError, match="different content"):
        updater.update_from_machining_record(record(surface_roughness=3.0))


def test_feedback_receipt_survives_database_reload(graph, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.knowledge_graph.models import Base
    from app.knowledge_graph.persistence import GraphPersistence
    engine = create_engine(f"sqlite:///{(tmp_path / 'graph.db').as_posix()}")
    Base.metadata.create_all(engine)
    persistence = GraphPersistence(session_factory=sessionmaker(bind=engine))
    try:
        FeedbackUpdater(graph).update_from_machining_record(record(surface_roughness=2.0))
        persistence.flush_to_repository(graph)
        reloaded = GraphStore(auto_load=False)
        persistence.load_from_repository(reloaded)
        FeedbackUpdater(reloaded).update_from_machining_record(record(surface_roughness=2.0))
        assert reloaded.get_node("process-1")["properties"]["sample_count"] == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("missing", [{}, {"surface_roughness": None}])
def test_missing_roughness_does_not_create_zero_measurement(graph, missing):
    updater = FeedbackUpdater(graph)
    updater.update_from_machining_record(record(surface_roughness=2.0))
    updater.update_from_machining_record(record("rec-2", **missing))
    updater.update_from_machining_record(record("rec-3", surface_roughness=4.0))
    props = graph.get_node("process-1")["properties"]
    assert props["sample_count"] == 3
    assert props["roughness_sample_count"] == 2
    assert props["avg_surface_roughness"] == 3.0


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), "2", True])
def test_invalid_measurement_has_no_side_effect(graph, value):
    with pytest.raises(ValueError):
        FeedbackUpdater(graph).update_from_machining_record(record(surface_roughness=value))
    assert graph.node_count() == 0


def test_chinese_material_feedback_uses_same_identity_for_adjustment(graph):
    updater = FeedbackUpdater(graph)
    for material, tool_id in [("material-steel", "tool-1"), ("45钢", "tool-2")]:
        stats = updater.update_from_machining_record({"record_id": tool_id, "tool_id": tool_id,
            "workpiece_material": material, "first_pass_acceptance": False})
        assert stats["confidence_adjustments"] == 1
        edge = graph.get_edge(tool_id, updater._material_node_id(material), "SUITABLE_FOR")
        assert edge["properties"]["confidence"] == pytest.approx(.4)


def test_failed_feedback_rolls_back_nodes_edges_and_receipt(graph):
    updater = FeedbackUpdater(graph)
    bad = record(tool_id="bad id", workpiece_material="steel")
    with pytest.raises(ValueError):
        updater.update_from_machining_record(bad)
    assert graph.node_count() == 0 and graph.edge_count() == 0
    updater.update_from_machining_record(record(tool_id="tool-1", workpiece_material="steel"))
    assert graph.get_node("process-1")["properties"]["sample_count"] == 1


def test_database_failure_rolls_back_receipt_and_allows_retry(graph, monkeypatch):
    updater = FeedbackUpdater(graph, persist_updates=True)
    def broken_flush():
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(graph, "flush_to_repository", broken_flush)
    with pytest.raises(RuntimeError, match="database unavailable"):
        updater.update_from_machining_record(record(surface_roughness=2.0))
    assert graph.node_count() == 0
    monkeypatch.setattr(graph, "flush_to_repository", lambda: {"nodes_written": 2})
    updater.update_from_machining_record(record(surface_roughness=2.0))
    assert graph.get_node("process-1")["properties"]["sample_count"] == 1


@pytest.fixture
def review(tmp_path):
    manager = ReviewManager(storage_path=tmp_path / "reviews")
    review_id = manager.load_extraction_result({
        "entities": [{"id": "tool-1", "entity_type": "Tool", "name": "T"},
                     {"id": "material-1", "entity_type": "Material", "name": "M"}],
        "relations": [{"source_id": "tool-1", "target_id": "material-1", "relation_type": "SUITABLE_FOR"}],
    })
    return manager, review_id


def test_deleted_entity_invalidates_relations_and_persists(review):
    manager, review_id = review
    manager.approve_all(review_id)
    manager.delete_entity(review_id, "material-1")
    assert manager.get_approved_data(review_id)["relations"] == []
    restarted = ReviewManager(storage_path=manager.storage_path)
    restarted.load_from_disk()
    assert restarted.get_approved_data(review_id)["relations"] == []


def test_bulk_approval_cannot_hide_revision_or_export_dangling_relations(review):
    manager, review_id = review
    manager.update_entity(review_id, "tool-1", {"status": "needs_revision"})
    manager.approve_all(review_id)
    assert manager.get_review(review_id).overall_status == "needs_revision"
    assert manager.get_approved_data(review_id)["relations"] == []
    manager.update_entity(review_id, "tool-1", {"status": "approved"})
    assert manager.get_review(review_id).overall_status == "approved"
    assert len(manager.get_approved_data(review_id)["relations"]) == 1


@pytest.mark.parametrize("material,expected", [
    ("alloy steel", "steel"), ("aluminum alloy", "aluminum"), ("aluminium alloy", "aluminum"),
    ("titanium alloy", "titanium"), ("45钢", "steel"), ("6061", "aluminum"),
    ("unknown alloy", "unknown alloy"),
])
def test_material_identity_is_not_inferred_from_generic_alloy(material, expected):
    assert normalize_material(material) == expected


@pytest.mark.parametrize("text,entity", [("加工45钢工件", "45钢"), ("调整铝合金切削速度", "铝合金"),
    ("监测切削力和主轴功率", "切削力"), ("增大进给量", "进给量")])
def test_chinese_entities_do_not_require_spaces(text, entity):
    assert entity in extract_entities(text)


def test_numeric_material_does_not_match_identifier_suffix():
    assert "45钢" not in extract_entities("ABC45钢X")
