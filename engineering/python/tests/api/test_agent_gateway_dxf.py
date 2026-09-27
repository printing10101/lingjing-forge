"""Agent Gateway DXF 解析端点测试（W13 扩面）。

用仓库真实 DXF 夹具（data/test_fixtures）验证解析摘要、
白名单 fail-closed 与参数校验。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.api.v1.agent_gateway._state import resolve_agent_input_path
from fastapi import HTTPException


@pytest.fixture()
def dxf_root(tmp_path, monkeypatch):
    """指向仓库真实 DXF 夹具目录（仓库根 data/test_fixtures）。"""
    fixtures = Path(__file__).resolve().parents[4] / "data" / "test_fixtures"
    monkeypatch.setenv("LINGJING_AGENT_INPUT_ROOTS", str(fixtures))
    return fixtures


class TestDxfSummary:
    def test_real_fixture_parses(self, client, dxf_root):
        resp = client.get(
            "/api/agent/v1/dxf/summary",
            params={"path": str(dxf_root / "case1_simple_box.dxf")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        payload = data["data"]
        assert payload["success"] is True
        assert payload["total_entities"] > 0
        assert payload["dxf_version"]
        assert "extents" in payload
        assert payload["path"].endswith("case1_simple_box.dxf")

    def test_missing_file_404(self, client, dxf_root):
        resp = client.get(
            "/api/agent/v1/dxf/summary",
            params={"path": str(dxf_root / "nope.dxf")},
        )
        assert resp.status_code == 404

    def test_outside_roots_403(self, client, dxf_root):
        resp = client.get(
            "/api/agent/v1/dxf/summary",
            params={"path": "C:/Windows/system.ini.dxf"},
        )
        assert resp.status_code == 403

    def test_wrong_suffix_422(self, client, dxf_root, tmp_path):
        fake = dxf_root / "evil.json"
        fake.write_text("{}", encoding="utf-8")
        resp = client.get(
            "/api/agent/v1/dxf/summary",
            params={"path": str(fake)},
        )
        assert resp.status_code == 422

    def test_missing_param_422(self, client, dxf_root):
        resp = client.get("/api/agent/v1/dxf/summary")
        assert resp.status_code == 422


class TestResolveAgentInputPath:
    """共享白名单校验单元（gcode/dxf 两个端点共用）。"""

    def test_json_suffix_accepted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINGJING_AGENT_INPUT_ROOTS", str(tmp_path))
        f = tmp_path / "a.json"
        f.write_text("{}", encoding="utf-8")
        assert resolve_agent_input_path(str(f), "f", ".json") == f.resolve()

    def test_gcode_env_fallback(self, tmp_path, monkeypatch):
        """旧变量名 LINGJING_GCODE_INPUT_ROOTS 向后兼容。"""
        monkeypatch.delenv("LINGJING_AGENT_INPUT_ROOTS", raising=False)
        monkeypatch.setenv("LINGJING_GCODE_INPUT_ROOTS", str(tmp_path))
        f = tmp_path / "a.json"
        f.write_text("{}", encoding="utf-8")
        assert resolve_agent_input_path(str(f), "f", ".json").is_relative_to(
            tmp_path.resolve()
        )

    def test_rejects_traversal_outside_root(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINGJING_AGENT_INPUT_ROOTS", str(tmp_path / "inside"))
        (tmp_path / "inside").mkdir()
        outside = tmp_path / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        with pytest.raises(HTTPException) as ei:
            resolve_agent_input_path(str(outside), "f", ".json")
        assert ei.value.status_code == 403
