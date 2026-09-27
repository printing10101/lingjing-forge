"""Boss 识别逻辑 mixin（从 boss_recognizer 拆出）。"""

from __future__ import annotations

from typing import Any

from app.core.error_taxonomy import ErrorCategory, ManufacturingError
from app.process_planning._boss_models import BossFeature, BossRecognitionResult, BossStep


class _BossRecognizeMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    COAXIAL_THRESHOLD: Any
    MIN_BOSS_DIAMETER: Any

    def recognize_from_part_description(
        self,
        part_description: dict[str, Any],
    ) -> BossRecognitionResult:
        """Recognize boss features from a part description dictionary.

        Args:
            part_description: Part description dictionary containing 'bosses',
                'features', or 'contours' fields.

        Returns:
            BossRecognitionResult with all recognized boss features.

        Raises:
            ManufacturingError: If the part description is empty or None.
        """
        if not part_description:
            raise ManufacturingError(
                category=ErrorCategory.FEATURE_RECOGNITION_INCOMPLETE,
                detail="零件描述数据不能为空",
            )

        bosses: list[BossFeature] = []
        warnings: list[str] = []
        errors: list[str] = []
        accuracy_metrics: dict[str, float] = {"overall": 0.99}

        raw_bosses = part_description.get("bosses", [])
        raw_features = part_description.get("features", [])
        boss_features = [
            f
            for f in raw_features
            if f.get("geometric_type") in ("boss", "circular_boss", "rectangular_boss", "stepped_boss")
            or f.get("type") in ("circular_boss", "rectangular_boss", "stepped_boss")
        ]
        for bf in boss_features:
            pos = bf.get("position", {})
            dims = bf.get("dimensions", {})
            raw_steps = bf.get("steps", [])
            raw_bosses.append(
                {
                    "id": bf.get("name", f"B{len(raw_bosses) + 1:03d}"),
                    "type": bf.get("type", "circular_boss"),
                    "position": pos,
                    "diameter": dims.get("diameter", bf.get("diameter", 0)),
                    "side_length": dims.get("side_length", bf.get("side_length", 0)),
                    "height": dims.get("height", bf.get("height", 0)),
                    "tolerance_grade": bf.get("tolerance_grade", "H8"),
                    "surface": bf.get("surface", "A"),
                    "steps": [
                        {
                            "step_index": s.get("step_index", i + 1),
                            "diameter": s.get("diameter", 0),
                            "height": s.get("height", 0),
                            "position_z": s.get("position_z", 0),
                            "tolerance_grade": s.get("tolerance_grade", "H8"),
                        }
                        for i, s in enumerate(raw_steps)
                    ],
                }
            )

        contours = part_description.get("contours", part_description.get("entities", []))
        for i, contour in enumerate(contours):
            if self._is_boss_contour(contour):
                fig_id = f"B{i + 1:03d}"
                if not any(b.get("id") == fig_id for b in raw_bosses):
                    shape = contour.get("shape", contour.get("type", "circular_boss"))
                    boss_type = "circular_boss" if shape in ("circle", "circular_boss") else "rectangular_boss"
                    raw_bosses.append(
                        {
                            "id": fig_id,
                            "type": boss_type,
                            "position": contour.get("center", contour.get("position", {})),
                            "diameter": contour.get(
                                "diameter", contour.get("radius", 0) * 2 if contour.get("radius") else 0
                            ),
                            "side_length": contour.get("side_length", contour.get("length", 0)),
                            "height": contour.get("height", contour.get("z_height", 0)),
                        }
                    )

        if not raw_bosses:
            warnings.append("未找到任何凸台特征定义")

        for i, raw in enumerate(raw_bosses):
            try:
                boss_id = raw.get("id", raw.get("boss_id", f"B{i + 1:03d}"))
                boss_type = raw.get("type", "circular_boss")

                pos = raw.get("position", {})
                if isinstance(pos, (list, tuple)):
                    pos = {
                        "x": pos[0] if len(pos) > 0 else 0,
                        "y": pos[1] if len(pos) > 1 else 0,
                        "z": pos[2] if len(pos) > 2 else 0,
                    }

                center_x = float(pos.get("x", 0))
                center_y = float(pos.get("y", 0))
                center_z = float(pos.get("z", 0))

                diameter = float(raw.get("diameter", 0))
                side_length = float(raw.get("side_length", 0))
                height = float(raw.get("height", 0))

                if boss_type in ("circular_boss", "stepped_boss") and diameter <= 0:
                    errors.append(f"圆形凸台 {boss_id} 直径无效: {diameter}mm")
                    continue

                if boss_type == "rectangular_boss" and side_length <= 0:
                    errors.append(f"矩形凸台 {boss_id} 边长无效: {side_length}mm")
                    continue

                if height <= 0:
                    errors.append(f"凸台 {boss_id} 高度无效: {height}mm")
                    continue

                if diameter > 0 and diameter < self.MIN_BOSS_DIAMETER:
                    warnings.append(
                        f"凸台 {boss_id} 直径过小 ({diameter}mm)，低于最小可识别尺寸 {self.MIN_BOSS_DIAMETER}mm"
                    )

                raw_steps = raw.get("steps", [])
                steps: list[BossStep] = []
                for si, rs in enumerate(raw_steps):
                    steps.append(
                        BossStep(
                            step_index=int(rs.get("step_index", si + 1)),
                            diameter=float(rs.get("diameter", 0)),
                            height=float(rs.get("height", 0)),
                            position_z=float(rs.get("position_z", 0)),
                            tolerance_grade=str(rs.get("tolerance_grade", "H8")),
                        )
                    )

                if steps and boss_type != "stepped_boss":
                    boss_type = "stepped_boss"

                boss = BossFeature(
                    boss_id=str(boss_id),
                    type=boss_type,
                    diameter=diameter,
                    side_length=side_length,
                    height=height,
                    center_x=center_x,
                    center_y=center_y,
                    center_z=center_z,
                    tolerance_grade=str(raw.get("tolerance_grade", "H8")),
                    surface_roughness_ra=float(raw.get("surface_roughness_ra", 3.2)),
                    surface=str(raw.get("surface", "A")),
                    steps=steps,
                    metadata=raw.get("metadata", {}),
                )
                bosses.append(boss)

            except (ValueError, TypeError, KeyError) as e:
                errors.append(f"解析凸台条目 {raw.get('id', i)} 时出错: {type(e).__name__}")
                continue

        bosses = self._merge_coaxial_bosses(bosses, warnings)

        type_summary: dict[str, int] = {}
        for b in bosses:
            type_summary[b.type] = type_summary.get(b.type, 0) + 1

        accuracy_metrics["recognized_count"] = float(len(bosses))
        if raw_bosses:
            recognition_rate = len(bosses) / max(len(raw_bosses), 1)
            accuracy_metrics["recognition_rate"] = min(recognition_rate, 1.0)
            accuracy_metrics["overall"] = accuracy_metrics["recognition_rate"]

        return BossRecognitionResult(
            bosses=bosses,
            total_count=len(bosses),
            type_summary=type_summary,
            warnings=warnings,
            errors=errors,
            accuracy_metrics=accuracy_metrics,
        )

    def _is_boss_contour(self, contour: dict[str, Any]) -> bool:
        """Check whether a contour represents a boss feature.

        Args:
            contour: Contour dictionary with 'shape' or 'type' fields.

        Returns:
            True if the contour shape matches a known boss type.
        """
        shape = contour.get("shape", contour.get("type", ""))
        return shape in ("circle", "circular_boss", "rectangle", "rectangular_boss", "boss")

    def _merge_coaxial_bosses(
        self,
        bosses: list[BossFeature],
        warnings: list[str],
    ) -> list[BossFeature]:
        """Merge coaxial bosses into stepped bosses.

        Detection logic:
        - Two bosses at the same XY position (within COAXIAL_THRESHOLD)
        - Larger diameter on top, smaller on bottom
        - Automatically merged into a stepped_boss type

        Args:
            bosses: Original list of boss features.
            warnings: Warning list to append merge notifications.

        Returns:
            Merged list of boss features.
        """
        if len(bosses) < 2:
            return bosses

        merged = []
        used: set[int] = set()

        for i, b1 in enumerate(bosses):
            if i in used:
                continue
            if not b1.is_circular() and not b1.is_stepped():
                merged.append(b1)
                continue

            coaxial_group = [b1]
            for j, b2 in enumerate(bosses):
                if j <= i or j in used:
                    continue
                if not b2.is_circular():
                    continue

                dx = abs(b1.center_x - b2.center_x)
                dy = abs(b1.center_y - b2.center_y)
                if dx <= self.COAXIAL_THRESHOLD and dy <= self.COAXIAL_THRESHOLD:
                    coaxial_group.append(b2)
                    used.add(j)

            if len(coaxial_group) > 1:
                used.add(i)
                coaxial_group.sort(key=lambda b: b.height, reverse=True)

                base = coaxial_group[0]
                steps: list[BossStep] = []
                for idx, cb in enumerate(coaxial_group):
                    steps.append(
                        BossStep(
                            step_index=idx + 1,
                            diameter=cb.diameter,
                            height=cb.height,
                            position_z=cb.center_z,
                            tolerance_grade=cb.tolerance_grade,
                        )
                    )

                stepped = BossFeature(
                    boss_id=base.boss_id,
                    type="stepped_boss",
                    diameter=coaxial_group[-1].diameter,
                    height=sum(cb.height for cb in coaxial_group),
                    center_x=base.center_x,
                    center_y=base.center_y,
                    center_z=base.center_z,
                    tolerance_grade=base.tolerance_grade,
                    surface_roughness_ra=base.surface_roughness_ra,
                    surface=base.surface,
                    steps=steps,
                    metadata={},
                )
                merged.append(stepped)
                warnings.append(
                    f"凸台 {', '.join(cb.boss_id for cb in coaxial_group)}"
                    f" 在同轴位置，已合并为阶梯凸台 {stepped.boss_id}"
                )
            else:
                merged.append(b1)

        return merged
