"""CAM 主链路 MCP 工具组单元测试（W11 扩面）。

覆盖：工具注册（名称/数量）、标识符校验、参数校验拦截、
HTTP 调用包装（monkeypatch _get）与异常兜底格式。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("LINGJING_MCP_DEV", "1")
os.environ.setdefault("LINGJING_AGENT_TOKEN", "x" * 40)

import pytest

import mcp_server.cam_tools as ct
from mcp_server.cam_tools import _sanitize_identifier, register_cam_tools


class _FakeServer:
    def __init__(self) -> None:
        self.registered: list[str] = []

    def tool(self, name: str = "", description: str = ""):
        def deco(fn):
            self.registered.append(name)
            return fn

        return deco


class TestRegistration:
    def test_four_tools_registered(self) -> None:
        server = _FakeServer()
        register_cam_tools(server)
        assert server.registered == [
            "gcode_get_failure_stats",
            "gcode_list_failure_cases",
            "cam_recommend_process",
            "cam_get_quadruple_stats",
        ]


class TestSanitizeIdentifier:
    def test_valid(self) -> None:
        assert _sanitize_identifier("hole_d10", "feature") == "hole_d10"
        assert _sanitize_identifier("Alu-6061", "material") == "Alu-6061"

    @pytest.mark.parametrize(
        "bad", ["", "a" * 65, "has space", "../etc", "dot.dot", "-leading"]
    )
    def test_invalid(self, bad: str) -> None:
        with pytest.raises(ValueError):
            _sanitize_identifier(bad, "feature")


class TestToolHandlers:
    @pytest.mark.asyncio
    async def test_stats_success(self, monkeypatch) -> None:
        async def fake_get(path, params=None):
            assert path == "/api/agent/v1/gcode/failure-stats"
            return {"data": {"total": 10, "one_pass_rate": 0.8}}

        monkeypatch.setattr(ct, "_get", fake_get)
        payload = json.loads(await ct._handle_get_failure_stats())
        assert payload["data"]["total"] == 10

    @pytest.mark.asyncio
    async def test_stats_error_format(self, monkeypatch) -> None:
        async def boom(path, params=None):
            raise RuntimeError("gateway down")

        monkeypatch.setattr(ct, "_get", boom)
        payload = json.loads(await ct._handle_get_failure_stats())
        assert payload["error"] is True
        assert "gateway down" in payload["message"]

    @pytest.mark.asyncio
    async def test_list_cases_param_validation(self, monkeypatch) -> None:
        called = {"n": 0}

        async def fake_get(path, params=None):
            called["n"] += 1
            assert params["outcome"] == "failure"
            assert params["source"] == "safety_validator"
            return {"data": {"count": 0, "cases": []}}

        monkeypatch.setattr(ct, "_get", fake_get)
        # limit 越界 → 拦截，不触 HTTP
        out = await ct._handle_list_failure_cases(limit=500)
        assert json.loads(out)["error"] is True
        # outcome 非法 → 拦截
        out = await ct._handle_list_failure_cases(outcome="weird")
        assert json.loads(out)["error"] is True
        assert called["n"] == 0
        # 合法参数 → 触发调用且过滤参数透传
        out = await ct._handle_list_failure_cases(
            limit=5, outcome="failure", source="safety_validator"
        )
        assert called["n"] == 1
        assert json.loads(out)["data"]["count"] == 0

    @pytest.mark.asyncio
    async def test_recommend_identifier_rejected(self, monkeypatch) -> None:
        called = {"n": 0}

        async def fake_get(path, params=None):
            called["n"] += 1
            return {"data": {}}

        monkeypatch.setattr(ct, "_get", fake_get)
        out = await ct._handle_recommend_process(feature="../evil")
        assert json.loads(out)["error"] is True
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_recommend_top_k_validation(self, monkeypatch) -> None:
        async def fake_get(path, params=None):
            return {"data": {}}

        monkeypatch.setattr(ct, "_get", fake_get)
        out = await ct._handle_recommend_process(feature="hole", top_k=99)
        assert json.loads(out)["error"] is True

    @pytest.mark.asyncio
    async def test_recommend_happy_path(self, monkeypatch) -> None:
        async def fake_get(path, params=None):
            assert params == {"feature": "hole", "material": "steel_45", "top_k": 3}
            return {"data": {"recommendations": [{"feature": "hole"}]}}

        monkeypatch.setattr(ct, "_get", fake_get)
        out = await ct._handle_recommend_process("hole", "steel_45", 3)
        assert json.loads(out)["data"]["recommendations"]

    @pytest.mark.asyncio
    async def test_quadruple_stats_success(self, monkeypatch) -> None:
        async def fake_get(path, params=None):
            assert path == "/api/agent/v1/cam/quadruple-stats"
            return {"data": {"total_quadruples": 5}}

        monkeypatch.setattr(ct, "_get", fake_get)
        payload = json.loads(await ct._handle_get_quadruple_stats())
        assert payload["data"]["total_quadruples"] == 5
