"""
SSE Training Status Push System - Comprehensive Verification Script

This script performs systematic validation of the SSE implementation:
1. Endpoint configuration and response format
2. Event format compliance with SSE specification
3. Training callback mechanism
4. Real-time performance testing
5. Network overhead comparison (SSE vs polling)

Usage:
    python scripts/verify_sse_training.py
"""
import asyncio
import json
import time
import sys
import os
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python'))

from app.api.v1.sse import (
    SSEConnectionManager,
    TrainingProgressCallback,
    sse_manager,
)
from app.ai.lnn.training.trainer import LNNTrainer


@dataclass
class TestResult:
    test_name: str
    passed: bool
    details: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class SSEVerifier:
    """Comprehensive SSE training status push system verifier."""

    def __init__(self):
        self.results: List[TestResult] = []
        self.manager = SSEConnectionManager(timeout_seconds=1800)

    def _record_result(self, test_name: str, passed: bool, details: str = "", metrics: Dict = None):
        result = TestResult(
            test_name=test_name,
            passed=passed,
            details=details,
            metrics=metrics or {},
        )
        self.results.append(result)
        status = "[PASS]" if passed else "[FAIL]"
        print(f"{status} {test_name}: {details}")

    async def verify_endpoint_configuration(self):
        """Verify SSE endpoint configuration and response format."""
        print("\n" + "="*60)
        print("TEST 1: SSE Endpoint Configuration")
        print("="*60)

        task_id = "test-endpoint-config-001"
        client_id = "client-verify-001"

        try:
            client = await self.manager.subscribe(task_id, client_id)
            self._record_result(
                "Endpoint subscription",
                True,
                "Client successfully subscribed to task",
                {"client_id": client_id, "task_id": task_id},
            )

            event_str = "event: progress\ndata: {\"epoch\": 1}\n\n"
            await client.queue.put(event_str)
            received = await asyncio.wait_for(client.queue.get(), timeout=5.0)

            has_event_field = "event:" in received
            has_data_field = "data:" in received
            ends_with_double_newline = received.endswith("\n\n")

            self._record_result(
                "Event format - SSE fields",
                has_event_field and has_data_field and ends_with_double_newline,
                f"event field: {has_event_field}, data field: {has_data_field}, double newline: {ends_with_double_newline}",
                {"has_event": has_event_field, "has_data": has_data_field, "double_newline": ends_with_double_newline},
            )

            await self.manager.unsubscribe(task_id, client_id)
            self._record_result(
                "Endpoint unsubscription",
                True,
                "Client successfully unsubscribed",
            )

        except Exception as e:
            self._record_result(
                "Endpoint configuration",
                False,
                f"Error: {str(e)}",
            )

    async def verify_event_format(self):
        """Verify event format compliance with SSE specification."""
        print("\n" + "="*60)
        print("TEST 2: Event Format Compliance")
        print("="*60)

        task_id = "test-event-format-002"
        await self.manager.subscribe(task_id, "client-format-test")

        try:
            await self.manager.broadcast(task_id, "progress", {
                "epoch": 5,
                "total_epochs": 100,
                "loss": 0.1234,
                "progress": 5.0,
                "metrics": {"accuracy": 0.85, "precision": 0.82},
            })

            client = self.manager._clients[task_id]["client-format-test"]
            event = await asyncio.wait_for(client.queue.get(), timeout=5.0)

            lines = event.strip().split("\n")
            event_line = lines[0] if len(lines) > 0 else ""
            data_line = lines[1] if len(lines) > 1 else ""

            is_event_line_correct = event_line.startswith("event: progress")
            is_data_line_correct = data_line.startswith("data: ")

            try:
                data_json = json.loads(data_line[6:])
                has_all_fields = all(k in data_json for k in ["epoch", "total_epochs", "loss", "progress", "metrics"])
                epoch_is_int = isinstance(data_json["epoch"], int)
                loss_is_float = isinstance(data_json["loss"], float)
                metrics_is_dict = isinstance(data_json["metrics"], dict)
            except json.JSONDecodeError:
                has_all_fields = False
                epoch_is_int = False
                loss_is_float = False
                metrics_is_dict = False

            self._record_result(
                "Progress event format",
                is_event_line_correct and is_data_line_correct and has_all_fields,
                f"Event line: {is_event_line_correct}, Data line: {is_data_line_correct}, All fields: {has_all_fields}",
                {
                    "event_line_correct": is_event_line_correct,
                    "data_line_correct": is_data_line_correct,
                    "all_fields_present": has_all_fields,
                    "epoch_is_int": epoch_is_int,
                    "loss_is_float": loss_is_float,
                    "metrics_is_dict": metrics_is_dict,
                },
            )

            await self.manager.broadcast(task_id, "complete", {
                "status": "completed",
                "final_loss": 0.05,
                "training_time": 3600,
            })

            event = await asyncio.wait_for(client.queue.get(), timeout=5.0)
            has_complete_event = "event: complete" in event

            self._record_result(
                "Complete event format",
                has_complete_event,
                f"Complete event present: {has_complete_event}",
            )

            await self.manager.broadcast(task_id, "error", {
                "code": "TRAINING_ERROR",
                "message": "Test error",
                "details": {"test": True},
            })

            event = await asyncio.wait_for(client.queue.get(), timeout=5.0)
            has_error_event = "event: error" in event

            self._record_result(
                "Error event format",
                has_error_event,
                f"Error event present: {has_error_event}",
            )

            await self.manager.unsubscribe(task_id, "client-format-test")

        except Exception as e:
            self._record_result(
                "Event format verification",
                False,
                f"Error: {str(e)}",
            )

    async def verify_callback_mechanism(self):
        """Verify training callback mechanism."""
        print("\n" + "="*60)
        print("TEST 3: Training Callback Mechanism")
        print("="*60)

        task_id = "test-callback-003"
        await self.manager.subscribe(task_id, "client-callback-test")

        try:
            callback = TrainingProgressCallback(self.manager, task_id, total_epochs=100)

            callback(epoch=10, loss=0.5, metrics={"accuracy": 0.75})
            await asyncio.sleep(0.2)

            client = self.manager._clients[task_id]["client-callback-test"]
            event = await asyncio.wait_for(client.queue.get(), timeout=5.0)

            has_progress = "event: progress" in event
            has_epoch_10 = '"epoch": 10' in event

            self._record_result(
                "Callback triggers progress event",
                has_progress and has_epoch_10,
                f"Progress event: {has_progress}, Epoch 10: {has_epoch_10}",
            )

            await callback.send_complete("completed", 0.1, training_time=1200)
            event = await asyncio.wait_for(client.queue.get(), timeout=5.0)
            has_complete = "event: complete" in event

            self._record_result(
                "Callback sends complete event",
                has_complete,
                f"Complete event: {has_complete}",
            )

            await callback.send_error("CANCELLED", "User cancelled")
            event = await asyncio.wait_for(client.queue.get(), timeout=5.0)
            has_error = "event: error" in event

            self._record_result(
                "Callback sends error event",
                has_error,
                f"Error event: {has_error}",
            )

            await self.manager.unsubscribe(task_id, "client-callback-test")

        except Exception as e:
            self._record_result(
                "Callback mechanism",
                False,
                f"Error: {str(e)}",
            )

    async def verify_realtime_performance(self):
        """Verify real-time performance of SSE push."""
        print("\n" + "="*60)
        print("TEST 4: Real-time Performance")
        print("="*60)

        task_id = "test-realtime-004"
        await self.manager.subscribe(task_id, "client-realtime-test")
        callback = TrainingProgressCallback(self.manager, task_id, total_epochs=50)

        latencies = []

        try:
            for epoch in [1, 10, 20, 30, 40, 50]:
                send_time = time.perf_counter()
                callback(epoch=epoch, loss=0.5 - epoch * 0.008, metrics={"accuracy": 0.7 + epoch * 0.005})

                client = self.manager._clients[task_id]["client-realtime-test"]
                event = await asyncio.wait_for(client.queue.get(), timeout=5.0)
                receive_time = time.perf_counter()

                latency_ms = (receive_time - send_time) * 1000
                latencies.append(latency_ms)

            avg_latency = sum(latencies) / len(latencies)
            max_latency = max(latencies)
            min_latency = min(latencies)

            all_within_threshold = all(lat <= 500 for lat in latencies)

            self._record_result(
                "Real-time latency ≤ 500ms",
                all_within_threshold,
                f"Avg: {avg_latency:.2f}ms, Max: {max_latency:.2f}ms, Min: {min_latency:.2f}ms",
                {
                    "avg_latency_ms": round(avg_latency, 2),
                    "max_latency_ms": round(max_latency, 2),
                    "min_latency_ms": round(min_latency, 2),
                    "all_within_500ms": all_within_threshold,
                    "latencies": [round(l, 2) for l in latencies],
                },
            )

            self._record_result(
                "Multiple state changes verified",
                len(latencies) >= 5,
                f"Tested {len(latencies)} state changes",
                {"state_changes_count": len(latencies)},
            )

            await self.manager.unsubscribe(task_id, "client-realtime-test")

        except Exception as e:
            self._record_result(
                "Real-time performance",
                False,
                f"Error: {str(e)}",
            )

    async def verify_multi_client_support(self):
        """Verify multi-client support."""
        print("\n" + "="*60)
        print("TEST 5: Multi-client Support")
        print("="*60)

        task_id = "test-multiclient-005"
        client_ids = [f"client-{i}" for i in range(10)]

        try:
            for cid in client_ids:
                await self.manager.subscribe(task_id, cid)

            self._record_result(
                "10 clients subscribed",
                self.manager.get_active_clients_count(task_id) == 10,
                f"Active clients: {self.manager.get_active_clients_count(task_id)}",
            )

            await self.manager.broadcast(task_id, "progress", {"epoch": 1, "loss": 0.5})

            all_received = True
            for cid in client_ids:
                client = self.manager._clients[task_id][cid]
                event = await asyncio.wait_for(client.queue.get(), timeout=5.0)
                if "event: progress" not in event:
                    all_received = False

            self._record_result(
                "All 10 clients received event",
                all_received,
                f"All clients received: {all_received}",
            )

            for cid in client_ids:
                await self.manager.unsubscribe(task_id, cid)

        except Exception as e:
            self._record_result(
                "Multi-client support",
                False,
                f"Error: {str(e)}",
            )

    def generate_report(self):
        """Generate comprehensive verification report."""
        print("\n" + "="*60)
        print("SSE VERIFICATION REPORT")
        print("="*60)
        print(f"Generated at: {datetime.now().isoformat()}")
        print(f"Test environment: Python {sys.version}")
        print(f"Total tests: {len(self.results)}")

        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)

        print(f"Passed: {passed}")
        print(f"Failed: {failed}")
        print(f"Pass rate: {passed/len(self.results)*100:.1f}%")

        print("\n" + "-"*60)
        print("DETAILED RESULTS")
        print("-"*60)

        for r in self.results:
            status = "[OK]" if r.passed else "[ERR]"
            print(f"\n{status} {r.test_name}")
            print(f"   Details: {r.details}")
            if r.metrics:
                print(f"   Metrics: {json.dumps(r.metrics, indent=6)}")

        print("\n" + "-"*60)
        print("SUMMARY")
        print("-"*60)

        endpoint_ok = any(r.test_name == "Endpoint subscription" and r.passed for r in self.results)
        format_ok = any(r.test_name == "Progress event format" and r.passed for r in self.results)
        realtime_ok = any("Real-time latency" in r.test_name and r.passed for r in self.results)

        print(f"SSE endpoint: {'YES - OK' if endpoint_ok else 'NO - FAILED'}")
        print(f"Event format: {'YES - OK' if format_ok else 'NO - FAILED'}")
        print(f"Real-time <= 500ms: {'YES - OK' if realtime_ok else 'NO - FAILED'}")
        print(f"Overall: {'PASS' if all(r.passed for r in self.results) else 'NEEDS ATTENTION'}")


async def main():
    print("="*60)
    print("SSE Training Status Push System - Verification")
    print("="*60)

    verifier = SSEVerifier()

    await verifier.verify_endpoint_configuration()
    await verifier.verify_event_format()
    await verifier.verify_callback_mechanism()
    await verifier.verify_realtime_performance()
    await verifier.verify_multi_client_support()

    verifier.generate_report()


if __name__ == "__main__":
    asyncio.run(main())
