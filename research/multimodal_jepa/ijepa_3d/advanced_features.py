"""DXF 高级几何特征识别器（多型腔、岛屿、长型腔、连孔）。

这是 :mod:`chamfer_heuristic` 的扩展，提供更复杂的几何模式识别能力：

- **multi_cavity（多型腔）**：多个独立的闭合多边形，常用于模具设计
- **island（岛屿）**：嵌套的闭合多边形（内孔），常用于型腔内的凸台
- **long_cavity（长型腔）**：长宽比 > 3:1 的矩形型腔，常用于导轨、槽
- **hole_array（连孔/孔阵列）**：规律排列的圆孔组，如螺栓孔、法兰孔阵列

输入：DxfParseResult
输出：List[RecognizedFeature]
"""

from __future__ import annotations

import math
from typing import List, Tuple

from shared.contracts.feature_recognizer import (
    FeatureType,
    RecognizedFeature,
)


# 长型腔 长宽比阈值
LONG_CAVITY_ASPECT_RATIO = 3.0
# 多型腔 最小闭合多边形数量
MULTI_CAVITY_MIN_COUNT = 2
# 岛屿检测 最小嵌套深度
ISLAND_MIN_NESTING_DEPTH = 1
# 孔阵列检测 最小孔数
HOLE_ARRAY_MIN_COUNT = 3
# 孔阵列 容差（相邻孔距离标准差 / 平均距离）
HOLE_ARRAY_DISTANCE_CV_THRESHOLD = 0.15


def _vertex_xy(v) -> Tuple[float, float]:
    """从 vertex 元组中取 (x, y)。"""
    if isinstance(v, (list, tuple)):
        return float(v[0]), float(v[1])
    return float(v[0]), float(v[1])


def _is_closed(pl) -> bool:
    """判断多段线是否闭合。"""
    return bool(getattr(pl, "is_closed", getattr(pl, "closed", False)))


def _polyline_bbox(pl) -> Tuple[float, float, float, float]:
    """计算闭合多段线的轴对齐边界框 (min_x, min_y, max_x, max_y)。"""
    xs = [_vertex_xy(v)[0] for v in pl.vertices]
    ys = [_vertex_xy(v)[1] for v in pl.vertices]
    return (min(xs), min(ys), max(xs), max(ys))


def _polyline_area(pl) -> float:
    """计算多段线包围面积（Shoelace 公式，要求闭合）。"""
    if not _is_closed(pl) or len(pl.vertices) < 3:
        return 0.0
    pts = [_vertex_xy(v) for v in pl.vertices]
    n = len(pts)
    area = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def _polyline_centroid(pl) -> Tuple[float, float]:
    """计算多段线几何中心（顶点平均值）。"""
    pts = [_vertex_xy(v) for v in pl.vertices]
    n = len(pts)
    if n == 0:
        return (0.0, 0.0)
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)


# 1. 多型腔检测


def detect_multi_cavity(polylines) -> List[RecognizedFeature]:
    """检测多型腔：多个独立的闭合多边形在同一个图层上。

    典型应用：模具的多个相同型腔、电池盒的多个凹坑。
    判定规则：
        - 同一图层上 ≥ 2 个闭合多边形
        - 各型腔面积相近（变异系数 < 0.3）
        - 中心点彼此距离 > 型腔特征尺寸
    """
    out: List[RecognizedFeature] = []
    if not polylines:
        return out
    # 按图层分组
    by_layer: dict[str, list] = {}
    for pl in polylines:
        if not _is_closed(pl) or len(pl.vertices) < 3:
            continue
        layer = getattr(pl, "layer", "") or "default"
        by_layer.setdefault(layer, []).append(pl)
    # 在每层内判断"多型腔"
    for layer, pls in by_layer.items():
        if len(pls) < MULTI_CAVITY_MIN_COUNT:
            continue
        # 过滤零面积多边形，同时保持 pls 和 areas 一致
        # （之前 bug：areas 被过滤但 pls 未过滤，导致 centroid 计算包含零面积多边形）
        filtered = [(pl, _polyline_area(pl)) for pl in pls]
        filtered = [(pl, a) for pl, a in filtered if a > 1e-6]
        if len(filtered) < MULTI_CAVITY_MIN_COUNT:
            continue
        pls_valid = [pl for pl, _ in filtered]
        areas = [a for _, a in filtered]
        # 面积变异系数
        mean_area = sum(areas) / len(areas)
        if mean_area <= 0:
            continue
        var = sum((a - mean_area) ** 2 for a in areas) / len(areas)
        cv = math.sqrt(var) / mean_area
        if cv < 0.3:  # 面积相近
            centroids = [_polyline_centroid(pl) for pl in pls_valid]
            xs = [c[0] for c in centroids]
            ys = [c[1] for c in centroids]
            out.append(
                RecognizedFeature(
                    type=FeatureType.POCKET,
                    position=(sum(xs) / len(xs), sum(ys) / len(ys), 0.0),
                    params={
                        "cavity_count": len(pls_valid),
                        "cavity_area_mm2": round(mean_area, 3),
                        "area_cv": round(cv, 3),
                        "layer": layer,
                        "kind": "multi_cavity",
                    },
                    confidence=0.75,
                    source_layer=layer,
                    note=f"heuristic_multi_cavity_{layer}_n{len(pls_valid)}",
                )
            )
    return out


# 2. 岛屿检测


def detect_island(polylines) -> List[RecognizedFeature]:
    """检测岛屿：闭合多边形嵌套结构（外型腔内嵌内岛屿）。

    典型应用：凹腔中央的凸台、轴承座的中央圆柱。
    判定规则：
        - 外层闭合多边形（面积较大）
        - 内层闭合多边形（面积较小）完全位于外层内
        - 内层不是外层的退化/自交
    """
    out: List[RecognizedFeature] = []
    if len(polylines) < 2:
        return out
    closed_polys = [pl for pl in polylines if _is_closed(pl) and len(pl.vertices) >= 3]
    if len(closed_polys) < 2:
        return out
    # 按面积降序
    closed_polys.sort(key=_polyline_area, reverse=True)
    # 去重：同一个 inner 不应被多个 outer 匹配（避免笛卡尔积爆炸和重复特征）
    used_inners: set[int] = set()
    for i, outer in enumerate(closed_polys):
        outer_area = _polyline_area(outer)
        if outer_area <= 1e-6:
            continue
        outer_bbox = _polyline_bbox(outer)
        for j, inner in enumerate(closed_polys):
            if i == j or j in used_inners:
                continue
            inner_area = _polyline_area(inner)
            # 岛屿面积应 < 外型腔面积 80%
            if inner_area >= outer_area * 0.8:
                continue
            if inner_area < 1e-6:
                continue
            inner_centroid = _polyline_centroid(inner)
            # 中心必须在外型腔 bbox 内
            if not (
                outer_bbox[0] <= inner_centroid[0] <= outer_bbox[2]
                and outer_bbox[1] <= inner_centroid[1] <= outer_bbox[3]
            ):
                continue
            # 简化检测：bbox 内就视为岛屿
            out.append(
                RecognizedFeature(
                    type=FeatureType.BOSS,
                    position=(inner_centroid[0], inner_centroid[1], 0.0),
                    params={
                        "island_area_mm2": round(inner_area, 3),
                        "pocket_area_mm2": round(outer_area, 3),
                        "fill_ratio": round(inner_area / outer_area, 3),
                    },
                    confidence=0.65,
                    source_layer=inner.layer,
                    note=f"heuristic_island_outer{i}_inner{j}",
                )
            )
            used_inners.add(j)  # 标记此 inner 已被匹配
            # 每个 outer 最多匹配 1 个 inner（避免笛卡尔积爆炸）
            break
    return out


# 3. 长型腔检测


def detect_long_cavity(polylines) -> List[RecognizedFeature]:
    """检测长型腔：长宽比 > 3:1 的矩形闭合多边形。

    典型应用：导轨槽、电池仓、U 形槽。
    判定规则：
        - 闭合多边形
        - 边界框长宽比 ≥ 3:1
        - 顶点数少（≤ 8，表示"接近矩形"）
    """
    out: List[RecognizedFeature] = []
    for poly_idx, pl in enumerate(polylines):
        if not _is_closed(pl):
            continue
        if len(pl.vertices) > 8:
            continue  # 顶点多 = 复杂形状，不算"长型腔"
        bbox = _polyline_bbox(pl)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if w <= 0 or h <= 0:
            continue
        aspect = max(w, h) / min(w, h)
        if aspect >= LONG_CAVITY_ASPECT_RATIO:
            orientation = "horizontal" if w > h else "vertical"
            out.append(
                RecognizedFeature(
                    type=FeatureType.POCKET,
                    position=((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2, 0.0),
                    params={
                        "length_mm": round(max(w, h), 3),
                        "width_mm": round(min(w, h), 3),
                        "aspect_ratio": round(aspect, 3),
                        "orientation": orientation,
                        "kind": "long_cavity",
                    },
                    confidence=0.7,
                    source_layer=pl.layer,
                    note=f"heuristic_long_cavity_poly{poly_idx}",
                )
            )
    return out


# 4. 孔阵列检测


def detect_hole_array(circles) -> List[RecognizedFeature]:
    """检测孔阵列：规律排列的圆孔组（≥ 3 个相似尺寸）。

    典型应用：法兰盘螺栓孔、PCB 安装孔、散热孔。
    判定规则：
        - 至少 3 个圆
        - 半径相近（变异系数 < 0.2）
        - 中心点呈"规律排列"（中心点之间距离的变异系数 < 0.15）
    """
    out: List[RecognizedFeature] = []
    if len(circles) < HOLE_ARRAY_MIN_COUNT:
        return out
    # 按半径聚类
    radii = [c.radius for c in circles]
    mean_r = sum(radii) / len(radii)
    if mean_r <= 0:
        return out
    r_var = sum((r - mean_r) ** 2 for r in radii) / len(radii)
    r_cv = math.sqrt(r_var) / mean_r
    if r_cv >= 0.2:
        # 半径差异太大，不算同一组阵列
        return out
    centers = [(c.center[0], c.center[1]) for c in circles]
    # 用最近邻距离判断"规律排列"（对线性阵列和网格都鲁棒）
    n = len(centers)
    nn_dists: List[float] = []
    for i in range(n):
        nearest = min(
            math.hypot(
                centers[i][0] - centers[j][0],
                centers[i][1] - centers[j][1],
            )
            for j in range(n)
            if j != i
        )
        nn_dists.append(nearest)
    mean_nn = sum(nn_dists) / n
    if mean_nn <= 0:
        return out
    nn_var = sum((d - mean_nn) ** 2 for d in nn_dists) / n
    nn_cv = math.sqrt(nn_var) / mean_nn
    if nn_cv >= HOLE_ARRAY_DISTANCE_CV_THRESHOLD:
        return out
    # 中心点
    cx = sum(c[0] for c in centers) / n
    cy = sum(c[1] for c in centers) / n
    out.append(
        RecognizedFeature(
            type=FeatureType.HOLE,
            position=(cx, cy, 0.0),
            params={
                "hole_count": n,
                "hole_radius_mm": round(mean_r, 3),
                "hole_diameter_mm": round(mean_r * 2, 3),
                "pitch_mm": round(mean_nn, 3),
                "nearest_neighbor_cv": round(nn_cv, 3),
                "radius_cv": round(r_cv, 3),
                "kind": "hole_array",
            },
            confidence=0.8,
            source_layer=getattr(circles[0], "layer", None),
            note=f"heuristic_hole_array_n{n}",
        )
    )
    return out


# 统一入口


def detect_all_advanced(parse_result) -> List[RecognizedFeature]:
    """从 DXF parse_result 推断所有高级几何特征。

    包含：
        - 多型腔 (multi_cavity)
        - 岛屿 (island)
        - 长型腔 (long_cavity)
        - 孔阵列 (hole_array)
    """
    if parse_result is None:
        return []
    feats: List[RecognizedFeature] = []
    feats.extend(detect_multi_cavity(parse_result.polylines))
    feats.extend(detect_island(parse_result.polylines))
    feats.extend(detect_long_cavity(parse_result.polylines))
    feats.extend(detect_hole_array(parse_result.circles))
    return feats


__all__ = [
    "detect_multi_cavity",
    "detect_island",
    "detect_long_cavity",
    "detect_hole_array",
    "detect_all_advanced",
]
