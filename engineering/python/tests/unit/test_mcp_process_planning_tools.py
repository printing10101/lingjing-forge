"""工艺规划 MCP 工具单元测试（W14 扩面）。"""

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

import mcp_server.process_planning_tools as pt
from mcp_server.process_planning_tools import register_process_planning_tools


class _FakeServer:
    def __init__(self) -> None:
        self.registered: list[str] = []

    def tool(self, name: str = "", description: str = ""):
        def deco(fn):
            self.registered.append(name)
            return fn

        return deco


def _valid_desc() -> dict:
    return {
        "material": "45#钢",
        "holes": [
            {"id": "h1", "type": "through", "position": {"x": 20, "y": 20}, "diameter": 8.0, "depth": 12.0}
        ],
    }


class TestRegistration:
    def test_tool_registered(self) -> None:
        server = _FakeServer()
        register_process_planning_tools(server)
        assert server.registered == ["process_plan_run"]


class TestHandler:
    @pytest.mark.asyncio
    async def test_happy_path(self, monkeypatch) -> None:
        captured = {}

        async def fake_post(path, payload):
            captured["path"] = path
            captured["payload"] = payload
            return {"data": {"success": True, "summary": "ok"}}

        monkeypatch.setattr(pt, "_post", fake_post)
        out = await pt._handle_run(_valid_desc(), "fanuc_0i", 60.0, 2000)
        payload = json.loads(out)
        assert payload["data"]["success"] is True
        assert captured["path"] == "/api/agent/v1/process-planning/run"
        assert captured["payload"]["program_number"] == 2000

    @pytest.mark.asyncio
    async def test_client_side_validation_intercepts(self, monkeypatch) -> None:
        called = {"n": 0}

        async def fake_post(path, payload):
            called["n"] += 1
            return {"data": {}}

        monkeypatch.setattr(pt, "_post", fake_post)
        # 非法控制器
        out = await pt._handle_run(_valid_desc(), controller_type="evil")
        assert json.loads(out)["error"] is True
        # 缺 material
        out = await pt._handle_run({"holes": []})
        assert json.loads(out)["error"] is True
        # holes 非列表
        out = await pt._handle_run({"material": "45#钢", "holes": "many"})
        assert json.loads(out)["error"] is True
        # holes 超量
        holes = [{"id": f"h{i}", "diameter": 8.0} for i in range(51)]
        out = await pt._handle_run({"material": "45#钢", "holes": holes})
        assert json.loads(out)["error"] is True
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_post_failure_formats_error(self, monkeypatch) -> None:
        async def boom(path, payload):
            raise RuntimeError("422: validation failed")

        monkeypatch.setattr(pt, "_post", boom)
        out = await pt._handle_run(_valid_desc())
        payload = json.loads(out)
        assert payload["error"] is True
        assert "422" in payload["message"]
