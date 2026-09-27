"""DNC传输进度回调功能单元测试。

目标：验证DNC传输过程中进度回调机制的正确性和可靠性。
覆盖范围：
- 回调函数触发时机
- 回调参数正确性（已发送字节数、总字节数、进度百分比）
- TCP和串口两种协议的回调行为
- 回调异常处理
- 大数据量传输的回调频率
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from app.integrations.dnc.transfer import (
    ControllerType,
    DNCConfig,
    DNCResult,
    DNCStatus,
    DNCTarget,
    DNCTransfer,
    Protocol,
)


# Fixtures


@pytest.fixture
def tcp_target() -> DNCTarget:
    return DNCTarget(
        host="192.168.1.100",
        port=8193,
        protocol=Protocol.TCP,
        controller_type=ControllerType.FANUC,
    )


@pytest.fixture
def serial_target() -> DNCTarget:
    return DNCTarget(
        host="COM1",
        port=0,
        protocol=Protocol.SERIAL,
        controller_type=ControllerType.FANUC,
        baud_rate=9600,
    )


@pytest.fixture
def small_gcode() -> str:
    return "O1000\nG90 G54\nT1 M6\nG0 X0 Y0\nM30\n"


@pytest.fixture
def large_gcode() -> str:
    lines = ["O1000", "G90 G54"]
    for i in range(200):
        lines.append(f"G0 X{i} Y{i}")
    lines.append("M30")
    return "\n".join(lines)


def _make_tcp_transfer(config: DNCConfig) -> DNCTransfer:
    """创建已mock连接的TCP传输实例"""
    transfer = DNCTransfer(config)
    mock_sock = mock.MagicMock()
    mock_sock.send.side_effect = lambda data: len(data)
    transfer._socket = mock_sock
    return transfer


def _make_serial_transfer(config: DNCConfig) -> DNCTransfer:
    """创建已mock连接的串口传输实例"""
    transfer = DNCTransfer(config)
    mock_serial = mock.MagicMock()
    mock_serial.write.side_effect = lambda data: len(data)
    transfer._serial = mock_serial
    return transfer


# TCP传输回调测试


class TestTCPProgressCallback:
    def test_callback_invoked_tcp(self, tcp_target, small_gcode):
        """TCP传输时回调函数被调用"""
        config = DNCConfig(chunk_size=1024, tcp_send_delay=0.0)
        transfer = _make_tcp_transfer(config)

        callback_calls = []

        def progress_callback(sent, total, pct):
            callback_calls.append((sent, total, pct))

        # 直接调用_send_tcp避免连接逻辑
        data = small_gcode.encode("utf-8")
        bytes_sent = transfer._send_tcp(data, on_progress=progress_callback, total_bytes=len(data))

        assert bytes_sent > 0
        assert len(callback_calls) > 0

    def test_callback_parameters_correctness(self, tcp_target, small_gcode):
        """回调参数的正确性"""
        config = DNCConfig(chunk_size=10, tcp_send_delay=0.0)
        transfer = _make_tcp_transfer(config)

        callback_calls = []

        def progress_callback(sent, total, pct):
            callback_calls.append({"sent": sent, "total": total, "pct": pct})

        data = small_gcode.encode("utf-8")
        transfer._send_tcp(data, on_progress=progress_callback, total_bytes=len(data))

        for call in callback_calls:
            assert isinstance(call["sent"], int)
            assert isinstance(call["total"], int)
            assert isinstance(call["pct"], float)
            assert call["sent"] >= 0
            assert call["total"] > 0
            assert 0.0 <= call["pct"] <= 100.0

        # 进度递增
        if len(callback_calls) > 1:
            for i in range(1, len(callback_calls)):
                assert callback_calls[i]["sent"] >= callback_calls[i - 1]["sent"]
                assert callback_calls[i]["pct"] >= callback_calls[i - 1]["pct"]

    def test_callback_final_progress_100_percent(self, tcp_target, small_gcode):
        """最后一次回调进度为100%"""
        config = DNCConfig(chunk_size=1024, tcp_send_delay=0.0)
        transfer = _make_tcp_transfer(config)

        last_progress = {}

        def progress_callback(sent, total, pct):
            last_progress["sent"] = sent
            last_progress["total"] = total
            last_progress["pct"] = pct

        data = small_gcode.encode("utf-8")
        transfer._send_tcp(data, on_progress=progress_callback, total_bytes=len(data))

        assert last_progress["pct"] == 100.0
        assert last_progress["sent"] == last_progress["total"]

    def test_large_data_multiple_callbacks(self, tcp_target, large_gcode):
        """大数据量传输时多次触发回调"""
        config = DNCConfig(chunk_size=256, tcp_send_delay=0.0)
        transfer = _make_tcp_transfer(config)

        callback_count = 0

        def progress_callback(sent, total, pct):
            nonlocal callback_count
            callback_count += 1

        data = large_gcode.encode("utf-8")
        transfer._send_tcp(data, on_progress=progress_callback, total_bytes=len(data))

        assert callback_count > 1, f"大数据传输应触发多次回调，实际触发{callback_count}次"

    def test_callback_exception_handling(self, tcp_target, small_gcode):
        """回调函数异常不影响传输"""
        config = DNCConfig(chunk_size=1024, tcp_send_delay=0.0)
        transfer = _make_tcp_transfer(config)

        def failing_callback(sent, total, pct):
            raise ValueError("回调函数故意抛出异常")

        data = small_gcode.encode("utf-8")
        bytes_sent = transfer._send_tcp(data, on_progress=failing_callback, total_bytes=len(data))

        assert bytes_sent > 0

    def test_no_callback_still_works(self, tcp_target, small_gcode):
        """不提供回调函数时传输正常"""
        config = DNCConfig(chunk_size=1024, tcp_send_delay=0.0)
        transfer = _make_tcp_transfer(config)

        data = small_gcode.encode("utf-8")
        bytes_sent = transfer._send_tcp(data, on_progress=None, total_bytes=len(data))

        assert bytes_sent > 0


# 串口传输回调测试


class TestSerialProgressCallback:
    def test_callback_invoked_serial(self, serial_target, small_gcode):
        """串口传输时回调函数被调用"""
        config = DNCConfig(chunk_size=1024, serial_send_delay=0.0)
        transfer = _make_serial_transfer(config)

        callback_calls = []

        def progress_callback(sent, total, pct):
            callback_calls.append((sent, total, pct))

        data = small_gcode.encode("utf-8")
        bytes_sent = transfer._send_serial(data, on_progress=progress_callback, total_bytes=len(data))

        assert bytes_sent > 0
        assert len(callback_calls) > 0

    def test_callback_parameters_serial(self, serial_target, small_gcode):
        """串口回调参数正确性"""
        config = DNCConfig(chunk_size=10, serial_send_delay=0.0)
        transfer = _make_serial_transfer(config)

        callback_calls = []

        def progress_callback(sent, total, pct):
            callback_calls.append({"sent": sent, "total": total, "pct": pct})

        data = small_gcode.encode("utf-8")
        transfer._send_serial(data, on_progress=progress_callback, total_bytes=len(data))

        for call in callback_calls:
            assert call["sent"] >= 0
            assert call["total"] > 0
            assert 0.0 <= call["pct"] <= 100.0


# 边界条件测试


class TestCallbackEdgeCases:
    def test_empty_data_callback(self, tcp_target):
        """空数据的回调行为"""
        config = DNCConfig(chunk_size=1024, tcp_send_delay=0.0)
        transfer = _make_tcp_transfer(config)

        callback_calls = []

        def progress_callback(sent, total, pct):
            callback_calls.append((sent, total, pct))

        data = b""
        bytes_sent = transfer._send_tcp(data, on_progress=progress_callback, total_bytes=0)

        assert bytes_sent == 0
        # 空数据不触发回调（while循环不进入）

    def test_callback_with_none_total(self, tcp_target, small_gcode):
        """total_bytes为None时的回调"""
        config = DNCConfig(chunk_size=1024, tcp_send_delay=0.0)
        transfer = _make_tcp_transfer(config)

        data = small_gcode.encode("utf-8")
        callback_calls = []

        def progress_callback(sent, total, pct):
            callback_calls.append((sent, total, pct))

        transfer._send_tcp(data, on_progress=progress_callback, total_bytes=None)

        assert len(callback_calls) > 0
        for _, total, _ in callback_calls:
            assert total == len(data)

    def test_callback_progress_monotonic(self, tcp_target, large_gcode):
        """进度值单调递增"""
        config = DNCConfig(chunk_size=128, tcp_send_delay=0.0)
        transfer = _make_tcp_transfer(config)

        progress_values = []

        def progress_callback(sent, total, pct):
            progress_values.append(pct)

        data = large_gcode.encode("utf-8")
        transfer._send_tcp(data, on_progress=progress_callback, total_bytes=len(data))

        for i in range(1, len(progress_values)):
            assert progress_values[i] >= progress_values[i - 1]


# 集成场景测试


class TestCallbackIntegration:
    def test_progress_ui_update_simulation(self, tcp_target, large_gcode):
        """模拟UI进度条更新场景"""
        config = DNCConfig(chunk_size=256, tcp_send_delay=0.0)
        transfer = _make_tcp_transfer(config)

        ui_updates = []

        def ui_progress_handler(sent, total, pct):
            ui_updates.append(int(pct))

        data = large_gcode.encode("utf-8")
        transfer._send_tcp(data, on_progress=ui_progress_handler, total_bytes=len(data))

        assert len(ui_updates) > 0
        assert ui_updates[-1] == 100

    def test_progress_logging_simulation(self, tcp_target, small_gcode):
        """模拟日志记录场景"""
        config = DNCConfig(chunk_size=1024, tcp_send_delay=0.0)
        transfer = _make_tcp_transfer(config)

        log_entries = []

        def log_progress(sent, total, pct):
            log_entries.append(f"传输进度: {sent}/{total} 字节 ({pct:.1f}%)")

        data = small_gcode.encode("utf-8")
        transfer._send_tcp(data, on_progress=log_progress, total_bytes=len(data))

        assert len(log_entries) > 0
        assert "字节" in log_entries[0]
        assert "%" in log_entries[0]
