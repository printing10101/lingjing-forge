"""插件市场 - 方言插件可见性测试（P4）。

验证声明式方言插件（postprocessor-plugins/*/dialect.yaml）作为
plugin_type=postprocessor 的条目出现在统一插件市场（/api/v1/plugins/marketplace），
id 带 ``dialect:`` 前缀隔离命名空间。

测试方式：mini FastAPI 应用只挂 plugins 路由（插件系统未初始化时
get_plugin_manager 抛 RuntimeError 被 except 降级，市场扫描路径不受影响）。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import plugins


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(plugins.router)
    with TestClient(app) as c:
        yield c


@pytest.mark.api
@pytest.mark.postprocessor
class TestDialectPluginsInMarket:
    def test_market_contains_dialect_plugins(self, client):
        resp = client.get("/api/v1/plugins/marketplace?page_size=100")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        entries = body["data"]["plugins"]

        dialect_entries = [e for e in entries if e.get("plugin_type") == "postprocessor"]
        assert len(dialect_entries) >= 5  # KND/GSK/HNC/Mitsubishi/Fagor

        ids = {e["id"] for e in dialect_entries}
        assert "dialect:knd_1000_2000_3000" in ids
        assert "dialect:gsk_980_25i" in ids
        assert "dialect:hnc_848_22" in ids
        assert "dialect:mitsubishi_m70_m80" in ids
        assert "dialect:fagor_8055" in ids

    def test_dialect_entry_fields(self, client):
        resp = client.get("/api/v1/plugins/marketplace?page_size=100")
        entries = resp.json()["data"]["plugins"]
        gsk = next(e for e in entries if e["id"] == "dialect:gsk_980_25i")

        assert gsk["plugin_type"] == "postprocessor"
        assert gsk["category"] == "dialect"
        assert gsk["version"] == "1.0.0"
        assert gsk["extends"] == "fanuc_0i"
        assert "format_arc" in gsk["template_methods"]
        assert gsk["entry_point"] == "dialect.yaml"

    def test_plugin_type_filter(self, client):
        # 按 plugin_type=postprocessor 过滤应只返回方言插件
        resp = client.get("/api/v1/plugins/marketplace?plugin_type=postprocessor&page_size=100")
        entries = resp.json()["data"]["plugins"]
        assert len(entries) >= 5
        assert all(e["plugin_type"] == "postprocessor" for e in entries)
