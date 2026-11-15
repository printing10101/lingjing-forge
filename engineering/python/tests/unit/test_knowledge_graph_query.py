"""T3 knowledge_graph 补测：内存图 + 查询 API。"""

from __future__ import annotations

import pytest

from app.knowledge_graph.graph_store import GraphStore
from app.knowledge_graph.query_api import (
    KnowledgeGraphQueryAPI,
    _match,
    _wildcard_to_regex,
)

pytestmark = pytest.mark.unit


@pytest.fixture()
def store() -> GraphStore:
    graph = GraphStore(auto_load=False)
    graph.add_node("material", "material-45steel", {"name": "45钢", "hardness": "HB220"})
    graph.add_node("tool", "tool-endmill-d10", {"name": "D10 立铣刀", "diameter": 10})
    graph.add_node("tool", "tool-ball-d6", {"name": "D6 球刀", "diameter": 6})
    graph.add_node("process", "process-roughing", {"name": "粗铣"})
    graph.add_edge("tool-endmill-d10", "material-45steel", "SUITABLE_FOR",
                   {"confidence": 0.9, "source": "rule"})
    graph.add_edge("tool-ball-d6", "material-45steel", "SUITABLE_FOR", {"confidence": 0.7})
    graph.add_edge("tool-endmill-d10", "process-roughing", "USED_IN", {"confidence": 0.8})
    return graph


def test_wildcard_conversion_and_match():
    import re

    assert re.match(_wildcard_to_regex("tool-%"), "tool-abc")
    assert not re.match(_wildcard_to_regex("tool-%"), "material-abc")
    assert _match("tool-ball-d6", None) is True
    assert _match("tool-ball-d6", "tool-%-d6") is True
    assert _match("material-45steel", "tool-%") is False


def test_add_node_validation():
    graph = GraphStore(auto_load=False)
    with pytest.raises(ValueError):
        graph.add_node("", "material-x")
    with pytest.raises(ValueError):
        graph.add_node("material", "bad id with space")
    graph.add_node("material", "material-x", {"type": "ignored", "name": "X"})
    node = graph.get_node("material-x")
    assert node["node_type"] == "material"
    assert "type" not in node["properties"]


def test_graph_store_crud(store: GraphStore):
    assert store.has_node("tool-endmill-d10") is True
    assert store.node_count("tool") == 2
    assert store.node_count() == 4
    assert [node["node_id"] for node in store.list_nodes_by_type("tool")] == [
        "tool-ball-d6", "tool-endmill-d10"]

    store.update_node_properties("tool-ball-d6", {"diameter": 8})
    assert store.get_node("tool-ball-d6")["properties"]["diameter"] == 8

    assert store.has_edge("tool-endmill-d10", "material-45steel", "SUITABLE_FOR") is True
    edge = store.get_edge("tool-endmill-d10", "material-45steel", "SUITABLE_FOR")
    assert edge["properties"]["confidence"] == 0.9
    assert len(store.list_edges_by_type("SUITABLE_FOR")) == 2
    assert len(store.list_edges_by_source("tool-endmill-d10")) == 2
    assert len(store.list_edges_by_target("material-45steel")) == 2

    assert store.remove_edge("tool-ball-d6", "material-45steel", "SUITABLE_FOR") is True
    assert store.remove_node("process-roughing") is True
    assert store.has_node("process-roughing") is False


def test_add_edge_rejects_unknown_endpoint_and_bad_confidence(store: GraphStore):
    with pytest.raises(ValueError):
        store.add_edge("tool-endmill-d10", "material-unknown", "SUITABLE_FOR")
    with pytest.raises(ValueError):
        store.add_edge("tool-endmill-d10", "material-45steel", "CUTS", {"confidence": 1.5})


def test_query_api_node_and_type_filters(store: GraphStore):
    api = KnowledgeGraphQueryAPI(store)
    assert api.node("tool-ball-d6")["node_type"] == "tool"
    assert api.node("missing-node") is None
    tools = api.nodes_by_type("tool")
    assert [node["node_id"] for node in tools] == ["tool-ball-d6", "tool-endmill-d10"]


def test_query_api_search_with_pattern_and_props(store: GraphStore):
    api = KnowledgeGraphQueryAPI(store)
    assert [node["node_id"] for node in api.search_nodes(id_pattern="tool-%")] == [
        "tool-ball-d6", "tool-endmill-d10"]
    assert api.search_nodes(node_type="material")[0]["node_id"] == "material-45steel"
    assert [node["node_id"] for node in api.search_nodes(prop_filter={"diameter": 6})] == [
        "tool-ball-d6"]
    limited = api.search_nodes(id_pattern="tool-%", limit=1)
    # limit 在排序前生效，命中顺序取决于插入顺序（endmill 先插入）
    assert len(limited) == 1 and limited[0]["node_id"] == "tool-endmill-d10"


def test_query_api_edges_and_neighbors(store: GraphStore):
    api = KnowledgeGraphQueryAPI(store)
    assert len(api.edges(edge_type="SUITABLE_FOR")) == 2
    assert len(api.edges(source_id="tool-endmill-d10")) == 2
    assert len(api.edges(target_id="material-45steel")) == 2

    one_hop = api.neighbors("tool-endmill-d10", max_hops=1)
    assert {item["node_id"] for item in one_hop} == {"material-45steel", "process-roughing"}
    two_hop = api.neighbors("process-roughing", max_hops=2)
    assert "material-45steel" in {item["node_id"] for item in two_hop}


def test_query_api_stats_and_tools_for_material(store: GraphStore):
    api = KnowledgeGraphQueryAPI(store)
    stats = api.stats()
    assert stats["node_count"] == 4
    assert stats["edge_count"] == 3
    assert stats["node_types"]["tool"] == 2
    assert stats["edge_types"]["SUITABLE_FOR"] == 2

    tools = api.tools_for_material("material-45steel", min_confidence=0.8)
    assert [item["tool"]["node_id"] for item in tools] == ["tool-endmill-d10"]
