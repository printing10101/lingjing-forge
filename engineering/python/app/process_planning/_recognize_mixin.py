"""孔特征识别逻辑 mixin（从 hole_recognizer 拆出）。"""

from __future__ import annotations

from typing import Any

from app.process_planning._hole_models import HoleFeature, HoleRecognitionResult


class _RecognizeMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    COAXIAL_THRESHOLD: Any
    STANDARD_DRILL_POINT_ANGLE: Any

    def recognize_from_part_description(
        self,
        part_description: dict[str, Any],
    ) -> HoleRecognitionResult:
        """从零件描述字典中识别孔特征。

        这是主要的辨识入口，接受由前端/STEP导入模块输出的零件几何描述。

        Args:
            part_description: 零件描述字典，包含以下字段：
                - holes (list): 直接定义的孔列表，格式：
                    [{"id": "H01", "type": "through_hole", "position": {...}, "diameter": 8.0, "depth": ...}, ...]
                - material (str): 零件材料名称
                - part_type (str): 零件类型
                - features (list): 可选，已识别的特征列表（通过STEP解析器得到）

        Returns:
            HoleRecognitionResult: 识别结果

        Raises:
            ValueError: 当输入数据格式无效时
        """
        if not part_description:
            raise ValueError("零件描述数据不能为空")

        holes: list[HoleFeature] = []
        warnings: list[str] = []
        errors: list[str] = []
        accuracy_metrics: dict[str, float] = {"overall": 0.99}

        # 路径1: 从 holes 字段直接解析
        raw_holes = part_description.get("holes", [])

        # 路径2: 从 features 字段提取孔特征
        raw_features = part_description.get("features", [])
        hole_features = [
            f
            for f in raw_features
            if f.get("geometric_type") == "cylinder"
            and f.get("type") in ("through_hole", "inner_bore", "counterbore", "center_hole", "blind_hole")
        ]
        # 合并 features 中的孔到 raw_holes
        for hf in hole_features:
            pos = hf.get("position", {})
            dims = hf.get("dimensions", {})
            raw_holes.append(
                {
                    "id": hf.get("name", f"H{len(raw_holes) + 1:03d}"),
                    "type": hf.get("type", "through_hole"),
                    "position": pos,
                    "diameter": dims.get("diameter", hf.get("diameter", 0)),
                    "depth": dims.get("depth", hf.get("depth", 0)),
                    "tolerance_grade": hf.get("tolerance_grade", "H8"),
                    "surface": hf.get("surface", "A"),
                    "is_threaded": hf.get("is_threaded", False),
                    "thread_spec": hf.get("thread_spec", ""),
                }
            )

        # 路径3: 从 solids 字段的圆柱面提取
        solids = part_description.get("solids", [])
        for solid in solids:
            faces = solid.get("faces", [])
            for face in faces:
                if face.get("type") == "cylindrical":
                    # 分析圆柱面的拓扑关系
                    boundaries = face.get("boundaries", [])
                    is_through = all(b.get("open", True) for b in boundaries)
                    hole_type = "through_hole" if is_through else "blind_hole"
                    center = face.get("center", {})
                    raw_holes.append(
                        {
                            "id": f"H{len(raw_holes) + 1:03d}",
                            "type": hole_type,
                            "position": center,
                            "diameter": face.get("diameter", 0),
                            "depth": face.get("height", face.get("length", 0)),
                            "tolerance_grade": face.get("tolerance", "H8"),
                        }
                    )

        if not raw_holes:
            warnings.append("未找到任何孔特征定义。请检查输入数据是否包含 holes/features/solids 字段")

        # 解析每个孔条目
        for i, raw in enumerate(raw_holes):
            try:
                hole_id = raw.get("id", raw.get("hole_id", f"H{i + 1:03d}"))
                hole_type = raw.get("type", "through_hole")

                pos = raw.get("position", {})
                if isinstance(pos, (list, tuple)):
                    pos = {
                        "x": pos[0] if len(pos) > 0 else 0,
                        "y": pos[1] if len(pos) > 1 else 0,
                        "z": pos[2] if len(pos) > 2 else 0,
                    }

                position_x = float(pos.get("x", 0))
                position_y = float(pos.get("y", 0))
                position_z = float(pos.get("z", 0))

                diameter = float(raw.get("diameter", 0))
                if diameter <= 0:
                    errors.append(f"孔 {hole_id} 的直径无效: {diameter}mm")
                    continue

                depth = float(raw.get("depth", raw.get("length", 0)))
                if depth <= 0 and hole_type != "center_hole":
                    errors.append(f"孔 {hole_id} 的深度无效: {depth}mm")
                    continue

                # 深度校验：盲孔深度必须 > 钻尖高度
                if hole_type == "blind_hole":
                    drill_tip_height = (diameter / 2) * (
                        1.0 / __import__("math").tan(__import__("math").radians(self.STANDARD_DRILL_POINT_ANGLE / 2))
                    )
                    if depth <= drill_tip_height:
                        warnings.append(
                            f"盲孔 {hole_id} 深度 {depth:.1f}mm ≤ 钻尖高度"
                            f" {drill_tip_height:.1f}mm，可能无法形成有效盲孔"
                        )

                direction = raw.get("direction", [0.0, 0.0, -1.0])
                if not isinstance(direction, list) or len(direction) != 3:
                    direction = [0.0, 0.0, -1.0]

                hole = HoleFeature(
                    hole_id=str(hole_id),
                    type=hole_type,
                    position_x=position_x,
                    position_y=position_y,
                    position_z=position_z,
                    diameter=diameter,
                    depth=depth,
                    bottom_angle=float(raw.get("bottom_angle", self.STANDARD_DRILL_POINT_ANGLE)),
                    tolerance_grade=str(raw.get("tolerance_grade", "H8")),
                    surface_roughness_ra=float(raw.get("surface_roughness_ra", 3.2)),
                    direction=[float(d) for d in direction],
                    surface=str(raw.get("surface", "A")),
                    parent_feature_id=str(raw.get("parent_feature_id", "")),
                    is_threaded=bool(raw.get("is_threaded", False)),
                    thread_spec=str(raw.get("thread_spec", "")),
                    counterbore_diameter=float(raw.get("counterbore_diameter", 0)),
                    counterbore_depth=float(raw.get("counterbore_depth", 0)),
                    metadata=raw.get("metadata", {}),
                )
                holes.append(hole)

            except (ValueError, TypeError, KeyError) as e:
                errors.append(f" 解析孔条目 {raw.get('id', i)} 时出错: {type(e).__name__}")
                continue

        # 合并共轴孔为沉头孔
        holes = self._merge_coaxial_holes(holes, warnings)

        # 统计各类型数量
        type_summary: dict[str, int] = {}
        for h in holes:
            type_summary[h.type] = type_summary.get(h.type, 0) + 1

        # 计算识别准确率指标
        accuracy_metrics["recognized_count"] = float(len(holes))
        if raw_holes:
            # 识别率 = 成功解析的孔数 / 原始孔条目数
            recognition_rate = len(holes) / max(len(raw_holes), 1)
            accuracy_metrics["recognition_rate"] = min(recognition_rate, 1.0)
            accuracy_metrics["overall"] = accuracy_metrics["recognition_rate"]

        return HoleRecognitionResult(
            holes=holes,
            total_count=len(holes),
            type_summary=type_summary,
            warnings=warnings,
            errors=errors,
            accuracy_metrics=accuracy_metrics,
        )

    def _merge_coaxial_holes(
        self,
        holes: list[HoleFeature],
        warnings: list[str],
    ) -> list[HoleFeature]:
        """合并共轴孔为沉头孔。

        检测逻辑：
        - 两个孔在同一XY位置(误差 < COAXIAL_THRESHOLD)
        - 直径上大下小
        - 自动合并为counterbore类型

        Args:
            holes: 原始孔列表
            warnings: 警告信息列表（会被追加）

        Returns:
            合并后的孔列表
        """
        if len(holes) < 2:
            return holes

        merged = []
        used = set()

        for i, h1 in enumerate(holes):
            if i in used:
                continue
            best_match = None
            for j, h2 in enumerate(holes):
                if j <= i or j in used:
                    continue
                # 检查共轴性：XY位置相同
                dx = abs(h1.position_x - h2.position_x)
                dy = abs(h1.position_y - h2.position_y)
                if dx > self.COAXIAL_THRESHOLD or dy > self.COAXIAL_THRESHOLD:
                    continue
                # 确定大小孔关系
                larger = h1 if h1.diameter > h2.diameter else h2
                smaller = h1 if h1.diameter <= h2.diameter else h2
                # 方向一致
                if larger.direction != smaller.direction:
                    continue
                # 合并：大孔为沉头部分
                best_match = (j, larger, smaller)
                break

            if best_match:
                j, larger, smaller = best_match
                used.add(i)
                used.add(j)
                # 创建沉头孔特征
                cb_hole = HoleFeature(
                    hole_id=larger.hole_id,
                    type="counterbore",
                    position_x=larger.position_x,
                    position_y=larger.position_y,
                    position_z=larger.position_z,
                    diameter=smaller.diameter,  # 通孔直径
                    depth=smaller.depth,  # 通孔深度
                    tolerance_grade=smaller.tolerance_grade,
                    surface_roughness_ra=smaller.surface_roughness_ra,
                    direction=larger.direction,
                    surface=larger.surface,
                    parent_feature_id="",
                    counterbore_diameter=larger.diameter,
                    counterbore_depth=abs(larger.position_z - smaller.position_z) or larger.depth,
                )
                merged.append(cb_hole)
                warnings.append(
                    f"孔 {larger.hole_id}(φ{larger.diameter}mm)和"
                    f" {smaller.hole_id}(φ{smaller.diameter}mm)在同轴位置，"
                    f"已合并为沉头孔 {cb_hole.hole_id}"
                )
            else:
                merged.append(h1)

        return merged
