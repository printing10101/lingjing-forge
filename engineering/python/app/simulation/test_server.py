"""Simulation test server - development environment only.

WARNING: This server is for development and integration testing purposes only.
It must not be exposed in production environments. Loads a minimal set of
routes, bypassing the full application's authentication and security middleware.

Usage:
    LINGJING_ENV=development python -m app.simulation.test_server

The server requires LINGJING_ENV=development to be set; it will assert
on startup if the environment is not "development".
"""

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.simulation.api import router as simulation_router
from app.projects.project_api import router as project_router
from app.config import config
from app.middleware.cors_config import cors_settings

# 检查环境变量，如果未设置则默认为 development
_env = os.environ.get("LINGJING_ENV", "development")
assert _env == "development", (
    "Test server is only allowed in development environment. Please set LINGJING_ENV=development"
)

app = FastAPI(
    title="LingJing Manufacturing - Integration Test (DEV ONLY)",
    version=f"{config.app_version}-test",
    docs_url="/api/docs",
    description="WARNING: Development/test server. Do not use in production.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_settings.get_origins(),
    allow_origin_regex=cors_settings.get_origin_regex(),
    allow_credentials=cors_settings.allow_credentials,
    allow_methods=cors_settings.get_methods(),
    allow_headers=cors_settings.get_headers(),
    expose_headers=cors_settings.get_expose_headers(),
    max_age=cors_settings.max_age,
)

app.include_router(simulation_router)
app.include_router(project_router)


@app.get("/api/health")
async def health() -> dict:
    """Health check endpoint for the test server.

    Returns:
        Dictionary with service status and environment info.
    """
    return {"status": "ok", "service": "simulation-test", "env": "development-only"}


@app.get("/api/health/ping")
async def ping() -> dict:
    """Lightweight ping endpoint.

    Returns:
        Simple ping confirmation.
    """
    return {"ping": True}
