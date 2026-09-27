"""Unit tests for optimizer API routes（Phase D，mini FastAPI app）。"""

from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("LNN_PERMISSION_ENFORCED", "false")

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.optimizer_routes import router


@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    return application


@pytest_asyncio.fixture
async def client(app: FastAPI):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


class TestRecommend:
    @pytest.mark.asyncio
    async def test_recommend_baseline(self, client) -> None:
        resp = await client.post(
            "/optimizer/recommend",
            json={"material": "AL6061", "machining_type": "milling"},
        )
        assert resp.status_code == 200
        data = resp.json()
        rec = data["recommendation"]
        assert rec["strategy"] == "L0_baseline"
        assert rec["depth_of_cut_mm"] == 2.0
        assert rec["feed_mm_per_rev"] == 0.2
        assert rec["spindle_rpm"] == 8000

    @pytest.mark.asyncio
    async def test_recommend_unknown_returns_404(self, client) -> None:
        resp = await client.post(
            "/optimizer/recommend",
            json={"material": "UNKNOWN-XYZ", "machining_type": "milling"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_recommend_target_tool_life(self, client) -> None:
        resp = await client.post(
            "/optimizer/recommend",
            json={
                "material": "AL6061",
                "machining_type": "milling",
                "target": "tool_life",
            },
        )
        assert resp.status_code == 200
        rec = resp.json()["recommendation"]
        assert rec["feed_mm_per_rev"] < 0.2

    @pytest.mark.asyncio
    async def test_recommend_invalid_target_422(self, client) -> None:
        resp = await client.post(
            "/optimizer/recommend",
            json={"material": "AL6061", "target": "bogus"},
        )
        assert resp.status_code == 422


class TestEvaluate:
    @pytest.mark.asyncio
    async def test_evaluate_good_result(self, client) -> None:
        resp = await client.post(
            "/optimizer/evaluate",
            json={
                "cycle_time_s": 100.0,
                "tool_wear_percent": 10.0,
                "surface_roughness_ra": 1.0,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["score"] > 0.9
        assert data["result_ok"] is True

    @pytest.mark.asyncio
    async def test_evaluate_scrap(self, client) -> None:
        resp = await client.post(
            "/optimizer/evaluate",
            json={"cycle_time_s": 100.0, "result": "scrap"},
        )
        assert resp.status_code == 200
        assert resp.json()["score"] <= 0.6

    @pytest.mark.asyncio
    async def test_evaluate_invalid_result_422(self, client) -> None:
        resp = await client.post(
            "/optimizer/evaluate",
            json={"result": "bogus"},
        )
        assert resp.status_code == 422


class TestCompare:
    @pytest.mark.asyncio
    async def test_compare_a_better(self, client) -> None:
        resp = await client.post(
            "/optimizer/compare",
            json={
                "a_results": [{"cycle_time_s": 80.0}, {"cycle_time_s": 90.0}],
                "b_results": [{"cycle_time_s": 120.0}, {"cycle_time_s": 130.0}],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["better"] == "a"
        assert data["improvement_pct"] > 25.0

    @pytest.mark.asyncio
    async def test_compare_empty_422(self, client) -> None:
        resp = await client.post(
            "/optimizer/compare",
            json={"a_results": [], "b_results": [{"cycle_time_s": 100.0}]},
        )
        assert resp.status_code == 422


class TestBaselines:
    @pytest.mark.asyncio
    async def test_list_all(self, client) -> None:
        resp = await client.get("/optimizer/baselines")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 15
        assert any(e["material"] == "AL6061" for e in data["entries"])

    @pytest.mark.asyncio
    async def test_list_filter_material(self, client) -> None:
        resp = await client.get("/optimizer/baselines?material=SS304")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 3
        assert all(e["material"] == "SS304" for e in data["entries"])

    @pytest.mark.asyncio
    async def test_list_filter_type(self, client) -> None:
        resp = await client.get("/optimizer/baselines?machining_type=turning")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 5
        assert all(e["machining_type"] == "turning" for e in data["entries"])
