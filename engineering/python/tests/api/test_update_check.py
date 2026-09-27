"""update_check 服务与 /api/v1/system/update-check 端点测试。"""

from __future__ import annotations

import pytest

from app.services import update_check
from app.services.update_check import check_for_updates, parse_version, version_gt


class TestParseVersion:
    """语义化版本解析。"""

    def test_basic(self):
        assert parse_version("2.7.0") == (2, 7, 0)

    def test_v_prefix(self):
        assert parse_version("v2.7.0") == (2, 7, 0)

    def test_prerelease_suffix(self):
        assert parse_version("2.7.0-beta.1") == (2, 7, 0)

    def test_build_metadata(self):
        assert parse_version("2.7.0+build5") == (2, 7, 0)

    def test_short_version(self):
        assert parse_version("2.7") == (2, 7, 0)

    def test_malformed(self):
        assert parse_version("abc") == (0, 0, 0)


class TestVersionGt:
    """版本比较。"""

    def test_greater(self):
        assert version_gt("2.8.0", "2.7.0")

    def test_equal_not_greater(self):
        assert not version_gt("2.7.0", "2.7.0")

    def test_less(self):
        assert not version_gt("2.6.0", "2.7.0")

    def test_v_prefix_compare(self):
        assert version_gt("v2.8.0", "2.7.0")


class TestCheckForUpdates:
    """check_for_updates 逻辑（mock _fetch_latest，不触网）。"""

    @pytest.mark.asyncio
    async def test_update_available(self, monkeypatch):
        monkeypatch.setattr(
            update_check, "_fetch_latest",
            lambda: ("v2.8.0", "https://github.com/x/y/releases/tag/v2.8.0"),
        )
        result = await check_for_updates()
        assert result["update_available"] is True
        assert result["latest_version"] == "v2.8.0"
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_up_to_date(self, monkeypatch):
        monkeypatch.setattr(
            update_check, "_fetch_latest",
            lambda: ("v2.7.0", "https://github.com/x/y/releases/tag/v2.7.0"),
        )
        result = await check_for_updates()
        assert result["update_available"] is False
        assert result["latest_version"] == "v2.7.0"
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_network_error(self, monkeypatch):
        monkeypatch.setattr(update_check, "_fetch_latest", lambda: (None, None))
        result = await check_for_updates()
        assert result["error"] == "network"
        assert result["update_available"] is False
        assert result["latest_version"] is None

    @pytest.mark.asyncio
    async def test_current_version_always_present(self, monkeypatch):
        monkeypatch.setattr(update_check, "_fetch_latest", lambda: (None, None))
        result = await check_for_updates()
        assert result["current_version"] == update_check.VERSION

    def test_fetch_latest_real_http_mocked(self, monkeypatch):
        """验证 _fetch_latest 对非 200 响应 fail-soft。"""
        import httpx

        class FakeResp:
            def raise_for_status(self):
                raise httpx.HTTPStatusError("boom", request=None, response=None)

        monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResp())
        tag, url = update_check._fetch_latest()
        assert tag is None
        assert url is None


class TestUpdateCheckEndpoint:
    """/api/v1/system/update-check 端点（client fixture 来自 tests/api/conftest.py）。"""

    def test_endpoint_returns_shape(self, client, monkeypatch):
        monkeypatch.setattr(
            update_check, "_fetch_latest",
            lambda: ("v2.8.0", "https://github.com/x/y/releases/tag/v2.8.0"),
        )
        response = client.get("/api/v1/system/update-check")
        assert response.status_code == 200
        data = response.json()
        assert data["update_available"] is True
        assert data["current_version"]
        assert data["error"] is None

    def test_endpoint_network_fail_soft(self, client, monkeypatch):
        monkeypatch.setattr(update_check, "_fetch_latest", lambda: (None, None))
        response = client.get("/api/v1/system/update-check")
        assert response.status_code == 200
        data = response.json()
        assert data["update_available"] is False
        assert data["error"] == "network"

    def test_endpoint_no_network_call_without_mock(self, client):
        """未 mock 时端点在真实网络不可用场景下仍返回 200（fail-soft 兜底）。"""
        response = client.get("/api/v1/system/update-check")
        assert response.status_code == 200
        assert "current_version" in response.json()
