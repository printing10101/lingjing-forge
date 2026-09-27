"""仿真工厂 API 桥接 单元测试（升级3：① 仿真工厂 API 化）。

运行：unset PYTHONPATH && python -m pytest engineering/python/tests/unit/test_factory_bridge.py -v --no-cov
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("LINGJING_MCP_DEV", "1")
os.environ.setdefault("LINGJING_AGENT_TOKEN", "x" * 40)

from app.simulation.factory_bridge import (  # noqa: E402
    get_factory_demo_status,
    is_factory_sandbox_available,
    run_factory_closed_loop,
)


class TestFactoryBridge:
    def test_sandbox_available(self) -> None:
        assert is_factory_sandbox_available() is True

    def test_closed_loop_report(self) -> None:
        report = run_factory_closed_loop(n_parts=3, max_ticks=300, seed=7)
        assert report is not None
        assert report["parts_completed"] == 3
        assert "score" in report
        assert report["score"]["total"] > 0

    def test_demo_status_lists_devices(self) -> None:
        status = get_factory_demo_status()
        assert status is not None
        device_ids = [d["device_id"] for d in status["devices"]]
        assert "cnc_mill_01" in device_ids
        assert "vib_sensor_01" in device_ids


class TestApiEndpoints:
    def test_endpoints_registered_on_router(self) -> None:
        from app.simulation import api as sim_api

        routes = {r.path for r in sim_api.router.routes}
        assert "/api/simulation/factory/closed-loop" in routes
        assert "/api/simulation/factory/demo-status" in routes
