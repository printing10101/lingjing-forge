"""MES/ERP 系统集成 API 测试。

测试覆盖：
- 工单同步
- 生产数据上报
- 物料查询
- 质量数据上报
- 健康检查
- 配置验证

设计说明（2026-08-14 收编修复）：
- 原文件位于仓库根 tests/，未进入 CI 收集（孤儿测试），且未适配
  强制鉴权（LNN_PERMISSION_ENFORCED）导致全部 401。
- 迁入 engineering/python/tests/api/（pytest.ini testpaths 覆盖），
  由 engineering/python/tests/conftest.py 预置 LNN_PERMISSION_ENFORCED=false
  测试环境，鉴权自动放行。
- 业务类用例通过 app.dependency_overrides[get_mes_client] 注入 mock 客户端，
  不依赖真实 MES 服务（hermetic）；仅「未启用/未配置」用例走真实依赖
  验证 503 降级路径（5xx 消息按安全设计脱敏为通用文案）。
- Mock 使用 unittest.mock + monkeypatch（不依赖 pytest-mock，CI 未安装）。
- 修复原文件 bug：mocker 未声明为 fixture 参数（NameError）、
  datetime.now() 未带时区（P2-7 约定）、底部 import 上提到模块顶层。
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient, ASGITransport
from fastapi.testclient import TestClient

from app.main import app
from app.integrations.mes.api import get_mes_client
from app.integrations.mes.client import MESClient


# Fixtures


@pytest.fixture
def client():
    """创建测试客户端"""
    return TestClient(app)


@pytest.fixture
async def async_client():
    """创建异步测试客户端"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def mock_mes_client():
    """创建 mock MES 客户端"""
    mock = MagicMock(spec=MESClient)
    mock.health_check = AsyncMock(return_value=True)
    return mock


def _override(mock_mes_client):
    """注入 mock 客户端并返回清理函数。"""
    app.dependency_overrides[get_mes_client] = lambda: mock_mes_client

    def _cleanup():
        app.dependency_overrides.pop(get_mes_client, None)

    return _cleanup


def _sync_result(message: str, data_id: str):
    """构造带 UTC 时间戳的 SyncResult（P2-7 时区约定）。"""
    from app.integrations.mes.client import SyncResult

    return SyncResult(
        success=True,
        message=message,
        data_id=data_id,
        timestamp=datetime.now(timezone.utc),
    )


# 工单同步测试


class TestWorkOrderSync:
    """工单同步测试"""

    def test_sync_work_order_success(self, client, mock_mes_client):
        """测试工单同步成功"""
        mock_mes_client.sync_work_order = AsyncMock(return_value=_sync_result("工单同步成功", "WO-001"))
        cleanup = _override(mock_mes_client)
        try:
            response = client.post(
                "/api/v1/mes/sync-work-order",
                json={
                    "work_order_no": "WO-2024-001",
                    "product_code": "PROD-001",
                    "quantity": 100,
                    "priority": 5,
                    "planned_start": "2024-01-01T08:00:00",
                    "planned_end": "2024-01-05T17:00:00",
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["message"] == "工单同步成功"
        finally:
            cleanup()

    def test_sync_work_order_missing_fields(self, client, mock_mes_client):
        """测试工单同步缺少必填字段（Pydantic 422）"""
        cleanup = _override(mock_mes_client)
        try:
            response = client.post(
                "/api/v1/mes/sync-work-order",
                json={
                    "work_order_no": "WO-2024-001",
                    # 缺少 product_code 和 quantity
                },
            )
            assert response.status_code == 422
        finally:
            cleanup()

    def test_sync_work_order_invalid_quantity(self, client, mock_mes_client):
        """测试工单同步数量无效（gt=0 约束 → 422）"""
        cleanup = _override(mock_mes_client)
        try:
            response = client.post(
                "/api/v1/mes/sync-work-order",
                json={
                    "work_order_no": "WO-2024-001",
                    "product_code": "PROD-001",
                    "quantity": -10,  # 负数
                },
            )
            assert response.status_code == 422
        finally:
            cleanup()


# 生产数据上报测试


class TestProductionReport:
    """生产数据上报测试"""

    def test_report_production_success(self, client, mock_mes_client):
        """测试生产数据上报成功"""
        mock_mes_client.report_production = AsyncMock(return_value=_sync_result("生产数据上报成功", "PROD-001"))
        cleanup = _override(mock_mes_client)
        try:
            response = client.post(
                "/api/v1/mes/report-production",
                json={
                    "batch_no": "BATCH-2024-001",
                    "quantity": 100,
                    "qualified": 95,
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
        finally:
            cleanup()

    def test_report_production_client_rejects(self, client, mock_mes_client):
        """测试合格数量超过总数：由 MES 客户端校验拒绝 → 路由映射为 400"""
        # 数量合法性校验在 MES 客户端侧（真实服务），这里 mock 客户端
        # 对「合格数 > 总数」抛 ValueError，验证路由正确映射为 400。
        mock_mes_client.report_production = AsyncMock(side_effect=ValueError("qualified exceeds quantity"))
        cleanup = _override(mock_mes_client)
        try:
            response = client.post(
                "/api/v1/mes/report-production",
                json={
                    "batch_no": "BATCH-2024-001",
                    "quantity": 100,
                    "qualified": 150,  # 超过总数量
                },
            )
            assert response.status_code == 400
            assert response.json()["message"] == "请求参数无效"
        finally:
            cleanup()


# 物料查询测试


class TestMaterialQuery:
    """物料查询测试"""

    def test_query_material_success(self, client, mock_mes_client):
        """测试物料查询成功"""
        from app.integrations.mes.client import MaterialInfo

        mock_mes_client.query_material = AsyncMock(
            return_value=MaterialInfo(
                material_code="MAT-001",
                name="铝合金 6061-T6",
                specification="100x100x50mm",
                unit="件",
                stock_quantity=500.0,
                warehouse_location="A-01-01",
                batch_no="BATCH-MAT-001",
                expiry_date=datetime(2025, 12, 31, tzinfo=timezone.utc),
            )
        )
        cleanup = _override(mock_mes_client)
        try:
            response = client.get("/api/v1/mes/material/MAT-001")
            assert response.status_code == 200
            data = response.json()
            assert data["material_code"] == "MAT-001"
            assert data["name"] == "铝合金 6061-T6"
        finally:
            cleanup()

    def test_query_material_not_found(self, client, mock_mes_client):
        """测试物料未找到 → 404"""
        mock_mes_client.query_material = AsyncMock(return_value=None)
        cleanup = _override(mock_mes_client)
        try:
            response = client.get("/api/v1/mes/material/MAT-NOT-EXIST")
            assert response.status_code == 404
        finally:
            cleanup()


# 质量数据上报测试


class TestQualityReport:
    """质量数据上报测试"""

    def test_report_quality_success(self, client, mock_mes_client):
        """测试质量数据上报成功"""
        mock_mes_client.report_quality = AsyncMock(return_value=_sync_result("质量数据上报成功", "QUAL-001"))
        cleanup = _override(mock_mes_client)
        try:
            response = client.post(
                "/api/v1/mes/report-quality",
                json={
                    "batch_no": "BATCH-2024-001",
                    "product_code": "PROD-001",
                    "inspection_type": "in_process",
                    "result": "pass",
                    "inspector": "张三",
                    "inspection_time": "2024-01-01T10:00:00",
                    "sample_size": 10,
                    "qualified_qty": 9,
                    "defective_qty": 1,
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
        finally:
            cleanup()


# 健康检查测试


class TestHealthCheck:
    """健康检查测试"""

    def test_health_check_healthy(self, client, mock_mes_client):
        """测试健康检查 - 系统正常"""
        cleanup = _override(mock_mes_client)
        try:
            response = client.get("/api/v1/mes/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"
            assert data["mes_connected"] is True
        finally:
            cleanup()

    def test_health_check_unhealthy(self, client, mock_mes_client):
        """测试健康检查 - 系统异常"""
        mock_mes_client.health_check = AsyncMock(return_value=False)
        cleanup = _override(mock_mes_client)
        try:
            response = client.get("/api/v1/mes/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "unhealthy"
            assert data["mes_connected"] is False
        finally:
            cleanup()


# 配置验证测试


class TestConfiguration:
    """配置验证测试"""

    def test_mes_not_enabled(self, client):
        """测试 MES 未启用 → 503（5xx 消息按安全设计脱敏）"""
        # 确保依赖覆盖已清理（走真实 get_mes_client 依赖）
        app.dependency_overrides.clear()

        response = client.get("/api/v1/mes/health")
        assert response.status_code == 503
        # 5xx 异常消息统一脱敏（exception_handlers 安全设计，
        # 真实 detail 仅记日志，避免向客户端泄露内部配置信息）。
        assert response.json()["message"] == "系统内部错误，请联系管理员"

    def test_mes_not_configured(self, client, monkeypatch):
        """测试 MES 配置不完整 → 503（5xx 消息按安全设计脱敏）"""
        from app.config import config

        app.dependency_overrides.clear()

        # Mock 配置
        monkeypatch.setattr(config.mes, "enabled", True)
        monkeypatch.setattr(config.mes, "base_url", "")
        monkeypatch.setattr(config.mes, "api_key", "")

        response = client.get("/api/v1/mes/health")
        assert response.status_code == 503
        # 同上：5xx 消息统一脱敏，真实 detail（not configured）仅记日志
        assert response.json()["message"] == "系统内部错误，请联系管理员"
