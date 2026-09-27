"""DXF 图纸解析 MCP 工具单元测试（W13 扩面）。"""

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

import mcp_server.dxf_tools as dt
from mcp_server.dxf_tools import register_dxf_tools


class _FakeServer:
    def __init__(self) -> None:
        self.registered: list[str] = []

    def tool(self, name: str = "", description: str = ""):
        def deco(fn):
            self.registered.append(name)
            return fn

        return deco


class TestRegistration:
    def test_tool_registered(self) -> None:
        server = _FakeServer()
        register_dxf_tools(server)
        assert server.registered == ["dxf_describe_file"]


class TestHandler:
    @pytest.mark.asyncio
    async def test_happy_path(self, monkeypatch) -> None:
        captured = {}

        async def fake_get(path, params=None):
            captured["path"] = path
            captured["params"] = params
            return {"data": {"success": True, "total_entities": 42}}

        monkeypatch.setattr(dt, "_get", fake_get)
        out = await dt._handle_describe_file("D:/data/fixture.dxf")
        payload = json.loads(out)
        assert payload["data"]["total_entities"] == 42
        assert captured["path"] == "/api/agent/v1/dxf/summary"
        assert captured["params"] == {"path": "D:/data/fixture.dxf"}

    @pytest.mark.asyncio
    async def test_empty_path_rejected(self, monkeypatch) -> None:
        called = {"n": 0}

        async def fake_get(path, params=None):
            called["n"] += 1
            return {"data": {}}

        monkeypatch.setattr(dt, "_get", fake_get)
        out = await dt._handle_describe_file("")
        assert json.loads(out)["error"] is True
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_error_format(self, monkeypatch) -> None:
        async def boom(path, params=None):
            raise RuntimeError("403 path outside roots")

        monkeypatch.setattr(dt, "_get", boom)
        out = await dt._handle_describe_file("x.dxf")
        payload = json.loads(out)
        assert payload["error"] is True
        assert "403" in payload["message"]
