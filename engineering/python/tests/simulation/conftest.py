"""simulation 测试包共享 fixtures.

为 ``tests/simulation/`` 下所有测试模块提供：
- ``voxel_cutter_class``：rust_engine.VoxelCutter 类
- ``rust_engine_module``：rust_engine 模块本身
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def rust_engine_module() -> Any:
    """返回 ``app.simulation.rust_engine`` 模块。"""
    from app.simulation import rust_engine

    return rust_engine


@pytest.fixture
def voxel_cutter_class() -> Any:
    """返回 ``VoxelCutter`` 类（rust_engine 子类，fallback 兼容）。"""
    from app.simulation.rust_engine import VoxelCutter

    return VoxelCutter
