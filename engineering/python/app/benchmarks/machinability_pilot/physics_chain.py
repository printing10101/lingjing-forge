"""M3: S4 三轴可达性 + S5 加工物理链（颤振稳定域/切削力/水线刀轨/运动学/体素切削）。

口径（全部为 pilot 参数，记录进 manifest，论文阶段做敏感性分析）：
- 材料 6061-T6（Kienzle kc1.1=800）、φ10 四齿立铣刀、转速 6000 rpm、
  每齿进给 0.1 mm、切宽 ae=5 mm、层降 2 mm；
- 颤振：Tlusty 解析解 compute_stability_limit，判据 ap_lim >= 层降；
- 力：Kienzle Fz vs 2000 N（pilot 常数，VMC850 级）；
- 刀轨：trimesh 等高线水线 + planar_engine 外轮廓精修（内环/孔位跳过并计数）；
- 运动学：合成最小 G 代码 + K001-K009 校验；
- 体素：1mm 切削仿真（Rust 内核不可用时 Python 回退），判碰撞与去除率。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import cadquery as cq
import numpy as np
import trimesh

from app.simulation.chatter.stability import (
    ChatterParams,
    ToolParams,
    compute_stability_limit,
    get_machine_params,
)
from app.simulation.cutting_force.kienzle import compute_cutting_forces
from app.simulation.kinematics.validator import KinematicsValidator
from app.simulation.rust_engine import VoxelCutter
from app.simulation.toolpath_parser import ToolpathSegment
from app.simulation.voxel_cutter.mesher import ToolModel
from app.toolpath import planar_engine

PILOT_PARAMS = {
    "material": "aluminum_6061",
    "tool_diameter_mm": 10.0,
    "num_flutes": 4,
    "spindle_rpm": 6000,
    "feed_per_tooth_mm": 0.1,
    "radial_depth_mm": 5.0,
    "stepdown_mm": 2.0,
    "force_limit_n": 2000.0,
    "kienzle_kc1_1": 800.0,  # 颤振 K_s 与材料系数对齐（6061）
    "undercut_multiaxis_threshold": 0.10,
    "voxel_size_mm": 1.0,
    "waterline_max_levels": 12,
    "waterline_max_rings_per_level": 6,
    "waterline_inner_rings": "孔环同样按轮廓槽加工；过小孔触发 narrow_errors 计数",
}

_SAFE_Z = 15.0
_XY_ORIGIN_OFFSET = (20.0, 20.0)  # 平移使最小 XY 落在 (20,20)，处于行程内


# ---------------------------------------------------------------- S4


def s4_accessibility(mesh: trimesh.Trimesh) -> dict[str, Any]:
    """三轴可达性：按法向把表面积分成上向/竖直/下向；下向（排除底面坐台）为 undercut。"""
    areas = mesh.area_faces
    total = float(areas.sum())
    nz = mesh.face_normals[:, 2]
    z_min = mesh.bounds[0][2]
    face_z = mesh.triangles[:, :, 2].mean(axis=1)
    on_base = face_z <= z_min + 1.0  # 底面 1mm 内视为坐台面，不参与可达性

    up = nz > 0.3
    down = nz < -0.3
    vert = ~(up | down)
    undercut = float(areas[down & ~on_base].sum() / total)
    return {
        "upfacing_fraction": float(areas[up].sum() / total),
        "vertical_fraction": float(areas[vert].sum() / total),
        "undercut_fraction": undercut,
        "needs_multiaxis": undercut > PILOT_PARAMS["undercut_multiaxis_threshold"],
    }


# ---------------------------------------------------------------- S5 前置计算


def chatter_limit_depth() -> tuple[float, float]:
    """返回 (给定转速下的极限切深 ap_lim, 规划层降)。"""
    machine = get_machine_params("vmc_850")
    tool = ToolParams(
        diameter=PILOT_PARAMS["tool_diameter_mm"],
        num_flutes=PILOT_PARAMS["num_flutes"],
        cutting_force_coeff=PILOT_PARAMS["kienzle_kc1_1"],
    )
    params = ChatterParams(spindle_rpm=PILOT_PARAMS["spindle_rpm"], machine=machine, tool=tool)
    ap_lim = compute_stability_limit(params)
    return float(ap_lim), PILOT_PARAMS["stepdown_mm"]


def cutting_force_n() -> dict[str, float]:
    return compute_cutting_forces(
        PILOT_PARAMS["material"],
        width=PILOT_PARAMS["radial_depth_mm"],
        chip_thickness=PILOT_PARAMS["feed_per_tooth_mm"],
    )


# ---------------------------------------------------------------- 水线刀轨


def _section_polygons(mesh: trimesh.Trimesh, height: float) -> list[np.ndarray]:
    """某 Z 高度的闭合水线环顶点列表（用 Path2D.discrete，避免 shapely 依赖）。

    包含外环与孔环：孔环同样按轮廓槽加工，过小孔触发 PocketTooNarrowError
    被计数为 narrow_errors（3 轴口径下该孔确实需要更小刀具）。
    """
    section = mesh.section(plane_origin=[0, 0, height], plane_normal=[0, 0, 1])
    if section is None:
        return []
    to_2d = getattr(section, "to_planar", None) or getattr(section, "to_2D")
    path2, _ = to_2d()
    rings = []
    for ring in path2.discrete[: PILOT_PARAMS["waterline_max_rings_per_level"]]:
        ring = np.asarray(ring)
        if len(ring) >= 3 and np.allclose(ring[0], ring[-1]):
            ring = ring[:-1]
        if len(ring) >= 3:
            rings.append(ring[:, :2])
    return rings


def waterline_toolpath(mesh: trimesh.Trimesh) -> tuple[list[Any], dict[str, Any]]:
    """Z 分层外轮廓精修刀轨：返回 (所有 MillingToolpath, 统计)。"""
    z_min, z_max = (float(v) for v in mesh.bounds[:, 2])
    n_levels = PILOT_PARAMS["waterline_max_levels"]
    step = max((z_max - z_min) / n_levels, PILOT_PARAMS["stepdown_mm"])
    heights = np.arange(z_max - 0.5 * step, z_min - 1e-6, -step)
    engine = planar_engine.PlanarToolpathEngine(
        tool_diameter=PILOT_PARAMS["tool_diameter_mm"],
        feed_rate=600.0,
    )
    toolpaths: list[Any] = []
    stats = {"levels": 0, "rings": 0, "skipped_rings": 0, "narrow_errors": 0}
    for h in heights:
        rings = _section_polygons(mesh, float(h))
        stats["levels"] += 1
        stats["rings"] += len(rings)
        z_top, z_bot = float(h), float(max(h - step, z_min))
        for ring in rings:
            try:
                toolpaths.append(engine.profile(ring, z_top=z_top, z_bottom=z_bot))
            except (
                planar_engine.PocketTooNarrowError,
                getattr(planar_engine, "PlanarToolpathError", planar_engine.PocketTooNarrowError),
            ):
                # 小孔/退化环的刀具无法进入或偏置失败：跳过该环并计数
                stats["narrow_errors"] += 1
    return toolpaths, stats


def _shift_moves(toolpaths: list[Any]) -> tuple[list[dict], np.ndarray]:
    """把刀轨平移到 (20,20) 起步、Z 从 0 起；返回 (move dict 列表, 平移向量)。"""
    pts = np.array([[m.x, m.y, m.z] for tp in toolpaths for m in tp.moves])
    offset = np.array([_XY_ORIGIN_OFFSET[0], _XY_ORIGIN_OFFSET[1], 0.0]) - pts.min(axis=0)
    moves = [
        {"kind": m.kind, "x": m.x + offset[0], "y": m.y + offset[1], "z": m.z + offset[2], "feed": m.feed}
        for tp in toolpaths
        for m in tp.moves
    ]
    return moves, offset


def moves_to_gcode(moves: list[dict], rpm: int) -> tuple[str, list[ToolpathSegment]]:
    lines = ["G21 G90", "T1 M6", f"S{rpm} M3", f"G0 Z{_SAFE_Z}"]
    segments: list[ToolpathSegment] = []
    cur = np.array([moves[0]["x"], moves[0]["y"], _SAFE_Z]) if moves else np.zeros(3)
    block = 10
    for m in moves:
        target = np.array([m["x"], m["y"], m["z"]])
        if m["kind"] in ("rapid_z", "rapid_xy"):
            g, seg_type, feed = "G00", "rapid", None
        else:  # plunge / cut
            g, seg_type, feed = "G01", "linear", float(m["feed"] or 600.0)
        lines.append(f"{g} X{target[0]:.3f} Y{target[1]:.3f} Z{target[2]:.3f}" + (f" F{feed:.0f}" if feed else ""))
        segments.append(
            ToolpathSegment(
                type=seg_type,
                start_point=tuple(cur),
                end_point=tuple(target),
                feed_rate=feed,
                spindle_speed=rpm,
                tool_id=1,
                block_number=block,
                g_code=g,
            )
        )
        cur = target
        block += 1
    lines.append("M30")
    return "\n".join(lines), segments


# ---------------------------------------------------------------- S5 主流程


def run_s5(mesh: trimesh.Trimesh, work_dir: Path) -> dict[str, Any]:
    """对单个模型跑完整 S5，产物（刀轨/G 代码/仿真 STL）落 work_dir。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"stages": {}, "errors": []}

    # 1. 颤振与力（解析解，模型无关，逐案记录以便单案失败可追溯）
    try:
        ap_lim, ap_planned = chatter_limit_depth()
        forces = cutting_force_n()
        result["stages"]["chatter"] = {
            "ap_lim_mm": round(ap_lim, 3),
            "ap_planned_mm": ap_planned,
            "ok": bool(ap_lim >= ap_planned),
        }
        result["stages"]["force"] = {
            **{k: round(v, 1) for k, v in forces.items()},
            "limit_n": PILOT_PARAMS["force_limit_n"],
            "ok": bool(forces["Fz"] <= PILOT_PARAMS["force_limit_n"]),
        }
    except Exception as exc:
        result["errors"].append(f"physics_prep: {type(exc).__name__}: {exc}")
        result["stages"]["chatter"] = {"ok": None}
        result["stages"]["force"] = {"ok": None}

    # 2. 水线刀轨 + G 代码 + 运动学
    gcode_text, segments = None, None
    try:
        toolpaths, wl_stats = waterline_toolpath(mesh)
        result["stages"]["waterline"] = {**wl_stats, "ok": bool(wl_stats["rings"] > 0)}
        if wl_stats["rings"] == 0:
            result["errors"].append("waterline: 无可用轮廓")
        else:
            moves, _ = _shift_moves(toolpaths)
            gcode_text, segments = moves_to_gcode(moves, int(PILOT_PARAMS["spindle_rpm"]))
            (work_dir / "toolpath.nc").write_text(gcode_text, encoding="utf-8")
            validator = KinematicsValidator()
            report = validator.validate(gcode_text)
            result["stages"]["kinematics"] = {
                "passed": bool(report.passed),
                "ok": bool(report.passed),
                "issues": [f"{i.code}:{i.message}" for i in report.issues[:10]],
            }
    except Exception as exc:
        result["stages"]["waterline"] = {"ok": False}
        result["errors"].append(f"toolpath_kinematics: {type(exc).__name__}: {exc}")
        result["stages"]["kinematics"] = {"passed": None}

    # 3. 体素切削（毛坯 = bbox + 2mm）
    try:
        if segments is None:
            raise RuntimeError("无刀轨段，跳过体素仿真")
        bounds = mesh.bounds
        size = bounds[1] - bounds[0] + 4.0
        center = (bounds[0] + bounds[1]) / 2
        stock = (
            cq.Workplane("XY")
            .box(float(size[0]), float(size[1]), float(size[2]))
            .translate((float(center[0]), float(center[1]), float(center[2])))
        )
        stock_stl = work_dir / "stock.stl"
        cq.exporters.export(stock, str(stock_stl), tolerance=0.05, angularTolerance=0.2)
        sim = VoxelCutter(voxel_size=PILOT_PARAMS["voxel_size_mm"]).run_simulation(
            stock_stl_path=stock_stl,
            tool=ToolModel(diameter=PILOT_PARAMS["tool_diameter_mm"], flute_count=PILOT_PARAMS["num_flutes"]),
            segments=segments,
            output_dir=work_dir,
        )
        sim_dict = sim.to_dict() if hasattr(sim, "to_dict") else vars(sim)
        result["stages"]["voxel_sim"] = {
            "collision": bool(sim_dict.get("collision", {}).get("collided", False))
            if isinstance(sim_dict.get("collision"), dict)
            else bool(sim_dict.get("collision", False)),
            "removed_voxels": sim_dict.get("removed_voxel_count"),
            "duration_s": sim_dict.get("duration_seconds"),
            "ok": not (
                sim_dict.get("collision", {}).get("collided", False)
                if isinstance(sim_dict.get("collision"), dict)
                else sim_dict.get("collision", False)
            ),
        }
    except Exception as exc:
        result["stages"]["voxel_sim"] = {"ok": None}
        result["errors"].append(f"voxel_sim: {type(exc).__name__}: {exc}")

    def ok_of(stage: str) -> bool | None:
        value = result["stages"].get(stage, {})
        return value.get("ok") if isinstance(value, dict) else None

    result["machinable_3axis"] = all(ok_of(s) is True for s in ("chatter", "force", "kinematics", "voxel_sim"))
    return result


def export_case_stl_copy(source: Path, work_dir: Path) -> Path:
    target = work_dir / "model.stl"
    shutil.copyfile(source, target)
    return target
