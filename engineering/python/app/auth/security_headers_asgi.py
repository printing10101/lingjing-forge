"""Pure ASGI Security Headers Middleware.

Does NOT buffer request/response bodies, unlike BaseHTTPMiddleware.
Security headers are injected on the send side of the ASGI receive/send loop.
"""

from __future__ import annotations


from starlette.types import ASGIApp, Message, Receive, Scope, Send


SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-XSS-Protection": "1; mode=block",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class SecurityHeadersMiddleware:
    """Pure ASGI middleware that adds security headers to every response.

    Implemented as a pure ASGI middleware so it does NOT read the full
    request body or response body into memory.  Headers are appended
    to the ``http.response.start`` message before it is forwarded
    downstream, which keeps SSE streaming intact.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Inject security headers into the response-start message.
                # Starlette sends headers as a list of (name, value) byte tuples.
                headers: list[tuple[bytes, bytes]] = list(message.get("headers", []))
                for key, value in SECURITY_HEADERS.items():
                    # Only add if not already present (case-insensitive check)
                    key_lower = key.lower()
                    if not any(h[0].decode("latin-1", errors="ignore").lower() == key_lower for h in headers):
                        headers.append((key.encode("latin-1"), value.encode("latin-1")))
                message = {"type": "http.response.start", **message}
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
