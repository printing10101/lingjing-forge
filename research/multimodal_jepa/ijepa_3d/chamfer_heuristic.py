"""DXF 几何启发式识别 chamfer / fillet / step / slot。

研究阶段产物：基于规则的候选特征识别器，
将来会被 IJEPA-3D 模型推理结果替代。

启发式：
- chamfer：LWPOLYLINE 顶点处 bulge 较小（接近 0）但角度变化明显
- fillet：LWPOLYLINE 顶点处 bulge 较大（>0.1 且为正）
- step：连续的 L 形线段（同一方向两次折返）
- slot：两个相对的弧 + 直线段（半圆 + 直线）

输入：DxfParseResult
输出：List[RecognizedFeature]
"""
from __future__ import annotations

import logging
import math
from typing import List, Tuple

from shared.contracts.feature_recognizer import (
    FeatureType,
    RecognizedFeature,
)

logger = logging.getLogger(__name__)


CHAMFER_BULGE_THRESHOLD = 0.05  # 小 bulge = 尖角（可能是 chamfer）
FILLET_BULGE_THRESHOLD = 0.1  # 大 bulge = 圆角
STEP_TOLERANCE_MM = 1.0  # step 高度/宽度判定
SLOT_ARC_RADIUS_MM_MIN = 1.0  # 最小键槽半径


def _vertex_xy(v) -> Tuple[float, float]:
    """从 vertex 元组中取 (x, y)。"""
    if isinstance(v, (list, tuple)):
        return float(v[0]), float(v[1])
    return float(v[0]), float(v[1])


def _vertex_bulge(v) -> float:
    """从 vertex 元组中取 bulge（如果存在），否则 0。"""
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        try:
            return float(v[2])
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def _vertex_angle(p0, p1, p2) -> float:
    """计算 p1 处的内角（度）。"""
    v1 = (p0[0] - p1[0], p0[1] - p1[1])
    v2 = (p2[0] - p1[0], p2[1] - p1[1])
    l1 = math.hypot(*v1)
    l2 = math.hypot(*v2)
    if l1 < 1e-9 or l2 < 1e-9:
        return 0.0
    cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)
    cos = max(-1.0, min(1.0, cos))
    return math.degrees(math.acos(cos))


def _is_closed(pl) -> bool:
    return bool(getattr(pl, "is_closed", getattr(pl, "closed", False)))


def detect_chamfer(polylines) -> List[RecognizedFeature]:
    """检测倒角：LWPOLYLINE 顶点处 bulge 接近 0 但内角非 90°/180°。

    Args:
        polylines: List[DxfPolyline]

    Returns:
        List[RecognizedFeature] of type CHAMFER
    """
    out: List[RecognizedFeature] = []
    for poly_idx, pl in enumerate(polylines):
        vertices = pl.vertices
        if len(vertices) < 3:
            continue
        closed = _is_closed(pl)
        rng = range(len(vertices)) if closed else range(len(vertices) - 1)
        for i in rng:
            prev_i = (i - 1) % len(vertices) if closed else max(i - 1, 0)
            next_i = (i + 1) % len(vertices) if closed else min(i + 1, len(vertices) - 1)
            p_prev = _vertex_xy(vertices[prev_i])
            p_curr = _vertex_xy(vertices[i])
            p_next = _vertex_xy(vertices[next_i])
            bulge = _vertex_bulge(vertices[i])
            if abs(bulge) > CHAMFER_BULGE_THRESHOLD:
                continue
            angle = _vertex_angle(p_prev, p_curr, p_next)
            # 倒角通常把 90° 直角切掉，留下 135° 左右
            if 100.0 < angle < 170.0:
                out.append(
                    RecognizedFeature(
                        type=FeatureType.CHAMFER,
                        position=(p_curr[0], p_curr[1], 0.0),
                        params={"angle_deg": round(angle, 2), "bulge": round(bulge, 4)},
                        confidence=0.55,
                        source_layer=pl.layer,
                        note=f"heuristic_chamfer_poly{poly_idx}_v{i}",
                    )
                )
    return out


def detect_fillet(polylines) -> List[RecognizedFeature]:
    """检测圆角：LWPOLYLINE 顶点处 bulge > 0.1。

    bulge > 0：逆时针弧（外凸圆角）
    bulge < 0：顺时针弧（内凹圆角）
    """
    out: List[RecognizedFeature] = []
    for poly_idx, pl in enumerate(polylines):
        vertices = pl.vertices
        if len(vertices) < 3:
            continue
        closed = _is_closed(pl)
        for i, v in enumerate(vertices):
            bulge = _vertex_bulge(v)
            if abs(bulge) < FILLET_BULGE_THRESHOLD:
                continue
            n = len(vertices)
            next_i = (i + 1) % n if closed else min(i + 1, n - 1)
            p_curr = _vertex_xy(v)
            p_next = _vertex_xy(vertices[next_i])
            chord = math.hypot(p_next[0] - p_curr[0], p_next[1] - p_curr[1])
            if chord < 1e-6:
                continue
            radius = abs(bulge) * chord / 2.0
            out.append(
                RecognizedFeature(
                    type=FeatureType.FILLET,
                    position=(p_curr[0], p_curr[1], 0.0),
                    params={"radius_mm": round(radius, 3), "bulge": round(bulge, 4)},
                    confidence=0.7,
                    source_layer=pl.layer,
                    note=f"heuristic_fillet_poly{poly_idx}_v{i}",
                )
            )
    return out


def detect_step(lines) -> List[RecognizedFeature]:
    """检测台阶：连续的 L 形折线（X 方向走一段、Z 方向走一段、再 X 方向走一段）。"""
    out: List[RecognizedFeature] = []
    if len(lines) < 3:
        return out
    for i in range(len(lines) - 2):
        l1 = lines[i]
        l2 = lines[i + 1]
        l3 = lines[i + 2]
        v1 = (l1.end[0] - l1.start[0], l1.end[1] - l1.start[1])
        v2 = (l2.end[0] - l2.start[0], l2.end[1] - l2.start[1])
        v3 = (l3.end[0] - l3.start[0], l3.end[1] - l3.start[1])
        l1_len = math.hypot(*v1)
        l2_len = math.hypot(*v2)
        l3_len = math.hypot(*v3)
        if l1_len < 1e-6 or l2_len < 1e-6 or l3_len < 1e-6:
            continue
        d1 = (v1[0] / l1_len, v1[1] / l1_len)
        d2 = (v2[0] / l2_len, v2[1] / l2_len)
        d3 = (v3[0] / l3_len, v3[1] / l3_len)
        if abs(d1[0] * d3[0] + d1[1] * d3[1]) > 0.9 and abs(d2[0] * d1[0] + d2[1] * d1[1]) < 0.1:
            if abs(l2_len - l1_len * 0.3) < STEP_TOLERANCE_MM * 10:
                out.append(
                    RecognizedFeature(
                        type=FeatureType.STEP,
                        position=(l2.end[0], l2.end[1], 0.0),
                        params={
                            "step_height_mm": round(l2_len, 3),
                            "step_width_mm": round(l1_len, 3),
                        },
                        confidence=0.45,
                        source_layer=l1.layer,
                        note=f"heuristic_step_l{i}",
                    )
                )
    return out


def detect_slot(circles, polylines) -> List[RecognizedFeature]:
    """检测键槽：内孔 + 矩形外延的组合。"""
    out: List[RecognizedFeature] = []
    if not circles or not polylines:
        return out
    for c_idx, c in enumerate(circles):
        cx, cy, cr = c.center[0], c.center[1], c.radius
        for poly_idx, pl in enumerate(polylines):
            closed = _is_closed(pl)
            if not closed or len(pl.vertices) < 4:
                continue
            xs = [_vertex_xy(v)[0] for v in pl.vertices]
            ys = [_vertex_xy(v)[1] for v in pl.vertices]
            w = max(xs) - min(xs)
            h = max(ys) - min(ys)
            if w <= 0 or h <= 0:
                continue
            p0 = _vertex_xy(pl.vertices[0])
            dist = math.hypot(p0[0] - cx, p0[1] - cy)
            if dist > cr + 5.0:
                continue
            if w < cr * 1.5 and h < cr * 1.5:
                out.append(
                    RecognizedFeature(
                        type=FeatureType.SLOT,
                        position=((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, 0.0),
                        params={
                            "slot_width_mm": round(w, 3),
                            "slot_height_mm": round(h, 3),
                            "bore_radius_mm": round(cr, 3),
                        },
                        confidence=0.4,
                        source_layer=pl.layer,
                        note=f"heuristic_slot_circle{c_idx}_poly{poly_idx}",
                    )
                )
    return out


def detect_all(parse_result) -> List[RecognizedFeature]:
    """从 DXF parse_result 推断所有高级特征（chamfer/fillet/step/slot）。"""
    if parse_result is None:
        return []
    feats: List[RecognizedFeature] = []
    feats.extend(detect_chamfer(parse_result.polylines))
    feats.extend(detect_fillet(parse_result.polylines))
    feats.extend(detect_step(parse_result.lines))
    feats.extend(detect_slot(parse_result.circles, parse_result.polylines))
    return feats


def detect_all_extended(parse_result) -> List[RecognizedFeature]:
    """从 DXF parse_result 推断所有高级特征 + 复杂几何（多型腔/岛屿/长型腔/孔阵列）。

    这是 detect_all 的超集，包含 8 个识别器：
        - detect_chamfer         倒角
        - detect_fillet          圆角
        - detect_step            台阶
        - detect_slot            键槽
        - detect_multi_cavity    多型腔
        - detect_island          岛屿
        - detect_long_cavity     长型腔
        - detect_hole_array      孔阵列
    """
    if parse_result is None:
        return []
    # 基础识别器失败不应阻塞高级特征识别
    try:
        feats = detect_all(parse_result)
    except Exception as exc:  # noqa: BLE001
        logger.debug("detect_all failed in detect_all_extended: %s", exc)
        feats = []
    # 增量加入复杂几何（避免循环 import，运行时 import）
    try:
        from multimodal_jepa.ijepa_3d.advanced_features import (
            detect_all_advanced,
        )
        feats.extend(detect_all_advanced(parse_result))
    except Exception as exc:  # noqa: BLE001
        # 高级特征模块加载失败不影响基础特征
        logger.debug("detect_all_advanced failed: %s", exc)
    return feats


__all__ = [
    "detect_chamfer",
    "detect_fillet",
    "detect_step",
    "detect_slot",
    "detect_all",
    "detect_all_extended",
]

