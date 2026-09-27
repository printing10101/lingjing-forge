"""Agent Gateway 工艺规划端点测试（W14 扩面）。

真实跑 ProcessPlanningPipeline（孔描述 → G 代码），覆盖成功路径、
规模护栏与参数校验。
"""

from __future__ import annotations


def _valid_desc(holes: list | None = None) -> dict:
    return {
        "material": "45#钢",
        "part_type": "plate",
        "holes": holes if holes is not None else [
            {"id": "h1", "type": "through", "position": {"x": 20, "y": 20}, "diameter": 8.0, "depth": 12.0}
        ],
    }


class TestProcessPlanningRun:
    def test_real_pipeline_success(self, client):
        resp = client.post(
            "/api/agent/v1/process-planning/run",
            json={"part_description": _valid_desc()},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        payload = data["data"]
        assert payload["success"] is True
        assert payload["stages"]
        assert payload["gcode"]["program_text"]
        assert "CAM" in payload["disclaimer"]

    def test_invalid_controller_422(self, client):
        resp = client.post(
            "/api/agent/v1/process-planning/run",
            json={"part_description": _valid_desc(), "controller_type": "hack"},
        )
        assert resp.status_code == 422

    def test_missing_material_422(self, client):
        resp = client.post(
            "/api/agent/v1/process-planning/run",
            json={"part_description": {"holes": []}},
        )
        assert resp.status_code == 422

    def test_holes_not_list_422(self, client):
        resp = client.post(
            "/api/agent/v1/process-planning/run",
            json={"part_description": {"material": "45#钢", "holes": "many"}},
        )
        assert resp.status_code == 422

    def test_too_many_holes_422(self, client):
        holes = [
            {"id": f"h{i}", "type": "through", "position": {"x": i, "y": i}, "diameter": 8.0, "depth": 10.0}
            for i in range(51)
        ]
        resp = client.post(
            "/api/agent/v1/process-planning/run",
            json={"part_description": _valid_desc(holes)},
        )
        assert resp.status_code == 422

    def test_empty_holes_ok(self, client):
        """空孔列表（纯平面零件）也应走完流水线。"""
        resp = client.post(
            "/api/agent/v1/process-planning/run",
            json={"part_description": _valid_desc(holes=[])},
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == 0
