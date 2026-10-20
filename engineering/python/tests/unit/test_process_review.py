"""Human review, atomic persistence, authorization and invalidation acceptance."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.rag import process_quadruple as module
from app.rag import review_routes
from app.rag.process_review import content_digest, is_approved
from tests.utils.process_applicability import drilling_limits, machining_conditions

pytestmark = pytest.mark.unit


def recipe():
    return module.ProcessQuadruple("hole", "drill", "fixture_d8", {"spindle_rpm": 1200}, material="fixture_steel", source="manual")


def evidence():
    return {"source_url": "https://example.org/synthetic-test.pdf", "document_sha256": "a" * 64,
            "page": 1, "unresolved": [], "material": "fixture_steel", "tool": "fixture_d8",
            "parameter_units": {"spindle_rpm": "rpm"}, "applicability": "Synthetic test only; diameter 8 mm",
            "verification_method": "Synthetic fixture, no production approval", "constraints": drilling_limits()}


def action(index, record, decision="approved", **kwargs):
    return index.update_review(record["record_id"], record["revision"], actor="fixture_engineer",
                               decision=decision, note="Synthetic test only", evidence=evidence(), **kwargs)


@pytest.fixture
def stored(tmp_path):
    index = module.ProcessQuadrupleIndex(str(tmp_path))
    index.add(recipe())
    return index, index.list_review_records()[0]


def test_complete_lifecycle_and_restart(stored, tmp_path):
    index, pending = stored
    assert index.recommend_process("hole", "fixture_steel") == []
    approved = action(index, pending)
    assert is_approved(approved)
    assert index.recommend_process("hole", "fixture_steel", conditions=machining_conditions())[0]["revision"] == 2
    index = module.ProcessQuadrupleIndex(str(tmp_path))
    assert index.recommend_process("hole", "fixture_steel", conditions=machining_conditions())[0]["review"]["actor"] == "fixture_engineer"
    revised = action(index, approved, "revise", replacement={**recipe().to_dict(), "parameters": {"spindle_rpm": 900}})
    assert revised["review"]["status"] == "pending"
    assert revised["review_history"][1]["before"]["parameters"]["spindle_rpm"] == 1200
    assert index.recommend_process("hole", "fixture_steel") == []
    rejected = action(index, revised, "rejected")
    with pytest.raises(module.ReviewConflict):
        action(index, rejected)
    revised = action(index, rejected, "revise", replacement=recipe().to_dict())
    approved = action(index, revised)
    revoked = action(index, approved, "revoked")
    assert revoked["revision"] == 7
    assert len(revoked["review_history"]) == 6
    assert module.ProcessQuadrupleIndex(str(tmp_path)).recommend_process("hole", "fixture_steel") == []


def test_stale_version_does_not_overwrite(stored):
    index, pending = stored
    approved = action(index, pending)
    with pytest.raises(module.ReviewConflict, match="版本"):
        action(index, pending, "rejected")
    assert index.list_review_records()[0] == approved


@pytest.mark.parametrize("field,value", [("source_url", "http://example.org"), ("source_url", "https://a:b@example.org"),
    ("document_sha256", "wrong"), ("page", True), ("page", 0), ("unresolved", ["material unknown"]),
    ("unresolved", None), ("material", "other"), ("tool", "other"), ("parameter_units", {}),
    ("parameter_units", {"spindle_rpm": " "}), ("applicability", ""), ("verification_method", "")])
def test_incomplete_evidence_cannot_approve(stored, field, value):
    index, pending = stored
    invalid = {**evidence(), field: value}
    with pytest.raises(ValueError):
        index.update_review(pending["record_id"], 1, actor="engineer", decision="approved", note="test", evidence=invalid)
    assert index.list_review_records()[0] == pending


def test_failed_write_does_not_approve_or_lose_history(stored, tmp_path, monkeypatch):
    index, pending = stored
    before = (tmp_path / "process_quadruple.json").read_bytes()
    monkeypatch.setattr(module.os, "replace", Mock(side_effect=PermissionError("fixture")))
    with pytest.raises(RuntimeError, match="变更已撤回"):
        action(index, pending)
    assert index.list_review_records()[0] == pending
    assert index.recommend_process("hole", "fixture_steel") == []
    assert (tmp_path / "process_quadruple.json").read_bytes() == before


def test_label_and_body_cannot_forge_review(stored):
    index, pending = stored
    approved = action(index, pending)
    forged = module.ProcessQuadruple.from_dict(approved)
    assert forged.review == {"status": "pending"}
    assert forged.record_id != approved["record_id"]
    forged.review = approved["review"]
    index.add(forged)
    assert index.list_review_records()[1]["review"] == {"status": "pending"}
    result = deepcopy(approved)
    result["parameters"]["spindle_rpm"] = 9000
    assert not is_approved(result)
    assert content_digest(result) != approved["review"]["digest"]


def test_objects_returned_or_submitted_cannot_mutate_review(stored):
    index, pending = stored
    action(index, pending)
    returned = index.find_by_feature_process("hole", "drill")[0]
    returned.parameters["spindle_rpm"] = 9000
    assert index.recommend_process("hole", "fixture_steel", conditions=machining_conditions())[0]["parameters"]["spindle_rpm"] == 1200
    new = recipe()
    index.add(new)
    new.parameters["spindle_rpm"] = 2
    assert index.list_review_records()[1]["parameters"]["spindle_rpm"] == 1200


@pytest.mark.parametrize("actor,note", [("_anonymous_", "test"), ("", "test"), ("engineer", " ")])
def test_invalid_identity_or_note(stored, actor, note):
    index, pending = stored
    with pytest.raises(ValueError):
        index.update_review(pending["record_id"], 1, actor=actor, decision="approved", note=note, evidence=evidence())


@pytest.fixture
def client(stored, monkeypatch):
    index, _ = stored
    monkeypatch.setattr(review_routes, "get_process_quadruple_index", lambda: index)
    app = FastAPI()
    app.include_router(review_routes.router)
    app.dependency_overrides[review_routes.get_current_user] = lambda: {"username": "engineer_a", "role": "engineer"}
    with TestClient(app) as test_client:
        yield test_client, app


def test_api_identity_is_server_derived_and_stale_returns_409(client, stored):
    http, _ = client
    _, pending = stored
    url = f'/api/rag/process/review/{pending["record_id"]}'
    body = {"expected_revision": 1, "decision": "approved", "note": "Synthetic", "evidence": evidence()}
    assert http.post(url, json={**body, "actor": "admin_forged"}).status_code == 422
    response = http.post(url, json=body)
    assert response.status_code == 200
    assert response.json()["review"]["actor"] == "engineer_a"
    assert http.post(url, json=body).status_code == 409
    assert len(http.get("/api/rag/process/review/records").json()["records"]) == 1


@pytest.mark.parametrize("identity", [{"username": "_anonymous_", "role": "T"},
    {"username": "guest", "role": "admin", "is_guest": True}, {"username": "operator", "role": "operator"}])
def test_api_denies_unqualified_users_even_when_permission_switch_off(client, monkeypatch, identity):
    http, app = client
    monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "false")
    app.dependency_overrides[review_routes.get_current_user] = lambda: identity
    assert http.get("/api/rag/process/review/records").status_code == 403


def test_api_validation_and_storage_failures(client, stored, monkeypatch):
    http, _ = client
    index, pending = stored
    url = f'/api/rag/process/review/{pending["record_id"]}'
    body = {"expected_revision": 1, "decision": "approved", "note": "Synthetic"}
    assert http.post(url, json=body).status_code == 400
    assert http.post(url, json={**body, "decision": "revise"}).status_code == 400
    assert http.post("/api/rag/process/review/missing", json=body).status_code == 404
    monkeypatch.setattr(index, "flush", lambda **kwargs: False)
    assert http.post(url, json={**body, "evidence": evidence()}).status_code == 503


@pytest.mark.parametrize("value", [0, -1, True, "1200", [1, 2]])
def test_non_numeric_or_non_positive_parameters_cannot_be_approved(value):
    index = module.ProcessQuadrupleIndex()
    quad = recipe()
    quad.parameters["spindle_rpm"] = value
    index.add(quad)
    with pytest.raises(ValueError, match="有限正数"):
        action(index, index.list_review_records()[0])


def test_general_material_cannot_be_approved():
    index = module.ProcessQuadrupleIndex()
    quad = recipe()
    quad.material = "general"
    index.add(quad)
    with pytest.raises(ValueError, match="材料"):
        action(index, index.list_review_records()[0])


def test_invalid_approved_evidence_and_missing_actor_fail_closed(stored):
    index, pending = stored
    approved = action(index, pending)
    approved["review"]["evidence"] = None
    assert not is_approved(approved)
    approved = index.list_review_records()[0]
    approved["review"]["actor"] = ""
    assert not is_approved(approved)


def test_duplicate_batch_is_atomic(stored):
    index, pending = stored
    quad = recipe()
    with pytest.raises(ValueError, match="重复"):
        index.add_batch([quad, quad])
    assert index.list_review_records() == [pending]


@pytest.mark.parametrize("fail_write", [False, True])
def test_revision_updates_indexes_and_rolls_back_together(stored, tmp_path, monkeypatch, fail_write):
    index, pending = stored
    replacement = {**recipe().to_dict(), "feature": "pocket", "process": "mill", "material": "fixture_aluminum"}
    if fail_write:
        monkeypatch.setattr(index, "flush", lambda **kwargs: False)
        with pytest.raises(RuntimeError, match="变更已撤回"):
            action(index, pending, "revise", replacement=replacement)
        assert index.get_features() == ["hole"]
        assert index.find_by_feature_process("pocket", "mill") == []
        assert index.list_review_records()[0] == pending
    else:
        revised = action(index, pending, "revise", replacement=replacement)
        assert index.get_features() == ["pocket"]
        assert index.find_by_feature_process("hole", "drill") == []
        restarted = module.ProcessQuadrupleIndex(str(tmp_path))
        assert restarted.find_by_feature_process("pocket", "mill")[0].record_id == pending["record_id"]
        assert restarted.list_review_records()[0] == revised


def test_legacy_migration_identity_is_stable_without_implicit_write(tmp_path):
    legacy = recipe().to_dict()
    for key in ("record_id", "revision", "review", "review_history"):
        legacy.pop(key)
    path = tmp_path / "process_quadruple.json"
    path.write_text(json.dumps({"quadruples": [legacy, legacy]}), encoding="utf-8")
    before = path.read_bytes()
    first = module.ProcessQuadrupleIndex(str(tmp_path)).list_review_records()
    second = module.ProcessQuadrupleIndex(str(tmp_path)).list_review_records()
    assert first == second
    assert first[0]["record_id"] != first[1]["record_id"]
    assert all(record["review"]["status"] == "pending" for record in first)
    assert path.read_bytes() == before


def test_pending_context_survives_restart_without_implying_approval(tmp_path):
    index = module.ProcessQuadrupleIndex(str(tmp_path))
    quad = recipe()
    quad.candidate_context = {"source_url": "https://example.org/synthetic.pdf", "page": 2,
                              "parameter_units": {"spindle_rpm": "rpm"}, "unresolved": ["material mapping"],
                              "note": "Synthetic candidate"}
    index.add(quad)
    restored = module.ProcessQuadrupleIndex(str(tmp_path))
    record = restored.list_review_records()[0]
    assert record["candidate_context"] == quad.candidate_context
    assert record["review"] == {"status": "pending"}
    assert restored.recommend_process("hole", "fixture_steel") == []
    updated = action(restored, record, "revise", replacement={**quad.to_dict(), "parameters": {"spindle_rpm": 900}})
    assert updated["candidate_context"] == quad.candidate_context


@pytest.mark.parametrize("context", [None, {"approved": True}, {"note": 5}, {"page": True}, {"page": 0},
    {"parameter_units": []}, {"parameter_units": {"rpm": 5}}, {"unresolved": "unknown"}, {"unresolved": [5]}])
def test_malformed_pending_context_rejected(context):
    data = recipe().to_dict()
    data["candidate_context"] = context
    with pytest.raises(ValueError):
        module.ProcessQuadruple.from_dict(data)
