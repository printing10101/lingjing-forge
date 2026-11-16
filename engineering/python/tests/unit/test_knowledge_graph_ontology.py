"""T3 补测（六）：本体注册表与提案审核。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.knowledge_graph.ontology_proposal import (
    Evidence,
    OntologyRegistry,
    Proposal,
)

pytestmark = pytest.mark.unit


def test_fixed_default_registry_knowledge():
    registry = OntologyRegistry.fixed_default()
    assert registry.is_registered("entity_type", "Material") is True
    assert registry.is_registered("entity_type", "unknown_type") is False
    assert registry.is_registered("bogus_kind", "x") is False
    assert "Material" in registry.registered("entity_type")
    with pytest.raises(ValueError):
        registry.registered("bogus_kind")


def test_propose_pending_and_approve_flow():
    registry = OntologyRegistry.fixed_default()
    evidence = Evidence(source_id="sub-1", field="entity_types", excerpt="新增：夹具板")
    proposal = registry.propose(kind="entity_type", name="fixture_plate",
                                evidence=evidence, rationale="现场出现新零件类型")
    assert proposal.status == "pending"
    assert proposal.occurrences == 1
    assert [item.proposal_id for item in registry.pending()] == [proposal.proposal_id]
    assert registry.get("no-such-id") is None
    assert isinstance(registry.get(proposal.proposal_id), Proposal)

    assert registry.approve(proposal.proposal_id, reviewer="工程师A") is True
    assert registry.is_registered("entity_type", "fixture_plate") is True
    assert registry.pending() == []
    # 已批准提案再次批准返回 False
    assert registry.approve(proposal.proposal_id, reviewer="工程师A") is False
    # 已登记类型不可再提案
    with pytest.raises(ValueError, match="already registered"):
        registry.propose(kind="entity_type", name="fixture_plate")


def test_approve_and_reject_require_reviewer():
    registry = OntologyRegistry.fixed_default()
    proposal = registry.propose(kind="relation_type", name="CLAMPED_BY")
    with pytest.raises(ValueError, match="anonymous approval"):
        registry.approve(proposal.proposal_id, reviewer="  ")
    with pytest.raises(ValueError, match="anonymous rejection"):
        registry.reject(proposal.proposal_id, reviewer="")
    with pytest.raises(KeyError):
        registry.approve("missing-id", reviewer="工程师A")

    assert registry.reject(proposal.proposal_id, reviewer="工程师B", reason="证据不足") is True
    assert registry.reject(proposal.proposal_id, reviewer="工程师B") is False
    assert registry.is_registered("relation_type", "CLAMPED_BY") is False
    # 被拒提案不静默重开
    again = registry.propose(kind="relation_type", name="CLAMPED_BY")
    assert again.status == "rejected"


def test_propose_merges_occurrences_and_evidence():
    registry = OntologyRegistry.fixed_default()
    first = registry.propose(kind="property_key", name="fixture_force_n")
    second = registry.propose(kind="property_key", name="fixture_force_n",
                              evidence=Evidence(source_id="sub-2", field="property_keys"))
    assert first.proposal_id == second.proposal_id
    assert second.occurrences == 2
    assert len(second.evidence) == 1


def test_propose_validates_kind_and_name():
    registry = OntologyRegistry.fixed_default()
    with pytest.raises(ValueError, match="unknown proposal kind"):
        registry.propose(kind="bogus", name="x")
    with pytest.raises(ValueError, match="must not be empty"):
        registry.propose(kind="entity_type", name="   ")


def test_registry_persistence_roundtrip(tmp_path: Path):
    db_path = tmp_path / "ontology.sqlite"
    registry = OntologyRegistry.fixed_default(db_path=db_path)
    proposal = registry.propose(kind="entity_type", name="fixture_plate")
    registry.approve(proposal.proposal_id, reviewer="工程师A")
    assert db_path.exists()

    reloaded = OntologyRegistry.fixed_default(db_path=db_path)
    assert reloaded.is_registered("entity_type", "fixture_plate") is True
    restored = reloaded.get(proposal.proposal_id)
    assert restored is not None and restored.status == "approved"


def test_scan_candidate_detects_new_types():
    registry = OntologyRegistry.fixed_default()
    proposals = registry.scan_candidate(
        {"entity_types": ["fixture_plate"], "relation_types": "CLAMPED_BY",
         "property_keys": ["fixture_force_n"]},
        source_id="sub-9", doc_sha256="abc123")
    kinds = {(item.kind, item.name) for item in proposals}
    assert ("entity_type", "fixture_plate") in kinds
    assert ("relation_type", "CLAMPED_BY") in kinds
    assert ("property_key", "fixture_force_n") in kinds
    assert all(item.status == "pending" for item in proposals)
    assert all(item.evidence for item in proposals)
    # 已登记类型不产生提案
    assert registry.scan_candidate({"entity_types": ["Material"]}, source_id="sub-9") == []
