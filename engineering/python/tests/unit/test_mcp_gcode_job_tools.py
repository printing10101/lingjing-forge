"""G 代码生成任务 MCP 工具组单元测试（W12 扩面）。"""

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

import mcp_server.gcode_job_tools as gt
from mcp_server.gcode_job_tools import register_gcode_job_tools


class _FakeServer:
    def __init__(self) -> None:
        self.registered: list[str] = []

    def tool(self, name: str = "", description: str = ""):
        def deco(fn):
            self.registered.append(name)
            return fn

        return deco


class TestRegistration:
    def test_three_tools_registered(self) -> None:
        server = _FakeServer()
        register_gcode_job_tools(server)
        assert server.registered == [
            "gcode_create_job",
            "gcode_get_job_status",
            "gcode_list_jobs",
        ]


class TestCreateJobHandler:
    @pytest.mark.asyncio
    async def test_client_side_validation_intercepts(self, monkeypatch) -> None:
        called = {"n": 0}

        async def fake_post(path, payload):
            called["n"] += 1
            return {"data": {}}

        monkeypatch.setattr(gt, "_post", fake_post)
        # 空路径
        out = await gt._handle_create_job("", "/x/plan.json")
        assert json.loads(out)["error"] is True
        # 非法控制器
        out = await gt._handle_create_job(
            "/x/chatter.json", "/x/plan.json", controller_type="evil"
        )
        assert json.loads(out)["error"] is True
        # 超长 material
        out = await gt._handle_create_job(
            "/x/chatter.json", "/x/plan.json", material_name="m" * 65
        )
        assert json.loads(out)["error"] is True
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_create_happy_path(self, monkeypatch) -> None:
        captured = {}

        async def fake_post(path, payload):
            captured["path"] = path
            captured["payload"] = payload
            return {"data": {"task_id": "gc_1", "status": "pending"}}

        monkeypatch.setattr(gt, "_post", fake_post)
        out = await gt._handle_create_job(
            "D:/data/chatter.json", "D:/data/plan.json", "fanuc_0i", "45#钢"
        )
        payload = json.loads(out)
        assert payload["data"]["task_id"] == "gc_1"
        assert captured["path"] == "/api/agent/v1/gcode/jobs"
        assert captured["payload"]["controller_type"] == "fanuc_0i"

    @pytest.mark.asyncio
    async def test_post_failure_formats_error(self, monkeypatch) -> None:
        async def boom(path, payload):
            raise RuntimeError("403 forbidden: path outside roots")

        monkeypatch.setattr(gt, "_post", boom)
        out = await gt._handle_create_job("bad.json", "plan.json")
        payload = json.loads(out)
        assert payload["error"] is True
        assert "403" in payload["message"]


class TestStatusHandlers:
    @pytest.mark.asyncio
    async def test_get_status_invalid_task_id(self, monkeypatch) -> None:
        called = {"n": 0}

        async def fake_get(path, params=None):
            called["n"] += 1
            return {"data": {}}

        monkeypatch.setattr(gt, "_get", fake_get)
        for bad in ["", "a" * 300, "has/slash", "has..dot"]:
            out = await gt._handle_get_job_status(bad)
            assert json.loads(out)["error"] is True
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_get_status_include_gcode_param(self, monkeypatch) -> None:
        captured = {}

        async def fake_get(path, params=None):
            captured["params"] = params
            return {"data": {"status": "generated"}}

        monkeypatch.setattr(gt, "_get", fake_get)
        out = await gt._handle_get_job_status("gc_1", include_gcode=True)
        assert json.loads(out)["data"]["status"] == "generated"
        assert captured["params"] == {"include_gcode": "true"}

    @pytest.mark.asyncio
    async def test_list_jobs_limit_validation(self, monkeypatch) -> None:
        called = {"n": 0}

        async def fake_get(path, params=None):
            called["n"] += 1
            return {"data": {"count": 0, "tasks": []}}

        monkeypatch.setattr(gt, "_get", fake_get)
        out = await gt._handle_list_jobs(limit=0)
        assert json.loads(out)["error"] is True
        out = await gt._handle_list_jobs(limit=101)
        assert json.loads(out)["error"] is True
        out = await gt._handle_list_jobs(limit=5)
        assert json.loads(out)["data"]["count"] == 0
        assert called["n"] == 1
