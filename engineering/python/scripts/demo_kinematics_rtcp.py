"""XM-100 五轴运动学与 RTCP 补偿算法展示脚本。

本脚本演示 XM-100 桌面五轴加工中心（工作台型 A+C 轴）的运动学求解
与 RTCP（旋转刀具中心点）补偿能力，是五轴加工的核心算法基础。

展示场景：
    1. 运动学正解：给定机床轴位置，计算工件坐标系中的刀尖位置和刀轴方向
    2. 运动学逆解：给定工件坐标系中目标位置和刀轴方向，反解机床轴位置
    3. RTCP 补偿：A/C 轴旋转时 X/Y/Z 的补偿量变化（保持刀尖位置不变）
    4. 奇异点警告：A 轴接近 ±90° 时的奇异点检测
    5. 多姿态扫描：扫描 A/C 轴角度，验证工作空间覆盖与轴限

输出：
    - 控制台对比表格
    - JSON 报告：python/output/xm100_demo/kinematics_rtcp_report.json
    - Markdown 报告：python/output/xm100_demo/kinematics_rtcp_report.md

注意：
    - XM-100 行程：X/Y/Z = 0-100mm，A = -30°~110°，C = 0°~360°
    - 工作台型五轴：工件随工作台旋转/倾斜，刀具固定向下
    - RTCP 通过反向移动 X/Y/Z 补偿工作台旋转引起的刀触点偏移
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, asdict, field
from typing import Any

import numpy as np

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app.simulation.kinematics import (
    XM100Kinematics,
    XM100_LIMITS,
    rot_x,
    rot_z,
)


# 数据模型


@dataclass
class ForwardResult:
    """正解结果。"""

    x: float
    y: float
    z: float
    a_deg: float
    c_deg: float
    tool_axis_in_wp: list[float]
    point_in_machine: list[float] | None = None


@dataclass
class InverseResult:
    """逆解结果。"""

    target_position: list[float]
    tool_axis_direction: list[float]
    solved_x: float
    solved_y: float
    solved_z: float
    solved_a: float
    solved_c: float
    feasible: bool
    note: str = ""


@dataclass
class RTCPStep:
    """RTCP 补偿单步结果。"""

    step: int
    a_deg: float
    c_deg: float
    comp_x: float
    comp_y: float
    comp_z: float
    new_x: float
    new_y: float
    new_z: float


@dataclass
class SingularityCheck:
    """奇异点检查结果。"""

    a_deg: float
    is_singular: bool
    warning: str


@dataclass
class WorkspaceScanPoint:
    """工作空间扫描点。"""

    a_deg: float
    c_deg: float
    feasible: bool
    solved_x: float | None = None
    solved_y: float | None = None
    solved_z: float | None = None
    warning: str = ""


@dataclass
class KinematicsReport:
    """运动学展示报告。"""

    generated_at: str
    machine_limits: dict[str, float]
    forward_results: list[dict[str, Any]]
    inverse_results: list[dict[str, Any]]
    rtcp_compensation: list[dict[str, Any]]
    singularity_checks: list[dict[str, Any]]
    workspace_scan: list[dict[str, Any]]
    summary: dict[str, Any] = field(default_factory=dict)


# 场景1：运动学正解


def run_forward_demo(kin: XM100Kinematics) -> list[ForwardResult]:
    """正解演示：给定机床轴位置，计算刀轴方向和工件点变换。"""
    print("\n" + "=" * 70)
    print("场景1：运动学正解（机床轴 → 工件坐标系刀轴方向）")
    print("=" * 70)

    # 典型加工姿态
    cases = [
        # (x, y, z, A, C, 描述)
        (50.0, 50.0, 50.0, 0.0, 0.0, "初始姿态（A=0, C=0）"),
        (50.0, 50.0, 50.0, 30.0, 0.0, "A轴倾斜30°（侧铣）"),
        (50.0, 50.0, 50.0, 90.0, 0.0, "A轴水平（水平铣削）"),
        (50.0, 50.0, 50.0, 0.0, 45.0, "C轴旋转45°"),
        (50.0, 50.0, 50.0, 30.0, 45.0, "A=30° C=45° 复合姿态"),
        (50.0, 50.0, 50.0, 90.0, 90.0, "A=90° C=90° 极限姿态"),
    ]

    results: list[ForwardResult] = []
    print(f"\n{'描述':<28} {'A':>6} {'C':>6} {'刀轴(i)':>9} {'刀轴(j)':>9} {'刀轴(k)':>9}")
    print("-" * 80)

    for x, y, z, a, c, desc in cases:
        # 工件坐标系原点
        point_wp = np.array([10.0, 10.0, 0.0])
        fwd = kin.forward(x, y, z, a, c, point_in_workpiece=point_wp)

        axis = fwd["tool_axis_in_wp"]
        pim = fwd.get("point_in_machine")

        result = ForwardResult(
            x=x,
            y=y,
            z=z,
            a_deg=a,
            c_deg=c,
            tool_axis_in_wp=[float(v) for v in axis],
            point_in_machine=[float(v) for v in pim] if pim is not None else None,
        )
        results.append(result)

        print(f"{desc:<28} {a:>6.1f} {c:>6.1f} {axis[0]:>9.4f} {axis[1]:>9.4f} {axis[2]:>9.4f}")

    print(f"\n共 {len(results)} 个正解案例")
    return results


# 场景2：运动学逆解


def run_inverse_demo(kin: XM100Kinematics) -> list[InverseResult]:
    """逆解演示：给定工件坐标系中目标位置和刀轴方向，反解机床轴位置。"""
    print("\n" + "=" * 70)
    print("场景2：运动学逆解（工件坐标系目标 → 机床轴位置）")
    print("=" * 70)

    cases = [
        # (target_pos, tool_axis, 描述)
        ([20.0, 20.0, 50.0], [0.0, 0.0, -1.0], "垂直向下铣削（A=0, C=0）"),
        ([30.0, 30.0, 50.0], [0.0, -0.5, -0.866], "A=30° 侧铣（刀轴 YZ 平面）"),
        ([40.0, 40.0, 60.0], [0.0, -0.707, -0.707], "A=45° 侧铣"),
        ([50.0, 50.0, 80.0], [0.0, -1.0, 0.0], "A=90° 水平铣削"),
        ([20.0, 20.0, 50.0], [0.5, -0.5, -0.707], "A=45° C=45° 复合"),
        ([60.0, 60.0, 50.0], [0.0, 0.8, 0.6], "刀轴向上（不可达）"),
    ]

    results: list[InverseResult] = []
    print(f"\n{'描述':<32} {'目标位':>18} {'刀轴':>22} {'可行':>5} {'A':>7} {'C':>7}")
    print("-" * 100)

    for target, axis, desc in cases:
        solved = kin.inverse(np.array(target), np.array(axis))
        if solved is not None:
            result = InverseResult(
                target_position=list(target),
                tool_axis_direction=list(axis),
                solved_x=solved["x"],
                solved_y=solved["y"],
                solved_z=solved["z"],
                solved_a=solved["a"],
                solved_c=solved["c"],
                feasible=True,
                note="在机床行程内",
            )
            print(
                f"{desc:<32} "
                f"({target[0]:.0f},{target[1]:.0f},{target[2]:.0f}) "
                f"({axis[0]:.2f},{axis[1]:.2f},{axis[2]:.2f}) "
                f"{'是':>5} {solved['a']:>7.2f} {solved['c']:>7.2f}"
            )
        else:
            result = InverseResult(
                target_position=list(target),
                tool_axis_direction=list(axis),
                solved_x=0,
                solved_y=0,
                solved_z=0,
                solved_a=0,
                solved_c=0,
                feasible=False,
                note="超出机床行程或A轴范围",
            )
            print(
                f"{desc:<32} "
                f"({target[0]:.0f},{target[1]:.0f},{target[2]:.0f}) "
                f"({axis[0]:.2f},{axis[1]:.2f},{axis[2]:.2f}) "
                f"{'否':>5} {'N/A':>7} {'N/A':>7}"
            )

        results.append(result)

    feasible_count = sum(1 for r in results if r.feasible)
    print(f"\n共 {len(results)} 个逆解案例，{feasible_count} 个可行，{len(results) - feasible_count} 个不可行")
    return results


# 场景3：RTCP 补偿


def run_rtcp_demo(kin: XM100Kinematics) -> list[RTCPStep]:
    """RTCP 补偿演示：A/C 轴连续旋转时 X/Y/Z 的补偿量。"""
    print("\n" + "=" * 70)
    print("场景3：RTCP 补偿（A/C 轴旋转时 X/Y/Z 补偿量）")
    print("=" * 70)

    # 初始位置：刀尖对准工件上的点 (20, 20, 0)
    init_x, init_y, init_z = 20.0, 20.0, 0.0
    # 刀触点在工件坐标系中的位置（RTCP 围绕该点旋转）
    tool_contact_point = [20.0, 20.0, 0.0]

    # 模拟 A 轴从 0° 旋转到 60°（10° 步进）
    print(f"\n初始位置：X={init_x}, Y={init_y}, Z={init_z}")
    print(f"刀触点（工件坐标系）：{tool_contact_point}")
    print("\n--- A 轴旋转扫描（C=0° 固定）---")
    print(f"{'步':>3} {'A°':>7} {'C°':>7} {'ΔX':>9} {'ΔY':>9} {'ΔZ':>9} {'新X':>8} {'新Y':>8} {'新Z':>8}")
    print("-" * 90)

    steps: list[RTCPStep] = []
    for i, a_deg in enumerate([0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0]):
        c_deg = 0.0  # 固定 C 轴
        new_x, new_y, new_z = kin.rtcp_compensate(
            init_x,
            init_y,
            init_z,
            a_deg,
            c_deg,
            current_a=0.0,
            current_c=0.0,
            tool_contact_point=tool_contact_point,
        )
        dx = new_x - init_x
        dy = new_y - init_y
        dz = new_z - init_z

        step = RTCPStep(
            step=i,
            a_deg=a_deg,
            c_deg=c_deg,
            comp_x=dx,
            comp_y=dy,
            comp_z=dz,
            new_x=new_x,
            new_y=new_y,
            new_z=new_z,
        )
        steps.append(step)
        print(
            f"{i:>3} {a_deg:>7.1f} {c_deg:>7.1f} "
            f"{dx:>9.4f} {dy:>9.4f} {dz:>9.4f} "
            f"{new_x:>8.3f} {new_y:>8.3f} {new_z:>8.3f}"
        )

    # C 轴旋转扫描
    print("\n--- C 轴旋转扫描（A=30° 固定，从 A=0/C=0 起始）---")
    print(f"{'步':>3} {'A°':>7} {'C°':>7} {'ΔX':>9} {'ΔY':>9} {'ΔZ':>9} {'新X':>8} {'新Y':>8} {'新Z':>8}")
    print("-" * 90)

    for i, c_deg in enumerate([0.0, 30.0, 60.0, 90.0, 120.0, 150.0, 180.0]):
        a_deg = 30.0
        new_x, new_y, new_z = kin.rtcp_compensate(
            init_x,
            init_y,
            init_z,
            a_deg,
            c_deg,
            current_a=0.0,
            current_c=0.0,
            tool_contact_point=tool_contact_point,
        )
        dx = new_x - init_x
        dy = new_y - init_y
        dz = new_z - init_z

        step = RTCPStep(
            step=i + 100,
            a_deg=a_deg,
            c_deg=c_deg,
            comp_x=dx,
            comp_y=dy,
            comp_z=dz,
            new_x=new_x,
            new_y=new_y,
            new_z=new_z,
        )
        steps.append(step)
        print(
            f"{i + 100:>3} {a_deg:>7.1f} {c_deg:>7.1f} "
            f"{dx:>9.4f} {dy:>9.4f} {dz:>9.4f} "
            f"{new_x:>8.3f} {new_y:>8.3f} {new_z:>8.3f}"
        )

    # 复合旋转扫描
    print("\n--- 复合旋转扫描（A 和 C 同时变化）---")
    print(f"{'步':>3} {'A°':>7} {'C°':>7} {'ΔX':>9} {'ΔY':>9} {'ΔZ':>9} {'新X':>8} {'新Y':>8} {'新Z':>8}")
    print("-" * 90)

    complex_cases = [
        (15.0, 30.0),
        (30.0, 60.0),
        (45.0, 90.0),
        (60.0, 120.0),
        (45.0, 180.0),
        (30.0, 270.0),
    ]
    for i, (a_deg, c_deg) in enumerate(complex_cases):
        new_x, new_y, new_z = kin.rtcp_compensate(
            init_x,
            init_y,
            init_z,
            a_deg,
            c_deg,
            current_a=0.0,
            current_c=0.0,
            tool_contact_point=tool_contact_point,
        )
        dx = new_x - init_x
        dy = new_y - init_y
        dz = new_z - init_z

        step = RTCPStep(
            step=i + 200,
            a_deg=a_deg,
            c_deg=c_deg,
            comp_x=dx,
            comp_y=dy,
            comp_z=dz,
            new_x=new_x,
            new_y=new_y,
            new_z=new_z,
        )
        steps.append(step)
        print(
            f"{i + 200:>3} {a_deg:>7.1f} {c_deg:>7.1f} "
            f"{dx:>9.4f} {dy:>9.4f} {dz:>9.4f} "
            f"{new_x:>8.3f} {new_y:>8.3f} {new_z:>8.3f}"
        )

    print(f"\n共 {len(steps)} 个 RTCP 补偿步骤")
    return steps


# 场景4：奇异点检查


def run_singularity_demo(kin: XM100Kinematics) -> list[SingularityCheck]:
    """奇异点检查演示。"""
    print("\n" + "=" * 70)
    print("场景4：奇异点检查（A 轴接近 ±90° 时 C 轴失效）")
    print("=" * 70)

    a_angles = [-90.0, -85.0, -80.0, -30.0, 0.0, 30.0, 80.0, 85.0, 90.0, 95.0]
    results: list[SingularityCheck] = []

    print(f"\n{'A°':>7} {'奇异':>5} {'警告':<50}")
    print("-" * 70)
    for a in a_angles:
        warnings = kin.check_singularity(a)
        is_singular = len(warnings) > 0
        warning = warnings[0] if warnings else "正常"
        result = SingularityCheck(a_deg=a, is_singular=is_singular, warning=warning)
        results.append(result)
        print(f"{a:>7.1f} {'是' if is_singular else '否':>5} {warning:<50}")

    return results


# 场景5：工作空间扫描


def run_workspace_scan(kin: XM100Kinematics) -> list[WorkspaceScanPoint]:
    """扫描 A/C 轴组合，验证工作空间覆盖。

    直接用正解计算给定 A/C 姿态下刀触点的机床坐标，
    检查是否在 X/Y/Z 行程内。这能正确反映不同 A/C 姿态的可达性，
    避免"通过构造刀轴方向再调用逆解"导致的 C 轴信息丢失问题。
    """
    print("\n" + "=" * 70)
    print("场景5：工作空间扫描（A/C 组合 → 刀触点机床坐标可达性）")
    print("=" * 70)

    # 固定刀触点（工件坐标系），扫描 A/C 组合看哪些姿态可达
    target_pos = np.array([20.0, 20.0, 50.0])

    # 生成 A/C 组合
    a_values = [-30.0, 0.0, 30.0, 60.0, 90.0, 110.0]
    c_values = [0.0, 45.0, 90.0, 135.0, 180.0, 270.0]

    results: list[WorkspaceScanPoint] = []
    feasible_count = 0

    print(f"\n刀触点（工件坐标系）：({target_pos[0]}, {target_pos[1]}, {target_pos[2]})")
    print(f"{'A°':>7} {'C°':>7} {'可行':>5} {'X':>8} {'Y':>8} {'Z':>8} {'警告':<30}")
    print("-" * 80)

    for a in a_values:
        for c in c_values:
            # 直接用正解计算机床坐标：machine_pos = R_wc @ target_pos
            R_wc = rot_z(c) @ rot_x(a)
            machine_pos = R_wc @ target_pos
            x_val = float(machine_pos[0])
            y_val = float(machine_pos[1])
            z_val = float(machine_pos[2])

            # 检查轴限
            warnings = kin.check_limits(x_val, y_val, z_val, a, c)
            if not warnings:
                point = WorkspaceScanPoint(
                    a_deg=a,
                    c_deg=c,
                    feasible=True,
                    solved_x=x_val,
                    solved_y=y_val,
                    solved_z=z_val,
                )
                feasible_count += 1
                print(f"{a:>7.1f} {c:>7.1f} {'是':>5} {x_val:>8.2f} {y_val:>8.2f} {z_val:>8.2f} {'':<30}")
            else:
                point = WorkspaceScanPoint(
                    a_deg=a,
                    c_deg=c,
                    feasible=False,
                    warning=warnings[0],
                )
                print(f"{a:>7.1f} {c:>7.1f} {'否':>5} {'N/A':>8} {'N/A':>8} {'N/A':>8} {warnings[0]:<30}")

            results.append(point)

    total = len(results)
    print(f"\n共扫描 {total} 个 A/C 组合，{feasible_count} 个可行 ({100 * feasible_count / total:.1f}%)")
    return results


# 报告生成


def build_report(
    forward_results: list[ForwardResult],
    inverse_results: list[InverseResult],
    rtcp_steps: list[RTCPStep],
    singularity_checks: list[SingularityCheck],
    workspace_scan: list[WorkspaceScanPoint],
) -> KinematicsReport:
    """构建完整报告。"""
    feasible_inverse = sum(1 for r in inverse_results if r.feasible)
    feasible_workspace = sum(1 for r in workspace_scan if r.feasible)

    # RTCP 补偿范围统计
    a_scan_steps = [s for s in rtcp_steps if s.step < 100]
    c_scan_steps = [s for s in rtcp_steps if 100 <= s.step < 200]
    complex_scan_steps = [s for s in rtcp_steps if s.step >= 200]

    def _max_comp(steps_list):
        if not steps_list:
            return {"max_dx": 0, "max_dy": 0, "max_dz": 0}
        return {
            "max_dx": max(abs(s.comp_x) for s in steps_list),
            "max_dy": max(abs(s.comp_y) for s in steps_list),
            "max_dz": max(abs(s.comp_z) for s in steps_list),
        }

    rtcp_a_range = _max_comp(a_scan_steps)
    rtcp_c_range = _max_comp(c_scan_steps)
    rtcp_complex_range = _max_comp(complex_scan_steps)

    return KinematicsReport(
        generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        machine_limits={
            "x_range": f"{XM100_LIMITS.x_min}-{XM100_LIMITS.x_max} mm",
            "y_range": f"{XM100_LIMITS.y_min}-{XM100_LIMITS.y_max} mm",
            "z_range": f"{XM100_LIMITS.z_min}-{XM100_LIMITS.z_max} mm",
            "a_range": f"{XM100_LIMITS.a_min}~{XM100_LIMITS.a_max}°",
            "c_range": f"{XM100_LIMITS.c_min}~{XM100_LIMITS.c_max}°",
            "max_spindle_rpm": XM100_LIMITS.max_spindle_rpm,
            "max_feed_mm_min": XM100_LIMITS.max_feed_mm_min,
        },
        forward_results=[asdict(r) for r in forward_results],
        inverse_results=[asdict(r) for r in inverse_results],
        rtcp_compensation=[asdict(s) for s in rtcp_steps],
        singularity_checks=[asdict(s) for s in singularity_checks],
        workspace_scan=[asdict(p) for p in workspace_scan],
        summary={
            "forward_cases": len(forward_results),
            "inverse_cases": len(inverse_results),
            "inverse_feasible": feasible_inverse,
            "inverse_infeasible": len(inverse_results) - feasible_inverse,
            "rtcp_steps": len(rtcp_steps),
            "rtcp_a_scan_max_compensation_mm": rtcp_a_range,
            "rtcp_c_scan_max_compensation_mm": rtcp_c_range,
            "rtcp_complex_scan_max_compensation_mm": rtcp_complex_range,
            "singularity_cases": len(singularity_checks),
            "singularity_detected": sum(1 for s in singularity_checks if s.is_singular),
            "workspace_scan_total": len(workspace_scan),
            "workspace_scan_feasible": feasible_workspace,
            "workspace_coverage_pct": round(100 * feasible_workspace / len(workspace_scan), 1) if workspace_scan else 0,
        },
    )


def write_json_report(report: KinematicsReport, output_path: str) -> None:
    """写 JSON 报告。"""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(asdict(report), f, ensure_ascii=False, indent=2)


def write_markdown_report(report: KinematicsReport, output_path: str) -> None:
    """写 Markdown 报告。"""
    lines: list[str] = []
    lines.append("# XM-100 五轴运动学与 RTCP 补偿算法报告\n")
    lines.append(f"**生成时间**：{report.generated_at}\n")
    lines.append("\n## 1. 机床参数\n")
    lines.append("| 参数 | 值 |")
    lines.append("| --- | --- |")
    for k, v in report.machine_limits.items():
        lines.append(f"| {k} | {v} |")

    lines.append("\n## 2. 运动学正解\n")
    lines.append(f"共 {report.summary['forward_cases']} 个正解案例。\n")
    lines.append("| A(°) | C(°) | 刀轴i | 刀轴j | 刀轴k |")
    lines.append("| --- | --- | --- | --- | --- |")
    for r in report.forward_results:
        ax = r["tool_axis_in_wp"]
        lines.append(f"| {r['a_deg']:.1f} | {r['c_deg']:.1f} | {ax[0]:.4f} | {ax[1]:.4f} | {ax[2]:.4f} |")

    lines.append("\n## 3. 运动学逆解\n")
    lines.append(
        f"共 {report.summary['inverse_cases']} 个案例，"
        f"可行 {report.summary['inverse_feasible']} 个，"
        f"不可行 {report.summary['inverse_infeasible']} 个。\n"
    )
    lines.append("| 描述 | 目标位置 | 刀轴方向 | 可行 | X | Y | Z | A(°) | C(°) |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in report.inverse_results:
        tp = r["target_position"]
        ax = r["tool_axis_direction"]
        if r["feasible"]:
            lines.append(
                f"| - | ({tp[0]:.0f},{tp[1]:.0f},{tp[2]:.0f}) | "
                f"({ax[0]:.2f},{ax[1]:.2f},{ax[2]:.2f}) | 是 | "
                f"{r['solved_x']:.2f} | {r['solved_y']:.2f} | {r['solved_z']:.2f} | "
                f"{r['solved_a']:.2f} | {r['solved_c']:.2f} |"
            )
        else:
            lines.append(
                f"| - | ({tp[0]:.0f},{tp[1]:.0f},{tp[2]:.0f}) | "
                f"({ax[0]:.2f},{ax[1]:.2f},{ax[2]:.2f}) | 否 | "
                f"- | - | - | - | - |"
            )

    lines.append("\n## 4. RTCP 补偿\n")
    lines.append(f"共 {report.summary['rtcp_steps']} 个补偿步骤。\n")
    lines.append("### 4.1 A 轴扫描（C=0° 固定）\n")
    lines.append("| A(°) | ΔX(mm) | ΔY(mm) | ΔZ(mm) | 新X | 新Y | 新Z |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for s in report.rtcp_compensation:
        if s["step"] < 100:
            lines.append(
                f"| {s['a_deg']:.1f} | {s['comp_x']:.4f} | "
                f"{s['comp_y']:.4f} | {s['comp_z']:.4f} | "
                f"{s['new_x']:.3f} | {s['new_y']:.3f} | {s['new_z']:.3f} |"
            )

    lines.append("\n### 4.2 C 轴扫描（A=30° 固定，从 A=0/C=0 起始）\n")
    lines.append("| A(°) | C(°) | ΔX(mm) | ΔY(mm) | ΔZ(mm) | 新X | 新Y | 新Z |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for s in report.rtcp_compensation:
        if 100 <= s["step"] < 200:
            lines.append(
                f"| {s['a_deg']:.1f} | {s['c_deg']:.1f} | "
                f"{s['comp_x']:.4f} | {s['comp_y']:.4f} | {s['comp_z']:.4f} | "
                f"{s['new_x']:.3f} | {s['new_y']:.3f} | {s['new_z']:.3f} |"
            )

    lines.append("\n### 4.3 复合旋转扫描（A 和 C 同时变化）\n")
    lines.append("| A(°) | C(°) | ΔX(mm) | ΔY(mm) | ΔZ(mm) | 新X | 新Y | 新Z |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for s in report.rtcp_compensation:
        if s["step"] >= 200:
            lines.append(
                f"| {s['a_deg']:.1f} | {s['c_deg']:.1f} | "
                f"{s['comp_x']:.4f} | {s['comp_y']:.4f} | {s['comp_z']:.4f} | "
                f"{s['new_x']:.3f} | {s['new_y']:.3f} | {s['new_z']:.3f} |"
            )

    a_max = report.summary["rtcp_a_scan_max_compensation_mm"]
    c_max = report.summary["rtcp_c_scan_max_compensation_mm"]
    cx_max = report.summary["rtcp_complex_scan_max_compensation_mm"]
    lines.append(
        f"\n**A轴扫描最大补偿量**：ΔX={a_max['max_dx']:.4f}mm, ΔY={a_max['max_dy']:.4f}mm, ΔZ={a_max['max_dz']:.4f}mm"
    )
    lines.append(
        f"\n**C轴扫描最大补偿量**：ΔX={c_max['max_dx']:.4f}mm, ΔY={c_max['max_dy']:.4f}mm, ΔZ={c_max['max_dz']:.4f}mm"
    )
    lines.append(
        f"\n**复合扫描最大补偿量**：ΔX={cx_max['max_dx']:.4f}mm, "
        f"ΔY={cx_max['max_dy']:.4f}mm, ΔZ={cx_max['max_dz']:.4f}mm"
    )

    lines.append("\n## 5. 奇异点检查\n")
    lines.append(
        f"共 {report.summary['singularity_cases']} 个检查点，奇异 {report.summary['singularity_detected']} 个。\n"
    )
    lines.append("| A(°) | 奇异 | 警告 |")
    lines.append("| --- | --- | --- |")
    for s in report.singularity_checks:
        lines.append(f"| {s['a_deg']:.1f} | {'是' if s['is_singular'] else '否'} | {s['warning']} |")

    lines.append("\n## 6. 工作空间扫描\n")
    lines.append(
        f"共扫描 {report.summary['workspace_scan_total']} 个 A/C 组合，"
        f"可行 {report.summary['workspace_scan_feasible']} 个 "
        f"({report.summary['workspace_coverage_pct']:.1f}%)。\n"
    )
    lines.append("| A(°) | C(°) | 可行 | X | Y | Z | 警告 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for p in report.workspace_scan:
        if p["feasible"]:
            lines.append(
                f"| {p['a_deg']:.1f} | {p['c_deg']:.1f} | 是 | "
                f"{p['solved_x']:.2f} | {p['solved_y']:.2f} | {p['solved_z']:.2f} | - |"
            )
        else:
            lines.append(f"| {p['a_deg']:.1f} | {p['c_deg']:.1f} | 否 | - | - | - | {p['warning']} |")

    lines.append("\n## 7. 总结\n")
    s = report.summary
    lines.append(f"- 正解案例：{s['forward_cases']} 个")
    lines.append(
        f"- 逆解案例：{s['inverse_cases']} 个 (可行 {s['inverse_feasible']} / 不可行 {s['inverse_infeasible']})"
    )
    lines.append(f"- RTCP 补偿步骤：{s['rtcp_steps']} 个")
    lines.append(f"- 奇异点检查：{s['singularity_cases']} 个 (奇异 {s['singularity_detected']} 个)")
    lines.append(f"- 工作空间扫描：{s['workspace_scan_total']} 个组合，覆盖率 {s['workspace_coverage_pct']:.1f}%")
    lines.append("\n---")
    lines.append("\n*本报告由 XM-100 五轴运动学展示脚本自动生成。*")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# 主入口


def main() -> int:
    print("=" * 70)
    print("XM-100 五轴运动学与 RTCP 补偿算法展示")
    print("=" * 70)
    print("机床行程：X/Y/Z = 0-100mm, A = -30~110°, C = 0~360°")
    print(f"主轴最高：{XM100_LIMITS.max_spindle_rpm:.0f} RPM")
    print(f"最大进给：{XM100_LIMITS.max_feed_mm_min:.0f} mm/min")

    kin = XM100Kinematics()

    # 运行所有场景
    forward_results = run_forward_demo(kin)
    inverse_results = run_inverse_demo(kin)
    rtcp_steps = run_rtcp_demo(kin)
    singularity_checks = run_singularity_demo(kin)
    workspace_scan = run_workspace_scan(kin)

    # 构建报告
    report = build_report(
        forward_results,
        inverse_results,
        rtcp_steps,
        singularity_checks,
        workspace_scan,
    )

    # 输出报告
    output_dir = os.path.join(_PROJECT_ROOT, "output", "xm100_demo")
    os.makedirs(output_dir, exist_ok=True)

    json_path = os.path.join(output_dir, "kinematics_rtcp_report.json")
    md_path = os.path.join(output_dir, "kinematics_rtcp_report.md")

    write_json_report(report, json_path)
    write_markdown_report(report, md_path)

    print("\n" + "=" * 70)
    print("报告已生成：")
    print(f"  JSON: {json_path}")
    print(f"  Markdown: {md_path}")
    print("=" * 70)

    # 总结
    s = report.summary
    print("\n总结：")
    print(f"  正解案例：{s['forward_cases']} 个")
    print(f"  逆解案例：{s['inverse_cases']} 个 (可行 {s['inverse_feasible']} / 不可行 {s['inverse_infeasible']})")
    print(f"  RTCP 补偿步骤：{s['rtcp_steps']} 个")
    print(f"  奇异点检查：{s['singularity_cases']} 个 (奇异 {s['singularity_detected']} 个)")
    print(f"  工作空间扫描：{s['workspace_scan_total']} 个组合，覆盖率 {s['workspace_coverage_pct']:.1f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
