"""Agent Gateway CAM 只读端点测试（W11 扩面）。

覆盖 /api/agent/v1/gcode/* 与 /api/agent/v1/cam/* 四个端点的
成功路径、参数校验与响应包装格式。
"""

from __future__ import annotations


class TestGcodeFailureStats:
    def test_returns_success_wrapper(self, client):
        response = client.get("/api/agent/v1/gcode/failure-stats")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        payload = data["data"]
        # stats() 报表结构（M3 契约）
        assert "total" in payload
        assert "failures" in payload
        assert "successes" in payload
        assert "one_pass_rate" in payload
        assert "by_source" in payload
        assert "by_code" in payload


class TestGcodeFailureCases:
    def test_list_returns_success_wrapper(self, client):
        response = client.get("/api/agent/v1/gcode/failure-cases")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        payload = data["data"]
        assert "count" in payload
        assert isinstance(payload["cases"], list)
        assert payload["count"] == len(payload["cases"])

    def test_limit_upper_bound_enforced(self, client):
        response = client.get("/api/agent/v1/gcode/failure-cases?limit=500")
        assert response.status_code == 422  # FastAPI Query 校验拒绝

    def test_limit_negative_rejected(self, client):
        response = client.get("/api/agent/v1/gcode/failure-cases?limit=0")
        assert response.status_code == 422

    def test_outcome_filter_accepted(self, client):
        response = client.get("/api/agent/v1/gcode/failure-cases?outcome=failure&limit=5")
        assert response.status_code == 200
        assert response.json()["code"] == 0


class TestCamProcessRecommend:
    def test_requires_feature_param(self, client):
        response = client.get("/api/agent/v1/cam/process-recommend")
        assert response.status_code == 422

    def test_recommend_returns_structure(self, client):
        response = client.get(
            "/api/agent/v1/cam/process-recommend?feature=hole&material=steel_45&top_k=3"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        payload = data["data"]
        assert payload["feature"] == "hole"
        assert payload["material"] == "steel_45"
        assert isinstance(payload["recommendations"], list)

    def test_top_k_upper_bound(self, client):
        response = client.get("/api/agent/v1/cam/process-recommend?feature=hole&top_k=99")
        assert response.status_code == 422


class TestCamQuadrupleStats:
    def test_returns_stats_structure(self, client):
        response = client.get("/api/agent/v1/cam/quadruple-stats")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        payload = data["data"]
        assert "total_quadruples" in payload
        assert "feature_count" in payload
        assert "material_count" in payload
