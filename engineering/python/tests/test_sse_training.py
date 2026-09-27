"""
Unit tests for SSE training status push system.

Tests cover:
- SSE connection management
- Event broadcasting
- Training progress callback
- Cancel signal handling
- Multi-client support
"""

import asyncio
import json
import pytest

from app.api.v1.sse import (
    SSEClient,
    SSEConnectionManager,
    TrainingProgressCallback,
    create_progress_callback,
)


@pytest.fixture
def manager():
    return SSEConnectionManager(timeout_seconds=60)


@pytest.fixture
def sample_task_id():
    return "test-task-001"


@pytest.fixture
def sample_client_id():
    return "client-test-001"


class TestSSEClient:
    def test_client_creation(self):
        queue = asyncio.Queue()
        client = SSEClient(
            queue=queue,
            connected_at=1000.0,
            last_activity=1000.0,
            client_id="test-client",
        )
        assert client.client_id == "test-client"
        assert client.connected_at == 1000.0
        assert client.last_activity == 1000.0


class TestSSEConnectionManager:
    @pytest.mark.asyncio
    async def test_subscribe(self, manager, sample_task_id, sample_client_id):
        client = await manager.subscribe(sample_task_id, sample_client_id)
        assert client.client_id == sample_client_id
        assert manager.get_active_clients_count(sample_task_id) == 1

    @pytest.mark.asyncio
    async def test_unsubscribe(self, manager, sample_task_id, sample_client_id):
        await manager.subscribe(sample_task_id, sample_client_id)
        await manager.unsubscribe(sample_task_id, sample_client_id)
        assert manager.get_active_clients_count(sample_task_id) == 0

    @pytest.mark.asyncio
    async def test_broadcast_single_client(
        self, manager, sample_task_id, sample_client_id
    ):
        await manager.subscribe(sample_task_id, sample_client_id)
        await manager.broadcast(sample_task_id, "progress", {"epoch": 1, "loss": 0.5})

        client = manager._clients[sample_task_id][sample_client_id]
        event = await asyncio.wait_for(client.queue.get(), timeout=1.0)
        assert "event: progress" in event
        assert '"epoch": 1' in event
        assert '"loss": 0.5' in event

    @pytest.mark.asyncio
    async def test_broadcast_multiple_clients(self, manager, sample_task_id):
        await manager.subscribe(sample_task_id, "client-1")
        await manager.subscribe(sample_task_id, "client-2")
        await manager.subscribe(sample_task_id, "client-3")

        await manager.broadcast(sample_task_id, "progress", {"epoch": 5, "loss": 0.3})

        for cid in ["client-1", "client-2", "client-3"]:
            client = manager._clients[sample_task_id][cid]
            event = await asyncio.wait_for(client.queue.get(), timeout=1.0)
            assert "event: progress" in event

    @pytest.mark.asyncio
    async def test_broadcast_to_nonexistent_task(self, manager):
        await manager.broadcast("nonexistent-task", "progress", {})
        assert manager.get_total_clients_count() == 0

    @pytest.mark.asyncio
    async def test_send_to_specific_client(
        self, manager, sample_task_id, sample_client_id
    ):
        await manager.subscribe(sample_task_id, sample_client_id)
        await manager.subscribe(sample_task_id, "client-other")

        await manager.send_to_client(
            sample_task_id, sample_client_id, "complete", {"status": "done"}
        )

        target_client = manager._clients[sample_task_id][sample_client_id]
        event = await asyncio.wait_for(target_client.queue.get(), timeout=1.0)
        assert "event: complete" in event

        other_client = manager._clients[sample_task_id]["client-other"]
        assert other_client.queue.empty()

    @pytest.mark.asyncio
    async def test_cancel_signal(self, manager, sample_task_id, sample_client_id):
        await manager.subscribe(sample_task_id, sample_client_id)
        cancel_event = manager.get_cancel_event(sample_task_id)
        assert cancel_event is not None
        assert not cancel_event.is_set()

        await manager.signal_cancel(sample_task_id)
        assert cancel_event.is_set()

    @pytest.mark.asyncio
    async def test_cancel_event_for_nonexistent_task(self, manager):
        assert manager.get_cancel_event("nonexistent") is None

    @pytest.mark.asyncio
    async def test_cleanup_timeout_clients(self, manager, sample_task_id):
        short_timeout_manager = SSEConnectionManager(timeout_seconds=1)
        await short_timeout_manager.subscribe(sample_task_id, "client-1")

        import time

        client = short_timeout_manager._clients[sample_task_id]["client-1"]
        client.last_activity = time.time() - 2

        await short_timeout_manager.cleanup_timeout_clients()
        assert short_timeout_manager.get_active_clients_count(sample_task_id) == 0

    @pytest.mark.asyncio
    async def test_multi_task_isolation(self, manager):
        await manager.subscribe("task-1", "client-a")
        await manager.subscribe("task-2", "client-b")

        await manager.broadcast("task-1", "progress", {"epoch": 1})

        client_a = manager._clients["task-1"]["client-a"]
        client_b = manager._clients["task-2"]["client-b"]

        event = await asyncio.wait_for(client_a.queue.get(), timeout=1.0)
        assert "event: progress" in event
        assert client_b.queue.empty()

    @pytest.mark.asyncio
    async def test_total_clients_count(self, manager):
        await manager.subscribe("task-1", "c1")
        await manager.subscribe("task-1", "c2")
        await manager.subscribe("task-2", "c3")
        assert manager.get_total_clients_count() == 3

    def test_format_event(self, manager):
        event = manager._format_event("progress", {"epoch": 10, "loss": 0.1})
        assert event.startswith("event: progress\n")
        assert "data: " in event
        parsed = json.loads(event.split("data: ")[1].split("\n")[0])
        assert parsed["epoch"] == 10
        assert parsed["loss"] == 0.1


class TestTrainingProgressCallback:
    @pytest.mark.asyncio
    async def test_callback_sends_progress(self, sample_task_id):
        manager = SSEConnectionManager()
        await manager.subscribe(sample_task_id, "test-client")

        callback = TrainingProgressCallback(manager, sample_task_id, total_epochs=100)
        callback(epoch=10, loss=0.5, metrics={"accuracy": 0.85})

        await asyncio.sleep(0.1)
        client = manager._clients[sample_task_id]["test-client"]
        event = await asyncio.wait_for(client.queue.get(), timeout=1.0)

        assert "event: progress" in event
        assert '"epoch": 10' in event
        assert '"loss": 0.5' in event
        assert '"progress": 10.0' in event

    @pytest.mark.asyncio
    async def test_callback_complete_event(self, sample_task_id):
        manager = SSEConnectionManager()
        await manager.subscribe(sample_task_id, "test-client")

        callback = TrainingProgressCallback(manager, sample_task_id, total_epochs=100)
        await callback.send_complete("completed", 0.1, training_time=3600)

        client = manager._clients[sample_task_id]["test-client"]
        event = await asyncio.wait_for(client.queue.get(), timeout=1.0)

        assert "event: complete" in event
        assert '"status": "completed"' in event
        assert '"final_loss": 0.1' in event
        assert '"training_time": 3600' in event

    @pytest.mark.asyncio
    async def test_callback_error_event(self, sample_task_id):
        manager = SSEConnectionManager()
        await manager.subscribe(sample_task_id, "test-client")

        callback = TrainingProgressCallback(manager, sample_task_id, total_epochs=100)
        await callback.send_error("TRAINING_ERROR", "Out of memory", {"gpu": "0"})

        client = manager._clients[sample_task_id]["test-client"]
        event = await asyncio.wait_for(client.queue.get(), timeout=1.0)

        assert "event: error" in event
        assert '"code": "TRAINING_ERROR"' in event
        assert '"message": "Out of memory"' in event

    @pytest.mark.asyncio
    async def test_callback_progress_calculation(self, sample_task_id):
        manager = SSEConnectionManager()
        await manager.subscribe(sample_task_id, "test-client")

        callback = TrainingProgressCallback(manager, sample_task_id, total_epochs=100)
        callback(epoch=25, loss=0.4, metrics={})

        await asyncio.sleep(0.1)
        client = manager._clients[sample_task_id]["test-client"]
        event = await asyncio.wait_for(client.queue.get(), timeout=1.0)
        assert '"progress": 25.0' in event

    def test_create_progress_callback_factory(self, sample_task_id):
        callback = create_progress_callback(sample_task_id, total_epochs=50)
        assert isinstance(callback, TrainingProgressCallback)
        assert callback._task_id == sample_task_id
        assert callback._total_epochs == 50


class TestSSEEventFormats:
    def test_progress_event_format(self):
        manager = SSEConnectionManager()
        data = {
            "epoch": 5,
            "total_epochs": 100,
            "loss": 0.1234,
            "progress": 5.0,
            "metrics": {"accuracy": 0.9},
        }
        event = manager._format_event("progress", data)
        parsed = json.loads(event.split("data: ")[1].split("\n")[0])
        assert parsed["epoch"] == 5
        assert parsed["total_epochs"] == 100
        assert parsed["loss"] == 0.1234
        assert isinstance(parsed["metrics"], dict)

    def test_complete_event_format(self):
        manager = SSEConnectionManager()
        data = {
            "status": "completed",
            "final_loss": 0.05,
            "training_time": 7200,
        }
        event = manager._format_event("complete", data)
        parsed = json.loads(event.split("data: ")[1].split("\n")[0])
        assert parsed["status"] == "completed"
        assert isinstance(parsed["training_time"], int)

    def test_error_event_format(self):
        manager = SSEConnectionManager()
        data = {
            "code": "CANCELLED",
            "message": "Training cancelled",
            "details": {"reason": "user_request"},
        }
        event = manager._format_event("error", data)
        parsed = json.loads(event.split("data: ")[1].split("\n")[0])
        assert parsed["code"] == "CANCELLED"
        assert isinstance(parsed["details"], dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
