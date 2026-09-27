"""Tests for Tool Wear Prediction API endpoints (/api/v1/wear)."""

from __future__ import annotations

import pytest


VALID_WEAR_PARAMS = {
    "cutting_speed": 150.0,
    "feed_rate": 0.2,
    "depth_of_cut": 1.5,
    "material_type": "steel_45",
    "tool_type": "carbide",
    "current_wear": 0.1,
    "time_step": 1.0,
    "max_time": 300.0,
}


class TestWearPredict:
    """Tests for POST /api/v1/wear/predict."""

    def test_predict_with_valid_params_responds(self, client):
        response = client.post("/api/v1/wear/predict", json=VALID_WEAR_PARAMS)
        assert response.status_code == 200
        data = response.json()
        assert "code" in data

    @pytest.mark.parametrize(
        "field,value",
        [
            ("cutting_speed", "invalid"),
            ("feed_rate", "not-a-number"),
        ],
    )
    def test_predict_with_invalid_type_params_returns_422(self, client, field, value):
        params = {**VALID_WEAR_PARAMS, field: value}
        response = client.post("/api/v1/wear/predict", json=params)
        assert response.status_code == 422

    def test_predict_with_empty_body_responds(self, client):
        response = client.post("/api/v1/wear/predict", json={})
        assert response.status_code in (200, 422)

    def test_predict_with_different_materials(self, client):
        for material in ["steel_45", "aluminum", "titanium"]:
            params = {**VALID_WEAR_PARAMS, "material_type": material}
            response = client.post("/api/v1/wear/predict", json=params)
            assert response.status_code == 200


class TestRemainingLife:
    """Tests for POST /api/v1/wear/remaining-life."""

    def test_remaining_life_valid_request(self, client):
        payload = {
            "current_wear": 0.1,
            "cutting_speed": 150.0,
            "feed_rate": 0.2,
            "depth_of_cut": 1.5,
            "material_type": "steel_45",
            "tool_type": "carbide",
        }
        response = client.post("/api/v1/wear/remaining-life", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "code" in data

    def test_remaining_life_high_wear(self, client):
        payload = {
            "current_wear": 0.5,
            "cutting_speed": 200.0,
            "feed_rate": 0.3,
            "depth_of_cut": 2.0,
            "material_type": "titanium",
            "tool_type": "carbide",
        }
        response = client.post("/api/v1/wear/remaining-life", json=payload)
        assert response.status_code == 200


class TestSuggest:
    """Tests for POST /api/v1/wear/suggest."""

    def test_suggest_valid_request(self, client):
        payload = {
            "current_wear": 0.15,
            "remaining_life": 50.0,
            "cutting_speed": 150.0,
            "feed_rate": 0.2,
            "depth_of_cut": 1.5,
            "coolant_flow": 10.0,
            "material_type": "steel_45",
            "tool_type": "carbide",
        }
        response = client.post("/api/v1/wear/suggest", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "code" in data


class TestWearModels:
    """Tests for GET /api/v1/wear/models."""

    def test_list_models_returns_success(self, client):
        response = client.get("/api/v1/wear/models")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0


class TestWearThreshold:
    """Tests for POST /api/v1/wear/threshold."""

    def test_threshold_with_valid_params(self, client):
        payload = {
            "cutting_speed": 150.0,
            "feed_rate": 0.2,
            "depth_of_cut": 1.5,
            "material_type": "steel_45",
            "tool_type": "carbide",
        }
        response = client.post("/api/v1/wear/threshold", json=payload)
        assert response.status_code == 200


class TestWearCalibrate:
    """Tests for POST /api/v1/wear/calibrate."""

    def test_calibrate_with_valid_params(self, client):
        payload = {
            "measured_wear": 0.1,
            "elapsed_time": 30.0,
            "cutting_speed": 150.0,
            "feed_rate": 0.2,
            "depth_of_cut": 1.5,
            "material_type": "steel_45",
            "tool_type": "carbide",
        }
        response = client.post("/api/v1/wear/calibrate", json=payload)
        assert response.status_code == 200
