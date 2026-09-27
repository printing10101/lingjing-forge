"""Tests for Async Jobs API endpoints (GET/POST/DELETE /api/v1/jobs)."""

from __future__ import annotations


class TestJobsList:
    """Tests for GET /api/v1/jobs (list jobs)."""

    def test_list_jobs_returns_success(self, client):
        response = client.get("/api/v1/jobs")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "jobs" in data["data"]
        assert "total" in data["data"]
        assert isinstance(data["data"]["jobs"], list)

    def test_list_jobs_with_valid_task_type(self, client):
        response = client.get("/api/v1/jobs?task_type=lnn_training")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0

    def test_list_jobs_with_invalid_task_type(self, client):
        response = client.get("/api/v1/jobs?task_type=invalid_type")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 1002

    def test_list_jobs_with_valid_status(self, client):
        response = client.get("/api/v1/jobs?status=completed")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0

    def test_list_jobs_with_invalid_status(self, client):
        response = client.get("/api/v1/jobs?status=invalid_status")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 1002

    def test_list_jobs_respects_limit(self, client):
        response = client.get("/api/v1/jobs?limit=5")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]["jobs"]) <= 5

    def test_list_jobs_rejects_zero_limit(self, client):
        response = client.get("/api/v1/jobs?limit=0")
        assert response.status_code == 422

    def test_list_jobs_rejects_negative_offset(self, client):
        response = client.get("/api/v1/jobs?offset=-1")
        assert response.status_code == 422


class TestGetJob:
    """Tests for GET /api/v1/jobs/{job_id}."""

    def test_get_nonexistent_job_returns_not_found(self, client):
        response = client.get("/api/v1/jobs/nonexistent-job-id")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 1001


class TestCancelJob:
    """Tests for POST /api/v1/jobs/{job_id}/cancel."""

    def test_cancel_nonexistent_job_returns_error(self, client):
        response = client.post("/api/v1/jobs/nonexistent-job-id/cancel")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 1002


class TestDeleteJob:
    """Tests for DELETE /api/v1/jobs/{job_id}."""

    def test_delete_nonexistent_job_returns_error(self, client):
        response = client.delete("/api/v1/jobs/nonexistent-job-id")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 1002


class TestJobStats:
    """Tests for GET /api/v1/jobs/stats."""

    def test_get_stats_endpoint_exists(self, client):
        response = client.get("/api/v1/jobs/stats")
        assert response.status_code == 200
        data = response.json()
        assert "code" in data


class TestJobSSEStreaming:
    """Tests for GET /api/v1/jobs/{job_id}/stream (SSE)."""

    def test_stream_nonexistent_job_returns_not_found(self, client):
        response = client.get("/api/v1/jobs/nonexistent-job-id/stream")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 1001
