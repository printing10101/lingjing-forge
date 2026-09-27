"""NC代码刀具路径仿真与碰撞检测系统。

提供G代码解析、3D可视化、碰撞检测、体素切削仿真和仿真报告生成功能。
"""

from __future__ import annotations

from app.simulation.stock_model import StockBoundingBox, StockModel, CylindricalStock
from app.simulation.toolpath_parser import ToolpathParser, ToolpathSegment
from app.simulation.collision_detector import (
    CollisionDetector,
    CollisionEvent,
    CollisionReport,
)
from app.simulation.toolpath_visualizer import ToolpathVisualizer
from app.simulation.simulation_report import (
    SimulationReport,
    generate_summary_text,
)
from app.simulation.voxel_cutter import (
    VoxelCutter,
    VoxelSimulationResult,
    ToolModel,
    CollisionInfo,
)

__all__ = [
    "StockModel",
    "CylindricalStock",
    "StockBoundingBox",
    "ToolpathParser",
    "ToolpathSegment",
    "CollisionDetector",
    "CollisionEvent",
    "CollisionReport",
    "ToolpathVisualizer",
    "SimulationReport",
    "generate_summary_text",
    "VoxelCutter",
    "VoxelSimulationResult",
    "ToolModel",
    "CollisionInfo",
]
