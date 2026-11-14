"""切削参数推荐的稳定性证据（B1）.

为每条推荐参数附带可核查的稳定性依据，拒绝"只给数不给据"：

- ``limit_depth_mm``：Tlusty 1-DOF 绝对稳定极限切深（与转速无关的包络）；
- ``margin_ratio``：``limit_depth / axial_depth``，越大越安全；
- ``status``：``ok``（≥1.5）/ ``marginal``（1.0–1.5）/ ``violated``（<1.0）；
- ``lobe_sample``：叶瓣离散采样的最低点与少量点列（供前端画图/核对）。

计算失败（模块缺失、参数非法）时返回 ``status="unavailable"`` 并写明原因，
**不伪造数值**。所有口径与 ``app.simulation.chatter.stability`` 一致；
本模块只做组织与采样降噪，不改变稳定性物理模型。

边界：Tlusty 1-DOF、单模态、不含径向浸入增益；证据用于工程师复核，
不构成上机许可。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 工程闸值：裕度 = 极限切深 / 实际轴向切深
MARGIN_OK = 1.5
MARGIN_MARGINAL = 1.0

# 叶瓣采样：控制单次推荐的耗时（每条推荐都算）
LOBE_POINTS = 41
LOBE_COUNT = 2
LOBE_SAMPLE_STRIDE = 4


def _status_from_margin(margin: float) -> str:
    if margin >= MARGIN_OK:
        return "ok"
    if margin >= MARGIN_MARGINAL:
        return "marginal"
    return "violated"


def build_stability_evidence(
    *,
    machine_type: str,
    tool_diameter_mm: float,
    num_flutes: int,
    spindle_rpm: float,
    axial_depth_mm: float,
) -> dict[str, Any]:
    """构造稳定性证据字典（JSON 安全）。"""
    try:
        from app.simulation.chatter.stability import (
            ChatterParams,
            ToolParams,
            compute_stability_limit,
            compute_stability_lobe,
            get_machine_params,
        )
    except ImportError as exc:  # pragma: no cover - 依赖缺失时的降级分支
        return {"status": "unavailable", "reason": f"颤振稳定性模块不可用: {exc}"}

    try:
        machine = get_machine_params(machine_type)
        tool = ToolParams(diameter=float(tool_diameter_mm), num_flutes=int(num_flutes))
        params = ChatterParams(
            spindle_rpm=float(spindle_rpm),
            machine=machine,
            tool=tool,
            axial_depth=float(axial_depth_mm),
        )
        limit_depth = float(compute_stability_limit(params))
    except (ValueError, TypeError, ZeroDivisionError) as exc:
        return {"status": "unavailable", "reason": f"稳定性计算参数非法: {exc}"}

    margin = limit_depth / axial_depth_mm if axial_depth_mm > 0 else float("inf")
    evidence: dict[str, Any] = {
        "status": _status_from_margin(margin),
        "limit_depth_mm": round(limit_depth, 3),
        "axial_depth_mm": round(float(axial_depth_mm), 3),
        "margin_ratio": round(margin, 3),
        "machine_id": machine_type,
        "machine_params": {
            "natural_freq_hz": round(float(machine.natural_freq), 2),
            "damping_ratio": round(float(machine.damping_ratio), 4),
            "stiffness_z_n_per_m": float(machine.stiffness_z),
        },
        "method": "Tlusty 1-DOF 绝对包络（app.simulation.chatter.stability.compute_stability_limit）",
        "caveats": ["单模态 1-DOF，未计径向浸入增益", "证据用于工程师复核，不构成上机许可"],
    }

    # 叶瓣采样：仅在接近边界时提供（远处无决策价值，且控制耗时）
    if evidence["status"] != "ok":
        try:
            speed_low = max(500.0, 0.5 * float(spindle_rpm))
            speed_high = max(speed_low + 500.0, 1.5 * float(spindle_rpm))
            lobe = compute_stability_lobe(
                machine,
                tool,
                speed_range=(speed_low, speed_high),
                num_points=LOBE_POINTS,
                num_lobes=LOBE_COUNT,
            )
            points: list[list[float]] = []
            min_depth = float("inf")
            for speeds, depths in lobe.get("lobes", []):
                for index, (speed, depth) in enumerate(zip(speeds, depths)):
                    if depth <= 0:
                        continue
                    min_depth = min(min_depth, float(depth))
                    if index % LOBE_SAMPLE_STRIDE == 0:
                        points.append([round(float(speed), 1), round(float(depth), 3)])
            evidence["lobe_sample"] = {
                "min_limit_depth_mm": round(min_depth, 3) if min_depth != float("inf") else None,
                "points": points[:24],
                "speed_range_rpm": [round(speed_low, 1), round(speed_high, 1)],
            }
        except (ValueError, TypeError, ZeroDivisionError) as exc:
            evidence["lobe_sample"] = {"error": f"叶瓣采样失败: {exc}"}

    return evidence
