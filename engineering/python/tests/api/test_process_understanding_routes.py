"""W10.2 后续 / W 引擎验证：工艺理解主接口 /query 路由测试。

引擎与测试此前齐备但主入口 HTTP 路由从未注册（根因：迁移拆分时
只搬了 explain/stats/health 三个端点）。本测试锁定主接口契约：
- POST /query → 引擎 process → 结构化输出；
- 空查询 422；引擎异常 → 结构化错误信封。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai.process_understanding import routes as pu_routes
from app.ai.process_understanding._output import ProcessUnderstandingOutput

pytestmark = [pytest.mark.api]


class _StubEngine:
    """引擎桩：不加载 LLM/向量库，直接返回固定结构化输出。"""

    def __init__(self):
        self.calls: list[str] = []

    async def process(self, user_input: str) -> ProcessUnderstandingOutput:
        self.calls.append(user_input)
        return ProcessUnderstandingOutput(
            task_type="process_planning",
            intent="铣削加工",
            entities={"material": "45钢"},
            response="推荐铣削+钻孔复合工艺",
            confidence=0.82,
            sources=["kb-001"],
            actions=["铣削上平面", "钻孔 4xφ12"],
        )


@pytest.fixture
def client(monkeypatch):
    stub = _StubEngine()
    monkeypatch.setattr(pu_routes, "get_process_understanding_engine", lambda: stub)
    app = FastAPI()
    app.include_router(pu_routes.router)
    return {"client": TestClient(app), "stub": stub}


class TestQueryEndpoint:
    def test_query_returns_structured_output(self, client):
        resp = client["client"].post("/query", json={"query": "加工45钢法兰盘，钻孔+铣削"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert data["task_type"] == "process_planning"
        assert data["entities"]["material"] == "45钢"
        assert data["confidence"] == pytest.approx(0.82)
        assert client["stub"].calls == ["加工45钢法兰盘，钻孔+铣削"]

    def test_query_rejects_empty_query(self, client):
        resp = client["client"].post("/query", json={"query": ""})
        assert resp.status_code == 422

    def test_query_engine_error_returns_error_envelope(self, client, monkeypatch):
        class _BadEngine:
            async def process(self, user_input: str):
                raise ValueError("引擎内部爆炸")

        monkeypatch.setattr(pu_routes, "get_process_understanding_engine", lambda: _BadEngine())
        resp = client["client"].post("/query", json={"query": "任意输入"})
        body = resp.json()
        assert body["code"] != 0

    def test_health_still_available(self, client):
        resp = client["client"].get("/health")
        assert resp.status_code == 200
