"""几何精度验证核心引擎。

实现完整的3D重建→精度验证→报告生成流程。
"""

from __future__ import annotations

import html
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.validation.metrics import (
    DimensionResult,
    MetricsResult,
    TopologyEdge,
    compute_dimension_accuracy,
    compute_feature_iou,
    compute_feature_precision,
    compute_feature_recall,
    compute_tolerance_compliance,
    compute_topology_correctness,
)
from app.validation.benchmark_dataset import (
    BenchmarkDataset,
    PartMetadata,
)


# HTML 报告模板加载

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _load_template(name: str) -> str:
    """从 templates 目录读取指定模板文件内容。

    Args:
        name: 模板文件名（如 ``validation_report.html``）。

    Returns:
        str: 模板文件的文本内容。
    """
    return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")


_VALIDATION_REPORT_TEMPLATE = _load_template("validation_report.html")
# P2-1 重构：CSS 抽出到独立文件，运行时注入到模板的 {css_content} 占位符。
# 保持生成报告自包含（无需外部 .css 依赖），同时消除 3 份副本中的 CSS 重复。
_VALIDATION_REPORT_CSS = _load_template("validation_report.css")


@dataclass
class DimensionCheckResult:
    dimension_name: str
    nominal: float
    measured: float
    deviation: float
    tolerance_upper: float
    tolerance_lower: float
    within_tolerance: bool
    deviation_percent: float


@dataclass
class FeatureCheckResult:
    feature_name: str
    detected: bool
    confidence: float
    iou: float
    feature_type: str


@dataclass
class ValidationReport:
    part_id: str
    part_name: str
    timestamp: str
    metrics: MetricsResult
    dimension_checks: list[DimensionCheckResult]
    feature_checks: list[FeatureCheckResult]
    topology_checks: list[dict[str, Any]]
    overall_pass: bool
    validation_duration_seconds: float
    warnings: list[str]
    errors: list[str]
    report_version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "part_id": self.part_id,
            "part_name": self.part_name,
            "timestamp": self.timestamp,
            "metrics": self.metrics.to_dict(),
            "dimension_checks": [
                {
                    "dimension_name": d.dimension_name,
                    "nominal": d.nominal,
                    "measured": d.measured,
                    "deviation": round(d.deviation, 3),
                    "tolerance_upper": d.tolerance_upper,
                    "tolerance_lower": d.tolerance_lower,
                    "within_tolerance": d.within_tolerance,
                    "deviation_percent": round(d.deviation_percent, 3),
                }
                for d in self.dimension_checks
            ],
            "feature_checks": [
                {
                    "feature_name": f.feature_name,
                    "detected": f.detected,
                    "confidence": round(f.confidence, 4),
                    "iou": round(f.iou, 4),
                    "feature_type": f.feature_type,
                }
                for f in self.feature_checks
            ],
            "topology_checks": self.topology_checks,
            "overall_pass": self.overall_pass,
            "validation_duration_seconds": round(self.validation_duration_seconds, 2),
            "warnings": self.warnings,
            "errors": self.errors,
            "report_version": self.report_version,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


class GeometricValidator:
    """3D重建几何精度验证器。"""

    # 默认质量判定阈值（命名常量，便于统一调参与合规审计）
    # 尺寸偏差上限（mm）：超过此值判定为不合格，工业典型值 0.05-0.2mm
    DEFAULT_FAIL_ON_DIMENSION_DEVIATION = 0.1
    # 特征召回率下限：低于此值判定为不合格，工业典型值 0.85-0.95
    DEFAULT_FAIL_ON_FEATURE_RECALL = 0.90
    # 公差符合度下限（%）：低于此值判定为不合格，工业典型值 90-98%
    DEFAULT_FAIL_ON_TOLERANCE_COMPLIANCE = 95.0

    def __init__(
        self,
        dataset: BenchmarkDataset | None = None,
        fail_on_dimension_deviation: float | None = None,
        fail_on_feature_recall: float | None = None,
        fail_on_tolerance_compliance: float | None = None,
    ) -> None:
        self.dataset = dataset or BenchmarkDataset()
        # P2 硬编码修复：阈值默认值提取为模块级常量，便于统一调参与合规审计
        self.fail_on_dimension_deviation = (
            fail_on_dimension_deviation
            if fail_on_dimension_deviation is not None
            else self.DEFAULT_FAIL_ON_DIMENSION_DEVIATION
        )
        self.fail_on_feature_recall = (
            fail_on_feature_recall if fail_on_feature_recall is not None else self.DEFAULT_FAIL_ON_FEATURE_RECALL
        )
        self.fail_on_tolerance_compliance = (
            fail_on_tolerance_compliance
            if fail_on_tolerance_compliance is not None
            else self.DEFAULT_FAIL_ON_TOLERANCE_COMPLIANCE
        )

    def validate_reconstruction(
        self,
        part_id: str,
        reconstructed_model: dict[str, Any] | None = None,
        input_views_path: str | None = None,
        *,
        allow_mock_fallback: bool = False,
    ) -> ValidationReport:
        start_time = time.perf_counter()
        warnings: list[str] = []
        errors: list[str] = []

        metadata = self.dataset.load_metadata(part_id)

        # P1 学术诚信修复：默认禁止 mock 数据降级。
        # 早期实现用 random.seed(42) + random.uniform() 生成假尺寸/特征置信度，
        # 导致几何精度验证结果完全不可信，违反学术诚信（项目目标期刊：Journal of
        # Intelligent Manufacturing）。生产代码调用时必须传入真实 reconstructed_model，
        # 缺失时显式抛 ValueError。测试代码可通过 allow_mock_fallback=True 显式开启
        # mock 降级路径，但生产路径严格禁止。
        if reconstructed_model is None:
            if not allow_mock_fallback:
                raise ValueError(
                    f"validate_reconstruction 缺少 reconstructed_model 参数："
                    f"part_id={part_id}。几何精度验证必须基于真实重建模型，"
                    f"禁止使用 mock 数据降级（学术诚信要求）。"
                    f"如为测试用途，请显式传 allow_mock_fallback=True。"
                )
            warnings.append("使用 mock 重建模型进行验证（仅测试/演示用途，结果不可信，禁止用于生产或学术论文）")
            reconstructed_model = self._mock_reconstructed_model(metadata)

        dim_checks = self.check_dimensions(reconstructed_model, metadata.dimensions)

        feature_checks = self.check_feature_presence(reconstructed_model, metadata.features)

        topo_checks = self._check_topology(reconstructed_model, metadata.topology)

        dim_results = [
            DimensionResult(
                name=d.dimension_name,
                nominal=d.nominal,
                measured=d.measured,
                deviation_abs=abs(d.deviation),
                deviation_rel=abs(d.deviation) / d.nominal if d.nominal != 0 else 0.0,
                tolerance_upper=d.tolerance_upper,
                tolerance_lower=d.tolerance_lower,
                within_tolerance=d.within_tolerance,
            )
            for d in dim_checks
        ]

        detected_features = []
        for f in feature_checks:
            if not f.detected:
                continue
            ft = self._find_feature_def(metadata, f.feature_name)
            detected_features.append(
                {
                    "name": f.feature_name,
                    "confidence": f.confidence,
                    "iou": f.iou,
                    "area": ft.area if ft is not None else 0.0,
                    "bbox": ft.bbox if ft is not None else (0, 0, 0, 0),
                    "volume": ft.volume if ft is not None else 0.0,
                    "bbox_3d": ft.bbox_3d if ft is not None else (0, 0, 0, 0, 0, 0),
                }
            )

        gt_features = [
            {
                "name": ft.name,
                "area": ft.area,
                "bbox": ft.bbox,
                "volume": ft.volume,
                "bbox_3d": ft.bbox_3d,
            }
            for ft in metadata.features
        ]

        dim_accuracy = compute_dimension_accuracy(dim_results)
        f_iou = compute_feature_iou(detected_features, gt_features)
        f_recall = compute_feature_recall(detected_features, gt_features)
        f_precision = compute_feature_precision(detected_features, gt_features)
        t_correct = compute_topology_correctness(
            self._convert_topo_checks_to_edges(topo_checks),
            [
                TopologyEdge(
                    feature_a=gt_top.feature_a,
                    feature_b=gt_top.feature_b,
                    relation=gt_top.relation,
                )
                for gt_top in metadata.topology
            ],
        )
        tol_compliance = compute_tolerance_compliance(dim_results)

        metrics = MetricsResult(
            dimension_accuracy=dim_accuracy,
            feature_iou=f_iou,
            feature_recall=f_recall,
            feature_precision=f_precision,
            topology_correctness=t_correct,
            tolerance_compliance=tol_compliance,
        )

        overall_pass = True
        mean_dev = dim_accuracy.get("mean_absolute_deviation", 0)
        if mean_dev > self.fail_on_dimension_deviation:
            overall_pass = False
            warnings.append(f"尺寸偏差 {mean_dev}mm 超过阈值 {self.fail_on_dimension_deviation}mm")
        if f_recall < self.fail_on_feature_recall:
            overall_pass = False
            warnings.append(f"特征召回率 {f_recall} 低于阈值 {self.fail_on_feature_recall}")
        if tol_compliance < self.fail_on_tolerance_compliance:
            overall_pass = False
            warnings.append(f"公差符合度 {tol_compliance}% 低于阈值 {self.fail_on_tolerance_compliance}%")

        elapsed = time.perf_counter() - start_time

        return ValidationReport(
            part_id=part_id,
            part_name=metadata.part_name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            metrics=metrics,
            dimension_checks=dim_checks,
            feature_checks=feature_checks,
            topology_checks=topo_checks,
            overall_pass=overall_pass,
            validation_duration_seconds=elapsed,
            warnings=warnings,
            errors=errors,
        )

    def check_dimensions(
        self,
        model: dict[str, Any],
        dimension_specs: list[Any],
    ) -> list[DimensionCheckResult]:
        results = []
        measured_dims = model.get("dimensions", {})
        for spec in dimension_specs:
            name = spec.name if hasattr(spec, "name") else spec.get("name", "")
            nominal = spec.nominal if hasattr(spec, "nominal") else spec.get("nominal", 0.0)
            tol_u = spec.tolerance_upper if hasattr(spec, "tolerance_upper") else spec.get("tolerance_upper", 0.0)
            tol_l = spec.tolerance_lower if hasattr(spec, "tolerance_lower") else spec.get("tolerance_lower", 0.0)
            measured = measured_dims.get(name, nominal)
            deviation = measured - nominal
            within = tol_l <= deviation <= tol_u
            dev_pct = (deviation / nominal * 100) if nominal != 0 else 0.0

            results.append(
                DimensionCheckResult(
                    dimension_name=name,
                    nominal=nominal,
                    measured=measured,
                    deviation=deviation,
                    tolerance_upper=tol_u,
                    tolerance_lower=tol_l,
                    within_tolerance=within,
                    deviation_percent=dev_pct,
                )
            )
        return results

    def check_feature_presence(
        self,
        model: dict[str, Any],
        feature_defs: list[Any],
    ) -> list[FeatureCheckResult]:
        results = []
        detected_features = model.get("features", {})
        for ft_def in feature_defs:
            name = ft_def.name if hasattr(ft_def, "name") else ft_def.get("name", "")
            ft_type = ft_def.feature_type if hasattr(ft_def, "feature_type") else ft_def.get("feature_type", "")
            detected = name in detected_features
            conf = (
                detected_features.get(name, {}).get("confidence", 0.0)
                if isinstance(detected_features.get(name), dict)
                else (0.95 if detected else 0.0)
            )
            iou = (
                detected_features.get(name, {}).get("iou", 0.0)
                if isinstance(detected_features.get(name), dict)
                else (0.95 if detected else 0.0)
            )
            results.append(
                FeatureCheckResult(
                    feature_name=name,
                    detected=detected,
                    confidence=conf,
                    iou=iou,
                    feature_type=ft_type,
                )
            )
        return results

    def check_dimension(
        self,
        model: dict[str, Any],
        dimension_spec: dict[str, Any],
    ) -> dict[str, Any]:
        name = dimension_spec.get("name", "")
        nominal = dimension_spec.get("nominal", 0.0)
        tol_u = dimension_spec.get("tolerance_upper", 0.0)
        tol_l = dimension_spec.get("tolerance_lower", 0.0)
        measured_dims = model.get("dimensions", {})
        measured = measured_dims.get(name, nominal)
        deviation = measured - nominal
        return {
            "dimension_name": name,
            "nominal": nominal,
            "measured": measured,
            "deviation": round(deviation, 3),
            "within_tolerance": tol_l <= deviation <= tol_u,
        }

    def _check_topology(
        self,
        model: dict[str, Any],
        gt_topology: list[Any],
    ) -> list[dict[str, Any]]:
        results = []
        detected_edges = model.get("topology", [])
        for gt_edge in gt_topology:
            a = gt_edge.feature_a if hasattr(gt_edge, "feature_a") else gt_edge.get("feature_a", "")
            b = gt_edge.feature_b if hasattr(gt_edge, "feature_b") else gt_edge.get("feature_b", "")
            rel = gt_edge.relation if hasattr(gt_edge, "relation") else gt_edge.get("relation", "")
            matched = any(
                (de.get("feature_a") == a and de.get("feature_b") == b and de.get("relation") == rel)
                or (de.get("from") == a and de.get("to") == b and de.get("type") == rel)
                for de in detected_edges
            )
            results.append(
                {
                    "edge": [a, b],
                    "relation": rel,
                    "matched": matched,
                }
            )
        return results

    def generate_report(
        self,
        report: ValidationReport,
        output_path: str | None = None,
    ) -> str:
        html = self._build_html_report(report)
        if output_path:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(html)
        return html

    def _build_html_report(self, report: ValidationReport) -> str:
        m = report.metrics

        # 修复 [XSS]：所有用户/数据来源控制的字段（dimension_name、feature_name、
        # feature_type、relation、edge 等）在拼接到 HTML 字符串前必须经 html.escape 处理，
        # 避免恶意数据中包含 <script>、&、" 等字符触发反射型或存储型 XSS。
        def _h(value: Any) -> str:
            return html.escape("" if value is None else str(value), quote=True)

        dim_rows = ""
        for d in report.dimension_checks:
            status = "PASS" if d.within_tolerance else "FAIL"
            color = "#4caf50" if d.within_tolerance else "#f44336"
            dim_rows += f"""<tr>
<td>{_h(d.dimension_name)}</td>
<td>{_h(d.nominal)}</td>
<td>{_h(d.measured)}</td>
<td>{_h(round(d.deviation, 3))}</td>
<td>{_h(d.tolerance_lower)} ~ {_h(d.tolerance_upper)}</td>
<td style="color:{color};font-weight:bold">{status}</td>
</tr>"""

        feat_rows = ""
        for f in report.feature_checks:
            status = "DETECTED" if f.detected else "MISSING"
            color = "#4caf50" if f.detected else "#f44336"
            feat_rows += f"""<tr>
<td>{_h(f.feature_name)}</td>
<td>{_h(f.feature_type)}</td>
<td style="color:{color};font-weight:bold">{status}</td>
<td>{_h(round(f.confidence, 4))}</td>
<td>{_h(round(f.iou, 4))}</td>
</tr>"""

        topo_rows = ""
        for t in report.topology_checks:
            edge = t.get("edge", ["-", "-"])
            edge_a = edge[0] if len(edge) > 0 else "-"
            edge_b = edge[1] if len(edge) > 1 else "-"
            rel = t.get("relation", "-")
            matched = bool(t.get("matched", False))
            status = "MATCHED" if matched else "MISMATCH"
            color = "#4caf50" if matched else "#ff9800"
            topo_rows += f"""<tr>
<td>{_h(edge_a)} &harr; {_h(edge_b)}</td>
<td>{_h(rel)}</td>
<td style="color:{color};font-weight:bold">{status}</td>
</tr>"""

        overall_text = "✓ 通过" if report.overall_pass else "✗ 未通过"
        # P2-1：用 CSS 类切换背景色，取代内联 {overall_color} 占位符。
        # .overall 默认背景为 fail 红；.overall.pass 覆盖为通过绿。
        overall_class = " pass" if report.overall_pass else ""

        recall_color = "#4caf50" if m.feature_recall >= 0.90 else "#f44336"
        dim_color = "#4caf50" if m.dimension_accuracy.get("mean_absolute_deviation", 0) <= 0.1 else "#f44336"
        tol_color = "#4caf50" if m.tolerance_compliance >= 95.0 else "#f44336"

        warnings_html = ""
        if report.warnings:
            for w in report.warnings:
                warnings_html += f'<div class="warning">{_h(w)}</div>'

        # 修复 [XSS]：对 part_name、part_id 等报告头字段做 HTML 转义，
        # 同时对 IoU 表的字典键（特征名）做转义以避免通过文件名注入脚本。
        iou_rows_html = (
            "".join(f"<tr><td>{_h(k)}</td><td>{round(float(v), 4):.4f}</td></tr>" for k, v in m.feature_iou.items())
            or '<tr><td colspan="2">无数据</td></tr>'
        )

        return _VALIDATION_REPORT_TEMPLATE.format(
            css_content=_VALIDATION_REPORT_CSS,
            part_name=_h(report.part_name),
            part_id=_h(report.part_id),
            timestamp=_h(report.timestamp),
            duration=f"{report.validation_duration_seconds:.2f}",
            report_version=_h(report.report_version),
            overall_class=overall_class,
            overall_text=overall_text,
            warnings_html=warnings_html,
            dim_color=dim_color,
            mean_dim_dev=f"{m.dimension_accuracy.get('mean_absolute_deviation', 0):.3f}",
            recall_color=recall_color,
            feature_recall=f"{m.feature_recall:.4f}",
            feature_precision=f"{m.feature_precision:.4f}",
            topology_correctness=f"{m.topology_correctness:.4f}",
            tol_color=tol_color,
            tolerance_compliance=f"{m.tolerance_compliance:.1f}",
            max_dim_dev=f"{m.dimension_accuracy.get('max_absolute_deviation', 0):.3f}",
            iou_rows_html=iou_rows_html,
            dim_rows=dim_rows,
            feat_rows=feat_rows,
            topo_rows=topo_rows,
            year=str(datetime.now(timezone.utc).year),
        )

    @staticmethod
    def _find_feature_def(metadata: PartMetadata, name: str) -> Any:
        for f in metadata.features:
            if f.name == name:
                return f
        return None

    @staticmethod
    def _convert_topo_checks_to_edges(
        topo_checks: list[dict[str, Any]],
    ) -> list[TopologyEdge]:
        edges: list[TopologyEdge] = []
        for t in topo_checks:
            # 修复：原 t.get("edge", ("", "")) 的默认值是个空 tuple，会导致 feature_a/feature_b
            # 格式（无 "edge" 键）永远命中 isinstance 分支、被误解析为 ("", "")。改为无默认值。
            edge = t.get("edge")
            if isinstance(edge, (list, tuple)) and len(edge) >= 2:
                a, b = edge[0], edge[1]
            else:
                a = t.get("feature_a", "")
                b = t.get("feature_b", "")
            rel = t.get("relation", "")
            edges.append(TopologyEdge(feature_a=a, feature_b=b, relation=rel))
        return edges

    def _mock_reconstructed_model(self, metadata: PartMetadata) -> dict[str, Any]:
        """生成 mock 重建模型（仅供测试与演示使用，禁止用于生产路径）。

        P1 学术诚信修复说明：
            早期 validate_reconstruction 在 reconstructed_model 缺失时自动调用本方法，
            用 random.seed(42) + random.uniform() 生成假尺寸/特征置信度，
            导致几何精度验证结果完全不可信，违反学术诚信
            （项目目标期刊：Journal of Intelligent Manufacturing）。

            现已改为 validate_reconstruction 默认禁止 mock 降级，
            缺失 reconstructed_model 时抛 ValueError。本方法保留仅供测试代码
            显式调用（通过 allow_mock_fallback=True 路径），生产代码禁止调用。
        """
        import random

        random.seed(42)
        dims = {}
        for d in metadata.dimensions:
            dev = random.uniform(-0.03, 0.03)
            dims[d.name] = round(d.nominal + dev, 3)
        feats = {}
        for f in metadata.features:
            feats[f.name] = {
                "confidence": round(random.uniform(0.91, 0.99), 4),
                "iou": round(random.uniform(0.93, 0.99), 4),
            }
        topo = []
        for t in metadata.topology:
            topo.append(
                {
                    "feature_a": t.feature_a,
                    "feature_b": t.feature_b,
                    "relation": t.relation,
                }
            )
        return {
            "dimensions": dims,
            "features": feats,
            "topology": topo,
        }
