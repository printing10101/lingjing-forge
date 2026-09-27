"""Tests for business error triggering, system exception fallback, and error logging."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi import APIRouter, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app.core.error_taxonomy import ErrorCategory, ManufacturingError  # noqa: E402
from app.core.exception_handlers import (  # noqa: E402
    generic_exception_handler,
    manufacturing_error_handler,
    register_exception_handlers,
)
from app.core.response import success  # noqa: E402


class ConflictCheckRequest(BaseModel):
    tool_diameter: float = Field(default=20.0, ge=0.5, le=300.0)
    slot_width: float = Field(default=10.0, ge=0.1, le=500.0)
    material: str = Field(default="45钢")
    operation: str = Field(default="槽铣")


def _make_test_app() -> FastAPI:
    test_app = FastAPI()
    router = APIRouter()

    @router.post("/api/simulation/check-conflict")
    async def check_conflict(request: ConflictCheckRequest):
        tool_d = request.tool_diameter
        slot_w = request.slot_width
        if tool_d > slot_w:
            raise ManufacturingError(
                category=ErrorCategory.NO_SUITABLE_TOOL,
                detail=f"刀具直径({tool_d}mm)大于槽宽({slot_w}mm)，无法进入槽内进行加工。当前材料：{request.material}，工序：{request.operation}",
                suggestion=(
                    f"刀具直径({tool_d}mm)超出槽宽({slot_w}mm)限制。"
                    f"建议方案：1) 更换刀具，选用直径≤{slot_w}mm的立铣刀；"
                    "2) 调整加工工艺，改用分层加工或多刀铣削策略；"
                    f"3) 修改零件设计，增大槽宽至≥{tool_d}mm。"
                ),
                recoverable=False,
            )
        return success(
            data={"compatible": True, "tool_diameter": tool_d, "slot_width": slot_w},
            message="刀具与槽型匹配，无冲突",
        )

    @router.post("/api/simulation/trigger-system-error")
    async def trigger_system_error():
        _ = 1 / 0
        return success(data={"result": 0})

    test_app.include_router(router)
    register_exception_handlers(test_app)
    return test_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_make_test_app(), raise_server_exceptions=False)


class TestBusinessConflictDetection:
    """Test 1: 20mm tool milling 10mm slot must trigger structured conflict error."""

    def test_conflict_detected_with_dialog_fields(self, client: TestClient):
        response = client.post(
            "/api/simulation/check-conflict",
            json={
                "tool_diameter": 20.0,
                "slot_width": 10.0,
                "material": "45钢",
                "operation": "槽铣",
            },
        )

        assert response.status_code == 409
        data = response.json()

        assert "code" in data
        assert "error_code" in data, f"Expected error_code field, got: {data}"
        assert "message" in data
        assert "severity" in data
        assert data["severity"] in ("error", "critical", "warning")

        assert "suggestion" in data, (
            f"Expected suggestion field, got keys: {list(data.keys())}"
        )
        assert len(data["suggestion"]) > 20

        assert "刀具" in data["suggestion"] or "tool" in data["suggestion"].lower()

        assert "detail" in data
        assert "20" in data["detail"] or "20.0" in data["detail"]
        assert "10" in data["detail"] or "10.0" in data["detail"]

    def test_conflict_response_has_no_traceback(self, client: TestClient):
        response = client.post(
            "/api/simulation/check-conflict",
            json={"tool_diameter": 20.0, "slot_width": 10.0},
        )
        data = response.json()
        data_str = json.dumps(data, ensure_ascii=False)
        assert "Traceback" not in data_str
        assert "traceback" not in data_str.lower()
        assert "File " not in data_str

    def test_no_conflict_when_tool_fits(self, client: TestClient):
        response = client.post(
            "/api/simulation/check-conflict",
            json={"tool_diameter": 5.0, "slot_width": 10.0},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["compatible"] is True

    def test_conflict_suggests_smaller_tool(self, client: TestClient):
        response = client.post(
            "/api/simulation/check-conflict",
            json={"tool_diameter": 20.0, "slot_width": 8.0},
        )
        data = response.json()
        suggestion = data.get("suggestion", "").lower()
        assert (
            "更换" in suggestion
            or "换刀" in suggestion
            or "≤" in suggestion
            or "<=" in suggestion
        )


class TestSystemErrorFallback:
    """Test 2: ZeroDivisionError produces sanitized 500 with no sensitive info."""

    def test_divzero_returns_500_with_sanitized_message(self, client: TestClient):
        response = client.post("/api/simulation/trigger-system-error")

        assert response.status_code == 500
        data = response.json()

        assert data["code"] == 2001
        assert isinstance(data.get("detail"), dict)
        assert "error_id" in data["detail"]

        assert "管理员" in data.get("message", "")
        assert "内部错误" in data.get("message", "") or "ERROR" in str(
            data.get("code", "")
        )

    def test_divzero_response_has_no_stacktrace(self, client: TestClient):
        response = client.post("/api/simulation/trigger-system-error")
        data = response.json()

        data_str = json.dumps(data, ensure_ascii=False)
        assert "Traceback" not in data_str
        assert "ZeroDivisionError" not in data_str

    def test_divzero_response_has_no_path_info(self, client: TestClient):
        response = client.post("/api/simulation/trigger-system-error")
        data = response.json()
        data_str = json.dumps(data, ensure_ascii=False)
        assert "/python/" not in data_str
        assert "simulation" not in str(data.get("detail", {}))


class TestErrorLogging:
    """Test 3: Error logging verification (WARNING for business, ERROR for system)."""

    def test_manufacturing_error_handler_logs_warning(self, caplog):
        caplog.set_level(logging.WARNING)

        from fastapi import Request

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/simulation/check-conflict",
            "headers": [],
        }
        request = Request(scope)

        error = ManufacturingError(
            category=ErrorCategory.NO_SUITABLE_TOOL,
            detail="刀具直径(20mm)大于槽宽(10mm)",
            suggestion="建议更换刀具",
        )

        loop = asyncio.new_event_loop()
        loop.run_until_complete(manufacturing_error_handler(request, error))
        loop.close()

        warning_logs = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_logs) >= 1
        log_msg = warning_logs[0].message.lower()
        assert (
            "manufacturing" in log_msg
            or "e3002" in log_msg
            or "no_suitable_tool" in log_msg
        )

    def test_generic_handler_logs_error_level(self, caplog):
        caplog.set_level(logging.ERROR)

        from fastapi import Request

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/simulation/trigger-system-error",
            "headers": [],
        }
        request = Request(scope)

        try:
            1 / 0
        except ZeroDivisionError as e:
            import asyncio

            loop = asyncio.new_event_loop()
            loop.run_until_complete(generic_exception_handler(request, e))
            loop.close()

        error_logs = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_logs) >= 1
        assert "ZeroDivisionError" in error_logs[0].message

    def test_log_contains_timestamp_and_context(self, caplog):
        caplog.set_level(logging.WARNING)

        from fastapi import Request

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/simulation/check-conflict",
            "headers": [],
        }
        request = Request(scope)

        error = ManufacturingError(
            category=ErrorCategory.PARAMETER_OUT_OF_RANGE,
            detail="进给量 5.0 超出范围",
        )

        loop = asyncio.new_event_loop()
        loop.run_until_complete(manufacturing_error_handler(request, error))
        loop.close()

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) >= 1
        record = warning_records[0]
        assert record.created is not None
        assert hasattr(record, "asctime") or record.created > 0
        msg = record.getMessage()
        assert (
            "api" in msg.lower()
            or "check-conflict" in msg.lower()
            or "manufacturing" in msg.lower()
        )


class TestNetworkErrorMessageFormat:
    """Test 4: Verify error response format for network errors."""

    def test_500_response_format(self, client: TestClient):
        response = client.post("/api/simulation/trigger-system-error")
        assert response.status_code == 500
        data = response.json()

        assert "code" in data
        assert "message" in data
        assert "request_id" in data
        assert data["code"] == 2001

    def test_409_response_format(self, client: TestClient):
        response = client.post(
            "/api/simulation/check-conflict",
            json={"tool_diameter": 20.0, "slot_width": 10.0},
        )
        assert response.status_code == 409
        data = response.json()

        assert "code" in data
        assert "error_code" in data
        assert data["error_code"].startswith("E")
        assert "message" in data
        assert "severity" in data
        assert data["severity"] in ("error", "critical", "warning")
        assert "suggestion" in data
        assert "request_id" in data
