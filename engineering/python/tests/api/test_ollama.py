

"""Tests for Ollama integration routes (/api/ollama)."""

from __future__ import annotations
import pytest

pytestmark = pytest.mark.skip_ci









class TestOllamaStatus:
    """Tests for GET /api/ollama/status."""

    def test_status_endpoint_responds(self, client):
        response = client.get("/api/ollama/status")
        assert response.status_code in (200, 502, 503)


class TestOllamaModels:
    """Tests for GET /api/ollama/models."""

    def test_models_endpoint_responds(self, client):
        response = client.get("/api/ollama/models")
        assert response.status_code in (200, 502, 503)
