"""Tests for SecurityHeadersMiddleware (pure ASGI)."""

import pytest
from starlette.testclient import TestClient
from fastapi import FastAPI

from app.auth.security_headers_asgi import (
    SecurityHeadersMiddleware,
    SECURITY_HEADERS,
)


@pytest.fixture
def app():
    """Create a FastAPI app with security headers middleware."""
    app = FastAPI()

    @app.get("/test")
    async def test_route():
        return {"message": "hello"}

    @app.get("/stream")
    async def stream_route():
        from starlette.responses import StreamingResponse

        async def generator():
            yield "data: hello\n\n"
            yield "data: world\n\n"

        return StreamingResponse(generator(), media_type="text/event-stream")

    app.add_middleware(SecurityHeadersMiddleware)
    return app


class TestSecurityHeadersMiddleware:
    """Test pure ASGI security headers middleware."""

    def test_security_headers_present(self, app):
        """All security headers should be present in response."""
        client = TestClient(app)
        response = client.get("/test")

        for key, value in SECURITY_HEADERS.items():
            assert key in response.headers, f"Missing header: {key}"
            assert response.headers[key] == value, f"Wrong value for {key}"

    def test_security_headers_not_duplicated(self, app):
        """Headers should not be duplicated."""
        client = TestClient(app)
        response = client.get("/test")

        for key in SECURITY_HEADERS:
            count = sum(1 for k in response.headers if k.lower() == key.lower())
            assert count == 1, f"Header {key} appears {count} times"

    def test_regular_response_body_unchanged(self, app):
        """Response body should not be modified."""
        client = TestClient(app)
        response = client.get("/test")

        assert response.json() == {"message": "hello"}
        assert response.status_code == 200

    def test_sse_streaming_not_blocked(self, app):
        """SSE streaming should work correctly with the middleware."""
        client = TestClient(app)
        response = client.get("/stream")

        assert response.status_code == 200
        # Security headers should still be present on SSE responses
        assert "X-Content-Type-Options" in response.headers

    def test_error_responses_have_headers(self, app):
        """Error responses should also have security headers."""
        client = TestClient(app)
        response = client.get("/nonexistent")

        for key in SECURITY_HEADERS:
            assert key in response.headers

    def test_post_request_has_headers(self, app):
        """POST requests should also get security headers."""
        client = TestClient(app)

        @app.post("/post-test")
        async def post_route():
            return {"method": "post"}

        response = client.post("/post-test")
        for key in SECURITY_HEADERS:
            assert key in response.headers

    def test_specific_header_values(self, app):
        """Test individual header values are correct."""
        client = TestClient(app)
        response = client.get("/test")

        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert response.headers["X-XSS-Protection"] == "1; mode=block"
        assert "camera=" in response.headers["Permissions-Policy"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
