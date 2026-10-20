"""Authenticated Nexent client for deployment scripts; credentials stay in private files."""
from __future__ import annotations

import json
from pathlib import Path

import httpx


def _verify_tools_response(response: httpx.Response) -> None:
    response.raise_for_status()
    if not isinstance(response.json(), list):
        raise ValueError("[响应错误] Nexent 工具列表格式异常。建议操作：检查服务地址和代理，勿仅凭 HTTP 200 判定成功。")


def authenticated_client(
    deployment: Path,
    *,
    base_url: str = "http://127.0.0.1:3000/api",
    timeout: float | httpx.Timeout = 30,
) -> httpx.Client:
    """Return an already-open client; use contextlib.closing or close() explicitly.

    Check saved authentication on an endpoint that returns 401 for expiry.
    Reauthenticate once on 401. A server failure must not trigger login retries.
    """
    client = httpx.Client(base_url=base_url, timeout=timeout, trust_env=False)
    token_file = deployment / "svc-token.txt"
    try:
        if token_file.exists():
            token = token_file.read_text(encoding="utf-8").strip()
            if token:
                client.headers["Authorization"] = "Bearer " + token
                check = client.get("/tool/list")
                if check.status_code == 200:
                    _verify_tools_response(check)
                    return client
                if check.status_code != 401:
                    check.raise_for_status()

        credentials_file = deployment / "svc-login.json"
        credentials = json.loads(credentials_file.read_text(encoding="utf-8"))
        if not isinstance(credentials, dict) or any(
            not isinstance(credentials.get(key), str) or not credentials[key].strip()
            for key in ("email", "password")
        ):
            raise ValueError("[配置错误] 服务账户配置不完整。建议操作：检查私有 svc-login.json 的 email/password。")
        login = client.post("/user/signin", json={key: credentials[key] for key in ("email", "password")})
        login.raise_for_status()
        payload = login.json()
        token = client.cookies.get("nexent_access_token")
        if not token:
            token = payload.get("data", {}).get("session", {}).get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise ValueError("[认证错误] 登录未返回访问令牌。建议操作：检查 Nexent 登录响应及安全 Cookie 配置。")
        client.headers["Authorization"] = "Bearer " + token
        _verify_tools_response(client.get("/tool/list"))
        temporary = token_file.with_suffix(".tmp")
        temporary.write_text(token, encoding="utf-8")
        temporary.replace(token_file)
        return client
    except Exception:
        client.close()
        raise
