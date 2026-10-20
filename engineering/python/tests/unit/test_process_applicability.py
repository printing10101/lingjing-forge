"""A current approval is necessary but insufficient for task applicability."""
from copy import deepcopy
import json
import math
import httpx
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.agent.auth import AgentTokenStore
from app.auth import middleware
from app.api.v1.agent_gateway.cam import router
from app.rag import process_quadruple as quads
from app.rag.process_applicability import DrillingLimits, check_applicability, validate_limits_draft, validate_supplied_conditions
from app.rag.process_review import is_approved
from tests.utils.process_applicability import drilling_limits, machining_conditions

pytestmark = pytest.mark.unit


def evidence():
    return {"source_url": "https://example.org/synthetic.pdf", "document_sha256": "a" * 64,
        "page": 1, "material": "fixture_steel", "tool": "fixture_d8", "parameter_units": {"spindle_rpm": "rpm"},
        "applicability": "Synthetic only", "verification_method": "Synthetic fixture, no manufacturing approval",
        "unresolved": [], "constraints": drilling_limits()}


@pytest.fixture
def stored(tmp_path, monkeypatch):
    index = quads.ProcessQuadrupleIndex(str(tmp_path / "knowledge"))
    index.add(quads.ProcessQuadruple("hole", "drill", "fixture_d8", {"spindle_rpm": 1200}, material="fixture_steel"))
    record = index.list_review_records()[0]
    approved = index.update_review(record["record_id"], 1, actor="synthetic_engineer",
        decision="approved", note="Synthetic only", evidence=evidence())
    monkeypatch.setattr(quads, "get_process_quadruple_index", lambda: index)
    return index, approved


def test_approval_requires_task_conditions_and_restarts_preserve_limits(stored, tmp_path):
    index, record = stored
    assert is_approved(record)
    assert index.recommend_process("hole", "fixture_steel") == []
    result = index.assess_recommendations("hole", "fixture_steel")
    assert result["applicability_checks"][0]["status"] == "conditions_missing"
    assert len(result["applicability_checks"][0]["missing"]) == 6
    restored = quads.ProcessQuadrupleIndex(str(tmp_path / "knowledge"))
    assert restored.recommend_process("hole", "fixture_steel", conditions=machining_conditions())[0]["record_id"] == record["record_id"]


@pytest.mark.parametrize("key,value,reason", [("tool", "other", "tool"), ("hardness_hrc", 24.0, "hardness_hrc"),
    ("hardness_hrc", 24.1, "hardness_hrc"), ("diameter_mm", 7.9375, "diameter_mm"),
    ("diameter_mm", 8.00001, "diameter_mm"), ("depth_mm", math.nextafter(32.0, math.inf), "depth_mm"),
    ("coolant", "dry", "coolant"), ("workholding", "chuck", "workholding")])
def test_each_mismatch_blocks_parameters(stored, key, value, reason):
    index, _ = stored
    result = index.assess_recommendations("hole", "fixture_steel", conditions={**machining_conditions(), key: value})
    assert result["recommendations"] == []
    assert reason in result["applicability_checks"][0]["mismatched"]
    assert result["applicability_checks"][0]["status"] == "conditions_mismatch"


def test_explicit_inclusive_upper_boundary_and_case_normalization(stored):
    _, record = stored
    record["review"]["evidence"]["constraints"]["hardness_max_inclusive"] = True
    result = check_applicability(record, {**machining_conditions(), "tool": " FIXTURE_D8 ", "hardness_hrc": 24.0})
    assert result["status"] == "matched"
    assert check_applicability(record, {**machining_conditions(), "hardness_hrc": 0.0})["status"] == "matched"


@pytest.mark.parametrize("key,value", [("schema_version", True), ("schema_version", 1.0), ("schema_version", 2),
    ("hardness_min_hrc", -1.0), ("hardness_max_hrc", 101.0), ("hardness_max_inclusive", "true"),
    ("diameter_min_mm", 0.0), ("max_depth_to_diameter", float("inf")), ("diameter_max_mm", float("nan")),
    ("coolants", []), ("coolants", ["unknown"]), ("workholdings", ["unknown"]), ("workholdings", []),
    ("hardness_min_hrc", 25.0), ("hardness_min_hrc", 24.0), ("diameter_min_mm", 9.0), ("approved", True)])
def test_bad_review_limits_rejected(key, value):
    with pytest.raises(ValueError):
        DrillingLimits.model_validate({**drilling_limits(), key: value})


def test_evidence_tampering_and_legacy_approval_cannot_be_used(stored):
    _, record = stored
    tampered = deepcopy(record)
    tampered["review"]["evidence"]["constraints"]["hardness_max_hrc"] = 50.0
    assert not is_approved(tampered)
    tampered = deepcopy(record)
    tampered["review"].pop("evidence_digest")
    assert not is_approved(tampered)
    tampered["review"]["evidence"].pop("constraints")
    assert not is_approved(tampered)
    assert check_applicability(tampered, machining_conditions())["status"] == "record_constraints_missing"
    unsupported = {**record, "feature": "pocket", "process": "mill"}
    assert check_applicability(unsupported, machining_conditions())["status"] == "unsupported_process"


def test_revise_and_revoke_invalidate_applicability(stored):
    index, record = stored
    revised = index.update_review(record["record_id"], 2, actor="synthetic_engineer", decision="revise", note="Synthetic",
        replacement={**record, "parameters": {"spindle_rpm": 900}})
    assert index.recommend_process("hole", "fixture_steel", conditions=machining_conditions()) == []
    approved = index.update_review(record["record_id"], revised["revision"], actor="synthetic_engineer",
        decision="approved", note="Synthetic", evidence=evidence())
    assert index.recommend_process("hole", "fixture_steel", conditions=machining_conditions())[0]["revision"] == approved["revision"]
    index.update_review(record["record_id"], approved["revision"], actor="synthetic_engineer", decision="revoked", note="Synthetic")
    assert index.recommend_process("hole", "fixture_steel", conditions=machining_conditions()) == []


@pytest.mark.parametrize("value", [[], "bad", {"unknown": 1}, {"depth_mm": 0}, {"diameter_mm": float("nan")},
    {"hardness_hrc": True}, {"hardness_hrc": "20"}, {"coolant": "unknown"}, {"tool": " "}])
def test_invalid_partial_conditions_do_not_disappear_into_missing(value):
    with pytest.raises(ValueError):
        validate_supplied_conditions(value)


def test_partial_limits_persist_but_cannot_approve(tmp_path):
    validate_limits_draft({"schema_version": 1, "hardness_max_hrc": 24.0})
    index = quads.ProcessQuadrupleIndex(str(tmp_path))
    index.add(quads.ProcessQuadruple("hole", "drill", "fixture_d8", {"spindle_rpm": 1200}, material="fixture_steel",
        candidate_context={"constraints": {"schema_version": 1, "hardness_max_hrc": 24.0}}))
    assert quads.ProcessQuadrupleIndex(str(tmp_path)).list_review_records()[0]["candidate_context"]["constraints"]["hardness_max_hrc"] == 24.0
    record = index.list_review_records()[0]
    with pytest.raises(ValueError):
        index.update_review(record["record_id"], 1, actor="synthetic_engineer", decision="approved", note="Synthetic",
            evidence={**evidence(), "constraints": record["candidate_context"]["constraints"]})


def test_missing_limits_cannot_be_approved(stored):
    index, record = stored
    pending = index.update_review(record["record_id"], 2, actor="synthetic_engineer", decision="revise", note="Synthetic", replacement=record)
    incomplete = evidence()
    incomplete.pop("constraints")
    with pytest.raises(ValueError):
        index.update_review(record["record_id"], pending["revision"], actor="synthetic_engineer", decision="approved", note="Synthetic", evidence=incomplete)
    assert index.list_review_records()[0]["review"]["status"] == "pending"


@pytest.mark.parametrize("limits", [[], {"unknown": 1}, {"schema_version": True},
    {"hardness_min_hrc": 25.0, "hardness_max_hrc": 24.0},
    {"diameter_min_mm": 9.0, "diameter_max_mm": 8.0}, {"diameter_min_mm": float("inf")}])
def test_invalid_limits_drafts_are_rejected(limits):
    with pytest.raises(ValueError):
        validate_limits_draft(limits)


def test_blank_full_tool_is_invalid_and_limits_are_inclusive_at_diameter_bounds(stored):
    _, record = stored
    with pytest.raises(ValueError, match="刀具为空"):
        check_applicability(record, {**machining_conditions(), "tool": " "})
    assert check_applicability(record, machining_conditions())["status"] == "matched"


def test_filter_before_top_k_does_not_hide_lower_ranked_applicable_record(stored):
    index, approved = stored
    index.add(quads.ProcessQuadruple("hole", "drill", "fixture_other", {"spindle_rpm": 900},
        material="fixture_steel", confidence=1.0))
    other = index.list_review_records()[-1]
    index.update_review(other["record_id"], 1, actor="synthetic_engineer", decision="approved", note="Synthetic",
        evidence={**evidence(), "tool": "fixture_other"})
    result = index.assess_recommendations("hole", "fixture_steel", top_k=1, conditions=machining_conditions())
    assert result["recommendations"][0]["record_id"] == approved["record_id"]
    assert len(result["applicability_checks"]) == 2


@pytest.fixture
def client(stored, tmp_path, monkeypatch):
    tokens = AgentTokenStore(str(tmp_path / "tokens.json"))
    monkeypatch.setattr(middleware, "_get_agent_token_store", lambda: tokens)
    monkeypatch.setattr(middleware.agent_audit_log, "log", Mock())
    app = FastAPI()
    app.add_middleware(middleware.UnifiedAuthMiddleware, lnn_auth_enabled=False)
    app.include_router(router, prefix="/api/agent/v1")
    token, _ = tokens.create_token(["R"], paper_only=True)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as http:
        yield http


@pytest.mark.parametrize("conditions,status,count", [(None, "conditions_missing", 0),
    ({"tool": "fixture_d8"}, "conditions_missing", 0), (machining_conditions(), "matched", 1),
    ({**machining_conditions(), "hardness_hrc": 24.0}, "conditions_mismatch", 0)])
def test_authenticated_api_returns_honest_decision(client, conditions, status, count):
    params = {"feature": "hole", "material": "fixture_steel"}
    if conditions is not None:
        params["conditions"] = json.dumps(conditions)
    response = client.get("/api/agent/v1/cam/process-recommend", params=params)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["recommendation_status"] == status
    assert len(data["recommendations"]) == count
    assert data["physical_validation_required"] is True


@pytest.mark.parametrize("value", ["null", "[]", "bad", '{"depth_mm":NaN}', '{"unknown":1}', '{"depth_mm":-1}'])
def test_api_invalid_conditions_are_422_without_nonfinite_reflection(client, value):
    response = client.get("/api/agent/v1/cam/process-recommend", params={"feature": "hole", "material": "fixture_steel", "conditions": value})
    assert response.status_code == 422
    assert "NaN" not in response.text


def test_api_other_process_is_explicitly_unsupported(client, stored):
    index, _ = stored
    index.add(quads.ProcessQuadruple("pocket", "mill", "fixture_d8", {"spindle_rpm": 1200}, material="fixture_steel"))
    record = index.list_review_records()[-1]
    index.update_review(record["record_id"], 1, actor="synthetic_engineer", decision="approved", note="Synthetic", evidence=evidence())
    response = client.get("/api/agent/v1/cam/process-recommend", params={"feature": "pocket", "material": "fixture_steel", "conditions": json.dumps(machining_conditions())})
    assert response.json()["data"]["recommendation_status"] == "unsupported_process"
    assert response.json()["data"]["recommendations"] == []


@pytest.mark.asyncio
async def test_mcp_conditions_traverse_authenticated_gateway(client, monkeypatch):
    from mcp_server import cam_tools
    original = httpx.AsyncClient
    monkeypatch.setattr(cam_tools, "BASE_URL", "http://synthetic")
    monkeypatch.setattr(cam_tools, "_headers", lambda: {"Authorization": client.headers["Authorization"]})
    monkeypatch.setattr(cam_tools.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.ASGITransport(app=client.app), **kwargs))
    for conditions, status, count in ((machining_conditions(), "matched", 1),
        ({**machining_conditions(), "hardness_hrc": 24.0}, "conditions_mismatch", 0), (None, "conditions_missing", 0)):
        result = json.loads(await cam_tools._handle_recommend_process("hole", "fixture_steel", 3, conditions))
        assert result["data"]["recommendation_status"] == status
        assert len(result["data"]["recommendations"]) == count
