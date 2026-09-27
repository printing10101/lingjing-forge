"""体素切割器仿真模块。"""

from app.simulation.voxel_cutter.cutter import (
    HAS_NUMBA,
    MAX_STL_RETRIES,
    STL_RETRY_INTERVAL,
    VoxelCutter,
    _apply_tool_mask_batch,
    _apply_tool_mask_single,
    _generate_stl_from_dxf,
    _generate_stl_from_step,
    _infer_source_paths,
)
from app.simulation.voxel_cutter.models import VoxelSimulationResult, CollisionInfo
from app.simulation.voxel_cutter.mesher import HAS_SKIMAGE, ToolModel, reconstruct_mesh
from app.simulation.voxel_cutter.auto_diff import (
    DEFAULT_GOUGE_REJECT_RATIO,
    DEFAULT_GOUGE_WARN_RATIO,
    DEFAULT_LEFTOVER_REJECT_RATIO,
    DEFAULT_LEFTOVER_WARN_RATIO,
    DiffRegion,
    DiffResult,
    GeometryDiffer,
)

__all__ = [
    "VoxelCutter",
    "VoxelSimulationResult",
    "ToolModel",
    "CollisionInfo",
    "reconstruct_mesh",
    "_apply_tool_mask_single",
    "_apply_tool_mask_batch",
    "_generate_stl_from_step",
    "_generate_stl_from_dxf",
    "_infer_source_paths",
    "MAX_STL_RETRIES",
    "STL_RETRY_INTERVAL",
    "HAS_NUMBA",
    "HAS_SKIMAGE",
    # Auto-Diff 几何比对
    "GeometryDiffer",
    "DiffResult",
    "DiffRegion",
    "DEFAULT_GOUGE_REJECT_RATIO",
    "DEFAULT_GOUGE_WARN_RATIO",
    "DEFAULT_LEFTOVER_REJECT_RATIO",
    "DEFAULT_LEFTOVER_WARN_RATIO",
]
