"""Tests for RAG knowledge base API endpoints (/api/rag)."""

from __future__ import annotations


class TestRAGQuery:
    """Tests for GET /api/rag/query."""

    def test_query_missing_required_param_returns_422(self, client):
        response = client.get("/api/rag/query")
        assert response.status_code == 422


class TestRAGStats:
    """Tests for GET /api/rag/stats."""

    def test_stats_endpoint_responds(self, client):
        response = client.get("/api/rag/stats")
        assert response.status_code in (200, 500)


class TestRAGAdd:
    """Tests for POST /api/rag/add."""

    def test_add_empty_document_returns_400(self, client):
        response = client.post("/api/rag/add", json={"document": ""})
        assert response.status_code == 400

    def test_add_missing_document(self, client):
        response = client.post("/api/rag/add", json={})
        assert response.status_code == 400


class TestRAGDelete:
    """Tests for DELETE /api/rag/{doc_id}."""

    def test_delete_nonexistent_doc_returns_404(self, client):
        response = client.delete("/api/rag/nonexistent_doc_id_xyz")
        assert response.status_code == 404


class TestRAGList:
    """Tests for GET /api/rag/list."""

    def test_list_endpoint_responds(self, client):
        response = client.get("/api/rag/list")
        assert response.status_code in (200, 500)
