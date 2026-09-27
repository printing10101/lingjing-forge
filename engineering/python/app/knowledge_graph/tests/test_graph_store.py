"""Unit tests for :mod:`app.knowledge_graph` (M1.2).

覆盖范围：
    1. NetworkX 内存图模型（GraphStore）的 CRUD 与查询。
    2. 节点 ID 格式校验与异常处理。
    3. 关系 / 节点属性与可信度区间筛选。
    4. Repository 同步 CRUD（基于内存 SQLite）。
    5. GraphPersistence 双向同步（内存图 ↔ 数据库）。
    6. 服务重启后数据不丢失（端到端持久化测试）。
    7. ORM 模型表结构（__tablename__、列、索引、约束）。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.knowledge_graph.graph_store import GraphStore
from app.knowledge_graph.models import Base, KGEdge, KGNode
from app.knowledge_graph.persistence import GraphPersistence
from app.knowledge_graph.repository import KnowledgeGraphRepository


# 1. GraphStore 内存图模型


class TestGraphStoreNodes:
    """GraphStore 节点 CRUD 与校验。"""

    def test_add_and_get_node(self) -> None:
        g = GraphStore()
        g.add_node("material", "material-45steel", {"name": "45 steel"})
        node = g.get_node("material-45steel")
        assert node is not None
        assert node["node_id"] == "material-45steel"
        assert node["node_type"] == "material"
        assert node["properties"] == {"name": "45 steel"}

    def test_add_node_with_none_properties(self) -> None:
        g = GraphStore()
        g.add_node("tool", "tool-endmill-10")
        node = g.get_node("tool-endmill-10")
        assert node is not None
        assert node["properties"] == {}

    def test_get_missing_node_returns_none(self) -> None:
        g = GraphStore()
        assert g.get_node("nonexistent") is None

    def test_has_node(self) -> None:
        g = GraphStore()
        g.add_node("material", "material-45steel")
        assert g.has_node("material-45steel") is True
        assert g.has_node("nonexistent") is False

    def test_remove_node(self) -> None:
        g = GraphStore()
        g.add_node("material", "material-45steel")
        assert g.remove_node("material-45steel") is True
        assert g.has_node("material-45steel") is False
        assert g.remove_node("material-45steel") is False

    def test_node_count(self) -> None:
        g = GraphStore()
        assert g.node_count() == 0
        g.add_node("material", "material-45steel")
        g.add_node("material", "material-al6061")
        g.add_node("tool", "tool-endmill-10")
        assert g.node_count() == 3
        assert g.node_count("material") == 2
        assert g.node_count("tool") == 1
        assert g.node_count("feature") == 0

    def test_list_nodes_by_type(self) -> None:
        g = GraphStore()
        g.add_node("material", "material-45steel", {"name": "45 steel"})
        g.add_node("material", "material-al6061", {"name": "Al 6061"})
        g.add_node("tool", "tool-endmill-10", {"name": "Endmill D10"})
        materials = g.list_nodes_by_type("material")
        assert len(materials) == 2
        # 按 node_id 升序
        assert materials[0]["node_id"] == "material-45steel"
        assert materials[1]["node_id"] == "material-al6061"
        tools = g.list_nodes_by_type("tool")
        assert len(tools) == 1
        assert tools[0]["node_id"] == "tool-endmill-10"

    def test_update_node_properties_merge(self) -> None:
        g = GraphStore()
        g.add_node("material", "material-45steel", {"name": "45 steel", "density": 7.85})
        assert g.update_node_properties("material-45steel", {"hardness_hb": 197.0}) is True
        node = g.get_node("material-45steel")
        assert node is not None
        assert node["properties"] == {
            "name": "45 steel",
            "density": 7.85,
            "hardness_hb": 197.0,
        }
        # 已存在键被覆盖
        g.update_node_properties("material-45steel", {"name": "45#钢"})
        node = g.get_node("material-45steel")
        assert node is not None
        assert node["properties"]["name"] == "45#钢"

    def test_update_node_properties_missing_returns_false(self) -> None:
        g = GraphStore()
        assert g.update_node_properties("missing", {"k": "v"}) is False

    def test_node_id_validation(self) -> None:
        g = GraphStore()
        # 空字符串
        with pytest.raises(ValueError):
            g.add_node("material", "")
        # 非法字符
        with pytest.raises(ValueError):
            g.add_node("material", "bad id with spaces")
        # 起始字符非法
        with pytest.raises(ValueError):
            g.add_node("material", "1material-45steel")
        # 超过 128 字符
        with pytest.raises(ValueError):
            g.add_node("material", "m-" + "x" * 130)
        # 非字符串
        with pytest.raises(TypeError):
            g.add_node("material", 123)  # type: ignore[arg-type]

    def test_node_type_validation(self) -> None:
        g = GraphStore()
        with pytest.raises(ValueError):
            g.add_node("", "material-45steel")
        with pytest.raises(ValueError):
            g.add_node(None, "material-45steel")  # type: ignore[arg-type]

    def test_properties_type_validation(self) -> None:
        g = GraphStore()
        with pytest.raises(TypeError):
            g.add_node("material", "material-45steel", properties=[1, 2])  # type: ignore[arg-type]


class TestGraphStoreEdges:
    """GraphStore 关系 CRUD 与查询。"""

    def _seed(self, g: GraphStore) -> None:
        g.add_node("material", "material-45steel", {"name": "45 steel"})
        g.add_node("material", "material-al6061", {"name": "Al 6061"})
        g.add_node("tool", "tool-endmill-10", {"name": "Endmill D10"})
        g.add_node("feature", "feature-pocket", {"name": "Pocket"})
        g.add_node("process", "process-face-mill", {"name": "Face Milling"})

    def test_add_and_get_edge(self) -> None:
        g = GraphStore()
        self._seed(g)
        g.add_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            {"confidence": 0.9, "source": "rule"},
        )
        edge = g.get_edge("tool-endmill-10", "material-45steel", "SUITABLE_FOR")
        assert edge is not None
        assert edge["source_id"] == "tool-endmill-10"
        assert edge["target_id"] == "material-45steel"
        assert edge["edge_type"] == "SUITABLE_FOR"
        assert edge["properties"]["confidence"] == 0.9
        assert edge["properties"]["source"] == "rule"

    def test_add_edge_with_default_confidence(self) -> None:
        g = GraphStore()
        self._seed(g)
        g.add_edge("tool-endmill-10", "material-45steel", "SUITABLE_FOR")
        edge = g.get_edge("tool-endmill-10", "material-45steel", "SUITABLE_FOR")
        assert edge is not None
        assert edge["properties"]["confidence"] == 0.5

    def test_add_edge_missing_endpoint_raises(self) -> None:
        g = GraphStore()
        g.add_node("material", "material-45steel")
        with pytest.raises(ValueError):
            g.add_edge("missing-source", "material-45steel", "SUITABLE_FOR")
        with pytest.raises(ValueError):
            g.add_edge("material-45steel", "missing-target", "SUITABLE_FOR")

    def test_add_edge_confidence_validation(self) -> None:
        g = GraphStore()
        self._seed(g)
        # 越界
        with pytest.raises(ValueError):
            g.add_edge(
                "tool-endmill-10",
                "material-45steel",
                "SUITABLE_FOR",
                {"confidence": 1.5},
            )
        with pytest.raises(ValueError):
            g.add_edge(
                "tool-endmill-10",
                "material-45steel",
                "SUITABLE_FOR",
                {"confidence": -0.1},
            )
        # 类型错误
        with pytest.raises(TypeError):
            g.add_edge(
                "tool-endmill-10",
                "material-45steel",
                "SUITABLE_FOR",
                {"confidence": "high"},
            )

    def test_add_edge_type_validation(self) -> None:
        g = GraphStore()
        self._seed(g)
        with pytest.raises(ValueError):
            g.add_edge("tool-endmill-10", "material-45steel", "")

    def test_has_edge(self) -> None:
        g = GraphStore()
        self._seed(g)
        g.add_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            {"confidence": 0.9},
        )
        assert (
            g.has_edge(
                "tool-endmill-10",
                "material-45steel",
                "SUITABLE_FOR",
            )
            is True
        )
        assert (
            g.has_edge(
                "tool-endmill-10",
                "material-al6061",
                "SUITABLE_FOR",
            )
            is False
        )

    def test_remove_edge(self) -> None:
        g = GraphStore()
        self._seed(g)
        g.add_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            {"confidence": 0.9},
        )
        assert (
            g.remove_edge(
                "tool-endmill-10",
                "material-45steel",
                "SUITABLE_FOR",
            )
            is True
        )
        assert (
            g.remove_edge(
                "tool-endmill-10",
                "material-45steel",
                "SUITABLE_FOR",
            )
            is False
        )

    def test_update_edge_properties(self) -> None:
        g = GraphStore()
        self._seed(g)
        g.add_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            {"confidence": 0.5, "source": "rule"},
        )
        assert (
            g.update_edge_properties(
                "tool-endmill-10",
                "material-45steel",
                "SUITABLE_FOR",
                {"confidence": 0.85},
            )
            is True
        )
        edge = g.get_edge("tool-endmill-10", "material-45steel", "SUITABLE_FOR")
        assert edge is not None
        assert edge["properties"]["confidence"] == 0.85
        assert edge["properties"]["source"] == "rule"

    def test_update_edge_properties_missing(self) -> None:
        g = GraphStore()
        self._seed(g)
        assert (
            g.update_edge_properties(
                "tool-endmill-10",
                "material-45steel",
                "SUITABLE_FOR",
                {"k": "v"},
            )
            is False
        )

    def test_list_edges_by_type(self) -> None:
        g = GraphStore()
        self._seed(g)
        g.add_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            {"confidence": 0.9},
        )
        g.add_edge(
            "tool-endmill-10",
            "material-al6061",
            "SUITABLE_FOR",
            {"confidence": 0.8},
        )
        g.add_edge(
            "process-face-mill",
            "feature-pocket",
            "APPLIED_TO",
            {"confidence": 0.7},
        )
        edges = g.list_edges_by_type("SUITABLE_FOR")
        assert len(edges) == 2
        for e in edges:
            assert e["edge_type"] == "SUITABLE_FOR"
        edges2 = g.list_edges_by_type("APPLIED_TO")
        assert len(edges2) == 1

    def test_list_edges_by_source(self) -> None:
        g = GraphStore()
        self._seed(g)
        g.add_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            {"confidence": 0.9},
        )
        g.add_edge(
            "tool-endmill-10",
            "material-al6061",
            "SUITABLE_FOR",
            {"confidence": 0.8},
        )
        g.add_edge(
            "process-face-mill",
            "feature-pocket",
            "APPLIED_TO",
            {"confidence": 0.7},
        )
        edges = g.list_edges_by_source("tool-endmill-10")
        assert len(edges) == 2
        edges_filtered = g.list_edges_by_source("tool-endmill-10", edge_type="SUITABLE_FOR")
        assert len(edges_filtered) == 2
        edges_other = g.list_edges_by_source("tool-endmill-10", edge_type="APPLIED_TO")
        assert len(edges_other) == 0

    def test_list_edges_by_target(self) -> None:
        g = GraphStore()
        self._seed(g)
        g.add_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            {"confidence": 0.9},
        )
        g.add_edge(
            "process-face-mill",
            "feature-pocket",
            "APPLIED_TO",
            {"confidence": 0.7},
        )
        edges = g.list_edges_by_target("material-45steel")
        assert len(edges) == 1
        assert edges[0]["source_id"] == "tool-endmill-10"

    def test_list_edges_by_confidence(self) -> None:
        g = GraphStore()
        self._seed(g)
        g.add_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            {"confidence": 0.9},
        )
        g.add_edge(
            "tool-endmill-10",
            "material-al6061",
            "SUITABLE_FOR",
            {"confidence": 0.6},
        )
        g.add_edge(
            "process-face-mill",
            "feature-pocket",
            "APPLIED_TO",
            {"confidence": 0.3},
        )
        # min=0.7
        edges = g.list_edges_by_confidence(min_confidence=0.7)
        assert len(edges) == 1
        assert edges[0]["source_id"] == "tool-endmill-10"
        assert edges[0]["target_id"] == "material-45steel"
        # [0.5, 0.7] 区间
        edges2 = g.list_edges_by_confidence(min_confidence=0.5, max_confidence=0.7)
        assert len(edges2) == 1
        assert edges2[0]["properties"]["confidence"] == 0.6
        # edge_type 过滤
        edges3 = g.list_edges_by_confidence(min_confidence=0.0, edge_type="APPLIED_TO")
        assert len(edges3) == 1
        # 降序
        edges4 = g.list_edges_by_confidence(min_confidence=0.0)
        assert edges4[0]["properties"]["confidence"] == 0.9
        assert edges4[-1]["properties"]["confidence"] == 0.3

    def test_list_edges_by_confidence_invalid(self) -> None:
        g = GraphStore()
        self._seed(g)
        with pytest.raises(ValueError):
            g.list_edges_by_confidence(min_confidence=0.8, max_confidence=0.5)
        with pytest.raises(ValueError):
            g.list_edges_by_confidence(min_confidence=-0.1)
        with pytest.raises(ValueError):
            g.list_edges_by_confidence(max_confidence=1.5)

    def test_edge_count(self) -> None:
        g = GraphStore()
        self._seed(g)
        assert g.edge_count() == 0
        g.add_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            {"confidence": 0.9},
        )
        g.add_edge(
            "tool-endmill-10",
            "material-al6061",
            "SUITABLE_FOR",
            {"confidence": 0.8},
        )
        g.add_edge(
            "process-face-mill",
            "feature-pocket",
            "APPLIED_TO",
            {"confidence": 0.7},
        )
        assert g.edge_count() == 3
        assert g.edge_count("SUITABLE_FOR") == 2
        assert g.edge_count("APPLIED_TO") == 1
        assert g.edge_count("USED") == 0

    def test_remove_node_cascades_edges(self) -> None:
        g = GraphStore()
        self._seed(g)
        g.add_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            {"confidence": 0.9},
        )
        g.remove_node("tool-endmill-10")
        assert g.edge_count() == 0
        assert g.get_edge("tool-endmill-10", "material-45steel", "SUITABLE_FOR") is None

    def test_clear(self) -> None:
        g = GraphStore()
        self._seed(g)
        g.add_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            {"confidence": 0.9},
        )
        g.clear()
        assert g.node_count() == 0
        assert g.edge_count() == 0


# 2. SQLAlchemy ORM 模型表结构


class TestORMModelSchema:
    """ORM 模型表结构 / 索引 / 唯一约束。"""

    def test_kg_node_tablename(self) -> None:
        assert KGNode.__tablename__ == "kg_nodes"

    def test_kg_edge_tablename(self) -> None:
        assert KGEdge.__tablename__ == "kg_edges"

    def test_kg_node_required_columns(self) -> None:
        columns = {c.name for c in KGNode.__table__.columns}
        expected = {
            "node_id",
            "node_type",
            "properties",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(columns)

    def test_kg_edge_required_columns(self) -> None:
        columns = {c.name for c in KGEdge.__table__.columns}
        expected = {
            "edge_id",
            "source_id",
            "target_id",
            "edge_type",
            "confidence",
            "properties",
            "created_at",
        }
        assert expected.issubset(columns)

    def test_kg_node_primary_key(self) -> None:
        pk_cols = [c.name for c in KGNode.__table__.primary_key.columns]
        assert pk_cols == ["node_id"]

    def test_kg_edge_primary_key(self) -> None:
        pk_cols = [c.name for c in KGEdge.__table__.primary_key.columns]
        assert pk_cols == ["edge_id"]

    def test_kg_node_required_indexes(self) -> None:
        index_names = {idx.name for idx in KGNode.__table__.indexes}
        assert "ix_kg_nodes_node_type" in index_names

    def test_kg_edge_required_indexes(self) -> None:
        index_names = {idx.name for idx in KGEdge.__table__.indexes}
        expected = {
            "ix_kg_edges_edge_type",
            "ix_kg_edges_source_id",
            "ix_kg_edges_target_id",
            "ix_kg_edges_confidence",
        }
        assert expected.issubset(index_names)

    def test_kg_edge_unique_constraint(self) -> None:
        constraint_names = {con.name for con in KGEdge.__table__.constraints}
        assert "uq_kg_edges_source_target_type" in constraint_names

    def test_kg_node_to_dict(self) -> None:
        node = KGNode(
            node_id="material-45steel",
            node_type="material",
            properties={"name": "45 steel"},
            created_at="2026-06-11 23:00:00",
            updated_at="2026-06-11 23:00:00",
        )
        d = node.to_dict()
        assert d["node_id"] == "material-45steel"
        assert d["node_type"] == "material"
        assert d["properties"] == {"name": "45 steel"}

    def test_kg_edge_to_dict(self) -> None:
        edge = KGEdge(
            edge_id="kgedge_test",
            source_id="tool-endmill-10",
            target_id="material-45steel",
            edge_type="SUITABLE_FOR",
            confidence=0.9,
            properties={"source": "rule"},
            created_at="2026-06-11 23:00:00",
        )
        d = edge.to_dict()
        assert d["edge_id"] == "kgedge_test"
        assert d["source_id"] == "tool-endmill-10"
        assert d["target_id"] == "material-45steel"
        assert d["edge_type"] == "SUITABLE_FOR"
        assert d["confidence"] == 0.9

    def test_kg_node_repr(self) -> None:
        node = KGNode(
            node_id="material-45steel",
            node_type="material",
            properties={},
        )
        r = repr(node)
        assert "material-45steel" in r
        assert "material" in r

    def test_kg_edge_repr(self) -> None:
        edge = KGEdge(
            edge_id="kgedge_test",
            source_id="tool-endmill-10",
            target_id="material-45steel",
            edge_type="SUITABLE_FOR",
            confidence=0.9,
            properties={},
        )
        r = repr(edge)
        assert "tool-endmill-10" in r
        assert "SUITABLE_FOR" in r
        assert "material-45steel" in r
        assert "0.9" in r


# 3. Repository CRUD 集成（基于内存 SQLite）


@pytest.fixture
def repo() -> Iterator[KnowledgeGraphRepository]:
    """为每个测试创建独立的内存 SQLite + Repository。"""
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    try:
        yield KnowledgeGraphRepository(session_factory=factory)
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


class TestRepositoryCRUD:
    """KnowledgeGraphRepository 同步 CRUD 端到端测试。"""

    def test_upsert_node_new(self, repo: KnowledgeGraphRepository) -> None:
        orm_obj = repo.upsert_node("material-45steel", "material", {"name": "45 steel"})
        assert orm_obj.node_id == "material-45steel"
        assert orm_obj.node_type == "material"
        assert orm_obj.properties == {"name": "45 steel"}

    def test_upsert_node_existing_merge(self, repo: KnowledgeGraphRepository) -> None:
        repo.upsert_node("material-45steel", "material", {"name": "45 steel", "density": 7.85})
        repo.upsert_node("material-45steel", "material", {"hardness_hb": 197.0})
        node = repo.get_node("material-45steel")
        assert node is not None
        assert node.properties == {
            "name": "45 steel",
            "density": 7.85,
            "hardness_hb": 197.0,
        }

    def test_get_node_missing(self, repo: KnowledgeGraphRepository) -> None:
        assert repo.get_node("nonexistent") is None

    def test_list_nodes_by_type(self, repo: KnowledgeGraphRepository) -> None:
        repo.upsert_node("material-45steel", "material", {})
        repo.upsert_node("material-al6061", "material", {})
        repo.upsert_node("tool-endmill-10", "tool", {})
        materials = repo.list_nodes_by_type("material")
        assert len(materials) == 2
        tools = repo.list_nodes_by_type("tool")
        assert len(tools) == 1

    def test_count_nodes(self, repo: KnowledgeGraphRepository) -> None:
        assert repo.count_nodes() == 0
        repo.upsert_node("material-45steel", "material", {})
        repo.upsert_node("tool-endmill-10", "tool", {})
        assert repo.count_nodes() == 2
        assert repo.count_nodes("material") == 1

    def test_delete_node_cascades_edges(self, repo: KnowledgeGraphRepository) -> None:
        repo.upsert_node("material-45steel", "material", {})
        repo.upsert_node("tool-endmill-10", "tool", {})
        repo.upsert_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            confidence=0.9,
        )
        assert repo.delete_node("tool-endmill-10") is True
        assert repo.get_node("tool-endmill-10") is None
        # 边被级联删除
        assert repo.get_edge("tool-endmill-10", "material-45steel", "SUITABLE_FOR") is None

    def test_upsert_edge_new(self, repo: KnowledgeGraphRepository) -> None:
        repo.upsert_node("material-45steel", "material", {})
        repo.upsert_node("tool-endmill-10", "tool", {})
        edge = repo.upsert_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            confidence=0.9,
            properties={"source": "rule"},
        )
        assert edge.source_id == "tool-endmill-10"
        assert edge.target_id == "material-45steel"
        assert edge.edge_type == "SUITABLE_FOR"
        assert edge.confidence == 0.9
        assert edge.properties == {"source": "rule"}

    def test_upsert_edge_existing_update(self, repo: KnowledgeGraphRepository) -> None:
        repo.upsert_node("material-45steel", "material", {})
        repo.upsert_node("tool-endmill-10", "tool", {})
        repo.upsert_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            confidence=0.5,
        )
        repo.upsert_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            confidence=0.85,
            properties={"source": "rule"},
        )
        edge = repo.get_edge("tool-endmill-10", "material-45steel", "SUITABLE_FOR")
        assert edge is not None
        assert edge.confidence == 0.85
        assert edge.properties == {"source": "rule"}

    def test_upsert_edge_confidence_validation(self, repo: KnowledgeGraphRepository) -> None:
        repo.upsert_node("material-45steel", "material", {})
        repo.upsert_node("tool-endmill-10", "tool", {})
        with pytest.raises(ValueError):
            repo.upsert_edge(
                "tool-endmill-10",
                "material-45steel",
                "SUITABLE_FOR",
                confidence=1.5,
            )

    def test_upsert_edge_missing_endpoint_raises(self, repo: KnowledgeGraphRepository) -> None:
        with pytest.raises(Exception):  # IntegrityError
            repo.upsert_edge(
                "missing-source",
                "missing-target",
                "SUITABLE_FOR",
                confidence=0.5,
            )

    def test_list_edges_by_type(self, repo: KnowledgeGraphRepository) -> None:
        repo.upsert_node("material-45steel", "material", {})
        repo.upsert_node("material-al6061", "material", {})
        repo.upsert_node("tool-endmill-10", "tool", {})
        repo.upsert_node("feature-pocket", "feature", {})
        repo.upsert_node("process-face-mill", "process", {})
        repo.upsert_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            confidence=0.9,
        )
        repo.upsert_edge(
            "tool-endmill-10",
            "material-al6061",
            "SUITABLE_FOR",
            confidence=0.8,
        )
        repo.upsert_edge(
            "process-face-mill",
            "feature-pocket",
            "APPLIED_TO",
            confidence=0.7,
        )
        edges = repo.list_edges_by_type("SUITABLE_FOR")
        assert len(edges) == 2
        edges2 = repo.list_edges_by_type("APPLIED_TO")
        assert len(edges2) == 1

    def test_list_edges_by_source(self, repo: KnowledgeGraphRepository) -> None:
        repo.upsert_node("material-45steel", "material", {})
        repo.upsert_node("material-al6061", "material", {})
        repo.upsert_node("tool-endmill-10", "tool", {})
        repo.upsert_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            confidence=0.9,
        )
        repo.upsert_edge(
            "tool-endmill-10",
            "material-al6061",
            "SUITABLE_FOR",
            confidence=0.8,
        )
        edges = repo.list_edges_by_source("tool-endmill-10")
        assert len(edges) == 2
        edges_filtered = repo.list_edges_by_source("tool-endmill-10", edge_type="SUITABLE_FOR")
        assert len(edges_filtered) == 2

    def test_list_edges_by_target(self, repo: KnowledgeGraphRepository) -> None:
        repo.upsert_node("material-45steel", "material", {})
        repo.upsert_node("tool-endmill-10", "tool", {})
        repo.upsert_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            confidence=0.9,
        )
        edges = repo.list_edges_by_target("material-45steel")
        assert len(edges) == 1
        assert edges[0].source_id == "tool-endmill-10"

    def test_list_edges_by_confidence(self, repo: KnowledgeGraphRepository) -> None:
        repo.upsert_node("material-45steel", "material", {})
        repo.upsert_node("material-al6061", "material", {})
        repo.upsert_node("tool-endmill-10", "tool", {})
        repo.upsert_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            confidence=0.9,
        )
        repo.upsert_edge(
            "tool-endmill-10",
            "material-al6061",
            "SUITABLE_FOR",
            confidence=0.6,
        )
        # min=0.7
        edges = repo.list_edges_by_confidence(min_confidence=0.7)
        assert len(edges) == 1
        # [0.5, 0.7]
        edges2 = repo.list_edges_by_confidence(min_confidence=0.5, max_confidence=0.7)
        assert len(edges2) == 1
        # edge_type 过滤
        edges3 = repo.list_edges_by_confidence(min_confidence=0.0, edge_type="SUITABLE_FOR")
        assert len(edges3) == 2
        # 降序
        edges4 = repo.list_edges_by_confidence(min_confidence=0.0)
        assert edges4[0].confidence == 0.9

    def test_list_edges_by_confidence_invalid(self, repo: KnowledgeGraphRepository) -> None:
        with pytest.raises(ValueError):
            repo.list_edges_by_confidence(min_confidence=0.8, max_confidence=0.5)

    def test_delete_edge(self, repo: KnowledgeGraphRepository) -> None:
        repo.upsert_node("material-45steel", "material", {})
        repo.upsert_node("tool-endmill-10", "tool", {})
        repo.upsert_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            confidence=0.9,
        )
        assert repo.delete_edge("tool-endmill-10", "material-45steel", "SUITABLE_FOR") is True
        assert repo.delete_edge("tool-endmill-10", "material-45steel", "SUITABLE_FOR") is False

    def test_count_edges(self, repo: KnowledgeGraphRepository) -> None:
        assert repo.count_edges() == 0
        repo.upsert_node("material-45steel", "material", {})
        repo.upsert_node("tool-endmill-10", "tool", {})
        repo.upsert_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            confidence=0.9,
        )
        assert repo.count_edges() == 1
        assert repo.count_edges("SUITABLE_FOR") == 1
        assert repo.count_edges("APPLIED_TO") == 0


# 4. GraphPersistence 双向同步


@pytest.fixture
def persistence_repo() -> Iterator[KnowledgeGraphRepository]:
    """带独立内存 SQLite 的 Persistence fixture。"""
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    try:
        yield KnowledgeGraphRepository(session_factory=factory)
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


class TestGraphPersistence:
    """GraphPersistence 双向同步测试。"""

    def _build_graph(self) -> GraphStore:
        g = GraphStore()
        g.add_node("material", "material-45steel", {"name": "45 steel"})
        g.add_node("material", "material-al6061", {"name": "Al 6061"})
        g.add_node("tool", "tool-endmill-10", {"name": "Endmill D10"})
        g.add_node("feature", "feature-pocket", {"name": "Pocket"})
        g.add_node("process", "process-face-mill", {"name": "Face Milling"})
        g.add_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            {"confidence": 0.9, "source": "rule"},
        )
        g.add_edge(
            "tool-endmill-10",
            "material-al6061",
            "SUITABLE_FOR",
            {"confidence": 0.7, "source": "rule"},
        )
        g.add_edge(
            "process-face-mill",
            "feature-pocket",
            "APPLIED_TO",
            {"confidence": 0.6, "source": "rule"},
        )
        return g

    def test_flush_then_load_roundtrip(self, persistence_repo: KnowledgeGraphRepository) -> None:
        g = self._build_graph()
        # 使用同一个 session_factory
        persistence = GraphPersistence(session_factory=persistence_repo._session_factory)
        stats = persistence.flush_to_repository(g)
        assert stats["nodes_written"] == 5
        assert stats["edges_written"] == 3

        # 重新构造内存图并加载
        g2 = GraphStore()
        stats2 = persistence.load_from_repository(g2)
        assert stats2["nodes_loaded"] == 5
        assert stats2["edges_loaded"] == 3
        assert g2.node_count() == 5
        assert g2.edge_count() == 3

    def test_persistence_survives_reload(self, persistence_repo: KnowledgeGraphRepository) -> None:
        """模拟服务重启：GraphStore 重新创建后通过 load 恢复。"""
        # 第一次会话：写入数据
        g1 = GraphStore()
        g1.add_node("material", "material-45steel", {"name": "45 steel"})
        g1.add_node("tool", "tool-endmill-10", {"name": "Endmill D10"})
        g1.add_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            {"confidence": 0.9, "source": "rule"},
        )
        persistence = GraphPersistence(session_factory=persistence_repo._session_factory)
        persistence.flush_to_repository(g1)

        # 第二次会话：模拟重启
        g2 = GraphStore()
        assert g2.node_count() == 0
        persistence.load_from_repository(g2)
        assert g2.node_count() == 2
        # 边也恢复
        edges = g2.list_edges_by_type("SUITABLE_FOR")
        assert len(edges) == 1
        assert edges[0]["properties"]["confidence"] == 0.9
        # 节点属性保留
        node = g2.get_node("material-45steel")
        assert node is not None
        assert node["properties"]["name"] == "45 steel"

    def test_flush_with_clear_first(self, persistence_repo: KnowledgeGraphRepository) -> None:
        persistence = GraphPersistence(session_factory=persistence_repo._session_factory)

        # 第一次写入
        g1 = self._build_graph()
        persistence.flush_to_repository(g1)
        assert persistence_repo.count_nodes() == 5

        # 第二次构建更小的图，clear_first=True
        g2 = GraphStore()
        g2.add_node("material", "material-45steel", {"name": "45 steel"})
        persistence.flush_to_repository(g2, clear_first=True)
        # 仅保留 g2 的 1 个节点
        assert persistence_repo.count_nodes() == 1
        assert persistence_repo.count_edges() == 0

    def test_flush_upsert_updates_existing(self, persistence_repo: KnowledgeGraphRepository) -> None:
        persistence = GraphPersistence(session_factory=persistence_repo._session_factory)

        g1 = GraphStore()
        g1.add_node("material", "material-45steel", {"name": "45 steel"})
        g1.add_node("tool", "tool-endmill-10", {"name": "Endmill D10"})
        g1.add_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            {"confidence": 0.5},
        )
        persistence.flush_to_repository(g1)

        # 第二次：更新节点属性和关系 confidence
        g2 = GraphStore()
        g2.add_node("material", "material-45steel", {"name": "45 steel", "hardness_hb": 197.0})
        g2.add_node("tool", "tool-endmill-10", {"name": "Endmill D10"})
        g2.add_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            {"confidence": 0.95},
        )
        persistence.flush_to_repository(g2)

        # 节点属性被合并
        node = persistence_repo.get_node("material-45steel")
        assert node is not None
        assert node.properties == {
            "name": "45 steel",
            "hardness_hb": 197.0,
        }
        # 关系 confidence 被更新
        edge = persistence_repo.get_edge("tool-endmill-10", "material-45steel", "SUITABLE_FOR")
        assert edge is not None
        assert edge.confidence == 0.95

    def test_end_to_end_persistence_acceptance(self, persistence_repo: KnowledgeGraphRepository) -> None:
        """任务 M1.2 验收脚本的等价测试。"""
        persistence = GraphPersistence(session_factory=persistence_repo._session_factory)

        # 创建并落库
        g = GraphStore()
        g.add_node("material", "material-45steel", {"name": "45 steel"})
        g.add_node("tool", "tool-endmill-10", {"name": "Endmill D10"})
        g.add_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            {"confidence": 0.9, "source": "rule"},
        )
        persistence.flush_to_repository(g)

        # 重启 GraphStore 实例
        g2 = GraphStore()
        persistence.load_from_repository(g2)

        # 验证：节点数量 >= 2
        assert g2.node_count() >= 2
        assert g2.edge_count() == 1

    def test_load_from_empty_db(self, persistence_repo: KnowledgeGraphRepository) -> None:
        """空数据库加载不应抛错。"""
        persistence = GraphPersistence(session_factory=persistence_repo._session_factory)
        g = GraphStore()
        stats = persistence.load_from_repository(g)
        assert stats["nodes_loaded"] == 0
        assert stats["edges_loaded"] == 0
        assert g.node_count() == 0

    def test_load_does_not_overwrite_existing_when_replace_false(
        self, persistence_repo: KnowledgeGraphRepository
    ) -> None:
        persistence = GraphPersistence(session_factory=persistence_repo._session_factory)

        # 先持久化 2 个节点
        g_seed = self._build_graph()
        persistence.flush_to_repository(g_seed)

        # 构造一个新图（不替换）并加载
        g_target = GraphStore()
        g_target.add_node("custom", "custom-extra", {"k": "v"})
        stats = persistence.load_from_repository(g_target, replace=False)
        # 加载的节点数等于 DB 中节点数（5）
        assert stats["nodes_loaded"] == 5
        # 自定义节点仍存在
        assert g_target.has_node("custom-extra")
        # DB 节点也加载
        assert g_target.has_node("material-45steel")
        assert g_target.node_count() == 6


# 5. 集成测试：API 验收场景


class TestAcceptanceScenarios:
    """任务 M1.2 验收脚本场景。"""

    def test_acceptance_end_to_end(self, persistence_repo: KnowledgeGraphRepository) -> None:
        """模拟任务描述中的端到端验收脚本。"""
        persistence = GraphPersistence(session_factory=persistence_repo._session_factory)

        g = GraphStore()
        g.add_node("material", "material-45steel", {"name": "45 steel"})
        g.add_node("tool", "tool-endmill-10", {"name": "Endmill D10"})
        g.add_edge(
            "tool-endmill-10",
            "material-45steel",
            "SUITABLE_FOR",
            {"confidence": 0.9, "source": "rule"},
        )
        persistence.flush_to_repository(g)

        # 重启 GraphStore 实例
        g2 = GraphStore()
        persistence.load_from_repository(g2)

        # 输出的节点数量(Node count)必须大于等于2
        assert g2.node_count() >= 2
