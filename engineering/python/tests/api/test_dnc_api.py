"""DNC 机床通信 API 测试。

测试覆盖：
- 机床连接管理（添加、删除、列出）
- 机床状态查询
- NC 程序传输
- 报警信息查询

设计说明（2026-08-14 收编修复）：
- 原文件位于仓库根 tests/，未进入 CI 收集（孤儿测试），且未适配
  强制鉴权（LNN_PERMISSION_ENFORCED）导致全部 401。
- 迁入 engineering/python/tests/api/（pytest.ini testpaths 覆盖），
  由 engineering/python/tests/conftest.py 预置测试环境，鉴权自动放行。
- 原用例会真实连接 OPC UA / MTConnect 端点（含 5 次重试，最长可挂起
  数十秒）。现通过 autouse fixture mock dnc_manager 的网络方法，仅验证
  API 契约（请求校验 / 错误映射 / 响应结构），不依赖真实机床。
- Mock 使用 unittest.mock + monkeypatch（不依赖 pytest-mock，CI 未安装）。
- 响应断言对齐当前统一信封格式：成功 {"code":0,"data":...}，
  业务错误 HTTP 200 + 非零 code，异常 {"code","message"}（5xx 消息脱敏）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
from httpx import AsyncClient, ASGITransport
from fastapi.testclient import TestClient

from app.main import app
from app.dnc.dnc_manager import DNCManager, dnc_manager, ProtocolType


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


@pytest.fixture(autouse=True)
def mock_manager(monkeypatch):
    """Mock dnc_manager 网络操作，保持测试 hermetic（不真实连接机床）。

    同时默认开启 LNN_DNC_ALLOW_UNVALIDATED_NC=1（下发闸门逃生阀）：
    本文件的传输用例针对「API 契约」而非闸门逻辑；闸门行为由
    TestNCProgramDispatchGate 与 tests/unit/test_dnc_nc_gate.py 覆盖。
    """
    monkeypatch.setenv("LNN_DNC_ALLOW_UNVALIDATED_NC", "1")
    monkeypatch.setattr(dnc_manager, "add_machine", AsyncMock(return_value=True))
    monkeypatch.setattr(dnc_manager, "remove_machine", AsyncMock())
    monkeypatch.setattr(dnc_manager, "list_machines", lambda: [])
    monkeypatch.setattr(
        dnc_manager,
        "get_machine_status",
        AsyncMock(return_value={"machine_id": "TEST-CNC-001", "state": "running"}),
    )
    monkeypatch.setattr(dnc_manager, "get_all_machines_status", AsyncMock(return_value={}))
    monkeypatch.setattr(dnc_manager, "send_nc_program", AsyncMock(return_value=True))
    # connections 属性（只读兼容视图）默认置空
    monkeypatch.setattr(DNCManager, "connections", PropertyMock(return_value={}))
    yield


def _set_connections(monkeypatch, machines: dict) -> None:
    """覆盖 connections 属性返回内容（供报警/状态用例注入伪连接）。"""
    monkeypatch.setattr(DNCManager, "connections", PropertyMock(return_value=machines))


# 机床连接管理测试


class TestMachineConnection:
    """机床连接管理测试"""

    def test_connect_machine_opcua(self, client):
        """测试通过 OPC UA 协议连接机床"""
        response = client.post(
            "/api/v1/dnc/machines",
            json={
                "machine_id": "TEST-CNC-001",
                "protocol": "opcua",
                "endpoint": "opc.tcp://192.168.1.100:4840",
                "username": "test_user",
                "password": "test_pass",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["machine_id"] == "TEST-CNC-001"
        assert data["data"]["status"] == "connected"

    def test_connect_machine_mtconnect(self, client):
        """测试通过 MTConnect 协议连接机床"""
        response = client.post(
            "/api/v1/dnc/machines",
            json={
                "machine_id": "TEST-CNC-002",
                "protocol": "mtconnect",
                "endpoint": "http://192.168.1.101:5000",
                "device_name": "CNC-Device",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["machine_id"] == "TEST-CNC-002"

    def test_list_machines(self, client):
        """测试列出已连接机床"""
        response = client.get("/api/v1/dnc/machines")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert isinstance(data["data"], list)

    def test_disconnect_machine(self, client):
        """测试断开机床连接"""
        response = client.delete("/api/v1/dnc/machines/TEST-CNC-DISC")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["status"] == "disconnected"


# 机床状态查询测试


class TestMachineStatus:
    """机床状态查询测试"""

    def test_get_machine_status(self, client):
        """测试获取单台机床状态（mock 正常返回）"""
        response = client.get("/api/v1/dnc/machines/TEST-CNC-STATUS/status")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0

    def test_get_machine_status_not_found(self, client, monkeypatch):
        """测试获取未连接机床状态 → 404（manager 返回 error 字典）"""
        monkeypatch.setattr(
            dnc_manager,
            "get_machine_status",
            AsyncMock(return_value={"error": "机床未连接"}),
        )
        response = client.get("/api/v1/dnc/machines/TEST-NOT-EXIST/status")
        assert response.status_code == 404

    def test_get_all_machines_status(self, client):
        """测试获取所有机床状态"""
        response = client.get("/api/v1/dnc/status")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert isinstance(data["data"], dict)


# NC 程序传输测试


class TestNCProgramTransfer:
    """NC 程序传输测试"""

    def test_send_nc_program_file_not_found(self, client):
        """测试发送不存在的 NC 程序文件 → 400"""
        response = client.post(
            "/api/v1/dnc/nc-program/send",
            json={
                "machine_id": "TEST-CNC-001",
                "program_path": "/nonexistent/path/program.nc",
                "program_name": "test_program",
            },
        )
        assert response.status_code == 400
        assert "文件不存在" in response.json()["message"]

    def test_send_nc_program_success(self, client):
        """测试发送 NC 程序成功（mock 传输）"""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".nc", delete=False) as f:
            f.write("O0001\nG90 G54\nM30\n")
            temp_path = f.name
        try:
            response = client.post(
                "/api/v1/dnc/nc-program/send",
                json={
                    "machine_id": "TEST-CNC-001",
                    "program_path": temp_path,
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["code"] == 0
            assert data["data"]["status"] == "sent"
        finally:
            os.unlink(temp_path)

    def test_send_nc_program_transfer_failed(self, client, monkeypatch):
        """测试 NC 程序发送失败 → 业务错误信封（HTTP 200 + 非零 code）"""
        import os
        import tempfile

        monkeypatch.setattr(dnc_manager, "send_nc_program", AsyncMock(return_value=False))

        with tempfile.NamedTemporaryFile(mode="w", suffix=".nc", delete=False) as f:
            f.write("O0001\n")
            temp_path = f.name
        try:
            response = client.post(
                "/api/v1/dnc/nc-program/send",
                json={
                    "machine_id": "TEST-CNC-001",
                    "program_path": temp_path,
                },
            )
            # 业务错误信封：HTTP 200 + 非零错误码（success/error 统一信封约定）
            assert response.status_code == 200
            assert response.json()["code"] != 0
        finally:
            os.unlink(temp_path)


# 仿真强制闭环：DNC 下发闸门测试（闸门逻辑单测见 tests/unit/test_dnc_nc_gate.py）


class TestNCProgramDispatchGate:
    """NC 下发闸门 API 契约测试（关闭逃生阀，验证端到端拦截/放行）。"""

    @pytest.fixture
    def cam_store(self):
        from app.cam_validation.cam_store import CamTaskStore

        store = CamTaskStore()
        yield store
        store.clear()

    def _register_cam_task(self, cam_store, program_path: str, status: str, voxel: bool | None) -> None:
        import time

        from app.cam_validation.cam_store import CamValidationTask

        cam_store.add_task(
            CamValidationTask(
                task_id=f"cam_gate_{abs(hash(program_path)) % 10**8}",
                source_gcode_report_path=str(program_path) + ".report.json",
                source_gcode_file_path=program_path,
                controller_type="fanuc_0i",
                status=status,
                voxel_check_passed=voxel,
                kinematics_check_passed=True if voxel is not None else None,
                completed_at=time.time(),
            )
        )

    def test_unvalidated_program_rejected_with_8013(self, client, monkeypatch):
        """未通过阶段 7 校验的程序下发 → 403 + 错误码 8013 + 可执行补救建议"""
        monkeypatch.delenv("LNN_DNC_ALLOW_UNVALIDATED_NC", raising=False)
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".nc", delete=False) as f:
            f.write("O0001\nG90 G54\nM30\n")
            temp_path = f.name
        try:
            response = client.post(
                "/api/v1/dnc/nc-program/send",
                json={"machine_id": "TEST-CNC-001", "program_path": temp_path},
            )
            assert response.status_code == 403
            data = response.json()
            assert data["code"] == 8013
            assert "校验记录" in data["message"]
            assert data["suggestion"]
        finally:
            os.unlink(temp_path)

    def test_validated_program_dispatch_allowed(self, client, monkeypatch, cam_store):
        """SUCCEEDED + 体素通过的任务导出程序 → 正常下发（200 sent）"""
        monkeypatch.delenv("LNN_DNC_ALLOW_UNVALIDATED_NC", raising=False)
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".nc", delete=False) as f:
            f.write("O0001\nG90 G54\nM30\n")
            temp_path = f.name
        self._register_cam_task(cam_store, temp_path, status="succeeded", voxel=True)
        try:
            response = client.post(
                "/api/v1/dnc/nc-program/send",
                json={"machine_id": "TEST-CNC-001", "program_path": temp_path},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["code"] == 0
            assert data["data"]["status"] == "sent"
        finally:
            os.unlink(temp_path)

    def test_partially_validated_program_rejected(self, client, monkeypatch, cam_store):
        """校验任务未 SUCCEEDED（如 validated 待审核）→ 403 拦截"""
        monkeypatch.delenv("LNN_DNC_ALLOW_UNVALIDATED_NC", raising=False)
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".nc", delete=False) as f:
            f.write("O0001\nG90 G54\nM30\n")
            temp_path = f.name
        self._register_cam_task(cam_store, temp_path, status="validated", voxel=True)
        try:
            response = client.post(
                "/api/v1/dnc/nc-program/send",
                json={"machine_id": "TEST-CNC-001", "program_path": temp_path},
            )
            assert response.status_code == 403
            assert response.json()["code"] == 8013
        finally:
            os.unlink(temp_path)


# 报警信息查询测试


class TestMachineAlarms:
    """机床报警信息查询测试"""

    def test_get_machine_alarms_not_connected(self, client):
        """测试查询未连接机床的报警 → 404"""
        response = client.get("/api/v1/dnc/machines/TEST-NOT-EXIST/alarms")
        assert response.status_code == 404

    def test_get_machine_alarms_mtconnect(self, client, monkeypatch):
        """测试查询 MTConnect 机床报警"""
        fake_client = MagicMock()
        fake_client.get_alarms = AsyncMock(return_value=[{"code": "A100", "message": "主轴过载"}])
        _set_connections(
            monkeypatch,
            {
                "TEST-CNC-ALARM": {
                    "client": fake_client,
                    "protocol": ProtocolType.MTCONNECT,
                    "endpoint": "http://192.168.1.101:5000",
                    "connected_at": "2026-08-14T00:00:00+00:00",
                }
            },
        )

        response = client.get("/api/v1/dnc/machines/TEST-CNC-ALARM/alarms")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"][0]["code"] == "A100"

    def test_get_machine_alarms_opcua(self, client, monkeypatch):
        """测试查询 OPC UA 机床报警（能力未接入 → 显式 501，不冒充空报警）"""
        _set_connections(
            monkeypatch,
            {
                "TEST-CNC-OPCUA-ALARM": {
                    "client": None,
                    "protocol": ProtocolType.OPC_UA,
                    "endpoint": "opc.tcp://192.168.1.100:4840",
                    "connected_at": "2026-08-14T00:00:00+00:00",
                }
            },
        )

        response = client.get("/api/v1/dnc/machines/TEST-CNC-OPCUA-ALARM/alarms")
        # 诚实失败：统一错误体 SERVICE_UNAVAILABLE(2002)，不再冒充"无报警"空列表
        body = response.json()
        assert body["code"] == 2002
        assert "暂未实现" in body["message"]
        assert "MTConnect" in body.get("suggestion", "")


# 参数验证测试


class TestValidation:
    """参数验证测试"""

    def test_connect_machine_missing_fields(self, client):
        """测试缺少必填字段 → 422"""
        response = client.post(
            "/api/v1/dnc/machines",
            json={
                "machine_id": "TEST-CNC-001",
                # 缺少 protocol 和 endpoint
            },
        )
        assert response.status_code == 422

    def test_connect_machine_invalid_protocol(self, client):
        """测试无效的协议类型 → 422"""
        response = client.post(
            "/api/v1/dnc/machines",
            json={
                "machine_id": "TEST-CNC-001",
                "protocol": "invalid_protocol",
                "endpoint": "tcp://192.168.1.100",
            },
        )
        assert response.status_code == 422
