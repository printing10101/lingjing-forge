"""体素材料去除仿真校验器（阶段 7 第三层校验，仿真强制闭环的核心）。

与 InternalValidator（AABB 包围盒预筛）不同，本模块执行**真实的体素
材料去除仿真**：把毛坯离散为体素网格，沿 G 代码刀轨逐段去除材料，
检测两类致命碰撞：

1. **过切**：切削段（G01/G02/G03）的离散点低于毛坯底面（Z < 0）；
2. **快移碰撞**：快速段（G00）在安全高度以下切入剩余材料。

设计决策：
    - 不走 ``VoxelCutter.run_simulation`` 的 STL 路径：该路径依赖可选的
      trimesh 且需要毛坯 STL 文件。本模块直接合成盒状体素网格（与
      StockModel「底面 Z=0、顶面 z_max=height」语义一致），复用
      ``VoxelCutter`` 的切削原语（``_build_tool_mask`` /
      ``_apply_tool_mask_batch`` / ``_discretize_segment`` /
      ``_check_rapid_collisions``），零文件输出、零可选依赖，
      并自动享受 Rust compute-core 加速（不可用时逐调用回退 Python）。
    - 体素网格索引约定与 ``_check_rapid_collisions`` /
      ``_apply_tool_mask_batch`` 一致：
      ``idx = round((p - bbox_min + padding) / voxel_size)``，
      ``padding = 2 * voxel_size``。

闭环语义（优化升级路线图 A 线「仿真强制闭环」）：
    - 校验结果写入任务级 ``voxel_check_passed``，随 cam_report.json 导出；
    - DNC 下发闸门（``app.dnc.nc_gate``）要求任务的 voxel_check_passed
      为 True 才允许把 NC 程序发送到机床；
    - 本校验无开关（项目记忆硬约束，与 cam_validation_required 同级），
      仅提供性能参数（体素尺寸 / 刀具直径 / 段数上限）。

已知局限（诚实告知，不掩盖）：
    - 3-axis 语义：不做刀柄/夹具干涉检测（CollisionDetector 已覆盖
      安全 Z 违规的快速预筛）；
    - 刀具参数三级来源（tool_source，与 software_check/task 口径一致）：
      任务显式提供（"actual"）> 阶段 6 report.json 携带（"report"）>
      配置默认值（"config_default"）。基于配置默认值的结论在刀具来源项
      如实标注，工程师审核界面会展示所用刀具参数，与实际装刀不符时需
      人工确认；
    - 5-axis 刀轨按 3-axis 投影校验，结论偏保守，5-axis 完整校验
      应通过 CamAdapter 调用 NX/PowerMill。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from app.cam_validation.cam_store import VoxelValidationError
from app.simulation.rust_engine import VoxelCutter, is_rust_available
from app.simulation.toolpath_parser import ToolpathParser
from app.simulation.voxel_cutter import ToolModel
from app.simulation.voxel_cutter.cutter import (
    _check_rapid_collisions,
    _discretize_segment,
)

if TYPE_CHECKING:
    from app.config import CamValidationConfig

logger = logging.getLogger(__name__)

# 碰撞坐标列表导出上限（与 VoxelCutter.run_simulation 的 :20 截断对齐）
_MAX_COLLISION_POSITIONS: int = 20


@dataclass
class VoxelValidationReport:
    """单次体素仿真校验报告（任务级判定）。

    Attributes:
        passed: 综合判定（无过切且无快移碰撞）。
        engine: 实际执行切削内核（"rust" / "python"）。
        voxel_size_mm: 体素边长（mm）。
        total_segments: 解析出的运动段总数。
        cutting_segments: 参与材料去除的切削段数（linear/arc）。
        collision_count: 碰撞点数（过切点 + 快移碰撞点，去重前）。
        collision_blocks: 涉事 G 代码 block_number 去重升序列表，
            供按 feature line_range 归因。
        collision_positions: 碰撞坐标采样（最多 20 个）。
        severity: "none" / "warning" / "critical"
            （与 VoxelCutter 的阈值一致：>3 个碰撞点为 critical）。
        removed_voxel_count: 被切除的体素数（材料去除量指标）。
        voxel_count: 毛坯初始体素数。
        duration_seconds: 仿真耗时。
        warnings: 非致命警告（如 G 代码无运动段、未归因碰撞）。
        tool_diameter_mm: 本次仿真实际使用的刀具直径（mm）。
        tool_type: 本次仿真使用的刀具类型。
        tool_source: 刀具参数来源——"actual"（任务显式提供，来自实际装刀）/
            "report"（阶段 6 report.json 携带）/
            "config_default"（配置默认值，与实际装刀可能不符，结论需人工确认）。
    """

    passed: bool
    engine: str
    voxel_size_mm: float
    total_segments: int
    cutting_segments: int
    collision_count: int
    collision_blocks: list[int]
    collision_positions: list[list[float]]
    severity: str
    removed_voxel_count: int
    voxel_count: int
    duration_seconds: float
    warnings: list[str] = field(default_factory=list)
    tool_diameter_mm: float = 0.0
    tool_type: str = ""
    tool_source: str = "config_default"

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "engine": self.engine,
            "voxel_size_mm": self.voxel_size_mm,
            "total_segments": self.total_segments,
            "cutting_segments": self.cutting_segments,
            "collision_count": self.collision_count,
            "collision_blocks": list(self.collision_blocks),
            "collision_positions": [list(p) for p in self.collision_positions],
            "severity": self.severity,
            "removed_voxel_count": self.removed_voxel_count,
            "voxel_count": self.voxel_count,
            "duration_seconds": round(self.duration_seconds, 4),
            "warnings": list(self.warnings),
            "tool_diameter_mm": self.tool_diameter_mm,
            "tool_type": self.tool_type,
            "tool_source": self.tool_source,
        }


class VoxelValidator:
    """体素材料去除仿真校验器（无状态，每次 validate 独立构建网格）。

    组合（has-a）``VoxelCutter`` 的切削原语，不继承；
    ``VoxelCutter`` 内部自动选择 Rust / Python 内核。
    """

    def __init__(self, config: "CamValidationConfig") -> None:
        """初始化校验器。

        Args:
            config: CamValidationConfig（读取 voxel_size_mm /
                voxel_tool_diameter_mm / voxel_tool_type / voxel_max_segments）。
        """
        self._config = config

    def validate(
        self,
        *,
        gcode_text: str,
        controller_type: str,
        safe_z: float,
        stock_top_z: float,
        stock_length: float,
        stock_width: float,
        stock_height: float,
        tool_diameter_mm: float | None = None,
        tool_type: str = "",
        tool_source: str = "",
    ) -> VoxelValidationReport:
        """执行体素材料去除仿真校验。

        Args:
            gcode_text: G 代码文本（来自 GCodeLoader.load_from_report）。
            controller_type: 控制器类型（决定 ToolpathParser 方言）。
            safe_z: 安全 Z 平面绝对坐标（mm），来自阶段 6 GCodeReport。
            stock_top_z: 毛坯顶面 Z（mm）。本模块语义：毛坯底面 Z=0、
                顶面 Z=stock_height（与 StockModel 一致）。
            stock_length / stock_width / stock_height: 毛坯尺寸（mm）。
                调用方（pipeline）固定传 _DEFAULT_STOCK_* 常量，
                与 InternalValidator 的 StockModel 对齐。
            tool_diameter_mm: 实际装刀直径（mm）。提供时覆盖配置默认值，
                使仿真基于真实刀具执行（直径越大去除越多，碰撞判定越保守）；
                缺省回退 cfg.voxel_tool_diameter_mm，调用方应通过
                report.tool_source="config_default" 向工程师如实披露。
            tool_type: 实际刀具类型（缺省回退 cfg.voxel_tool_type）。
            tool_source: 刀具直径来源声明（"actual"=任务提供 /
                "report"=阶段 6 携带），随 report.tool_source 原样落盘；
                仅在 tool_diameter_mm 提供时生效，空串按 "actual" 处理。
                未提供直径时一律为 "config_default"。

        Returns:
            VoxelValidationReport

        Raises:
            VoxelValidationError: G 代码解析失败 / 运动段数超过
                voxel_max_segments 上限 / safe_z 不高于 stock_top_z /
                切削内核执行异常。
        """
        cfg = self._config
        start = time.perf_counter()
        warnings: list[str] = []

        # 刀具实参覆盖：任务/报告显式提供 > 配置默认；来源由调用方声明、
        # 随报告如实落盘（本模块不自行猜测来源，口径见模块头"已知局限"）
        if tool_diameter_mm:
            effective_diameter = float(tool_diameter_mm)
            effective_tool_type = tool_type or cfg.voxel_tool_type
            tool_source = tool_source or "actual"
        else:
            effective_diameter = cfg.voxel_tool_diameter_mm
            effective_tool_type = cfg.voxel_tool_type
            tool_source = "config_default"

        if safe_z <= stock_top_z:
            raise VoxelValidationError(
                f"safe_z={safe_z}mm 必须大于 stock_top_z={stock_top_z}mm"
                f"（与 InternalValidator 同一约束），无法确定安全高度余量。"
            )

        # 1. 解析 G 代码
        try:
            parser = ToolpathParser(controller_type=controller_type)
            segments = parser.parse_gcode(gcode_text)
        except Exception as e:
            raise VoxelValidationError(
                f"ToolpathParser 解析 G 代码失败（controller_type={controller_type}）: {e}"
            ) from e

        # 2. 段数上限（fail-closed：超限拒绝仿真，不允许"部分仿真"冒充通过）
        if len(segments) > cfg.voxel_max_segments:
            raise VoxelValidationError(
                f"G 代码运动段数 {len(segments)} 超过体素仿真上限 "
                f"{cfg.voxel_max_segments}（LNN_CAM_VOXEL_MAX_SEGMENTS）。"
                f"为避免部分仿真被误当完整校验，已拒绝执行；"
                f"如确需仿真本程序，请调大上限或用外部 CAM 全量校验。"
            )

        if not segments:
            warnings.append("G 代码无运动段，体素仿真未检测到碰撞（空程序视为通过）")

        # 3. 切削内核与刀具（直径/类型优先用实际装刀参数）。
        # 刀具类型走 ToolModel 白名单校验：任务/报告携带的类型不在白名单时
        # 回退配置默认并写警告，绝不让自由字符串炸掉强制校验层（fail-open 禁止）。
        cutter = VoxelCutter(voxel_size=cfg.voxel_size_mm)
        try:
            tool = ToolModel(
                diameter=effective_diameter,
                cutting_length=max(stock_height + cfg.voxel_size_mm * 4, effective_diameter),
                tool_type=effective_tool_type,
            )
        except ValueError as e:
            warnings.append(
                f"体素仿真刀具类型 {effective_tool_type!r} 无效（{e}），已回退配置默认 {cfg.voxel_tool_type!r}；"
                f"直径仍使用实际值 {effective_diameter}mm。"
            )
            effective_tool_type = cfg.voxel_tool_type
            tool = ToolModel(
                diameter=effective_diameter,
                cutting_length=max(stock_height + cfg.voxel_size_mm * 4, effective_diameter),
                tool_type=effective_tool_type,
            )

        # 4. 合成盒状毛坯体素网格（底面 Z=0，与 StockModel 语义一致）
        voxel_size = cutter._voxel_size
        padding = voxel_size * 2
        bbox_min = np.array([0.0, 0.0, 0.0])
        voxel_grid = self._build_box_grid(stock_length, stock_width, stock_height, voxel_size, padding)
        voxel_count = int(voxel_grid.sum())

        # 5. 切削循环（语义与 VoxelCutter.run_simulation 保持一致）
        collision_positions: list[list[float]] = []
        collision_blocks: list[int] = []
        below_bottom = False

        cutting_segments = [s for s in segments if s.type in ("linear", "arc")]
        all_cut_points: list[np.ndarray] = []
        for seg in cutting_segments:
            seg_points = _discretize_segment(seg, voxel_size * 0.5, voxel_size)
            for pt in seg_points:
                x, y, z = float(pt[0]), float(pt[1]), float(pt[2])
                if z < bbox_min[2] - 0.01:
                    below_bottom = True
                    collision_positions.append([x, y, z])
                    collision_blocks.append(seg.block_number)
                    continue
                all_cut_points.append(np.array([x, y, z]))

        removed_count = 0
        if all_cut_points:
            points_array = np.array(all_cut_points, dtype=np.float64)
            tool_mask = cutter._build_tool_mask(tool)
            removed_count = cutter._apply_tool_mask_batch(
                voxel_grid, tool_mask, points_array, bbox_min, voxel_size, padding
            )

        severity = "none"
        if below_bottom:
            severity = "critical" if len(collision_positions) > 3 else "warning"

        # 6. 快移碰撞：G00 在安全高度以下切入剩余材料
        # safe_z_height 语义为「相对毛坯底面（bbox_min[2]=0）的快速安全平面绝对高度」，
        # 故直接传 safe_z（绝对坐标）；高于安全平面的快速点由 _check_rapid_collisions 跳过，
        # 落在网格外（毛坯上方）的点由边界检查自然放行。
        # 首段运动排除：ToolpathParser 的模态起点 (0,0,0) 是虚拟起点（毛坯角点），
        # 不代表物理刀具位置；真实程序的首段快速定位（如 G00 G43 Z80.）从该虚拟
        # 点出发，按碰撞处理会误杀所有正常程序。与商用 CAM 仿真的初始定位约定一致。
        rapid_check = _check_rapid_collisions(
            segments[1:] if segments else segments, voxel_grid, bbox_min, safe_z, voxel_size
        )
        if rapid_check.collided:
            collision_positions.extend(rapid_check.collision_positions)
            collision_blocks.extend(rapid_check.collision_segment_indices)
            if rapid_check.collision_severity == "critical":
                severity = "critical"
            elif severity == "none":
                severity = "warning"

        # 7. 未归因碰撞提示（与 InternalValidator 的 unknown 归因口径一致）
        attributed_hint = f"（block 列表供特征归因：{sorted(set(collision_blocks))[:20]}）" if collision_blocks else ""
        if collision_positions:
            warnings.append(f"体素仿真检测到 {len(collision_positions)} 处碰撞（severity={severity}）{attributed_hint}")

        # 坐标去重 + 截断（与 run_simulation 的导出口径一致）
        unique_positions: list[list[float]] = []
        for pos in collision_positions:
            if pos not in unique_positions:
                unique_positions.append(pos)
        unique_positions = unique_positions[:_MAX_COLLISION_POSITIONS]

        report = VoxelValidationReport(
            passed=not collision_positions,
            engine="rust" if is_rust_available() else "python",
            voxel_size_mm=voxel_size,
            total_segments=len(segments),
            cutting_segments=len(cutting_segments),
            collision_count=len(collision_positions),
            collision_blocks=sorted(set(collision_blocks)),
            collision_positions=unique_positions,
            severity=severity,
            removed_voxel_count=int(removed_count),
            voxel_count=voxel_count,
            duration_seconds=time.perf_counter() - start,
            warnings=warnings,
            tool_diameter_mm=effective_diameter,
            tool_type=effective_tool_type,
            tool_source=tool_source,
        )

        logger.info(
            "VoxelValidator 完成：segments=%d cutting=%d collisions=%d severity=%s "
            "removed=%d/%d voxels engine=%s elapsed=%.2fs",
            report.total_segments,
            report.cutting_segments,
            report.collision_count,
            report.severity,
            report.removed_voxel_count,
            report.voxel_count,
            report.engine,
            report.duration_seconds,
        )
        return report

    @staticmethod
    def _build_box_grid(
        length: float,
        width: float,
        height: float,
        voxel_size: float,
        padding: float,
    ) -> np.ndarray:
        """构建实心盒状体素网格（True=材料存在）。

        索引约定与 ``_check_rapid_collisions`` 一致：
        体素 (ix, iy, iz) 中心位于
        ``bbox_min - padding + (i + 0.5) * voxel_size``。
        """
        nx = max(1, int(np.ceil((length + padding * 2) / voxel_size)))
        ny = max(1, int(np.ceil((width + padding * 2) / voxel_size)))
        nz = max(1, int(np.ceil((height + padding * 2) / voxel_size)))

        centers_x = -padding + (np.arange(nx) + 0.5) * voxel_size
        centers_y = -padding + (np.arange(ny) + 0.5) * voxel_size
        centers_z = -padding + (np.arange(nz) + 0.5) * voxel_size

        # 体素中心落在盒内（含边界）即视为材料
        mx = (centers_x >= 0.0) & (centers_x <= length)
        my = (centers_y >= 0.0) & (centers_y <= width)
        mz = (centers_z >= 0.0) & (centers_z <= height)

        return mx[:, None, None] & my[None, :, None] & mz[None, None, :]
