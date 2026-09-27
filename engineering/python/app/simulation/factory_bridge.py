"""仿真工厂闭环沙盒 → app 侧桥接（升级3：① 仿真工厂 API 化）。

参照 _research_bridge 的懒加载模式：生产容器缺 mcp_server 依赖时优雅降级
（返回 None / 明确报错），不破坏 app 启动。

mcp_server 位于仓库根（非 engineering/python 包内），桥接内部动态注入路径。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_import_attempted = False
_available = False


def _ensure_mcp_server_importable() -> None:
    """把仓库根加入 sys.path（mcp_server 在仓库根）。"""
    global _import_attempted
    if _import_attempted:
        return
    _import_attempted = True
    try:
        # engineering/python/app/... 仓库根
        root = Path(__file__).resolve().parents[4]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
    except Exception as e:  # noqa: BLE001
        logger.debug("factory bridge: 路径注入失败: %s", e)


def is_factory_sandbox_available() -> bool:
    """mcp_server.factory_sandbox 是否可导入。"""
    global _available
    if not _import_attempted:
        _ensure_mcp_server_importable()
    if not _available:
        try:
            import mcp_server.factory_sandbox  # noqa: F401

            _available = True
        except Exception as e:  # noqa: BLE001
            logger.warning("factory bridge: mcp_server 不可用: %s", e)
            return False
    return True


def run_factory_closed_loop(
    n_parts: int = 5,
    max_ticks: int = 800,
    seed: int = 42,
) -> dict[str, Any] | None:
    """运行仿真工厂闭环生产，返回 NLDF 风格 KPI 报告。

    Returns:
        KPI dict；mcp_server 不可用时返回 None。
    """
    if not is_factory_sandbox_available():
        return None
    try:
        from mcp_server.factory_agent import ClosedLoopAgent

        agent = ClosedLoopAgent(seed=seed)
        report = agent.run_production_cycle(n_parts=int(n_parts), max_ticks=int(max_ticks))
        return report
    except Exception as e:  # noqa: BLE001
        logger.error("factory closed loop 运行失败: %s", e, exc_info=True)
        return {"error": f"仿真工厂闭环运行失败: {e}"}


def get_factory_demo_status() -> dict[str, Any] | None:
    """返回演示设备清单（Phase 2 demo registry）。"""
    if not is_factory_sandbox_available():
        return None
    try:
        from mcp_server.device_registry import build_demo_registry

        devices = []
        for desc in build_demo_registry():
            devices.append(
                {
                    "device_id": desc.device_id,
                    "name": desc.name,
                    "device_type": desc.device_type,
                    "controller": desc.controller,
                    "operations": [op.name for op in desc.operations],
                    "signals": [s.name for s in desc.signals],
                }
            )
        return {"devices": devices}
    except Exception as e:  # noqa: BLE001
        logger.error("factory demo status 获取失败: %s", e)
        return {"error": f"获取仿真设备状态失败: {e}"}


__all__ = [
    "is_factory_sandbox_available",
    "run_factory_closed_loop",
    "get_factory_demo_status",
]
