"""DNCManager 单元测试。

覆盖 DNC 管理器的高层 API（多机床连接管理、状态查询、NC 发送、断开），
通过 mock 底层 adapter 避免真实网络/OPC UA/MTConnect 依赖。
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.dnc.dnc_manager import DNCManager, ProtocolType


class _FakeStatus:
    def to_dict(self):
        return {"state": "RUNNING", "machine": "m1"}


@pytest.mark.unit
class TestDNCManager:
    def test_initial_state_empty(self):
        mgr = DNCManager()
        assert mgr.connections == {}
        assert mgr.list_machines() == []

    @pytest.mark.asyncio
    async def test_add_machine_invalid_protocol(self):
        mgr = DNCManager()
        ok = await mgr.add_machine("m1", "bogus", "endpoint")  # type: ignore[arg-type]
        assert ok is False
        assert mgr.list_machines() == []

    @pytest.mark.asyncio
    async def test_add_machine_opcua(self):
        mgr = DNCManager()
        with patch("app.dnc.dnc_manager.UnifiedDNCAdapter") as MockAdapter:
            mock_adapter = MockAdapter.return_value
            mock_adapter.connect_single = AsyncMock(return_value=True)
            mock_adapter.primary = MagicMock(client=MagicMock())

            ok = await mgr.add_machine(
                "m1", ProtocolType.OPC_UA, "opc.tcp://localhost:4840",
                username="u", password="p",
            )

            assert ok is True
            assert MockAdapter.call_args.kwargs == {"machine_id": "m1"}
            mock_adapter.connect_single.assert_awaited_once()

        assert "m1" in mgr.connections
        assert mgr.connections["m1"]["protocol"] == ProtocolType.OPC_UA
        machines = mgr.list_machines()
        assert len(machines) == 1
        assert machines[0]["machine_id"] == "m1"
        assert machines[0]["protocol"] == "opcua"

    @pytest.mark.asyncio
    async def test_add_machine_mtconnect(self):
        mgr = DNCManager()
        with patch("app.dnc.dnc_manager.UnifiedDNCAdapter") as MockAdapter, \
             patch("app.dnc.unified_adapter.MTConnectAdapter") as MockMT:
            mock_adapter = MockAdapter.return_value
            mock_mt = MockMT.return_value
            mock_mt.connect = AsyncMock(return_value=True)

            ok = await mgr.add_machine(
                "m2", ProtocolType.MTCONNECT, "http://agent:5000",
                device_name="Dev-1",
            )

            assert ok is True
            mock_mt.connect.assert_awaited_once()

        assert "m2" in mgr.connections
        assert mgr.connections["m2"]["protocol"] == ProtocolType.MTCONNECT

    @pytest.mark.asyncio
    async def test_get_status_unknown_machine(self):
        mgr = DNCManager()
        status = await mgr.get_machine_status("nope")
        assert "error" in status
        assert "未连接" in status["error"]

    @pytest.mark.asyncio
    async def test_get_status_known_machine(self):
        mgr = DNCManager()
        with patch("app.dnc.dnc_manager.UnifiedDNCAdapter") as MockAdapter:
            mock_adapter = MockAdapter.return_value
            mock_adapter.connect_single = AsyncMock(return_value=True)
            mock_adapter.primary = MagicMock(client=MagicMock())
            mock_adapter.get_status = AsyncMock(return_value=_FakeStatus())
            await mgr.add_machine("m1", ProtocolType.OPC_UA, "opc.tcp://x")

        status = await mgr.get_machine_status("m1")
        assert status == {"state": "RUNNING", "machine": "m1"}

    @pytest.mark.asyncio
    async def test_send_nc_program_unknown_machine(self):
        mgr = DNCManager()
        ok = await mgr.send_nc_program("nope", "/tmp/x.nc", "x")
        assert ok is False

    @pytest.mark.asyncio
    async def test_remove_machine(self):
        mgr = DNCManager()
        with patch("app.dnc.dnc_manager.UnifiedDNCAdapter") as MockAdapter:
            mock_adapter = MockAdapter.return_value
            mock_adapter.connect_single = AsyncMock(return_value=True)
            mock_adapter.primary = MagicMock(client=MagicMock())
            mock_adapter.disconnect = AsyncMock()
            await mgr.add_machine("m1", ProtocolType.OPC_UA, "opc.tcp://x")

        await mgr.remove_machine("m1")
        assert mgr.connections == {}
        assert mgr.list_machines() == []

    @pytest.mark.asyncio
    async def test_disconnect_all(self):
        mgr = DNCManager()
        with patch("app.dnc.dnc_manager.UnifiedDNCAdapter") as MockAdapter:
            for mid in ("m1", "m2"):
                mock_adapter = MockAdapter.return_value
                mock_adapter.connect_single = AsyncMock(return_value=True)
                mock_adapter.primary = MagicMock(client=MagicMock())
                mock_adapter.disconnect = AsyncMock()
                await mgr.add_machine(mid, ProtocolType.OPC_UA, "opc.tcp://x")

        await mgr.disconnect_all()
        assert mgr.connections == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
