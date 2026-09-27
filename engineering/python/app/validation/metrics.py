"""3D重建几何精度评估指标模块。

实现全部六项精度评估指标：
- dimension_accuracy: 尺寸精度
- feature_iou: 特征交并比
- feature_recall: 特征召回率
- feature_precision: 特征精确率
- topology_correctness: 拓扑正确性
- tolerance_compliance: 公差符合度
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class DimensionResult:
    name: str
    nominal: float
    measured: float
    deviation_abs: float
    deviation_rel: float
    tolerance_upper: float
    tolerance_lower: float
    within_tolerance: bool


@dataclass
class FeatureResult:
    name: str
    detected: bool
    confidence: float
    iou: float


@dataclass
class TopologyEdge:
    feature_a: str
    feature_b: str
    relation: str


@dataclass
class MetricsResult:
    dimension_accuracy: dict[str, Any]
    feature_iou: dict[str, float]
    feature_recall: float
    feature_precision: float
    topology_correctness: float
    tolerance_compliance: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension_accuracy": self.dimension_accuracy,
            "feature_iou": self.feature_iou,
            "feature_recall": round(self.feature_recall, 4),
            "feature_precision": round(self.feature_precision, 4),
            "topology_correctness": round(self.topology_correctness, 4),
            "tolerance_compliance": round(self.tolerance_compliance, 1),
        }


def compute_dimension_accuracy(
    dimensions: list[DimensionResult],
) -> dict[str, Any]:
    deviations_abs = [d.deviation_abs for d in dimensions]
    deviations_rel = [d.deviation_rel for d in dimensions]
    within_count = sum(1 for d in dimensions if d.within_tolerance)

    return {
        "dimensions": [d.__dict__ for d in dimensions],
        "mean_absolute_deviation": round(sum(deviations_abs) / len(deviations_abs), 3) if deviations_abs else 0.0,
        "max_absolute_deviation": round(max(deviations_abs), 3) if deviations_abs else 0.0,
        "mean_relative_deviation": round(sum(deviations_rel) / len(deviations_rel), 5) if deviations_rel else 0.0,
        "max_relative_deviation": round(max(deviations_rel), 5) if deviations_rel else 0.0,
        "within_tolerance_count": within_count,
        "total_count": len(dimensions),
    }


def compute_feature_iou(
    detected_features: list[dict[str, Any]],
    ground_truth_features: list[dict[str, Any]],
    mode: str = "pixel",
) -> dict[str, float]:
    ious: dict[str, float] = {}
    gt_map = {f.get("name", ""): f for f in ground_truth_features}

    for det in detected_features:
        name = str(det.get("name", ""))
        if not name:
            # 修复 [类型安全]：无 name 的检测项不能静默通过，按 0.0 处理并跳过，
            # 避免与 gt_map[""] 冲突导致错误匹配。
            continue
        if name not in gt_map:
            ious[name] = 0.0
            continue

        precomputed = det.get("iou")
        if precomputed is not None and isinstance(precomputed, (int, float)):
            ious[name] = round(float(precomputed), 4)
            continue

        gt = gt_map[name]
        if mode == "pixel":
            det_area = float(det.get("area", 0))
            gt_area = float(gt.get("area", 0))
            det_bbox = det.get("bbox", (0, 0, 0, 0))
            gt_bbox = gt.get("bbox", (0, 0, 0, 0))
            intersection = _bbox_intersection_area(det_bbox, gt_bbox)
        else:
            det_area = float(det.get("volume", 0))
            gt_area = float(gt.get("volume", 0))
            det_bbox = det.get("bbox_3d", (0, 0, 0, 0, 0, 0))
            gt_bbox = gt.get("bbox_3d", (0, 0, 0, 0, 0, 0))
            intersection = _bbox3d_intersection_volume(det_bbox, gt_bbox)

        union = det_area + gt_area - intersection
        ious[name] = round(intersection / union, 4) if union > 0 else 0.0

    return ious


def compute_feature_recall(
    detected_features: list[dict[str, Any]],
    ground_truth_features: list[dict[str, Any]],
    iou_threshold: float = 0.5,
    confidence_threshold: float = 0.5,
) -> float:
    gt_names = {str(f.get("name", "")) for f in ground_truth_features}
    gt_names.discard("")
    if not gt_names:
        return 1.0

    det_by_name: dict[str, dict[str, Any]] = {}
    for feat in detected_features:
        name = str(feat.get("name", ""))
        if name:
            det_by_name[name] = feat

    ious = compute_feature_iou(detected_features, ground_truth_features)
    correct = 0
    for gt in ground_truth_features:
        name = str(gt.get("name", ""))
        if not name:
            continue
        det = det_by_name.get(name)
        if det is None:
            continue
        conf = float(det.get("confidence", 0))
        iou_val = ious.get(name, 0.0)
        if conf >= confidence_threshold and iou_val >= iou_threshold:
            correct += 1

    return round(correct / len(gt_names), 4)


def compute_feature_precision(
    detected_features: list[dict[str, Any]],
    ground_truth_features: list[dict[str, Any]],
    iou_threshold: float = 0.5,
    confidence_threshold: float = 0.5,
) -> float:
    if not detected_features:
        return 1.0

    gt_names = {str(f.get("name", "")) for f in ground_truth_features}
    gt_names.discard("")
    ious = compute_feature_iou(detected_features, ground_truth_features)

    correct = 0
    for det in detected_features:
        name = str(det.get("name", ""))
        if not name or name not in gt_names:
            continue
        conf = float(det.get("confidence", 0))
        iou_val = ious.get(name, 0.0)
        if conf >= confidence_threshold and iou_val >= iou_threshold:
            correct += 1

    return round(correct / len(detected_features), 4)


def compute_topology_correctness(
    detected_edges: list[TopologyEdge],
    ground_truth_edges: list[TopologyEdge],
) -> float:
    if not ground_truth_edges:
        return 1.0

    gt_set = {(e.feature_a, e.feature_b, e.relation) for e in ground_truth_edges}
    det_set = {(e.feature_a, e.feature_b, e.relation) for e in detected_edges}

    intersection = len(det_set & gt_set)

    tp = intersection
    fp = len(det_set - gt_set)
    fn = len(gt_set - det_set)

    denominator = tp + fp + fn
    return round(tp / denominator, 4) if denominator > 0 else 0.0


def compute_tolerance_compliance(
    dimensions: list[DimensionResult],
    tolerance_grades: list[str] | None = None,
) -> float:
    if not dimensions:
        return 100.0

    within = sum(1 for d in dimensions if d.within_tolerance)
    return round(within / len(dimensions) * 100, 1)


def _bbox_intersection_area(
    bbox_a: tuple[float, float, float, float],
    bbox_b: tuple[float, float, float, float],
) -> float:
    x1 = max(bbox_a[0], bbox_b[0])
    y1 = max(bbox_a[1], bbox_b[1])
    x2 = min(bbox_a[2], bbox_b[2])
    y2 = min(bbox_a[3], bbox_b[3])
    if x1 >= x2 or y1 >= y2:
        return 0.0
    return (x2 - x1) * (y2 - y1)


def _bbox3d_intersection_volume(
    bbox_a: tuple[float, float, float, float, float, float],
    bbox_b: tuple[float, float, float, float, float, float],
) -> float:
    x1 = max(bbox_a[0], bbox_b[0])
    y1 = max(bbox_a[1], bbox_b[1])
    z1 = max(bbox_a[2], bbox_b[2])
    x2 = min(bbox_a[3], bbox_b[3])
    y2 = min(bbox_a[4], bbox_b[4])
    z2 = min(bbox_a[5], bbox_b[5])
    if x1 >= x2 or y1 >= y2 or z1 >= z2:
        return 0.0
    return (x2 - x1) * (y2 - y1) * (z2 - z1)
