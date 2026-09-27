# 注意：本文件当前未被引用（保留骨架），保持导入完整以备接入。

from pydantic import BaseModel, Field

from app.simulation.voxel_cutter.models import VoxelSimulationResult


class SimulationRequest(BaseModel):
    """Request model for voxel cutting simulation.

    Contains all parameters needed to run a machining simulation including
    project identification, tool geometry, G-code toolpath, and stock model.

    Attributes:
        project_id: Project identifier for associating simulation jobs.
        voxel_size: Voxel resolution in mm (0.1-10.0). Smaller = higher accuracy.
        tool_diameter: Tool diameter in mm (0.5-300.0).
        tool_length: Tool cutting length in mm (1.0-500.0).
        tool_type: Tool type - "flat" (flat end mill), "ball" (ball nose), "drill".
        tool_corner_radius: Tool corner radius in mm (0.0-150.0).
        gcode: G-code text content for toolpath parsing.
        safe_z_height: Safe plane height in mm (0.0-200.0).
        stock_stl_path: Path to stock STL file (relative or absolute).
        source_file_path: Source file path (STEP/DXF) for auto-regeneration if STL is missing.
    """

    project_id: str = Field(
        default="default",
        description="Project ID for associating simulation jobs.",
    )
    voxel_size: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        description="Voxel resolution in mm. Smaller values yield higher accuracy.",
    )
    tool_diameter: float = Field(
        default=10.0,
        ge=0.5,
        le=300.0,
        description="Tool diameter in mm.",
    )
    tool_length: float = Field(
        default=50.0,
        ge=1.0,
        le=500.0,
        description="Tool cutting length in mm.",
    )
    tool_type: str = Field(
        default="flat",
        pattern="^(flat|ball|drill)$",
        description="Tool type: flat (flat end mill), ball (ball nose), drill.",
    )
    tool_corner_radius: float = Field(
        default=0.0,
        ge=0.0,
        le=150.0,
        description="Tool corner radius in mm.",
    )
    gcode: str = Field(
        default="",
        description="G-code text content for toolpath parsing.",
    )
    safe_z_height: float = Field(
        default=10.0,
        ge=0.0,
        le=200.0,
        description="Safe plane height in mm.",
    )
    stock_stl_path: str = Field(
        default="",
        description="Path to stock STL file (server-relative or absolute).",
    )
    source_file_path: str = Field(
        default="",
        description="Source file path (STEP/DXF) for auto-regeneration when STL is missing.",
    )


class SimulationResponse(BaseModel):
    """Response model containing voxel simulation results.

    Attributes:
        task_id: Unique simulation task identifier.
        stock_stl_url: URL path to the machined workpiece STL file.
        collision_collided: Whether any collision was detected.
        collision_positions: List of [x, y, z] collision coordinates.
        collision_segment_indices: Indices of toolpath segments with collisions.
        collision_severity: Collision severity level ("none"/"warning"/"critical").
        duration_seconds: Total simulation time in seconds.
        voxel_count: Total number of voxels in the stock model.
        removed_voxel_count: Number of voxels removed during simulation.
        voxel_size: Voxel resolution used (mm).
        original_bbox: Original stock bounding box dimensions.
        toolpath_segment_count: Number of toolpath segments processed.
    """

    task_id: str = ""
    stock_stl_url: str = ""
    collision_collided: bool = False
    collision_positions: list[list[float]] = []
    collision_segment_indices: list[int] = []
    collision_severity: str = "none"
    duration_seconds: float = 0.0
    voxel_count: int = 0
    removed_voxel_count: int = 0
    voxel_size: float = 1.0
    original_bbox: dict[str, float] | None = None
    toolpath_segment_count: int = 0

    @classmethod
    def from_result(cls, r: VoxelSimulationResult) -> "SimulationResponse":
        """Create a SimulationResponse from a VoxelSimulationResult.

        Args:
            r: The voxel simulation result to convert.

        Returns:
            A SimulationResponse populated with result data.
        """
        return cls(
            task_id=r.task_id,
            stock_stl_url=r.stock_stl_url,
            collision_collided=r.collision.collided,
            collision_positions=r.collision.collision_positions,
            collision_segment_indices=r.collision.collision_segment_indices,
            collision_severity=r.collision.collision_severity,
            duration_seconds=r.duration_seconds,
            voxel_count=r.voxel_count,
            removed_voxel_count=r.removed_voxel_count,
            voxel_size=r.voxel_size,
            original_bbox=r.original_bbox,
            toolpath_segment_count=r.toolpath_segment_count,
        )


class SimulationStatusResponse(BaseModel):
    """Response model for simulation task status queries.

    Attributes:
        task_id: The simulation task identifier.
        status: Current task status ("pending"/"running"/"completed").
        progress: Task completion progress (0.0-1.0).
        result: Simulation result data, available only when completed.
    """

    task_id: str = ""
    status: str = "pending"
    progress: float = 0.0
    result: SimulationResponse | None = None


# 以下模型由安装验证（2026-08-03）发现拆分遗漏，从拆分前 api.py 恢复


class ConflictCheckRequest(BaseModel):
    """Request model for tool-slot compatibility check.

    Attributes:
        tool_diameter: Tool diameter in mm (0.5-300.0).
        slot_width: Slot width in mm (0.1-500.0).
        material: Workpiece material (e.g., "45 steel").
        operation: Machining operation type (e.g., "slot milling").
    """

    tool_diameter: float = Field(
        default=20.0,
        ge=0.5,
        le=300.0,
        description="Tool diameter in mm.",
    )
    slot_width: float = Field(
        default=10.0,
        ge=0.1,
        le=500.0,
        description="Slot width in mm.",
    )
    material: str = Field(
        default="45 steel",
        description="Workpiece material.",
    )
    operation: str = Field(
        default="slot milling",
        description="Machining operation type.",
    )


class ExportAnimationRequest(BaseModel):
    """Request model for simulation animation export.

    Attributes:
        nc_code: G-code text content for toolpath visualization.
        format: Output format - "gif" or "mp4".
        voxel_size: Voxel resolution in mm (0.1-10.0).
        tool_diameter: Tool diameter in mm (0.5-300.0).
        tool_length: Tool cutting length in mm (1.0-500.0).
        tool_type: Tool type - "flat", "ball", "drill".
    """

    nc_code: str = Field(
        default="",
        max_length=200_000,
        description="G-code text content for toolpath visualization (capped at 200KB to bound render cost).",
    )
    format: str = Field(
        default="gif",
        pattern="^(gif|mp4)$",
        description="Output format: gif or mp4.",
    )
    voxel_size: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        description="Voxel resolution in mm.",
    )
    tool_diameter: float = Field(
        default=10.0,
        ge=0.5,
        le=300.0,
        description="Tool diameter in mm.",
    )
    tool_length: float = Field(
        default=50.0,
        ge=1.0,
        le=500.0,
        description="Tool cutting length in mm.",
    )
    tool_type: str = Field(
        default="flat",
        pattern="^(flat|ball|drill)$",
        description="Tool type.",
    )


class AutoDiffCompareRequest(BaseModel):
    """Auto-Diff 几何比对请求。

    Attributes:
        design_stl_path: 设计模型（目标工件）STL 路径，须位于允许目录内。
        actual_stl_path: 仿真切削结果 STL 路径（VoxelCutter 输出）。
        voxel_size: 体素分辨率（mm），默认 0.5，越小越精确但越慢。
        export_diff_stl: 是否导出偏差可视化 STL，默认 True。
        gouge_warn_ratio: 过切告警阈值（体积占比），可选覆盖默认值。
        gouge_reject_ratio: 过切拒收阈值（体积占比），可选覆盖默认值。
        leftover_warn_ratio: 残料告警阈值（体积占比），可选覆盖默认值。
        leftover_reject_ratio: 残料拒收阈值（体积占比），可选覆盖默认值。
    """

    design_stl_path: str = Field(
        ...,
        description="设计模型 STL 路径（须位于允许目录内）。",
    )
    actual_stl_path: str = Field(
        ...,
        description="仿真结果 STL 路径（须位于允许目录内）。",
    )
    voxel_size: float = Field(
        default=0.5,
        ge=0.1,
        le=5.0,
        description="体素分辨率（mm），越小越精确但越慢。",
    )
    export_diff_stl: bool = Field(
        default=True,
        description="是否导出偏差可视化 STL。",
    )
    gouge_warn_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="过切告警阈值（体积占比），留空使用默认 0.0001。",
    )
    gouge_reject_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="过切拒收阈值（体积占比），留空使用默认 0.001。",
    )
    leftover_warn_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="残料告警阈值（体积占比），留空使用默认 0.01。",
    )
    leftover_reject_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="残料拒收阈值（体积占比），留空使用默认 0.05。",
    )


class FEMSolveRequest(BaseModel):
    """FEM 求解请求体（标准简支梁三点弯曲场景）。"""

    material: str = Field("steel45", max_length=64, description="材料名称")
    elastic_modulus: float = Field(210.0, gt=0, le=1000, description="弹性模量（GPa）")
    poisson_ratio: float = Field(0.3, gt=0, lt=0.5, description="泊松比")
    density: float = Field(7850.0, gt=0, description="密度（kg/m3）")
    yield_strength: float = Field(355.0, gt=0, le=100000, description="屈服强度（MPa）")
    mesh_type: str = Field("tetrahedral", max_length=32, description="网格类型")
    element_size: float = Field(2.0, gt=0, le=100, description="网格尺寸（mm）")
    adaptive_refinement: bool = Field(True, description="是否启用自适应细化")
    beam_length: float = Field(100.0, gt=0, le=10000, description="试件长度（mm）")
    beam_width: float = Field(20.0, gt=0, le=1000, description="试件宽度（mm）")
    beam_height: float = Field(20.0, gt=0, le=1000, description="试件高度（mm）")
    load_force: float = Field(5000.0, gt=0, le=1e9, description="集中载荷（N）")
