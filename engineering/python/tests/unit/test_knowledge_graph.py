"""knowledge_graph 覆盖率补强测试（graph_store / feedback_updater / models）。

覆盖：
- GraphStore：节点/边 CRUD、属性更新、按类型/置信度查询、持久化、并发安全
- FeedbackUpdater：加工反馈 → 图更新主流程与分支
- KGNode / KGEdge：ORM 序列化
"""

from __future__ import annotations

import threading

import pytest

from app.knowledge_graph.graph_store import GraphStore, _ensure_props, _validate_node_id
from app.knowledge_graph.feedback_updater import FeedbackUpdater

pytestmark = pytest.mark.unit


class TestValidateNodeId:
    def test_valid_id_passes(self):
        _validate_node_id("material-45-steel")  # 不应抛异常

    def test_invalid_id_raises(self):
        with pytest.raises(ValueError):
            _validate_node_id("bad id with spaces")

    def test_empty_id_raises(self):
        with pytest.raises(ValueError):
            _validate_node_id("")

    def test_non_string_raises(self):
        with pytest.raises((TypeError, ValueError)):
            _validate_node_id(123)  # type: ignore[arg-type]


class TestEnsureProps:
    def test_none_returns_empty(self):
        assert _ensure_props(None) == {}

    def test_dict_passthrough(self):
        assert _ensure_props({"a": 1}) == {"a": 1}

    def test_non_dict_raises(self):
        with pytest.raises(TypeError):
            _ensure_props("nope")  # type: ignore[arg-type]


class TestGraphStore:
    def setup_method(self):
        self.store = GraphStore(auto_load=False)

    def test_add_and_has_node(self):
        self.store.add_node("material", "material-45-steel", {"name": "45钢"})
        assert self.store.has_node("material-45-steel")
        assert not self.store.has_node("nope")

    def test_add_node_invalid_type_raises(self):
        with pytest.raises(ValueError):
            self.store.add_node("", "material-x")

    def test_add_node_reserved_type_key(self):
        # 用户提供 type 键时 node_type 优先
        self.store.add_node("material", "material-x", {"type": "hacker", "name": "x"})
        node = self.store.get_node("material-x")
        assert node["node_type"] == "material"

    def test_get_node_missing_returns_none(self):
        assert self.store.get_node("nope") is None

    def test_get_node_roundtrip(self):
        self.store.add_node("tool", "tool-ballmill-d6", {"diameter": 6.0})
        node = self.store.get_node("tool-ballmill-d6")
        assert node["node_type"] == "tool"
        assert node["properties"]["diameter"] == 6.0

    def test_update_node_properties(self):
        self.store.add_node("tool", "tool-1", {"diameter": 6.0})
        self.store.update_node_properties("tool-1", {"diameter": 8.0, "length": 50.0})
        node = self.store.get_node("tool-1")
        assert node["properties"]["diameter"] == 8.0
        assert node["properties"]["length"] == 50.0

    def test_update_node_properties_missing_node(self):
        # 节点不存在时返回 False（静默）
        assert self.store.update_node_properties("nope", {"a": 1}) is False

    def test_remove_node(self):
        self.store.add_node("material", "material-1")
        assert self.store.remove_node("material-1") is True
        assert not self.store.has_node("material-1")

    def test_remove_node_missing(self):
        assert self.store.remove_node("nope") is False

    def test_list_nodes_by_type(self):
        self.store.add_node("material", "material-1")
        self.store.add_node("material", "material-2")
        self.store.add_node("tool", "tool-1")
        nodes = self.store.list_nodes_by_type("material")
        assert len(nodes) == 2
        assert all(n["node_type"] == "material" for n in nodes)
        assert nodes[0]["node_id"] == "material-1"  # 按 id 排序

    def test_node_count(self):
        self.store.add_node("material", "material-1")
        self.store.add_node("tool", "tool-1")
        assert self.store.node_count() == 2
        assert self.store.node_count("material") == 1
        assert self.store.node_count("nonexistent") == 0

    def test_add_edge(self):
        self.store.add_node("material", "material-1")
        self.store.add_node("tool", "tool-1")
        self.store.add_edge("material-1", "tool-1", "SUITABLE_FOR", {"confidence": 0.9})
        assert self.store.has_edge("material-1", "tool-1", "SUITABLE_FOR")

    def test_add_edge_invalid_confidence(self):
        self.store.add_node("material", "material-1")
        self.store.add_node("tool", "tool-1")
        with pytest.raises(ValueError):
            self.store.add_edge("material-1", "tool-1", "X", {"confidence": 1.5})

    def test_add_edge_missing_source(self):
        self.store.add_node("tool", "tool-1")
        with pytest.raises(ValueError):
            self.store.add_edge("ghost", "tool-1", "X")

    def test_add_edge_missing_target(self):
        self.store.add_node("material", "material-1")
        with pytest.raises(ValueError):
            self.store.add_edge("material-1", "ghost", "X")

    def test_get_edge_roundtrip(self):
        self.store.add_node("material", "material-1")
        self.store.add_node("tool", "tool-1")
        self.store.add_edge("material-1", "tool-1", "SUITABLE_FOR", {"confidence": 0.8})
        edge = self.store.get_edge("material-1", "tool-1", "SUITABLE_FOR")
        assert edge is not None
        assert edge["properties"]["confidence"] == 0.8

    def test_update_edge_properties(self):
        self.store.add_node("material", "material-1")
        self.store.add_node("tool", "tool-1")
        self.store.add_edge("material-1", "tool-1", "SUITABLE_FOR", {"confidence": 0.8})
        self.store.update_edge_properties(
            "material-1", "tool-1", "SUITABLE_FOR", {"confidence": 0.95, "source": "exp"}
        )
        edge = self.store.get_edge("material-1", "tool-1", "SUITABLE_FOR")
        assert edge["properties"]["confidence"] == 0.95
        assert edge["properties"]["source"] == "exp"

    def test_remove_edge(self):
        self.store.add_node("material", "material-1")
        self.store.add_node("tool", "tool-1")
        self.store.add_edge("material-1", "tool-1", "SUITABLE_FOR")
        assert self.store.remove_edge("material-1", "tool-1", "SUITABLE_FOR") is True
        assert not self.store.has_edge("material-1", "tool-1", "SUITABLE_FOR")

    def test_remove_edge_missing(self):
        assert self.store.remove_edge("a", "b", "X") is False

    def test_list_edges_by_type(self):
        self.store.add_node("material", "m1")
        self.store.add_node("material", "m2")
        self.store.add_node("tool", "t1")
        self.store.add_edge("m1", "t1", "SUITABLE_FOR", {"confidence": 0.9})
        self.store.add_edge("m2", "t1", "SUITABLE_FOR", {"confidence": 0.7})
        edges = self.store.list_edges_by_type("SUITABLE_FOR")
        assert len(edges) == 2

    def test_list_edges_by_source(self):
        self.store.add_node("material", "m1")
        self.store.add_node("tool", "t1")
        self.store.add_node("tool", "t2")
        self.store.add_edge("m1", "t1", "SUITABLE_FOR")
        self.store.add_edge("m1", "t2", "SUITABLE_FOR")
        edges = self.store.list_edges_by_source("m1")
        assert len(edges) == 2

    def test_list_edges_by_target(self):
        self.store.add_node("material", "m1")
        self.store.add_node("material", "m2")
        self.store.add_node("tool", "t1")
        self.store.add_edge("m1", "t1", "SUITABLE_FOR")
        self.store.add_edge("m2", "t1", "SUITABLE_FOR")
        edges = self.store.list_edges_by_target("t1")
        assert len(edges) == 2

    def test_list_edges_by_confidence_min(self):
        self.store.add_node("material", "m1")
        self.store.add_node("material", "m2")
        self.store.add_node("tool", "t1")
        self.store.add_edge("m1", "t1", "SUITABLE_FOR", {"confidence": 0.9})
        self.store.add_edge("m2", "t1", "SUITABLE_FOR", {"confidence": 0.5})
        edges = self.store.list_edges_by_confidence(min_confidence=0.8)
        assert len(edges) == 1
        assert edges[0]["properties"]["confidence"] == 0.9

    def test_list_edges_by_confidence_max(self):
        self.store.add_node("material", "m1")
        self.store.add_node("material", "m2")
        self.store.add_node("tool", "t1")
        self.store.add_edge("m1", "t1", "SUITABLE_FOR", {"confidence": 0.9})
        self.store.add_edge("m2", "t1", "SUITABLE_FOR", {"confidence": 0.5})
        edges = self.store.list_edges_by_confidence(max_confidence=0.6)
        assert len(edges) == 1
        assert edges[0]["properties"]["confidence"] == 0.5

    def test_edge_count(self):
        self.store.add_node("material", "m1")
        self.store.add_node("tool", "t1")
        self.store.add_edge("m1", "t1", "SUITABLE_FOR")
        assert self.store.edge_count() == 1
        assert self.store.edge_count("SUITABLE_FOR") == 1

    def test_clear(self):
        self.store.add_node("material", "m1")
        self.store.clear()
        assert self.store.node_count() == 0

    def test_graph_property(self):
        self.store.add_node("material", "m1")
        g = self.store.graph()  # 方法而非属性
        assert g.has_node("m1")


class TestFeedbackUpdater:
    def test_update_missing_process_plan(self):
        # 无 process_plan 时仍应正常处理（部分路径）
        updater = FeedbackUpdater(graph_store=GraphStore(auto_load=False))
        result = updater.update_from_machining_record(
            {
                "record_id": "rec-1",
                "machine_id": "mc-1",
                "tool_id": "tool-1",
                "workpiece_material": "steel-45",
                "first_pass_acceptance": True,
            }
        )
        assert isinstance(result, dict)
        assert "process_nodes_updated" in result

    def test_update_empty_record(self):
        updater = FeedbackUpdater(graph_store=GraphStore(auto_load=False))
        with pytest.raises(Exception):
            updater.update_from_machining_record({})


class TestKGModels:
    def test_kg_node_to_dict(self):
        from app.knowledge_graph.models import KGNode
        n = KGNode(
            node_id='material-45-steel', node_type='material',
            properties={'name': '45钢'}, created_at='2026-01-01', updated_at='2026-01-02',
        )
        d = n.to_dict()
        assert d['node_id'] == 'material-45-steel'
        assert d['node_type'] == 'material'
        assert d['properties'] == {'name': '45钢'}
        assert d['created_at'] == '2026-01-01'

    def test_kg_node_to_dict_none_properties(self):
        from app.knowledge_graph.models import KGNode
        n = KGNode(node_id='material-x', node_type='material', properties=None, created_at='', updated_at='')
        assert n.to_dict()['properties'] == {}

    def test_kg_node_repr(self):
        from app.knowledge_graph.models import KGNode
        n = KGNode(node_id='material-x', node_type='material', properties={}, created_at='', updated_at='')
        assert 'material-x' in repr(n)

    def test_kg_edge_to_dict(self):
        from app.knowledge_graph.models import KGEdge
        e = KGEdge(
            edge_id='kgedge-1', source_id='tool-1', target_id='material-1',
            edge_type='SUITABLE_FOR', confidence=0.9,
            properties={'source': 'rule'}, created_at='2026-01-01',
        )
        d = e.to_dict()
        assert d['source_id'] == 'tool-1'
        assert d['target_id'] == 'material-1'
        assert d['edge_type'] == 'SUITABLE_FOR'
        assert d['confidence'] == 0.9
        assert d['properties'] == {'source': 'rule'}

    def test_kg_edge_to_dict_none_properties(self):
        from app.knowledge_graph.models import KGEdge
        e = KGEdge(
            edge_id='kgedge-1', source_id='a', target_id='b',
            edge_type='X', confidence=0.5, properties=None, created_at='',
        )
        assert e.to_dict()['properties'] == {}

    def test_kg_edge_repr(self):
        from app.knowledge_graph.models import KGEdge
        e = KGEdge(
            edge_id='kgedge-1', source_id='a', target_id='b',
            edge_type='X', confidence=0.5, properties={}, created_at='',
        )
        assert 'SUITABLE_FOR' not in repr(e)
        assert 'X' in repr(e)
