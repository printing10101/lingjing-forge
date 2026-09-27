"""lingjing-mcp server entry point."""
from __future__ import annotations

import argparse
import asyncio
import hmac
import logging
import os
import sys

logger = logging.getLogger("lingjing-mcp")


def run_stdio():
    """Run MCP server in stdio mode (for Cursor, Claude Code local calls)."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print("Error: mcp package not installed. Run: pip install mcp", file=sys.stderr)
        sys.exit(1)

    from mcp_server.tools import register_tools

    server = FastMCP("lingjing-mcp")
    register_tools(server)

    logger.info("Starting lingjing-mcp server in stdio mode")
    server.run()


def _build_sse_app(server):
    """Return the Starlette ASGI app backing FastMCP's SSE transport.

    跨版本健壮：优先使用公开的 ``FastMCP.sse_app()``（mcp >= 1.3）。
    若当前安装的 mcp 版本不提供该访问器，则 fail-closed——拒绝在无法
    附加鉴权中间件的情况下对外提供 SSE，并给出明确指引。
    """
    accessor = getattr(server, "sse_app", None)
    app = None
    if callable(accessor):
        app = accessor()
    elif accessor is not None:
        app = accessor
    if app is None:
        raise RuntimeError(
            "当前 mcp 版本不提供 FastMCP.sse_app()，无法构建带鉴权的 SSE 应用。"
            "请升级 mcp（pip install -U 'mcp>=1.3'），或改用 stdio 传输 / 反代前置鉴权。"
        )
    return app


class _IngressAuthMiddleware:
    """纯 ASGI Bearer 鉴权中间件（不依赖具体 mcp/Starlette 版本）。

    对所有 HTTP 请求要求 ``Authorization: Bearer <token>``；校验失败返回 401。
    使用 ``hmac.compare_digest`` 比较，避免时序侧信道。

    兼容性：部分 MCP 客户端（如 Nexent remote_mcp_service）把 authorization_token
    原样写入 Authorization 头、不带 ``Bearer `` 前缀——裸 token 且值精确匹配时
    同样放行。
    """

    def __init__(self, app, token: str):
        self.app = app
        self._token = token.encode("utf-8")

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            # 非 HTTP（如 lifespan/websocket）直接放行给底层应用处理
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        auth = headers.get(b"authorization", b"")
        provided = b""
        if auth.lower().startswith(b"bearer "):
            provided = auth[7:].strip()
        elif auth:
            provided = auth.strip()
        if provided and hmac.compare_digest(provided, self._token):
            await self.app(scope, receive, send)
            return
        await _send_unauthorized(send)


async def _send_unauthorized(send) -> None:
    body = b'{"error":"unauthorized","message":"valid Bearer token required"}'
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"www-authenticate", b"Bearer"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


async def _serve_uvicorn(app, host: str, port: int) -> None:
    """用 uvicorn 运行 ASGI 应用（比 FastMCP.run 更可控地绑定 host/port）。"""
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(
            "运行 MCP SSE 需要 uvicorn。pip install 'uvicorn[standard]'"
        ) from exc
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    await uvicorn.Server(config).serve()


async def run_http(host: str = "127.0.0.1", port: int = 8080):
    """Run MCP server in HTTP SSE mode (for remote AI agents).

    安全模型（fail-closed）：
    - 默认仅绑定回环地址 127.0.0.1（仅本机进程可达，桌面 sidecar 的合理信任边界）。
    - 远程暴露（非回环）必须同时满足：① 显式 ``LNN_MCP_ALLOW_REMOTE=1``；且
      ② 配置强入站令牌 ``LINGJING_MCP_INGRESS_TOKEN``（>=32 字符）。
    - 配置入站令牌后，SSE 应用被 Bearer 鉴权中间件包裹，所有 HTTP 请求须携带
      ``Authorization: Bearer <token>`` 方可调用工具（未配置则不附加，仅限回环）。
    - 本地开发可用 ``LINGJING_MCP_DEV=1`` 跳过令牌强度校验（仍会打印警告）。
    """
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.server.transport_security import TransportSecuritySettings
    except ImportError:
        print("Error: mcp package not installed. Run: pip install mcp", file=sys.stderr)
        sys.exit(1)

    from mcp_server.tools import register_tools

    is_loopback = host in ("127.0.0.1", "::1", "localhost")
    ingress_token = os.environ.get("LINGJING_MCP_INGRESS_TOKEN", "")
    dev_mode = os.environ.get("LINGJING_MCP_DEV", "").lower() in ("1", "true", "yes")

    if not is_loopback:
        # 远程暴露：既要显式确认，又必须配置入站令牌（fail-closed）。
        if os.environ.get("LNN_MCP_ALLOW_REMOTE", "") != "1":
            raise RuntimeError(
                "Refusing to bind MCP SSE to %r without LNN_MCP_ALLOW_REMOTE=1. "
                "SSE endpoint must not be exposed remotely by default." % host
            )
        if not ingress_token and not dev_mode:
            raise RuntimeError(
                "Refusing to expose MCP SSE on %r without inbound auth. "
                "Set LINGJING_MCP_INGRESS_TOKEN (>=32 chars) to require a Bearer "
                "token, or set LINGJING_MCP_DEV=1 only for local development." % host
            )

    # 非 loopback 绑定：关闭 mcp 内建的 DNS rebinding Host 校验（它会把
    # host.docker.internal 等网关主机名拒为 421）。真正的门是 Bearer ingress
    # 令牌 + LNN_MCP_ALLOW_REMOTE 显式开关，二者缺一不可。
    if is_loopback:
        server = FastMCP("lingjing-mcp")
    else:
        server = FastMCP(
            "lingjing-mcp",
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=False
            ),
        )
    # 让 FastMCP 内部 settings 与实际绑定地址保持一致（供 sse_app 使用）。
    try:
        server.settings.host = host
        server.settings.port = port
    except Exception:
        pass
    register_tools(server)

    app = _build_sse_app(server)
    if ingress_token:
        if len(ingress_token) < 32 and not dev_mode:
            raise RuntimeError(
                "LINGJING_MCP_INGRESS_TOKEN too short "
                f"({len(ingress_token)} chars, need >= 32)."
            )
        # mcp 新版 sse_app 默认仅放行 localhost（DNS rebinding 防护会回 421），
        # 远程模式放行 docker 网关主机名；Bearer ingress 鉴权仍是真正的门。
        if host not in ("127.0.0.1", "localhost"):
            try:
                from starlette.middleware.trustedhost import TrustedHostMiddleware

                app = TrustedHostMiddleware(
                    app,
                    allowed_hosts=[
                        "localhost",
                        "127.0.0.1",
                        "host.docker.internal",
                        ".docker.internal",
                        "*",
                    ],
                )
                logger.info(
                    "SSE host allowlist 扩展至 docker 网关（远程模式，Bearer 鉴权生效）"
                )
            except ImportError:
                logger.warning("starlette TrustedHostMiddleware 不可用，跳过主机白名单调整")
        app = _IngressAuthMiddleware(app, ingress_token)
        logger.info(
            "Starting lingjing-mcp SSE on %s:%d WITH Bearer ingress auth", host, port
        )
    else:
        logger.info(
            "Starting lingjing-mcp SSE on %s:%d (loopback default, no ingress token)",
            host,
            port,
        )
    await _serve_uvicorn(app, host, port)


def main():
    parser = argparse.ArgumentParser(description="lingjing-mcp server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport mode: stdio for local, sse for remote",
    )
    # 默认 127.0.0.1；远程暴露需 --host 0.0.0.0 + LNN_MCP_ALLOW_REMOTE=1 +
    # LINGJING_MCP_INGRESS_TOKEN（入站 Bearer 鉴权，fail-closed）
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="HTTP host (for sse mode, default 127.0.0.1; remote requires "
        "LNN_MCP_ALLOW_REMOTE=1 and LINGJING_MCP_INGRESS_TOKEN)",
    )
    parser.add_argument("--port", type=int, default=8080, help="HTTP port (for sse mode)")
    parser.add_argument("--log-level", default="INFO", help="Logging level")

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    token = os.environ.get("LINGJING_AGENT_TOKEN", "")
    if not token:
        logger.warning("LINGJING_AGENT_TOKEN not set. MCP tools will fail authentication.")

    if args.transport == "sse":
        if not os.environ.get("LINGJING_MCP_INGRESS_TOKEN"):
            logger.info(
                "LINGJING_MCP_INGRESS_TOKEN not set: SSE runs with no inbound auth "
                "(safe only on loopback 127.0.0.1). Set it to require a Bearer token "
                "when exposing beyond loopback."
            )

    if args.transport == "stdio":
        run_stdio()
    else:
        asyncio.run(run_http(args.host, args.port))


if __name__ == "__main__":
    main()
