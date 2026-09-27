"""设备元数据→MCP 工具自动生成 单元测试（Phase 2：② A2M 思路）。

运行：unset PYTHONPATH && python -m pytest engineering/python/tests/unit/test_device_tools.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 仓库根不在 pytest sys.path（根 conftest 只注入 engineering/python），手动加入
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# mcp_server.tools 模块导入会触发 token 强度校验 测试环境用 dev 模式
os.environ.setdefault("LINGJING_MCP_DEV", "1")
os.environ.setdefault("LINGJING_AGENT_TOKEN", "x" * 40)

import json

import pytest

from mcp_server.device_registry import (
    DeviceDescriptor,
    DeviceDescriptorError,
    DeviceOperation,
    DeviceSignal,
    SimulatedDevice,
    build_cnc_milling_descriptor,
    build_demo_registry,
    build_vibration_sensor_descriptor,
)
from mcp_server.device_tools import (
    build_device_tool_handlers,
    register_device_tools,
)


class TestDescriptorValidation:
    def test_valid_cnc_descriptor(self) -> None:
        build_cnc_milling_descriptor().validate()  # 不抛即通过

    def test_bad_device_id_rejected(self) -> None:
        desc = build_cnc_milling_descriptor()
        desc.device_id = "CNC-Mill-01!"  # 大写 + 特殊字符
        with pytest.raises(DeviceDescriptorError):
            desc.validate()

    def test_duplicate_op_rejected(self) -> None:
        desc = DeviceDescriptor(
            device_id="d1",
            name="dup",
            operations=[
                DeviceOperation("run", "run once"),
                DeviceOperation("run", "run twice"),
            ],
        )
        with pytest.raises(DeviceDescriptorError, match="重复"):
            desc.validate()

    def test_bad_param_type_rejected(self) -> None:
        desc = DeviceDescriptor(
            device_id="d1",
            name="bad",
            operations=[
                DeviceOperation("run", "run", param_schema={"x": {"type": "vector"}}),
            ],
        )
        with pytest.raises(DeviceDescriptorError, match="type 非法"):
            desc.validate()

    def test_min_greater_than_max_rejected(self) -> None:
        desc = DeviceDescriptor(
            device_id="d1",
            name="bad-range",
            operations=[
                DeviceOperation(
                    "run",
                    "run",
                    param_schema={"x": {"type": "number", "min": 10.0, "max": 1.0}},
                ),
            ],
        )
        with pytest.raises(DeviceDescriptorError, match="min>max"):
            desc.validate()


class TestSimulatedDevice:
    def setup_method(self) -> None:
        self.device = SimulatedDevice(build_cnc_milling_descriptor())

    def test_execute_updates_state(self) -> None:
        result = self.device.execute("start_spindle", {"spindle_rpm": 8000})
        assert result["applied"]["spindle_rpm"] == 8000.0
        assert self.device.read_signal("spindle_rpm") == 8000.0

    def test_execute_bounds_fail_closed(self) -> None:
        with pytest.raises(ValueError, match="超过上限"):
            self.device.execute("start_spindle", {"spindle_rpm": 99999})
        with pytest.raises(ValueError, match="低于下限"):
            self.device.execute("set_feed_rate", {"feed_rate": 1.0})
        with pytest.raises(ValueError, match="超过上限"):
            self.device.execute("move_axis", {"x": 0, "y": 0, "z": 600})

    def test_missing_required_param(self) -> None:
        with pytest.raises(ValueError, match="缺少必填参数"):
            self.device.execute("start_spindle", {})

    def test_unknown_op(self) -> None:
        with pytest.raises(ValueError, match="无能力"):
            self.device.execute("fly", {})

    def test_status_snapshot(self) -> None:
        status = self.device.status()
        assert status["device_id"] == "cnc_mill_01"
        assert "spindle_rpm" in status["state"]


class TestToolGeneration:
    def setup_method(self) -> None:
        self.descriptor = build_cnc_milling_descriptor()
        self.handlers = build_device_tool_handlers(self.descriptor)

    def test_handler_names(self) -> None:
        names = set(self.handlers.keys())
        assert {
            "cnc_mill_01_start_spindle",
            "cnc_mill_01_stop_spindle",
            "cnc_mill_01_move_axis",
            "cnc_mill_01_set_feed_rate",
            "cnc_mill_01_read_status",
        } <= names

    @pytest.mark.asyncio
    async def test_op_handler_success(self) -> None:
        out = await self.handlers["cnc_mill_01_start_spindle"](spindle_rpm=8000)
        payload = json.loads(out)
        assert payload["ok"] is True
        assert payload["data"]["state"]["spindle_rpm"] == 8000.0

    @pytest.mark.asyncio
    async def test_op_handler_out_of_range_error_text(self) -> None:
        # 越界参数不抛异常，返回结构化错误文本（MCP 工具惯例）
        out = await self.handlers["cnc_mill_01_start_spindle"](spindle_rpm=99999)
        payload = json.loads(out)
        assert payload["ok"] is False
        assert "超过上限" in payload["error"]

    @pytest.mark.asyncio
    async def test_read_status_handler(self) -> None:
        out = await self.handlers["cnc_mill_01_read_status"]()
        payload = json.loads(out)
        assert payload["ok"] is True
        assert payload["data"]["device_id"] == "cnc_mill_01"


class TestRegisterOnFastMCP:
    def test_register_device_tools_returns_names(self) -> None:
        pytest.importorskip("mcp", reason="mcp 未安装（requirements 未声明），跳过 FastMCP 集成点")
        from mcp.server.fastmcp import FastMCP

        server = FastMCP("test-device-tools")
        names = register_device_tools(server, build_cnc_milling_descriptor())
        assert len(names) == 5  # 4 ops + read_status
        assert "cnc_mill_01_start_spindle" in names
        assert "cnc_mill_01_read_status" in names

    def test_register_all_demo_devices(self) -> None:
        pytest.importorskip("mcp", reason="mcp 未安装（requirements 未声明），跳过 FastMCP 集成点")
        from mcp.server.fastmcp import FastMCP

        server = FastMCP("test-demo")
        for descriptor in build_demo_registry():
            names = register_device_tools(server, descriptor)
            assert names  # 每台设备至少注册 1 个工具
        # 两设备工具名无冲突
        all_names = []
        for descriptor in build_demo_registry():
            all_names.extend(register_device_tools(server, descriptor))
        assert len(all_names) == len(set(all_names))


class TestToolsPyIntegration:
    def test_register_tools_keeps_lnn_and_adds_device(self) -> None:
        import mcp_server.tools as tools

        class FakeServer:
            def __init__(self) -> None:
                self.registered: list[tuple[str, str]] = []

            def tool(self, name: str = "", description: str = ""):
                def deco(fn):
                    self.registered.append((name, description))
                    return fn

                return deco

        fake = FakeServer()
        tools.register_tools(fake)
        names = [n for n, _ in fake.registered]
        # 既有 LNN 工具不受影响
        assert "lnn_list_models" in names
        assert "lnn_wait_for_training" in names
        # 新增设备工具
        assert "cnc_mill_01_start_spindle" in names
        assert "cnc_mill_01_read_status" in names
        assert "vib_sensor_01_read_status" in names
