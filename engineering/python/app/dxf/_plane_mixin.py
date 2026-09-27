"""加工特征平面提取 mixin（从 feature_extractor 拆出）。"""

from __future__ import annotations

import math

from app.dxf.dxf_parser import DxfLine, DxfParseResult
from app.dxf._dxf_feature_models import (
    RECTANGLE_ANGLE_TOLERANCE,
    RECTANGLE_LENGTH_TOLERANCE,
    FeatureExtractionResult,
    PlaneFeatureInfo,
)


class _PlaneMixin:
    def _extract_plane_features(
        self,
        parse_result: DxfParseResult,
        result: FeatureExtractionResult,
    ) -> None:
        """检测矩形轮廓作为平面特征。

        算法：
        1. 从线条中找出闭合矩形（4条边，相对边平行且等长）
        2. 使用直角检测和长度匹配来识别矩形
        """
        if len(parse_result.lines) < 4:
            return

        rectangles = []
        lines = parse_result.lines
        used_lines: set[int] = set()

        for i in range(len(lines)):
            if i in used_lines:
                continue
            rect = self._try_form_rectangle(i, lines, used_lines)
            if rect is not None:
                rectangles.append(rect)

        for idx, (length, width, cx, cy, layer) in enumerate(rectangles, 1):
            plane = PlaneFeatureInfo(
                plane_id=f"PLANE_{idx:03d}",
                center_x=cx,
                center_y=cy,
                length=length,
                width=width,
                surface="A",
                layer=layer,
            )
            result.planes.append(plane)

            if length > result.overall_length * 0.8 or width > result.overall_width * 0.8:
                result.overall_length = max(result.overall_length, length)
                result.overall_width = max(result.overall_width, width)

    def _try_form_rectangle(
        self,
        start_idx: int,
        lines: list[DxfLine],
        used_lines: set[int],
    ) -> tuple[float, float, float, float, str] | None:
        """尝试从起始线条构建矩形轮廓。

        矩形的判定条件：
        - 4条线段首尾相连
        - 相邻边接近正交（90°±允许偏差）
        - 相对边长度一致（允许误差）

        Returns:
            (length, width, center_x, center_y, layer) 或 None
        """
        l1 = lines[start_idx]
        p1, p2 = l1.start[:2], l1.end[:2]
        len1 = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        if len1 < 0.01:
            return None

        dir1 = ((p2[0] - p1[0]) / len1, (p2[1] - p1[1]) / len1)

        candidates: list[tuple[int, float]] = []
        for j, lj in enumerate(lines):
            if j == start_idx or j in used_lines:
                continue
            p3, p4 = lj.start[:2], lj.end[:2]
            dist_start = math.hypot(p3[0] - p2[0], p3[1] - p2[1])
            dist_end = math.hypot(p4[0] - p2[0], p4[1] - p2[1])

            conn_pt = p3 if dist_start < dist_end else p4
            conn_dist = min(dist_start, dist_end)
            other_pt = p4 if dist_start < dist_end else p3

            d2 = (other_pt[0] - conn_pt[0], other_pt[1] - conn_pt[1])
            len2 = math.hypot(d2[0], d2[1])
            if len2 < 0.01:
                continue

            dot = abs(d2[0] * dir1[0] + d2[1] * dir1[1]) / len2
            # 相邻边应接近正交（dot≈0）；原条件 dot > cos(90-tol) 误判平行，
            # 导致标准垂直矩形永远无法识别（平面特征提取失效）
            if dot < math.cos(math.radians(RECTANGLE_ANGLE_TOLERANCE)):
                candidates.append((j, conn_dist))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[1])
        best_j, _ = candidates[0]

        l2 = lines[best_j]
        p3, p4 = l2.start[:2], l2.end[:2]

        dist_s = math.hypot(p3[0] - p2[0], p3[1] - p2[1])
        dist_e = math.hypot(p4[0] - p2[0], p4[1] - p2[1])
        l2_start = p3 if dist_s < dist_e else p4
        l2_end = p4 if dist_s < dist_e else p3
        len2 = math.hypot(l2_end[0] - l2_start[0], l2_end[1] - l2_start[1])

        third_candidates = []
        for k, lk in enumerate(lines):
            if k in (start_idx, best_j) or k in used_lines:
                continue
            p5, p6 = lk.start[:2], lk.end[:2]
            dist_s = math.hypot(p5[0] - l2_end[0], p5[1] - l2_end[1])
            dist_e = math.hypot(p6[0] - l2_end[0], p6[1] - l2_end[1])
            third_candidates.append((k, min(dist_s, dist_e)))

        if not third_candidates:
            return None

        third_candidates.sort(key=lambda x: x[1])
        best_k, _ = third_candidates[0]

        l3 = lines[best_k]
        p5, p6 = l3.start[:2], l3.end[:2]
        dist_s = math.hypot(p5[0] - l2_end[0], p5[1] - l2_end[1])
        dist_e = math.hypot(p6[0] - l2_end[0], p6[1] - l2_end[1])
        l3_start = p5 if dist_s < dist_e else p6
        l3_end = p6 if dist_s < dist_e else p5
        len3 = math.hypot(l3_end[0] - l3_start[0], l3_end[1] - l3_start[1])

        if abs(len3 - len1) > RECTANGLE_LENGTH_TOLERANCE:
            return None

        fourth_candidates = []
        for m, lm in enumerate(lines):
            if m in (start_idx, best_j, best_k) or m in used_lines:
                continue
            p7, p8 = lm.start[:2], lm.end[:2]
            d1 = math.hypot(p7[0] - l3_end[0], p7[1] - l3_end[1])
            d2_ = math.hypot(p8[0] - l3_end[0], p8[1] - l3_end[1])
            fourth_candidates.append((m, min(d1, d2_)))

        if not fourth_candidates:
            return None

        fourth_candidates.sort(key=lambda x: x[1])
        best_m, last_dist = fourth_candidates[0]

        if last_dist > RECTANGLE_LENGTH_TOLERANCE:
            return None

        l4 = lines[best_m]
        p7, p8 = l4.start[:2], l4.end[:2]
        d1 = math.hypot(p7[0] - l3_end[0], p7[1] - l3_end[1])
        d2_ = math.hypot(p8[0] - l3_end[0], p8[1] - l3_end[1])
        l4_start = p7 if d1 < d2_ else p8
        l4_end = p8 if d1 < d2_ else p7
        len4 = math.hypot(l4_end[0] - l4_start[0], l4_end[1] - l4_start[1])

        if abs(len4 - len2) > RECTANGLE_LENGTH_TOLERANCE:
            return None

        close_dist = math.hypot(l4_end[0] - p1[0], l4_end[1] - p1[1])
        if close_dist >= RECTANGLE_LENGTH_TOLERANCE:
            return None

        used_lines.update([start_idx, best_j, best_k, best_m])

        length = max(len1, len2)
        width_val = min(len1, len2)
        # 中心 = 对角点中点（p1 与 l3_start 为矩形对角）；原实现取
        # (p1+l2_end+l3_end+l4_end)/4 是端点均值，标准矩形下会算出
        # 偏心中心（如 100x80 矩形给出 cx=25 而非 50）
        cx = (p1[0] + l3_start[0]) / 2.0
        cy = (p1[1] + l3_start[1]) / 2.00

        return (length, width_val, cx, cy, l1.layer)
