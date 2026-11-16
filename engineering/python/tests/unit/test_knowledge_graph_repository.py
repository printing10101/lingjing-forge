"""T3 knowledge_graph 补测（二）：仓储与持久化（sqlite 内存库）。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.knowledge_graph.graph_store import GraphStore
from app.knowledge_graph.models import Base
from app.knowledge_graph.persistence import GraphPersistence
from app.knowledge_graph.repository import KnowledgeGraphRepository

pytestmark = pytest.mark.unit


@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def repo(session_factory):
    repository = KnowledgeGraphRepository(session_factory=session_factory)
    repository.init_schema()
    return repository


def test_repo_upsert_and_get_node(repo):
    node = repo.upsert_node("material-45steel", "material", {"name": "45钢"})
    assert node.node_id == "material-45steel"
    fetched = repo.get_node("material-45steel")
    assert fetched is not None and fetched.properties["name"] == "45钢"
    assert repo.get_node("material-missing") is None

    updated = repo.upsert_node("material-45steel", "material", {"name": "45#钢"})
    assert updated.properties["name"] == "45#钢"
    assert repo.count_nodes("material") == 1


def test_repo_list_and_delete(repo):
    repo.upsert_node("tool-endmill-d10", "tool", {"name": "D10"})
    repo.upsert_node("tool-ball-d6", "tool", {"name": "D6"})
    tools = repo.list_nodes_by_type("tool")
    assert [item.node_id for item in tools] == ["tool-ball-d6", "tool-endmill-d10"]
    assert len(repo.list_all_nodes()) == 2
    assert repo.delete_node("tool-ball-d6") is True
    assert repo.delete_node("tool-ball-d6") is False
    assert repo.count_nodes() == 1


def test_repo_upsert_edge_and_queries(repo):
    repo.upsert_node("tool-endmill-d10", "tool")
    repo.upsert_node("material-45steel", "material")
    edge = repo.upsert_edge("tool-endmill-d10", "material-45steel", "SUITABLE_FOR",
                            confidence=0.9, properties={"source": "rule"})
    assert edge.edge_id
    assert repo.count_edges("SUITABLE_FOR") == 1
    assert len(repo.list_edges_by_type("SUITABLE_FOR")) == 1
    assert len(repo.list_edges_by_source("tool-endmill-d10")) == 1
    assert len(repo.list_edges_by_target("material-45steel")) == 1
    assert len(repo.list_edges_by_confidence(0.5)) == 1

    repo.upsert_edge("tool-endmill-d10", "material-45steel", "SUITABLE_FOR", confidence=0.8)
    assert repo.count_edges() == 1

    assert repo.delete_edge("tool-endmill-d10", "material-45steel", "SUITABLE_FOR") is True
    assert repo.count_edges() == 0


def test_repo_delete_node_cascades_edges(repo):
    repo.upsert_node("tool-endmill-d10", "tool")
    repo.upsert_node("material-45steel", "material")
    repo.upsert_edge("tool-endmill-d10", "material-45steel", "SUITABLE_FOR")
    assert repo.delete_node("material-45steel") is True
    assert repo.count_edges() == 0


def test_persistence_flush_and_load_roundtrip(repo, session_factory):
    graph = GraphStore(auto_load=False)
    graph.add_node("material", "material-45steel", {"name": "45钢"})
    graph.add_node("tool", "tool-endmill-d10", {"name": "D10"})
    graph.add_edge("tool-endmill-d10", "material-45steel", "SUITABLE_FOR", {"confidence": 0.9})

    persistence = GraphPersistence(session_factory=session_factory)
    stats = persistence.flush_to_repository(graph)
    assert stats["nodes_written"] == 2
    assert stats["edges_written"] == 1
    assert repo.count_nodes() == 2

    reloaded = GraphStore(auto_load=False)
    loaded = persistence.load_from_repository(reloaded)
    assert loaded["nodes_loaded"] == 2
    assert reloaded.has_node("tool-endmill-d10")
    assert reloaded.has_edge("tool-endmill-d10", "material-45steel", "SUITABLE_FOR")
