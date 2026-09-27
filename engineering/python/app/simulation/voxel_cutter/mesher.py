"""体素化切削仿真引擎 - 网格处理模块。

提供刀具几何建模、体素化算法和网格重建功能。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import trimesh

try:
    from skimage import measure as skmeasure

    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False

logger = logging.getLogger(__name__)


@dataclass
class ToolModel:
    """刀具几何与物理模型（符合ISO 13399标准）。

    定义刀具的三维几何表示和物理约束属性，用于体素化切削计算。
    支持平底刀(flat)、球头刀(ball)和钻头(drill)三种类型。

    Attributes:
        diameter: 刀具直径(mm)，范围[0.5, 300.0]
        cutting_length: 刀具刃长(mm)，范围[1.0, 500.0]
        overall_length: 刀具总长(mm)
        tool_type: 刀具类型 - "flat"(平底刀), "ball"(球头刀), "drill"(钻头)
        corner_radius: 刀尖圆角半径(mm)，平底刀=0, 球头刀=diameter/2
        material: 刀具材料 (e.g., "carbide", "HSS", "ceramic")
        coating: 涂层类型 (e.g., "TiAlN", "TiN", "AlCrN", "none")
        flute_count: 刃数，范围[1, 20]
        helix_angle_deg: 螺旋角(°)，范围[0, 60]
        rake_angle_deg: 前角(°)
        clearance_angle_deg: 后角(°)，范围[0, 30]
        max_depth_of_cut: 最大切深(mm)，默认≤直径
        max_cutting_force_n: 最大切削力(N)，基于刀具材料和直径估算
        tool_life_minutes: 预期刀具寿命(min)，默认60
        shank_diameter: 柄径(mm)，用于夹持分析
        max_spindle_speed_rpm: 最大允许转速(RPM)
    """

    diameter: float = 10.0
    cutting_length: float = 50.0
    overall_length: float = 80.0
    tool_type: str = "flat"
    corner_radius: float = 0.0
    material: str = "carbide"
    coating: str = "TiAlN"
    flute_count: int = 2
    helix_angle_deg: float = 30.0
    rake_angle_deg: float = 10.0
    clearance_angle_deg: float = 8.0
    max_depth_of_cut: float = 0.0
    max_cutting_force_n: float = 0.0
    tool_life_minutes: float = 60.0
    shank_diameter: float = 10.0
    max_spindle_speed_rpm: float = 20000.0

    _PHYSICAL_CONSTRAINTS = {
        "diameter": (0.5, 300.0),
        "cutting_length": (1.0, 500.0),
        "overall_length": (1.0, 600.0),
        "corner_radius": (0.0, 150.0),
        "flute_count": (1, 20),
        "helix_angle_deg": (0.0, 60.0),
        "clearance_angle_deg": (0.0, 30.0),
        "max_depth_of_cut": (0.0, 500.0),
        "max_spindle_speed_rpm": (0.0, 200000.0),
        "shank_diameter": (0.1, 300.0),
    }

    _VALID_TOOL_TYPES = frozenset(
        {
            "flat",
            "ball",
            "drill",
            "chamfer",
            "thread_mill",
            "reamer",
            "ballnose",
            "bullnose",
            "bull",
            "tapered",
            "balltapered",
            "tapered_ball",
            "form",
            "profile",
        }
    )
    _VALID_MATERIALS = frozenset({"carbide", "HSS", "ceramic", "CBN", "PCD"})

    def __post_init__(self) -> None:
        if self.tool_type not in self._VALID_TOOL_TYPES:
            raise ValueError(f"ToolModel.tool_type='{self.tool_type}'无效，可选: {sorted(self._VALID_TOOL_TYPES)}")
        if self.material not in self._VALID_MATERIALS:
            raise ValueError(f"ToolModel.material='{self.material}'无效，可选: {sorted(self._VALID_MATERIALS)}")
        if self.corner_radius > self.diameter / 2.0:
            raise ValueError(f"corner_radius({self.corner_radius})不能超过半径({self.diameter / 2.0})")
        if self.cutting_length > self.overall_length:
            raise ValueError(f"cutting_length({self.cutting_length})不能超过overall_length({self.overall_length})")
        if self.shank_diameter > self.diameter * 2.0:
            raise ValueError(f"shank_diameter({self.shank_diameter})与diameter({self.diameter})比例不合理")
        for field_name, (low, high) in self._PHYSICAL_CONSTRAINTS.items():
            value = getattr(self, field_name)
            if value < low or value > high:
                raise ValueError(f"ToolModel.{field_name}={value}超出物理约束范围[{low}, {high}]")

        if self.tool_type == "ball" and self.corner_radius < 0.001:
            self.corner_radius = self.diameter / 2.0

        if self.max_depth_of_cut <= 0:
            self.max_depth_of_cut = self.diameter * 1.5

        if self.max_cutting_force_n <= 0:
            if self.material == "carbide":
                self.max_cutting_force_n = self.diameter * 200.0
            elif self.material == "HSS":
                self.max_cutting_force_n = self.diameter * 80.0
            else:
                self.max_cutting_force_n = self.diameter * 100.0

    @property
    def length(self) -> float:
        return self.cutting_length

    def to_dict(self) -> dict[str, Any]:
        return {
            "diameter": self.diameter,
            "cutting_length": self.cutting_length,
            "overall_length": self.overall_length,
            "tool_type": self.tool_type,
            "corner_radius": self.corner_radius,
            "material": self.material,
            "coating": self.coating,
            "flute_count": self.flute_count,
            "helix_angle_deg": self.helix_angle_deg,
            "rake_angle_deg": self.rake_angle_deg,
            "clearance_angle_deg": self.clearance_angle_deg,
            "max_depth_of_cut": self.max_depth_of_cut,
            "max_cutting_force_n": self.max_cutting_force_n,
            "tool_life_minutes": self.tool_life_minutes,
            "shank_diameter": self.shank_diameter,
            "max_spindle_speed_rpm": self.max_spindle_speed_rpm,
        }

    def voxel_mask(self, voxel_size: float, z_offset: float = 0.0) -> np.ndarray:
        """生成刀具的体素掩码（NumPy向量化实现）。

        在刀具局部坐标系中(刀尖在原点+Z向上)创建体素网格，
        根据刀具类型确定哪些体素被刀具占据。

        Args:
            voxel_size: 体素边长(mm)
            z_offset: 刀尖Z轴偏移(mm)，正值=刀具深入工件

        Returns:
            3D布尔数组，True=刀具占据该体素
        """
        r = self.diameter / 2.0
        grid_half = int(np.ceil(r / voxel_size)) + 1
        grid_range = np.arange(-grid_half, grid_half + 1) * voxel_size

        active_length = self.length
        if self.tool_type == "drill":
            active_length = min(self.length, r * 0.3)

        X, Y, Z = np.meshgrid(
            grid_range,
            grid_range,
            grid_range,
            indexing="ij",
            sparse=False,
        )

        radial_sq = X * X + Y * Y
        radial_dist = np.sqrt(radial_sq)
        z_effective = Z + z_offset

        within_radius = radial_sq <= r * r + 1e-9
        within_z = (z_effective <= 0) & (z_effective >= -active_length)
        valid_region = within_radius & within_z

        if self.tool_type == "flat":
            if self.corner_radius > 0:
                is_corner_flat = (z_effective >= -self.corner_radius) & (radial_dist <= r - self.corner_radius)
                is_corner_fillet = (
                    (z_effective >= -self.corner_radius) & (radial_dist <= r) & (z_effective >= -radial_dist)
                )
                is_corner_region = is_corner_flat | is_corner_fillet
                is_cylinder = (z_effective < -self.corner_radius) & (radial_dist <= r)
                mask = valid_region & (is_corner_region | is_cylinder)
            else:
                mask = valid_region & (radial_dist <= r)

        elif self.tool_type == "ball":
            r_eff = self.corner_radius
            z_center = -r_eff + z_offset
            dist_to_center = np.sqrt(radial_sq + (z_effective - z_center) ** 2)
            mask = valid_region & (dist_to_center <= r_eff + 1e-9)

        elif self.tool_type == "drill":
            mask = valid_region & (radial_dist <= r)

        else:
            mask = valid_region & (radial_dist <= r)

        return mask


def voxelize_mesh(
    mesh: "trimesh.Trimesh",
    bbox_min: np.ndarray,
    bbox_max: np.ndarray,
    voxel_size: float,
) -> np.ndarray:
    """将三角网格转换为体素网格。

    Args:
        mesh: trimesh网格对象
        bbox_min: 包围盒最小点
        bbox_max: 包围盒最大点
        voxel_size: 体素边长

    Returns:
        3D布尔数组，True=体素在物体内部
    """
    padding = voxel_size * 2
    extents = bbox_max - bbox_min + padding * 2
    nx = max(1, int(np.ceil(extents[0] / voxel_size)))
    ny = max(1, int(np.ceil(extents[1] / voxel_size)))
    nz = max(1, int(np.ceil(extents[2] / voxel_size)))

    try:
        from trimesh.voxel import creation as voxel_creation

        pitch = voxel_size
        voxel_obj = voxel_creation.voxelize(mesh, pitch=pitch, method="subdivide")
        voxel_grid = voxel_obj.matrix.astype(bool)
        return voxel_grid
    except (ImportError, ModuleNotFoundError, ValueError, TypeError) as e:
        logger.debug(
            f"trimesh voxelize unavailable or failed, falling back to contains: {e}",
            exc_info=True,
        )

    try:
        return _voxelize_contains(mesh, bbox_min, padding, nx, ny, nz, voxel_size)
    except (ValueError, TypeError, np.exceptions.AxisError) as e:
        logger.debug(
            f"Voxelize-contains fallback failed, returning empty grid: {e}",
            exc_info=True,
        )

    voxel_grid = np.zeros((nx, ny, nz), dtype=bool)
    return voxel_grid


def _voxelize_contains(
    mesh: "trimesh.Trimesh",
    bbox_min: np.ndarray,
    padding: float,
    nx: int,
    ny: int,
    nz: int,
    voxel_size: float,
) -> np.ndarray:
    """使用trimesh的contains方法逐点判断体素占用。"""
    voxel_grid = np.zeros((nx, ny, nz), dtype=bool)
    batch_size = 2000

    all_points_list: list[np.ndarray] = []
    all_indices_list: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []

    for ix in range(nx):
        x = bbox_min[0] - padding + (ix + 0.5) * voxel_size
        for iy in range(ny):
            y = bbox_min[1] - padding + (iy + 0.5) * voxel_size
            for iz in range(nz):
                z = bbox_min[2] - padding + (iz + 0.5) * voxel_size
                all_points_list.append(np.array([x, y, z]))
                all_indices_list.append((np.array([ix]), np.array([iy]), np.array([iz])))

                if len(all_points_list) >= batch_size:
                    pts = np.array(all_points_list)
                    try:
                        inside = mesh.contains(pts)
                    except (ValueError, RuntimeError, TypeError) as contains_err:
                        logger.debug("mesh.contains批处理失败, 跳过本批: %s", contains_err)
                        return voxel_grid
                    for k, (ix_a, iy_a, iz_a) in enumerate(all_indices_list):
                        if inside[k]:
                            voxel_grid[ix_a, iy_a, iz_a] = True
                    all_points_list = []
                    all_indices_list = []

    if all_points_list:
        pts = np.array(all_points_list)
        try:
            inside = mesh.contains(pts)
        except (ValueError, RuntimeError, TypeError) as contains_err:
            logger.debug("mesh.contains尾批处理失败: %s", contains_err)
            return voxel_grid
        for k, (ix_a, iy_a, iz_a) in enumerate(all_indices_list):
            if inside[k]:
                voxel_grid[ix_a, iy_a, iz_a] = True

    return voxel_grid


def reconstruct_mesh(
    voxel_grid: np.ndarray,
    bbox_min: np.ndarray,
    voxel_size: float,
) -> "trimesh.Trimesh | None":
    """从体素网格重建三角网格（Marching Cubes算法）。

    Args:
        voxel_grid: 3D布尔体素网格
        bbox_min: 工件包围盒最小点
        voxel_size: 体素边长

    Returns:
        trimesh.Trimesh对象或None
    """
    try:
        import trimesh
    except ImportError:
        return None

    active_indices = np.argwhere(voxel_grid)
    if len(active_indices) == 0:
        return None

    padding = voxel_size * 2

    if HAS_SKIMAGE:
        try:
            padded = np.pad(voxel_grid, pad_width=1, mode="constant", constant_values=0)
            spacing = (voxel_size, voxel_size, voxel_size)

            verts, faces, _, _ = skmeasure.marching_cubes(
                padded.astype(np.float64),
                level=0.5,
                spacing=spacing,
            )

            verts_world = np.empty_like(verts)
            verts_world[:, 0] = bbox_min[0] - padding + verts[:, 0]
            verts_world[:, 1] = bbox_min[1] - padding + verts[:, 1]
            verts_world[:, 2] = bbox_min[2] - padding + verts[:, 2]

            if len(verts) == 0 or len(faces) == 0:
                logger.warning("Marching Cubes未生成有效网格")
                return _reconstruct_mesh_fallback(voxel_grid, bbox_min, voxel_size, trimesh)

            mesh = trimesh.Trimesh(
                vertices=verts_world,
                faces=faces,
                process=False,
            )

            non_degenerate = mesh.nondegenerate_faces()
            mesh.update_faces(non_degenerate)
            if len(mesh.faces) == 0:
                return _reconstruct_mesh_fallback(voxel_grid, bbox_min, voxel_size, trimesh)

            mesh.fix_normals()
            return mesh

        except (ValueError, RuntimeError, TypeError, AttributeError) as exc:
            logger.warning(
                "Marching Cubes重建失败(%s)，回退到box mesh方法",
                exc,
                exc_info=True,
            )
            return _reconstruct_mesh_fallback(voxel_grid, bbox_min, voxel_size, trimesh)
    else:
        return _reconstruct_mesh_fallback(voxel_grid, bbox_min, voxel_size, trimesh)


def _reconstruct_mesh_fallback(
    voxel_grid: np.ndarray,
    bbox_min: np.ndarray,
    voxel_size: float,
    trimesh_module,
) -> "trimesh.Trimesh | None":
    """回退方案：逐体素创建box mesh后合并。"""
    active_indices = np.argwhere(voxel_grid)
    if len(active_indices) == 0:
        return None

    padding = voxel_size * 2

    all_meshes: list["trimesh.Trimesh"] = []
    for idx in active_indices:
        cx = bbox_min[0] - padding + (idx[0] + 0.5) * voxel_size
        cy = bbox_min[1] - padding + (idx[1] + 0.5) * voxel_size
        cz = bbox_min[2] - padding + (idx[2] + 0.5) * voxel_size

        box = trimesh_module.creation.box(
            extents=[voxel_size * 0.98] * 3,
            transform=trimesh_module.transformations.translation_matrix([cx, cy, cz]),
        )
        all_meshes.append(box)

    if len(all_meshes) == 1:
        return all_meshes[0]

    try:
        combined = trimesh_module.util.concatenate(all_meshes)
        if hasattr(combined, "merge_vertices"):
            combined.merge_vertices()
        if hasattr(combined, "simplify"):
            combined = combined.simplify(threshold=voxel_size * 0.3)
        return combined
    except (ValueError, RuntimeError, TypeError, AttributeError) as combine_err:
        logger.debug("trimesh mesh 合并/简化失败，返回单 box mesh: %s", combine_err)
        return all_meshes[0]
