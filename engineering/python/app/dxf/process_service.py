"""DXF 端到端处理服务（稳定入口）。

为什么单独一个服务？
    - ``DxfParser`` / ``DxfToModelConverter`` / ``FeatureExtractor`` / 后处理
      各自有细节和异常，调用方要写一堆 try/except + 串接
    - 业务方（web / desktop / e2e test）只想要：
        "我给个 DXF 路径，给我一个 3D STL/GCode/Features 一站式结果"
    - :class:`DxfProcessService` 把这条流水线收口，输出统一结构

流水线：
    1. 解析 DXF（带友好错误信息）
    2. 提取特征（带 fallback）
    3. 3D 转换（box / cylinder / polyline）
    4. 应用高级特征（chamfer / fillet / step / slot）
    5. 后处理生成 G 代码
    6. 影子模式记录（产品轨 + 研究轨）

所有错误都不抛出，统一回传到 :class:`DxfProcessResult.errors`。
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 内环与已识别孔去重的容差（mm）：圆心距、等效半径差
HOLE_MATCH_CENTER_TOL = 1.0
HOLE_MATCH_RADIUS_TOL = 0.5


def _ring_effective_radius(ring: dict[str, Any]) -> float:
    """环的等效圆半径：优先用 CIRCLE 原始半径，否则按面积反算。"""
    r = ring.get("radius")
    if isinstance(r, (int, float)) and r > 0:
        return float(r)
    area = abs(float(ring.get("area") or 0.0))
    return (area / math.pi) ** 0.5 if area > 0 else 0.0


def _matches_hole(ring: dict[str, Any], holes: list[dict[str, Any]]) -> bool:
    """该环是否已被孔特征覆盖（避免同一个孔既钻孔又挖槽）。"""
    rr = _ring_effective_radius(ring)
    if rr <= 0:
        return False
    cx = float(ring.get("center_x") or 0.0)
    cy = float(ring.get("center_y") or 0.0)
    for h in holes:
        try:
            hr = float(h.get("diameter") or 0.0) / 2.0
            hx = float(h.get("position_x", h.get("center_x", 0.0)) or 0.0)
            hy = float(h.get("position_y", h.get("center_y", 0.0)) or 0.0)
        except (TypeError, ValueError):
            continue
        if hr <= 0:
            continue
        if abs(rr - hr) <= HOLE_MATCH_RADIUS_TOL and math.hypot(cx - hx, cy - hy) <= HOLE_MATCH_CENTER_TOL:
            return True
    return False


def contours_to_part_features(
    contours_detail: list[dict[str, Any]],
    holes: list[dict[str, Any]],
    thickness: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """把 DXF 闭合轮廓环翻译成工艺规划器的特征字典（轮廓桥）。

    Args:
        contours_detail: FeatureExtractor 下发的环明细（已按面积降序）
        holes: 已识别的孔特征（用于内环去重）
        thickness: 板厚/加工深度（mm），来自 overall_height（2D 图纸为推断值）

    Returns:
        (outlines, cavities, notes)
        - outlines: 外轮廓特征（type="outer_contour" → 精铣外形，偏置走刀把零件切出）
        - cavities: 内环特征（type="through_pocket" → 精铣挖槽，按多边形偏置环切）
        - notes: 口径说明（深度为推断值、含弧段弦近似等），供如实标注

    设计口径：
        - 2D 图纸没有 Z 信息，切深一律取板厚（贯通切割），并在 notes 里如实
          标注"深度为推断值"，不假装是图纸给定。
        - 内环若与已识别孔同心同径则跳过：孔走钻孔循环，不再重复挖槽。
        - 环顶点顺序即走刀顺序；带符号面积决定内外，最大者为外轮廓。
    """
    outlines: list[dict[str, Any]] = []
    cavities: list[dict[str, Any]] = []
    notes: list[str] = []

    if not contours_detail:
        return outlines, cavities, notes

    depth = float(thickness or 0.0)
    if depth <= 0:
        depth = 10.0
        notes.append("板厚缺失，外轮廓/型腔切深按 10mm 推断")
    else:
        notes.append(f"外轮廓/型腔切深取板厚 {depth}mm（2D 图纸推断值，贯通切割）")

    ordered = sorted(contours_detail, key=lambda c: abs(float(c.get("area") or 0.0)), reverse=True)
    outer = ordered[0]
    inner = ordered[1:]

    def _vertices(ring: dict[str, Any]) -> list[list[float]]:
        raw = ring.get("vertices") or []
        pts: list[list[float]] = []
        for p in raw:
            try:
                pts.append([float(p[0]), float(p[1])])
            except (TypeError, ValueError, IndexError):
                continue
        return pts

    outer_pts = _vertices(outer)
    if len(outer_pts) >= 3:
        outlines.append(
            {
                "name": f"OUTLINE_{outer.get('source', 'chained').upper()}",
                "type": "outer_contour",
                "geometric_type": "outline",
                "tolerance_grade": "IT8",
                "surface_roughness_ra": 3.2,
                "is_datum_candidate": False,
                "priority": "high",
                "surface": "A",
                "dimensions": {
                    "length": float(outer.get("length") or 0.0),
                    "width": float(outer.get("width") or 0.0),
                    "depth": depth,
                    "center_x": float(outer.get("center_x") or 0.0),
                    "center_y": float(outer.get("center_y") or 0.0),
                },
                "parent_feature": "",
                "tolerances": {},
                "contour": outer_pts,
            }
        )
        if outer.get("approximated_arcs"):
            notes.append("外轮廓含弧段，按折线采样/弦近似（非解析圆弧插补）")
    else:
        notes.append("外轮廓顶点不足 3，未生成外形工序（刀轨退回包络矩形面铣）")

    skipped_as_holes = 0
    for i, ring in enumerate(inner, start=1):
        pts = _vertices(ring)
        if len(pts) < 3:
            continue
        if _matches_hole(ring, holes):
            skipped_as_holes += 1
            continue
        cavities.append(
            {
                "name": f"POCKET_{i:03d}",
                "type": "through_pocket",
                "geometric_type": "pocket",
                "tolerance_grade": "IT8",
                "surface_roughness_ra": 3.2,
                "is_datum_candidate": False,
                "priority": "medium",
                "surface": "A",
                "dimensions": {
                    "length": float(ring.get("length") or 0.0),
                    "width": float(ring.get("width") or 0.0),
                    "depth": depth,
                    "center_x": float(ring.get("center_x") or 0.0),
                    "center_y": float(ring.get("center_y") or 0.0),
                },
                "parent_feature": "",
                "tolerances": {},
                "contour": pts,
            }
        )
    if skipped_as_holes:
        notes.append(f"{skipped_as_holes} 个内环与已识别孔同心同径，交由钻孔循环处理（不重复挖槽）")

    return outlines, cavities, notes


@dataclass
class StageResult:
    """单阶段结果。"""

    name: str
    success: bool
    latency_ms: float = 0.0
    summary: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class DxfProcessResult:
    """DXF 端到端处理结果。"""

    file_path: str = ""
    file_name: str = ""
    success: bool = False
    total_latency_ms: float = 0.0
    parse: StageResult | None = None
    features: StageResult | None = None
    model3d: StageResult | None = None
    gcode: StageResult | None = None
    output_files: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "file_name": self.file_name,
            "success": self.success,
            "total_latency_ms": round(self.total_latency_ms, 2),
            "parse": asdict(self.parse) if self.parse else None,
            "features": asdict(self.features) if self.features else None,
            "model3d": asdict(self.model3d) if self.model3d else None,
            "gcode": asdict(self.gcode) if self.gcode else None,
            "output_files": self.output_files,
            "errors": self.errors,
            "warnings": self.warnings,
        }


class DxfProcessService:
    """DXF 端到端服务（无状态，可多实例）。

    简单用法::

        svc = DxfProcessService()
        r = svc.process("part.dxf", output_dir="data/outputs/test1")
        # r.success, r.output_files
    """

    def __init__(self, default_postprocessor: str = "fanuc_0i") -> None:
        self._default_postprocessor = default_postprocessor

    # ============================================================== 入口

    def process(
        self,
        dxf_path: str | Path,
        output_dir: str | Path | None = None,
        postprocessor: str | None = None,
        user_id: str | None = None,
        material: str = "45钢",
    ) -> DxfProcessResult:
        """一站式处理 DXF 文件。

        Args:
            dxf_path: DXF 文件路径
            output_dir: 输出目录；提供时才执行 3D 导出与 G 代码落盘
            postprocessor: 目标控制器方言（fanuc_0i / siemens_840d / ...）
            user_id: 触发用户（用于影子模式/审计）
            material: 零件材料名（工艺规划知识库查询用）
        """
        t0 = time.time()
        path = Path(dxf_path)
        result = DxfProcessResult(file_path=str(path), file_name=path.name)
        out_dir: Path | None = None
        if output_dir is not None:
            out_dir = Path(output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)

        # 1. 解析
        result.parse = self._run_parse(path, user_id)
        if not result.parse.success:
            result.errors.extend([result.parse.error] if result.parse.error else [])
            result.total_latency_ms = (time.time() - t0) * 1000
            return result

        # 2. 特征
        result.features = self._run_features(path, user_id)
        if not result.features.success:
            result.warnings.append(f"特征提取失败: {result.features.error}；继续 3D 转换（仅基于 polylines）")

        # 3. 3D
        result.model3d = self._run_model3d(path, out_dir, user_id)
        if not result.model3d.success and not result.model3d.summary:
            result.warnings.append(f"3D 转换失败: {result.model3d.error}")

        # 4. G 代码（可选，依赖 output_dir 落盘）
        if out_dir is not None:
            ctl = postprocessor or self._default_postprocessor
            features_summary = result.features.summary if result.features else {}
            result.gcode = self._run_gcode(path, out_dir, ctl, user_id, features_summary, material)

        # 收集输出文件
        if out_dir is not None:
            for f in out_dir.iterdir():
                if f.is_file():
                    result.output_files[f.name] = str(f)

        result.success = bool(result.parse.success) and (
            result.model3d is None or result.model3d.success or bool(result.model3d.summary)
        )
        result.total_latency_ms = (time.time() - t0) * 1000
        # 影子模式：研究轨 IJepa-3D chamfer 启发式识别（不阻塞产品流程）
        try:
            self._run_ijepa3d_shadow(path, result, user_id)
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as e:
            logger.warning("ijepa3d shadow run failed: %s", e, exc_info=True)
        # 桥接层落盘
        try:
            from app.research_bridge import UsageDataCollector

            UsageDataCollector.get_instance().record_recognition(
                feature="dxf_pipeline",
                dxf_path=str(path),
                success=result.success,
                latency_ms=int(result.total_latency_ms),
                user_id=user_id,
                extra={
                    "parse_ok": result.parse.success,
                    "features_ok": bool(result.features and result.features.success),
                    "model3d_ok": bool(result.model3d and result.model3d.success),
                    "gcode_ok": bool(result.gcode and result.gcode.success),
                    "output_files": list(result.output_files.keys()),
                },
            )
        except (OSError, RuntimeError, ImportError) as e:
            logger.warning("bridge collect failed: %s", e, exc_info=True)
        return result

    def _run_ijepa3d_shadow(
        self,
        path: Path,
        result: "DxfProcessResult",
        user_id: str | None,
    ) -> None:
        """研究轨影子模式：跑 IJepa-3D chamfer 启发式识别。

        与产品轨（FeatureExtractor）输出对比，diff 落盘到
        data/bridge/usage_logs/shadow_diff.jsonl。
        不影响主流程 result.success / result.errors。
        """
        try:
            # P0#3 解耦: 通过 research_bridge 延迟导入
            from app.ai.lnn._research_bridge import get_multimodal_jepa_chamfer

            detect_all_extended = get_multimodal_jepa_chamfer()
            if detect_all_extended is None:
                raise ImportError("multimodal_jepa not available")
            from app.dxf.dxf_parser import DxfParser
        except ImportError as e:
            logger.warning("ijepa3d import failed: %s", e, exc_info=True)
            return

        # 解析 DXF 拿几何
        try:
            parsed = DxfParser().parse(str(path))
        except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
            logger.warning("ijepa3d shadow parse failed: %s", e, exc_info=True)
            return

        # 跑启发式（使用 detect_all_extended：8 个识别器）
        t0 = time.time()
        try:
            research_feats = detect_all_extended(parsed)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as e:
            # detect_all_extended 内部已对 detect_all 做了 try-except 保护，
            # 这里仅记录日志，不再回退调用 detect_all（避免重复抛出相同异常）
            logger.warning("ijepa3d shadow detect_all_extended failed: %s", e, exc_info=True)
            research_feats = []
        research_latency_ms = int((time.time() - t0) * 1000)

        # 拿产品轨 baseline
        product_feats: list[dict] = []
        if result.features and result.features.success:
            feats_summary = result.features.summary.get("features", {})
            for h in feats_summary.get("holes", []):
                product_feats.append({"type": "HOLE", "source": "product"})
            for p in feats_summary.get("planes", []):
                product_feats.append({"type": "PLANE", "source": "product"})

        research_count = len(research_feats)
        # 高级特征（chamfer / fillet / step / slot / multi_cavity / island / long_cavity / hole_array）
        advanced_types = {
            "chamfer",
            "fillet",
            "step",
            "slot",
            "pocket",  # multi_cavity + long_cavity 标记为 pocket
            "boss",  # island 标记为 boss
            "hole",  # hole_array 标记为 hole
        }
        research_advanced = sum(1 for f in research_feats if f.type.value in advanced_types)

        # 落盘 diff
        from pathlib import Path as _Path

        diff_path = _Path("data/bridge/usage_logs/shadow_diff.jsonl")
        diff_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.time(),
            "dxf": path.name,
            "research_advanced_count": research_advanced,
            "research_total": research_count,
            "research_latency_ms": research_latency_ms,
            "product_feature_count": len(product_feats),
            "delta_advanced": research_advanced - 0,  # 产品轨 baseline 当前不识别 chamfer
            "research_features_preview": [
                {
                    "type": f.type.value,
                    "confidence": f.confidence,
                    "params": f.params,
                }
                for f in research_feats[:5]
            ],
            "user_id_hash": hash(user_id) if user_id else None,
        }
        with diff_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def _run_parse(self, path: Path, user_id: str | None) -> StageResult:
        t0 = time.time()
        try:
            from app.dxf.dxf_parser import DxfParser
            from app.dxf.exceptions import DxfParseError, DxfFormatError

            parser = DxfParser()
            # parse() 现已原生支持 user_id 关键字参数（仅用于桥接层数据收集）
            parsed = parser.parse(str(path), user_id=user_id)
            ok = parsed.success
            err = "" if ok else "; ".join(parsed.errors)
            return StageResult(
                name="parse",
                success=ok,
                latency_ms=(time.time() - t0) * 1000,
                summary=parsed.to_dict(),
                error=err,
            )
        except (DxfParseError, DxfFormatError) as e:
            logger.warning("DXF parse failed: %s", e, exc_info=True)
            return StageResult(
                name="parse",
                success=False,
                latency_ms=(time.time() - t0) * 1000,
                error="DXF 文件解析失败，请检查文件格式",
            )
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as e:
            logger.error("Unexpected DXF parse error: %s", e, exc_info=True)
            return StageResult(
                name="parse",
                success=False,
                latency_ms=(time.time() - t0) * 1000,
                error="DXF 解析遇到未知错误，请联系管理员",
            )

    def _run_features(self, path: Path, user_id: str | None) -> StageResult:
        t0 = time.time()
        try:
            from app.dxf.feature_extractor import FeatureExtractor

            extractor = FeatureExtractor()
            r = extractor.extract(str(path))
            return StageResult(
                name="features",
                success=len(r.errors) == 0,
                latency_ms=(time.time() - t0) * 1000,
                summary={
                    "hole_count": r.hole_count,
                    "overall_length": r.overall_length,
                    "overall_width": r.overall_width,
                    "overall_height": r.overall_height,
                    # 包络角点：没有它刀轨只能从原点起扫，非原点图纸会扫到工件外
                    "overall_min_x": r.overall_min_x,
                    "overall_min_y": r.overall_min_y,
                    # 2026-10 轮廓桥：闭合环明细（外轮廓 + 内环）。
                    # 此前只有 holes/planes 下发，规划器拿不到零件外形，
                    # 20/20 案例的刀轨策略退化为 face_raster（外形从未被切削）。
                    "contour_count": r.contour_count,
                    "contours_detail": [c.to_dict() for c in r.contours],
                    # 2026-09 P1 特征桥：孔/平面明细此前在此处被丢弃（只留计数），
                    # 导致编排器 dxf_to_gcode 链拿不到规划器所需的 holes 数据，
                    # 真实 DXF 端到端必然"工序规划结果为空"。明细已由
                    # FeatureExtractor 算出，序列化随 summary 下发。
                    "holes_detail": [h.to_dict() for h in r.holes],
                    "planes_detail": [p.to_dict() for p in r.planes],
                },
                error="; ".join(r.errors) if r.errors else "",
            )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as e:
            logger.error("DXF feature extraction failed: %s", e, exc_info=True)
            return StageResult(
                name="features",
                success=False,
                latency_ms=(time.time() - t0) * 1000,
                error=f"feature extraction failed: {e}",
            )

    def _run_model3d(self, path: Path, out_dir: Path | None, user_id: str | None) -> StageResult:
        t0 = time.time()
        try:
            from app.dxf.dxf_parser import DxfParser
            from app.dxf.dxf_to_model import DxfToModelConverter

            parsed = DxfParser().parse(str(path))
            conv = DxfToModelConverter()
            # 优先用 polylines，没 polylines 才退化到 features
            if parsed.polylines:
                result = conv.convert_from_polylines(parsed.polylines, height=10.0)
            else:
                from app.dxf.feature_extractor import FeatureExtractor

                feats = FeatureExtractor().extract(str(path))
                result = conv.convert(feats, user_id=user_id, source_dxf=str(path))
            ok = result.success
            files = []
            if ok and out_dir is not None and result.workplane is not None:
                try:
                    stl_path = out_dir / f"{path.stem}.stl"
                    conv.export_stl(result, stl_path)
                    files.append(str(stl_path))
                except (OSError, RuntimeError, ValueError, TypeError) as e:
                    logger.warning("STL 导出失败: %s", e, exc_info=True)
            return StageResult(
                name="model3d",
                success=ok,
                latency_ms=(time.time() - t0) * 1000,
                summary={
                    "length": result.length,
                    "width": result.width,
                    "height": result.height,
                    "hole_count": result.hole_count,
                    "exported_files": files,
                },
                error="; ".join(result.errors) if result.errors else "",
            )
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as e:
            logger.error("DXF 3D conversion failed: %s", e, exc_info=True)
            return StageResult(
                name="model3d",
                success=False,
                latency_ms=(time.time() - t0) * 1000,
                error=f"3d conversion failed: {e}",
            )

    def _run_gcode(
        self,
        path: Path,
        out_dir: Path,
        controller: str,
        user_id: str | None,
        features_summary: dict[str, Any] | None = None,
        material: str = "45钢",
    ) -> StageResult:
        t0 = time.time()
        try:
            # 方言校验：未知控制器回退 fanuc_0i（与 PostProcessorRegistry 语义一致）
            from app.postprocessor.registry import PostProcessorRegistry

            regs = PostProcessorRegistry()
            try:
                regs.get_processor(controller)
            except KeyError:
                controller = "fanuc_0i"

            # 真实工艺规划链：特征明细 → ProcessPlanningPipeline → 完整程序文本。
            # 此前此处只写 header/footer 空程序作冒烟占位，HTTP 层"端到端"
            # 实际产不出可用 NC 代码。
            from app.process_planning.pipeline import ProcessPlanningPipeline

            summary = features_summary or {}
            holes = [
                {
                    "id": h.get("hole_id", f"H{i + 1:03d}"),
                    "type": h.get("hole_type", "through_hole"),
                    "position": [h.get("center_x", 0.0), h.get("center_y", 0.0), 0.0],
                    "diameter": h.get("diameter", 0.0),
                    "depth": h.get("depth", 0.0),
                    "tolerance_grade": h.get("tolerance_grade", "H8"),
                    "surface": h.get("surface", "A"),
                }
                for i, h in enumerate(summary.get("holes_detail", []))
            ]
            part_description: dict[str, Any] = {
                "material": material,
                "part_type": "general",
                "holes": holes,
                "overall_dimensions": {
                    "length": summary.get("overall_length", 0.0),
                    "width": summary.get("overall_width", 0.0),
                    "height": summary.get("overall_height", 0.0),
                    "min_x": summary.get("overall_min_x", 0.0),
                    "min_y": summary.get("overall_min_y", 0.0),
                },
            }

            # 轮廓桥（2026-10）：把 DXF 闭合环下发为外轮廓/型腔特征，
            # 让刀轨引擎走真实多边形（偏置挖槽 / 外形轮廓铣），
            # 而不是只按包络矩形面铣。无轮廓时保持旧行为（面铣 + 钻孔）。
            outlines, cavities, contour_notes = contours_to_part_features(
                summary.get("contours_detail", []),
                summary.get("holes_detail", []),
                float(summary.get("overall_height", 0.0) or 0.0),
            )
            if outlines:
                part_description["outlines"] = outlines
            if cavities:
                part_description["cavities"] = cavities
            if contour_notes:
                part_description["contour_notes"] = contour_notes
            process_result = ProcessPlanningPipeline().run(
                part_description=part_description,
                controller_type=controller,
            )
            if not process_result.success or not process_result.gcode_result:
                errs = "; ".join(e for s in process_result.stages for e in (s.errors or []))
                return StageResult(
                    name="gcode",
                    success=False,
                    latency_ms=(time.time() - t0) * 1000,
                    error=f"gcode generation failed: {errs or process_result.summary}",
                )

            gcode = process_result.gcode_result.program_text
            g_path = out_dir / f"{path.stem}.{controller}.nc"
            g_path.write_text(gcode, encoding="utf-8")
            op = process_result.operation_plan
            return StageResult(
                name="gcode",
                success=True,
                latency_ms=(time.time() - t0) * 1000,
                summary={
                    "controller": controller,
                    "output": str(g_path),
                    "lines": process_result.gcode_result.total_lines,
                    "operations": len(op.operations) if op else 0,
                    "estimated_time_min": round(op.estimated_time_min, 1) if op else 0.0,
                    "holes": len(holes),
                },
            )
        except (OSError, RuntimeError, KeyError, ValueError, TypeError) as e:
            logger.error("GCode generation failed: %s", e, exc_info=True)
            return StageResult(
                name="gcode",
                success=False,
                latency_ms=(time.time() - t0) * 1000,
                error=f"gcode generation failed: {e}",
            )


__all__ = ["DxfProcessService", "DxfProcessResult", "StageResult"]
