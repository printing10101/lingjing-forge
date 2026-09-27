"""DXF 解析结果 → 工艺规划器的桥接（从 orchestrator.py 拆出）.

把 ``DxfProcessResult``（阶段 1 DXF 解析产物）规范化为规划器兼容的
特征列表，使规划契约不依赖 DXF 模块的内部类型。
"""

from __future__ import annotations

from typing import Any


def normalize_dxf_output(parse_result: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """把 ``DxfProcessResult`` 桥接为规划器兼容的规范化特征列表。

    HoleFeatureInfo → 规划器孔 dict 契约：
    ``{type, id, hole_type, position{x,y}, diameter, depth,
    tolerance_grade, surface}``。通孔深度缺失（=0）时以板厚
    （overall_height）推断——规划器校验要求通孔深度 > 0。
    """
    features: list[dict[str, Any]] = []
    stage = getattr(parse_result, "features", None)
    summary = getattr(stage, "summary", None) if stage is not None else None
    if not isinstance(summary, dict):
        summary = {}
    plate_thickness = float(summary.get("overall_height") or 0.0)

    for h in summary.get("holes_detail") or []:
        if not isinstance(h, dict):
            continue
        hole_type = str(h.get("hole_type") or "through_hole")
        depth = float(h.get("depth") or 0.0)
        if depth <= 0 and hole_type == "through_hole":
            depth = plate_thickness
        features.append(
            {
                "type": "hole",
                "id": h.get("hole_id") or f"H{len(features) + 1:03d}",
                "hole_type": hole_type,
                "position": {
                    "x": float(h.get("center_x") or 0.0),
                    "y": float(h.get("center_y") or 0.0),
                },
                "diameter": float(h.get("diameter") or 0.0),
                "depth": depth,
                "tolerance_grade": h.get("tolerance_grade") or "IT8",
                "surface": h.get("surface") or "A",
            }
        )
    for p in summary.get("planes_detail") or []:
        if not isinstance(p, dict):
            continue
        features.append(
            {
                "type": "plane",
                "id": p.get("plane_id") or f"P{len(features) + 1:03d}",
                "position": {
                    "x": float(p.get("center_x") or 0.0),
                    "y": float(p.get("center_y") or 0.0),
                },
                "length": float(p.get("length") or 0.0),
                "width": float(p.get("width") or 0.0),
                "surface": p.get("surface") or "A",
            }
        )
    metadata = {
        "feature_stage_success": bool(getattr(stage, "success", False)),
        "feature_stage_error": getattr(stage, "error", "") or "",
        "hole_count": len([f for f in features if f["type"] == "hole"]),
        "plane_count": len([f for f in features if f["type"] == "plane"]),
        "overall": {
            "length": summary.get("overall_length"),
            "width": summary.get("overall_width"),
            "height": summary.get("overall_height"),
        },
    }
    return features, metadata
