"""体素化切削仿真引擎 - 数据模型模块。

定义仿真结果和碰撞检测的数据结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CollisionInfo:
    """碰撞检测结果详细信息。

    Attributes:
        collided: 是否发生碰撞
        collision_positions: 碰撞位置的XYZ坐标列表
        collision_segment_indices: 发生碰撞的刀位点序号列表
        collision_severity: 碰撞严重程度 - "none"/"warning"/"critical"
    """

    collided: bool = False
    collision_positions: list[list[float]] = field(default_factory=list)
    collision_segment_indices: list[int] = field(default_factory=list)
    collision_severity: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "collided": self.collided,
            "collision_positions": self.collision_positions,
            "collision_segment_indices": self.collision_segment_indices,
            "collision_severity": self.collision_severity,
        }


@dataclass
class VoxelSimulationResult:
    """体素切削仿真完整结果。

    Attributes:
        task_id: 仿真任务唯一标识
        stock_stl_url: 切削后工件STL文件URL(相对路径)
        stock_stl_raw: 切削后工件STL二进制数据(用于前端直接加载)
        collision: 碰撞检测结果
        duration_seconds: 仿真耗时(秒)
        voxel_count: 体素总数
        removed_voxel_count: 被切除的体素数量
        voxel_size: 体素分辨率(mm)
        original_bbox: 原始毛坯包围盒
        toolpath_segment_count: 处理的刀位点数量
    """

    task_id: str = ""
    stock_stl_url: str = ""
    stock_stl_raw: bytes = b""
    collision: CollisionInfo = field(default_factory=CollisionInfo)
    duration_seconds: float = 0.0
    voxel_count: int = 0
    removed_voxel_count: int = 0
    voxel_size: float = 1.0
    original_bbox: dict[str, float] | None = None
    toolpath_segment_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "stock_stl_url": self.stock_stl_url,
            "collision": self.collision.to_dict(),
            "duration_seconds": round(self.duration_seconds, 3),
            "voxel_count": self.voxel_count,
            "removed_voxel_count": self.removed_voxel_count,
            "voxel_size": self.voxel_size,
            "original_bbox": self.original_bbox,
            "toolpath_segment_count": self.toolpath_segment_count,
        }
