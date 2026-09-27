"""本地 MTConnect 模拟 Agent 单元测试。

验证：
- /probe 响应可被 ``MTConnectAdapter.probe`` 解析出设备标识
- /current 与 /sample 响应可被 ``app.integrations.mtconnect.parser`` 解析
- /current 响应可被 ``app.dnc.mtconnect_client.MTConnectClient`` 解析
- 模拟状态随 tick 有规律变化（数据流动）
- 服务启停幂等、端口可复用

设计说明：
- 通过 urllib 直接请求模拟 Agent 的 HTTP 端点（不 mock 网络），
  确保是真实协议级验证。
- 解析器断言对齐各自契约字段。
"""

from __future__ import annotations

import asyncio
import socket
import time
import urllib.request
from xml.etree import ElementTree as ET

import pytest

from app.dnc.mock_agent import (
    DEFAULT_DEVICE_NAME,
    DEFAULT_PORT,
    MachineSimulator,
    MockMTConnectAgent,
    build_devices_xml,
    build_streams_xml,
)
from app.dnc.mtconnect_client import MTConnectClient
from app.integrations.mtconnect.adapter import AdapterConfig, MTConnectAdapter
from app.integrations.mtconnect.parser import parse_sample_response
from app.pipelines.machining_collector import CollectorConfig, MachiningCollector


def _free_port() -> int:
    """探测一个空闲端口，避免 CI 环境端口冲突。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def agent():
    """启动一个真实 HTTP 模拟 Agent。"""
    port = _free_port()
    a = MockMTConnectAgent(port=port, device_name="TEST-CNC-001")
    a.start()
    yield a
    a.stop()


# XML 构造纯函数


class TestXMLBuilders:
    def test_build_devices_xml_is_wellformed_and_has_header(self) -> None:
        xml_text = build_devices_xml("VM-1", "uuid-1")
        root = ET.fromstring(xml_text)
        # 命名空间剥离后应为 MTConnectDevices
        assert root.tag.split("}")[-1] == "MTConnectDevices"
        # 找到 Header 的 sender / mtconnectVersion
        attrs: dict[str, str] = {}
        for elem in root.iter():
            if elem.tag.split("}")[-1] == "Header":
                attrs = dict(elem.attrib)
                break
        assert attrs.get("sender") == "mock-agent"
        assert attrs.get("mtconnectVersion") == "1.5"
        # 设备名存在
        assert any(elem.tag.split("}")[-1] == "Device" and elem.get("name") == "VM-1" for elem in root.iter())

    def test_build_streams_xml_has_required_data_items(self) -> None:
        sample = MachineSimulator(device_name="VM-1").next_sample()
        xml_text = build_streams_xml(sample)
        root = ET.fromstring(xml_text)
        tags = {elem.tag.split("}")[-1] for elem in root.iter()}
        # 两套解析器依赖的数据项
        for required in ("SpindleSpeed", "SpindleLoad", "Feedrate", "Execution", "PathFeedrate", "Xabs"):
            assert required in tags, f"缺少数据项 {required}"
        # SpindleSpeed 数值可解析
        parsed = parse_sample_response(xml_text)
        assert parsed.spindle_speed == sample["spindle_speed"]


# MachineSimulator 状态机


class TestMachineSimulator:
    def test_next_sample_values_change_over_ticks(self) -> None:
        sim = MachineSimulator()
        first = sim.next_sample()
        second = sim.next_sample()
        # tick / sequence 单调递增
        assert second["tick"] == first["tick"] + 1
        assert second["sequence"] == first["sequence"] + 1
        # 数值应随时间变化（主轴转速波动）
        assert second["spindle_speed"] != first["spindle_speed"]
        # 执行状态有 ACTIVE / IDLE 切换
        executions = {sim.next_sample()["execution"] for _ in range(10)}
        assert "ACTIVE" in executions
        assert "IDLE" in executions

    def test_overload_cycle_triggers_spindle_overload(self) -> None:
        sim = MachineSimulator(overload_cycle=5)
        loads = [sim.next_sample()["spindle_load"] for _ in range(10)]
        assert max(loads) > 80.0  # 至少一帧超过过载阈值


# 真实 HTTP 端点 + 两套解析器集成


class TestHTTPAgentEndpoints:
    def test_probe_endpoint_returns_devices(self, agent) -> None:
        with urllib.request.urlopen(f"{agent.url}/probe", timeout=5) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
        root = ET.fromstring(body)
        assert root.tag.split("}")[-1] == "MTConnectDevices"

    def test_current_endpoint_parses_via_integration_parser(self, agent) -> None:
        with urllib.request.urlopen(f"{agent.url}/current", timeout=5) as resp:
            body = resp.read().decode("utf-8")
        sample = parse_sample_response(body)
        assert sample.spindle_speed is not None
        assert sample.spindle_load is not None
        assert sample.feedrate is not None
        assert sample.execution in ("ACTIVE", "IDLE")
        # 额外字段（ControllerMode）应被捕获到 extras
        assert sample.extras.get("controller_mode") == "AUTOMATIC"

    def test_sample_endpoint_parses_via_integration_parser(self, agent) -> None:
        with urllib.request.urlopen(f"{agent.url}/sample", timeout=5) as resp:
            body = resp.read().decode("utf-8")
        sample = parse_sample_response(body)
        assert sample.spindle_speed is not None

    def test_unknown_endpoint_returns_404(self, agent) -> None:
        import urllib.error

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{agent.url}/nope", timeout=5)
        assert exc_info.value.code == 404


# 与 DNC MTConnectClient 的真实协议级连接


class TestDNCClientIntegration:
    @pytest.mark.asyncio
    async def test_connect_and_get_status(self, agent) -> None:
        client = MTConnectClient(agent_url=agent.url, device_name="TEST-CNC-001")
        ok = await client.connect()
        assert ok is True
        try:
            status = await client.get_current_status()
            assert status["connected"] is True
            assert status["spindle_speed"] is not None
            assert status["feed_rate"] is not None
            assert status["x_position"] is not None
            assert status["execution_mode"] in ("ACTIVE", "IDLE")
            assert status["controller_mode"] == "AUTOMATIC"
        finally:
            await client.disconnect()

    @pytest.mark.asyncio
    async def test_health_check(self, agent) -> None:
        client = MTConnectClient(agent_url=agent.url, device_name="TEST-CNC-001")
        await client.connect()
        try:
            assert await client.health_check() is True
        finally:
            await client.disconnect()


# 与 MTConnectAdapter 采集管道的真实协议级连接


class TestAdapterIntegration:
    def test_adapter_probe_and_fetch(self, agent) -> None:
        cfg = AdapterConfig(agent_url=agent.url, interval=0.05, batch_size=5, batch_interval=0.2)
        adapter = MTConnectAdapter(config=cfg)
        try:
            probe = adapter.probe()
            assert probe["sender"] == "mock-agent"
            assert probe["mtconnect_version"] == "1.5"
            sample = adapter.fetch_sample()
            assert sample.spindle_speed is not None
            assert sample.execution in ("ACTIVE", "IDLE")
        finally:
            adapter.close()

    def test_adapter_run_collects_multiple_samples(self, agent) -> None:
        cfg = AdapterConfig(agent_url=agent.url, interval=0.02, batch_size=100, batch_interval=0.0)
        adapter = MTConnectAdapter(config=cfg)
        collected: list[str] = []

        def _on_sample(sample) -> None:
            collected.append(sample.execution or "")

        try:
            count = adapter.run(duration=0.25, on_sample=_on_sample)
            assert count >= 3  # 250ms / 20ms ≈ 12 帧，至少 3 帧
            assert collected, "on_sample 未收到任何样本"
        finally:
            adapter.close()


# 端到端：MachiningCollector 整链路（拉取 聚合 双存储 sink）


class TestMachiningCollectorPipeline:
    @pytest.mark.asyncio
    async def test_collector_end_to_end(self, agent) -> None:
        """模拟 Agent → MTConnectAdapter → 聚合 → PostgreSQL/TDengine sink。

        注入内存 sink 替代真实数据库，验证采集循环在真实 HTTP 协议下
        可持续拉取、聚合、并正确写出关系型记录与时序样本。
        """
        written_records: list = []
        written_samples: list = []

        async def record_sink(records):
            written_records.extend(records)
            return len(records)

        async def tdengine_sink_fn(samples):
            written_samples.extend(samples)
            return len(samples)

        cfg = CollectorConfig(
            agent_url=agent.url,
            machine_id="CNC-01",
            tool_id="T-EM-10",
            material="45号钢",
            sample_interval=0.02,
            batch_size=5,
            flush_interval=0.2,
        )
        collector = MachiningCollector(
            config=cfg,
            record_sink=record_sink,
            tdengine_sink_fn=tdengine_sink_fn,
        )
        job_id = await collector.start()
        try:
            await asyncio.sleep(0.6)  # 让采集循环拉取多帧并触发 flush
            stats = await collector.stop()
        finally:
            await collector.stop()

        assert job_id
        assert stats["samples_consumed"] >= 5, f"samples_consumed={stats['samples_consumed']}"
        assert stats["records_written"] >= 1, f"records_written={stats['records_written']}"
        assert stats["tdengine_rows_written"] >= 1, f"tdengine_rows_written={stats['tdengine_rows_written']}"
        assert written_records, "PostgreSQL sink 未收到记录"
        assert written_samples, "TDengine sink 未收到样本"
        # 关系型记录包含静态上下文字段
        first = written_records[0]
        assert first.machine_id == "CNC-01"
        assert first.tool_id == "T-EM-10"
        assert first.material == "45号钢"


# 生命周期 / 端口复用


class TestLifecycle:
    def test_start_stop_idempotent(self) -> None:
        port = _free_port()
        a = MockMTConnectAgent(port=port)
        a.start()
        a.start()  # 重复 start 幂等
        assert a.is_running is True
        a.stop()
        a.stop()  # 重复 stop 幂等
        assert a.is_running is False

    def test_port_reusable_after_stop(self) -> None:
        port = _free_port()
        a1 = MockMTConnectAgent(port=port)
        a1.start()
        a1.stop()
        # 端口应立即释放，可重新绑定
        a2 = MockMTConnectAgent(port=port)
        a2.start()
        try:
            assert a2.is_running is True
        finally:
            a2.stop()

    def test_context_manager(self) -> None:
        port = _free_port()
        with MockMTConnectAgent(port=port) as a:
            assert a.is_running is True
        assert a.is_running is False
