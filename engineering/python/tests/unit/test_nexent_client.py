"""Cookie-based Nexent login must replace stale headers and preserve server failures."""
import importlib.util
from contextlib import closing
import json
from pathlib import Path

import httpx
import pytest

spec = importlib.util.spec_from_file_location("nexent_client", Path(__file__).resolve().parents[4] / "scripts/nexent_client.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
pytestmark = pytest.mark.unit


def install_transport(monkeypatch, handler):
    client_type = httpx.Client
    created = []
    def factory(**kwargs):
        assert kwargs["trust_env"] is False
        client = client_type(transport=httpx.MockTransport(handler), **kwargs)
        created.append(client)
        return client
    monkeypatch.setattr(module.httpx, "Client", factory)
    return created


def save_credentials(folder):
    (folder / "svc-login.json").write_text(json.dumps({"email": "test@example.invalid", "password": "private-test-value"}))


def test_valid_token_does_not_require_login(monkeypatch, tmp_path):
    (tmp_path / "svc-token.txt").write_text("existing")
    def handler(request):
        assert request.url.path == "/api/tool/list"
        assert request.headers["Authorization"] == "Bearer existing"
        return httpx.Response(200, json=[])
    install_transport(monkeypatch, handler)
    with closing(module.authenticated_client(tmp_path)) as client:
        assert not client.is_closed


@pytest.mark.parametrize("cookie", [True, False])
def test_expired_token_is_replaced_and_verified(monkeypatch, tmp_path, cookie):
    save_credentials(tmp_path)
    (tmp_path / "svc-token.txt").write_text("expired")
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("signin"):
            assert json.loads(request.content)["password"] == "private-test-value"
            return httpx.Response(200, json={"data": {"session": {} if cookie else {"access_token": "fresh"}}},
                                  headers={"set-cookie": "nexent_access_token=fresh; Path=/; HttpOnly"} if cookie else {})
        return httpx.Response(200 if request.headers["Authorization"] == "Bearer fresh" else 401, json=[])
    install_transport(monkeypatch, handler)
    with closing(module.authenticated_client(tmp_path)) as client:
        assert client.headers["Authorization"] == "Bearer fresh"
    assert calls == ["/api/tool/list", "/api/user/signin", "/api/tool/list"]
    assert (tmp_path / "svc-token.txt").read_text() == "fresh"


def test_server_failure_does_not_trigger_login(monkeypatch, tmp_path):
    (tmp_path / "svc-token.txt").write_text("existing")
    created = install_transport(monkeypatch, lambda request: httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        module.authenticated_client(tmp_path)
    assert created[0].is_closed
    assert (tmp_path / "svc-token.txt").read_text() == "existing"


@pytest.mark.parametrize("body", [{"error": True}, "not-a-tool-list"])
def test_http_200_with_wrong_body_is_not_authentication(monkeypatch, tmp_path, body):
    (tmp_path / "svc-token.txt").write_text("existing")
    created = install_transport(monkeypatch, lambda request: httpx.Response(200, json=body))
    with pytest.raises(ValueError, match="响应错误"):
        module.authenticated_client(tmp_path)
    assert created[0].is_closed


@pytest.mark.parametrize("credentials", [[], {}, {"email": "x", "password": ""}, {"email": 1, "password": "x"}])
def test_invalid_credentials_close_client(monkeypatch, tmp_path, credentials):
    (tmp_path / "svc-login.json").write_text(json.dumps(credentials))
    created = install_transport(monkeypatch, lambda request: pytest.fail("Unexpected network request"))
    with pytest.raises(ValueError, match="配置错误"):
        module.authenticated_client(tmp_path)
    assert created[0].is_closed


@pytest.mark.parametrize("mode", ["rejected", "missing_token", "verification_failed"])
def test_failed_login_does_not_overwrite_saved_token(monkeypatch, tmp_path, mode):
    save_credentials(tmp_path)
    (tmp_path / "svc-token.txt").write_text("expired")
    def handler(request):
        if request.url.path.endswith("signin"):
            if mode == "rejected":
                return httpx.Response(401)
            return httpx.Response(200, json={"data": {"session": {} if mode == "missing_token" else {"access_token": "fresh"}}})
        return httpx.Response(401)
    created = install_transport(monkeypatch, handler)
    with pytest.raises((httpx.HTTPStatusError, ValueError)):
        module.authenticated_client(tmp_path)
    assert created[0].is_closed
    assert (tmp_path / "svc-token.txt").read_text() == "expired"
