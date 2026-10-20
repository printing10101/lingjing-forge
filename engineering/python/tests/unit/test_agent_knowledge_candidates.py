"""Authenticated MCP -> gateway -> human review -> recommendation lifecycle."""

import json
import os
from pathlib import Path
import sys
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.auth import middleware
from app.agent.auth import AgentTokenStore
from app.rag import process_quadruple as quads
from app.rag import review_routes
from app.api.v1.agent_gateway import knowledge_candidates as routes
from app.api.v1.agent_gateway.cam import router as cam_router
from tests.utils.process_applicability import drilling_limits, machining_conditions

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
os.environ.setdefault("LINGJING_AGENT_TOKEN", "synthetic-test-only-" + "x" * 32)
from mcp_server import knowledge_candidate_tools as mcp_tools  # noqa: E402

pytestmark = pytest.mark.unit
BASE = "/api/agent/v1/cam/knowledge-candidates"


def submission():
    return {
        "submission_id": "synthetic_001",
        "content": {
            "feature": "hole",
            "process": "drill",
            "tool": "fixture_d8",
            "material": "fixture_steel",
            "parameters": {"spindle_rpm": 1200},
            "candidate_context": {
                "source_url": "https://example.org/synthetic.pdf",
                "page": 1,
                "parameter_units": {"spindle_rpm": "rpm"},
                "unresolved": ["Synthetic only"],
            },
        },
    }


@pytest.fixture
def setup(tmp_path, monkeypatch):
    index = quads.ProcessQuadrupleIndex(str(tmp_path / "knowledge"))
    monkeypatch.setattr(quads, "get_process_quadruple_index", lambda: index)
    monkeypatch.setattr(routes, "get_process_quadruple_index", lambda: index)
    monkeypatch.setattr(review_routes, "get_process_quadruple_index", lambda: index)
    tokens = AgentTokenStore(str(tmp_path / "tokens.json"))
    monkeypatch.setattr(middleware, "_get_agent_token_store", lambda: tokens)
    monkeypatch.setattr(middleware.agent_audit_log, "log", Mock())
    app = FastAPI()
    app.add_middleware(middleware.UnifiedAuthMiddleware, lnn_auth_enabled=False)
    app.include_router(routes.router)
    app.include_router(cam_router, prefix="/api/agent/v1")
    app.include_router(review_routes.router)
    token, identity = tokens.create_token(["R", "W"], paper_only=True)
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "synthetic-http-attempt"}
    with TestClient(app) as client:
        yield client, index, tokens, headers, identity


def approve(index, record):
    return index.update_review(
        record["record_id"],
        record["revision"],
        actor="synthetic_engineer",
        decision="approved",
        note="Synthetic test only",
        evidence={
            "source_url": "https://example.org/synthetic.pdf",
            "document_sha256": "a" * 64,
            "page": 1,
            "unresolved": [],
            "material": record["material"],
            "tool": record["tool"],
            "parameter_units": {"spindle_rpm": "rpm"},
            "applicability": "Synthetic only",
            "verification_method": "Synthetic test",
            "constraints": drilling_limits(),
        },
    )


def test_authenticated_roundtrip_restart_review_and_redecision(setup, tmp_path, monkeypatch):
    client, index, _, headers, identity = setup
    # The old generic cache must never intercept this content-bound route.
    monkeypatch.setattr(
        middleware.idempotency_store, "check_and_set", Mock(side_effect=AssertionError("STALE_CACHE_USED"))
    )
    first = client.post(BASE, json=submission(), headers=headers)
    assert first.status_code == 200
    record = first.json()["data"]["record"]
    assert record["submission"]["agent_id"] == identity.agent_id
    assert record["review"] == {"status": "pending"}
    assert index.recommend_process("hole", "fixture_steel") == []
    assert index.get_stats()["approved_quadruples"] == 0
    pending = client.get("/api/agent/v1/cam/process-recommend?feature=hole&material=fixture_steel", headers=headers)
    assert pending.json()["data"]["recommendation_status"] == "knowledge_requires_review"
    restarted = quads.ProcessQuadrupleIndex(str(tmp_path / "knowledge"))
    monkeypatch.setattr(routes, "get_process_quadruple_index", lambda: restarted)
    monkeypatch.setattr(quads, "get_process_quadruple_index", lambda: restarted)
    assert client.post(BASE, json=submission(), headers=headers).json()["data"]["created"] is False
    approved = approve(restarted, record)
    status = client.get(f"{BASE}/{record['record_id']}", headers=headers).json()["data"]
    assert status["eligible_for_recommendation"] is True
    assert restarted.get_stats()["approved_quadruples"] == 1
    assert status["next_action"] == "query_recommendation"
    replay = client.post(BASE, json=submission(), headers=headers).json()["data"]
    assert replay["record"]["revision"] == 2 and replay["human_review_required"] is False
    recommendation = client.get(
        "/api/agent/v1/cam/process-recommend", headers=headers,
        params={"feature": "hole", "material": "fixture_steel", "conditions": json.dumps(machining_conditions())}
    )
    assert recommendation.json()["data"]["recommendations"][0]["record_id"] == record["record_id"]
    restarted.update_review(
        record["record_id"], 2, actor="synthetic_engineer", decision="revoked", note="Synthetic end"
    )
    assert (
        client.get(f"{BASE}/{record['record_id']}", headers=headers).json()["data"]["eligible_for_recommendation"]
        is False
    )
    assert restarted.recommend_process("hole", "fixture_steel") == []
    assert len(restarted.list_review_records()) == 1
    assert approved["revision"] == 2


def test_changed_content_conflicts_and_cross_agent_isolation(setup):
    client, index, tokens, headers, _ = setup
    record = client.post(BASE, json=submission(), headers=headers).json()["data"]["record"]
    changed = submission()
    changed["content"]["parameters"]["spindle_rpm"] = 999
    assert client.post(BASE, json=changed, headers=headers).status_code == 409
    token, _ = tokens.create_token(["R", "W"])
    other = {**headers, "Authorization": f"Bearer {token}"}
    assert client.get(f"{BASE}/{record['record_id']}", headers=other).status_code == 404
    assert (
        client.post(BASE, json=submission(), headers=other).json()["data"]["record"]["record_id"] != record["record_id"]
    )
    assert len(index.list_review_records()) == 2


@pytest.mark.parametrize("scope", [[], ["R"], ["W"]])
def test_missing_scopes_fail_closed(setup, scope, monkeypatch):
    client, _, tokens, _, _ = setup
    monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "false")
    token, _ = tokens.create_token(scope)
    assert (
        client.post(
            BASE, json=submission(), headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "attempt"}
        ).status_code
        == 403
    )


def test_missing_auth_and_idempotency_header(setup):
    client, _, _, headers, _ = setup
    assert client.post(BASE, json=submission()).status_code == 401
    assert client.post(BASE, json=submission(), headers={"Authorization": headers["Authorization"]}).status_code == 400
    assert client.get(BASE + "/missing", headers=headers).status_code == 404


def test_route_scope_guard_is_independent_of_outer_middleware():
    read_only = Request({"type": "http", "state": {"agent_id": "synthetic", "agent_scopes": ["R"]}})
    assert routes.reader(read_only) == "synthetic"
    with pytest.raises(HTTPException) as error:
        routes.writer(read_only)
    assert error.value.status_code == 403
    anonymous = Request({"type": "http", "state": {}})
    with pytest.raises(HTTPException) as error:
        routes.reader(anonymous)
    assert error.value.status_code == 403


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor", "admin"),
        ("review", {"status": "approved"}),
        ("submission", {}),
        ("confidence", 1),
        ("source", "manual"),
    ],
)
def test_body_cannot_forge_trust(setup, field, value):
    client, _, _, headers, _ = setup
    body = submission()
    body["content"][field] = value
    assert client.post(BASE, json=body, headers=headers).status_code == 422


@pytest.mark.parametrize("value", [False, 0, -1, "1200"])
def test_invalid_parameter_types(setup, value):
    client, _, _, headers, _ = setup
    body = submission()
    body["content"]["parameters"]["spindle_rpm"] = value
    assert client.post(BASE, json=body, headers=headers).status_code == 422


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_json_is_rejected_without_reflecting_invalid_json(setup, value):
    client, index, _, headers, _ = setup
    body = submission()
    body["content"]["parameters"]["spindle_rpm"] = value
    response = client.post(BASE, content=json.dumps(body), headers={**headers, "Content-Type": "application/json"})
    assert response.status_code == 422
    assert "NaN" not in response.text and "Infinity" not in response.text
    assert index.list_review_records() == []


def test_invalid_context_size_and_failed_persistence(setup, monkeypatch):
    client, index, _, headers, _ = setup
    body = submission()
    body["content"]["candidate_context"] = {"approved": True}
    assert client.post(BASE, json=body, headers=headers).status_code == 400
    body = submission()
    body["content"]["candidate_context"]["note"] = "x" * 65536
    assert client.post(BASE, json=body, headers=headers).status_code == 413
    monkeypatch.setattr(index, "flush", lambda **kwargs: False)
    assert client.post(BASE, json=submission(), headers=headers).status_code == 503
    assert index.list_review_records() == []


def test_submission_body_is_ignored_by_regular_import_and_survives_revision(setup):
    _, index, _, _, identity = setup
    body = submission()
    record, _ = index.submit_candidate(identity.agent_id, body["submission_id"], body["content"])
    assert quads.ProcessQuadruple.from_dict(record).submission == {}
    revised = index.update_review(
        record["record_id"],
        1,
        actor="synthetic_engineer",
        decision="revise",
        note="Synthetic revision",
        replacement={**body["content"], "parameters": {"spindle_rpm": 900}},
    )
    replay, created = index.submit_candidate(identity.agent_id, body["submission_id"], body["content"])
    assert not created and replay == revised


@pytest.mark.asyncio
async def test_mcp_handlers_traverse_real_auth_and_gateway(setup, monkeypatch):
    client, _, _, headers, _ = setup
    original = httpx.AsyncClient
    monkeypatch.setattr(mcp_tools, "BASE_URL", "http://synthetic")
    monkeypatch.setattr(mcp_tools, "_headers", lambda: {"Authorization": headers["Authorization"]})
    monkeypatch.setattr(
        mcp_tools.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.ASGITransport(app=client.app), **kwargs),
    )
    data = json.loads(await mcp_tools._handle_submit("synthetic_001", submission()["content"]))["data"]
    assert data["created"] and data["human_review_required"]
    status = json.loads(await mcp_tools._handle_status(data["record"]["record_id"]))["data"]
    assert status["next_action"] == "human_review"
    retry = json.loads(await mcp_tools._handle_submit("synthetic_001", submission()["content"]))["data"]
    assert retry["created"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [{"review": {}}, None, {"source": "manual"}, {"parameters": {"rpm": float("nan")}}, {"note": "x" * 70000}],
)
async def test_mcp_blocks_bad_input(content, monkeypatch):
    request = Mock(side_effect=AssertionError("HTTP_REACHED"))
    monkeypatch.setattr(mcp_tools, "_request", request)
    assert json.loads(await mcp_tools._handle_submit("synthetic", content))["error"]
    request.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 413, 422, 503])
async def test_mcp_http_failures_are_actionable_not_blind_retries(status, monkeypatch):
    async def failure(*args, **kwargs):
        request = httpx.Request("POST", "http://synthetic/candidate")
        raise httpx.HTTPStatusError("Synthetic", request=request, response=httpx.Response(status, request=request))
    monkeypatch.setattr(mcp_tools, "_request", failure)
    for response in (await mcp_tools._handle_submit("synthetic", submission()["content"]), await mcp_tools._handle_status("synthetic")):
        error = json.loads(response)
        assert error["error"] and error["detail"]["http_status"] == status
        assert error["detail"]["retryable"] is (status >= 500)
        if status == 409:
            assert "冲突" in error["message"]


@pytest.mark.asyncio
async def test_mcp_failures_and_registration(monkeypatch):
    async def failure(*args, **kwargs):
        raise httpx.ConnectError("synthetic offline")

    monkeypatch.setattr(mcp_tools, "_request", failure)
    assert json.loads(await mcp_tools._handle_submit("synthetic", submission()["content"]))["error"]
    assert json.loads(await mcp_tools._handle_status("synthetic"))["error"]
    assert json.loads(await mcp_tools._handle_status("../bad"))["error"]
    assert json.loads(await mcp_tools._handle_submit("../bad", {}))["error"]
    registered = {}

    class Server:
        def tool(self, name, description):
            def decorate(fn):
                registered[name] = fn
                return fn

            return decorate

    mcp_tools.register_knowledge_candidate_tools(Server())
    assert set(registered) == {"cam_submit_knowledge_candidate", "cam_get_knowledge_candidate"}
    assert (await registered["cam_submit_knowledge_candidate"]("synthetic", {}))[0]["type"] == "text"
    assert (await registered["cam_get_knowledge_candidate"]("synthetic"))[0]["type"] == "text"
