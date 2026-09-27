"""MTConnectAdapter flush 失败回灌行为测试。

契约：持久化失败时样本不丢弃，回灌缓冲区头部等待重试；缓冲有界
（``AdapterConfig.max_buffer``），溢出丢弃最旧并计入 ``dropped_samples``。
"""

import pytest

from app.integrations.mtconnect.adapter import AdapterConfig, MTConnectAdapter
from app.integrations.mtconnect.parser import Sample

AGENT_URL = "http://demo.mtconnect.org:80"


class _FailingTDE:
    """模拟 TDengine 不可用：insert_rows 直接抛连接错误。"""

    def __init__(self) -> None:
        self.calls = 0

    async def insert_rows(self, **kwargs) -> int:
        self.calls += 1
        raise ConnectionError("tdengine down")


class _CountingTDE:
    """记录收到的行数，返回成功写入计数。"""

    def __init__(self) -> None:
        self.rows: list = []

    async def insert_rows(self, *, table_name: str, rows: list, **kwargs) -> int:
        self.rows.extend(rows)
        return len(rows)


def _sample(speed: float) -> Sample:
    return Sample(spindle_speed=speed, spindle_load=0.0, feedrate=0.0, execution="IDLE")


def _adapter(tdengine=None, max_buffer: int = 1000) -> MTConnectAdapter:
    return MTConnectAdapter(
        config=AdapterConfig(agent_url=AGENT_URL, max_buffer=max_buffer),
        tdengine_client=tdengine,
    )


class TestFlushRequeue:
    def test_failed_persist_keeps_samples_in_buffer(self):
        adapter = _adapter(_FailingTDE())
        adapter._enqueue(_sample(1.0))
        adapter._enqueue(_sample(2.0))

        assert adapter.flush() == 0
        assert adapter.buffer_size == 2, "持久化失败后样本不应被丢弃"

    def test_requeue_preserves_chronological_order(self):
        adapter = _adapter(_FailingTDE())
        first, second = _sample(1.0), _sample(2.0)

        adapter._enqueue(first)
        adapter.flush()
        adapter._enqueue(second)
        adapter.flush()

        assert list(adapter._buffer) == [first, second]

    def test_buffer_overflow_drops_oldest_and_counts(self):
        adapter = _adapter(_FailingTDE(), max_buffer=3)
        for speed in range(5):
            adapter._enqueue(_sample(float(speed)))
            adapter.flush()

        assert adapter.buffer_size == 3
        assert adapter.dropped_samples == 2
        # 保留的是最新的 3 个样本
        assert [s.spindle_speed for s in adapter._buffer] == [2.0, 3.0, 4.0]

    def test_recovers_after_backend_reconnect(self):
        failing = _FailingTDE()
        adapter = _adapter(failing)
        adapter._enqueue(_sample(1.0))
        assert adapter.flush() == 0

        healthy = _CountingTDE()
        adapter._tdengine = healthy
        assert adapter.flush() == 1
        assert adapter.buffer_size == 0
        assert len(healthy.rows) == 1
        assert failing.calls == 1

    def test_dry_run_mode_still_counts_as_ingested(self):
        adapter = _adapter(tdengine=None)
        adapter._enqueue(_sample(1.0))
        assert adapter.flush() == 1
        assert adapter.buffer_size == 0
        assert adapter.dropped_samples == 0

    def test_future_timeout_requeues_batch(self, monkeypatch):
        """事件循环线程内直接调 flush 的自锁场景：future 超时后样本仍在缓冲区。"""
        import asyncio
        import threading

        from app.integrations.mtconnect import adapter as adapter_module

        monkeypatch.setattr(adapter_module, "DEFAULT_MTCONNECT_FUTURE_TIMEOUT_SEC", 0.2)

        class _HangingTDE:
            async def insert_rows(self, **kwargs) -> int:
                await asyncio.sleep(3600)
                return 0

        adapter = _adapter(_HangingTDE())
        adapter._enqueue(_sample(1.0))

        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        try:

            async def _flush_on_loop():
                return adapter.flush()

            future = asyncio.run_coroutine_threadsafe(_flush_on_loop(), loop)
            written = future.result(timeout=5.0)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=2)
            loop.close()

        assert written == 0
        assert adapter.buffer_size == 1


@pytest.mark.parametrize("max_buffer", [0, -1])
def test_invalid_max_buffer_rejected(max_buffer):
    """max_buffer 非法时在配置构造期直接拒绝，而不是运行时悄悄丢数据。"""
    with pytest.raises(ValueError, match="max_buffer"):
        AdapterConfig(agent_url=AGENT_URL, max_buffer=max_buffer)
