"""CadQuery generator wrapper for CAD generation."""

from __future__ import annotations

import ast
import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any

# 平台兼容：resource 模块仅在 Unix 可用，Windows 下跳过 RLIMIT 配置
_resource: Any
try:
    import resource as _resource
except ImportError:
    _resource = None

import cadquery as cq

from app.cad.advanced_features import AdvancedFeatureBuilder
from app.cad._brep_validator import (
    BrepValidationReport,
    sanitize_dimensions,
    validate_exported_model,
    validate_workplane,
)

logger = logging.getLogger(__name__)


# CadQuery 临时输出目录的进程级清理
# 历史问题：execute_and_export / generate_3d_model / generate_with_features
# 三处使用固定名称的临时目录（cadquery_output / cadquery_models），产物文件
# 永不回收，高频建模场景下数日内即可耗尽 /tmp 磁盘。
# 修复策略：模块级维护已创建目录集合，atexit 退出时统一清理。
# 不能使用 TemporaryDirectory 上下文管理器，因为返回的 output_path 会被
# 后续步骤（NL2CAD pipeline / API 响应）读取，必须保留到进程退出。
_CADQUERY_TEMP_DIRS: set[Path] = set()


def _register_cadquery_temp_dir(path: Path) -> None:
    """注册一个 CadQuery 临时输出目录，供进程退出时清理。"""
    _CADQUERY_TEMP_DIRS.add(path)


# 安全修复：禁止访问的危险 dunder 属性，防止沙箱逃逸
_DANGEROUS_ATTRS = frozenset(
    {
        "__class__",
        "__mro__",
        "__subclasses__",
        "__globals__",
        "__builtins__",
        "__bases__",
        "__base__",
        "__code__",
        "__func__",
        "__self__",
        "__dict__",
        "__module__",
        "__import__",
        "__loader__",
        "__spec__",
    }
)


from app.cad._cadquery_helpers import (
    _register_cadquery_temp_dir,
    _unpack_generation_params,
    _build_shape_script,
    _build_solid,
    _wrap_script,
    _run_cadquery_script,
    _extract_cv_geometry_params,
    _merge_cv_results,
    _get_image_dimensions,
)


class _CadQueryScriptValidator(ast.NodeVisitor):
    """AST 审计器：拒绝危险属性访问，防止沙箱逃逸。"""

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _DANGEROUS_ATTRS:
            raise CadQueryScriptError(
                f"Access to dangerous attribute '{node.attr}' is forbidden in CadQuery scripts (line {node.lineno})"
            )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        # 禁止 import 语句，仅允许已注入的 cq/cadquery
        raise CadQueryScriptError(f"Import statements are forbidden in CadQuery scripts (line {node.lineno})")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        raise CadQueryScriptError(f"Import statements are forbidden in CadQuery scripts (line {node.lineno})")


class CadQueryError(Exception):
    """Base exception for CadQuery generation errors."""


class CadQueryScriptError(CadQueryError):
    """Raised when executing a CadQuery script fails."""


class CadQueryExportError(CadQueryError):
    """Raised when exporting a model file fails."""


class CadQueryGenerator:
    """CadQuery-based 3D model generator."""

    def __init__(self) -> None:
        self._initialized = True
        logger.info("CadQueryGenerator initialized")

    async def extract_geometry_params_from_views(self, views: dict[str, str]) -> dict[str, Any]:
        """Extract geometry parameters from three-view image paths.

        Uses a lightweight computer-vision pipeline (Pillow + numpy) to
        analyse each view image:

        1. **Image preprocessing** – convert to grayscale, apply a
           Gaussian blur and edge-detection filter, then auto-threshold
           to a binary edge map.
        2. **Connected-component labelling** – find foreground regions
           via a two-pass union-find algorithm and discard noise
           (regions < 0.5 % of image area).
        3. **Shape classification** – for the largest region, compute
           circularity, aspect ratio and taper ratio to classify the
           silhouette as ``box``, ``cylinder``, ``sphere`` or ``cone``.
        4. **Dimension extraction** – derive bounding-box pixel extents
           and scale them (÷ 10) to approximate millimetre dimensions.

        Per-view shape votes are weighted by confidence and merged.  If
        the CV pipeline fails for any view (corrupt file, unsupported
        format, empty edge map) the method falls back to the legacy
        header-only image dimension reader so that callers always
        receive a best-effort result.
        """
        logger.info("Extracting geometry params from views: %s", list(views.keys()))

        await asyncio.sleep(0)

        image_sizes: dict[str, tuple[int, int]] = {}
        cv_results: dict[str, dict[str, Any]] = {}

        for view_name, view_path in views.items():
            view_file = Path(view_path)
            if not view_file.exists():
                logger.warning("View file not found: %s (%s)", view_name, view_path)
                continue

            # CV-based extraction (primary path)
            try:
                cv_out = _extract_cv_geometry_params(view_file)
                if cv_out is not None:
                    cv_results[view_name] = cv_out
                    logger.debug(
                        "CV extracted view %s: shape=%s conf=%.3f bbox=%s",
                        view_name,
                        cv_out["shape_type"],
                        cv_out["confidence"],
                        cv_out["bbox_size"],
                    )
            except (ValueError, KeyError, TypeError, OSError, RuntimeError) as e:
                logger.warning(
                    "CV extraction failed for view %s (%s): %s",
                    view_name,
                    view_path,
                    e,
                )

            # Header-based fallback (always attempted)
            width: float
            height: float
            try:
                width, height = _get_image_dimensions(view_file)
                image_sizes[view_name] = (width, height)
                logger.debug(
                    "View %s header size: %s (%dx%d)",
                    view_name,
                    view_path,
                    width,
                    height,
                )
            except (OSError, ValueError) as e:
                logger.warning("Failed to read view %s (%s): %s", view_name, view_path, e)

        if not image_sizes and not cv_results:
            logger.warning("Could not read any view images; using default geometry params")

        # Merge CV results when available
        if cv_results:
            params = _merge_cv_results(cv_results, image_sizes)
        else:
            # Pure fallback: use header-based image sizes only.
            length: float = 50.0
            width = 30.0
            height = 20.0
            if "front" in image_sizes:
                length = max(image_sizes["front"][0] / 10.0, 10.0)
                height = max(image_sizes["front"][1] / 10.0, 10.0)
            if "top" in image_sizes:
                width = max(image_sizes["top"][1] / 10.0, 10.0)
            if "left" in image_sizes:
                width = max(image_sizes["left"][0] / 10.0, 10.0)

            params = {
                "shape_type": "box",
                "dimensions": {
                    "length": round(length, 1),
                    "width": round(width, 1),
                    "height": round(height, 1),
                },
                "position": {"x": 0, "y": 0, "z": 0},
            }

        logger.info("Extracted params: %s", params)
        return params

    async def generate_script_from_params(
        self,
        params: dict[str, Any],
        library_matches: list[dict[str, Any]] | None = None,
    ) -> str:
        """Generate a CadQuery Python script from geometry parameters."""
        logger.info(
            "Generating script from params: shape=%s, matches=%d",
            params.get("shape_type", "unknown"),
            len(library_matches) if library_matches else 0,
        )

        await asyncio.sleep(0)

        if library_matches:
            best_match = library_matches[0]
            cached_script = best_match.get("cadquery_script", "")
            if cached_script:
                logger.info("Using cached script from model library")
                return cached_script

        if params.get("dimensions") is None:
            raise ValueError(
                "参数错误：dimensions 不能为 None。请确保传入的参数字典中包含有效的 dimensions 字段，"
                "例如 {'length': 50, 'width': 30, 'height': 20}。"
            )

        shape_type, dimensions, position = _unpack_generation_params(params)

        script = _build_shape_script(shape_type, dimensions, position)
        logger.info("Generated CadQuery script (%d chars)", len(script))
        return script

    async def execute_and_export(self, script: str, task_id: str, output_format: str) -> str:
        """Execute a CadQuery script and export the resulting model."""
        logger.info(
            "Executing script for task %s (format=%s, script_len=%d)",
            task_id,
            output_format,
            len(script),
        )

        output_dir = Path(tempfile.gettempdir()) / "cadquery_output"
        output_dir.mkdir(parents=True, exist_ok=True)
        _register_cadquery_temp_dir(output_dir)

        output_path = output_dir / f"{task_id}.{output_format}"
        export_func_map = {
            "stl": "cq.exporters.export",
            "obj": "cq.exporters.export",
            "gltf": "cq.exporters.export",
            "step": "cq.exporters.export",
        }

        if output_format not in export_func_map:
            supported = ", ".join(export_func_map.keys())
            raise CadQueryExportError(
                f"CAD 模型导出失败：不支持的输出格式 '{output_format}'。支持的格式包括：{supported}。请将 output_format 参数设置为支持的格式之一后重试。"
            )

        wrapped_script = _wrap_script(script, str(output_path), output_format)

        _run_cadquery_script(wrapped_script, task_id)

        if not output_path.exists():
            raise CadQueryExportError(f"Export failed: output file not created at {output_path}")

        # B-rep 拓扑校验（导出后回读；STEP 全量 / STL 基本 / obj/gltf 跳过）
        vreport = validate_exported_model(output_path, output_format)
        if vreport is not None and vreport.errors:
            raise CadQueryExportError(
                f"CAD 模型导出成功但拓扑校验失败（错误码 {vreport.error_codes}）：{vreport.summary()}。"
                "建议检查生成脚本的几何操作或参数。"
            )
        logger.debug(
            "Exported model B-rep report: %s",
            vreport.summary() if vreport is not None else "skipped (format not validated)",
        )
        logger.info("Model exported: %s (%d bytes)", output_path, output_path.stat().st_size)
        return str(output_path)

    def generate_3d_model(self, params: dict[str, Any]) -> str:
        """Generate a 3D model from parameters and return the model path."""
        logger.info("Generating 3D model with params: %s", params)

        shape_type, dimensions, position = _unpack_generation_params(params)

        try:
            result = _build_solid(shape_type, dimensions, position)

            # B-rep 拓扑校验：拦截破损/退化实体（建模后、导出前）
            report = validate_workplane(result)
            if report.errors:
                raise CadQueryScriptError(
                    f"CAD 模型拓扑校验失败（错误码 {report.error_codes}）：{report.summary()}。"
                    "可能原因：几何参数退化或特征操作产生破损几何。请调整参数后重试。"
                )

            output_dir = Path(tempfile.gettempdir()) / "cadquery_models"
            output_dir.mkdir(parents=True, exist_ok=True)
            _register_cadquery_temp_dir(output_dir)
            output_path = output_dir / f"model_{shape_type}.stl"
            cq.exporters.export(result, str(output_path))
            logger.info("3D model exported to %s", output_path)
            return str(output_path)
        except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
            logger.error("CAD 模型生成失败: %s", e, exc_info=True)
            raise CadQueryScriptError(
                f"CAD 模型生成失败：执行 CadQuery 脚本时出现异常。错误类型: {type(e).__name__}。"
                "可能原因：1) 脚本语法错误；2) 几何参数无效；"
                "3) CadQuery 版本不兼容。"
                "请检查脚本内容和几何参数，或查看日志获取详细错误信息。"
            ) from e

    def generate_with_features(
        self,
        params: dict[str, Any],
        features: list[dict[str, Any]] | None = None,
        output_format: str = "stl",
    ) -> str:
        """带高级特征的 3D 模型生成。

        Args:
            params: 同 generate_3d_model
            features: 特征列表，每项 dict 包含 type 字段：
                - "chamfer": {length, angle?, edges_selector?}
                - "fillet": {radius, edges_selector?}
                - "step": {length?, width?, height?, offset_x?, offset_y?, offset_z?}
                - "slot": {center_x, center_y, length, width, depth, axis?, surface_z?}
            output_format: 输出格式（stl/step/obj/gltf）

        Returns:
            输出文件路径
        """
        logger.info("生成带特征的 3D 模型: params=%s features=%d", params, len(features or []))

        shape_type, dimensions, position = _unpack_generation_params(params)
        base = _build_solid(shape_type, dimensions, position)

        if features:
            builder = AdvancedFeatureBuilder()
            base = builder.apply_features(base, features)

        # B-rep 拓扑校验：捕获特征操作产生的破损几何
        # （apply_features 内部会吞掉单特征异常，只有几何校验能兜住坏结果）
        report = validate_workplane(base)
        if report.errors:
            raise CadQueryScriptError(
                f"带特征的模型拓扑校验失败（错误码 {report.error_codes}）：{report.summary()}。"
                "可调用 generate_3d_model_with_retry 自动剔除致错特征并重生成。"
            )

        output_dir = Path(tempfile.gettempdir()) / "cadquery_models"
        output_dir.mkdir(parents=True, exist_ok=True)
        _register_cadquery_temp_dir(output_dir)
        output_path = output_dir / f"model_{shape_type}_with_features.{output_format}"
        cq.exporters.export(base, str(output_path))
        logger.info("带特征的 3D 模型已导出: %s", output_path)
        return str(output_path)

    def generate_from_views(self, front: str, top: str, side: str) -> str:
        """Generate 3D model from three-view drawing descriptions."""
        logger.info(
            "Generating 3D model from three-view drawings (front=%s, top=%s, side=%s)",
            front[:50],
            top[:50],
            side[:50],
        )
        params = {
            "shape_type": "box",
            "dimensions": {"length": 50, "width": 30, "height": 20},
            "position": {"x": 0, "y": 0, "z": 0},
            "views": {"front": front, "top": top, "side": side},
        }
        return self.generate_3d_model(params)

    def generate_3d_model_with_retry(
        self,
        params: dict[str, Any],
        features: list[dict[str, Any]] | None = None,
        output_format: str = "stl",
        max_retries: int = 3,
    ) -> tuple[str, BrepValidationReport, int]:
        """带失败重生成的 3D 建模（借鉴 Pointer-CAD 的生成后校验 + 重生成闭环）。

        流程：
        1. 尝试生成并导出；
        2. 导出后回读校验（STEP 全量 / STL 基本 / obj/gltf 跳过）；
        3. 校验失败 → 逐个剔除致错特征后重试；无特征时夹取退化尺寸后重试；
        4. 达到 max_retries 仍失败则抛出 CadQueryScriptError。

        Returns:
            (输出文件路径, 最终校验报告, 实际尝试次数)
        """
        logger.info(
            "generate_3d_model_with_retry: features=%d max_retries=%d format=%s",
            len(features or []),
            max_retries,
            output_format,
        )
        feats: list[dict[str, Any]] = list(features) if features else []
        last_report: BrepValidationReport | None = None
        sanitized_once = False

        for attempt in range(1, max_retries + 1):
            try:
                path = self.generate_with_features(params, feats, output_format)
                last_report = validate_exported_model(path, output_format)
                if last_report is not None and last_report.errors:
                    raise CadQueryScriptError(
                        f"导出模型校验失败（错误码 {last_report.error_codes}）：{last_report.summary()}"
                    )
                logger.info("重生成第 %d 次成功: %s", attempt, path)
                if last_report is None:
                    raise CadQueryScriptError("导出模型校验失败：未生成校验报告")
                return path, last_report, attempt
            except CadQueryError as e:
                # 恢复策略 1：剔除致错特征（校验失败通常由某个特征引起）
                if feats:
                    dropped = feats.pop()
                    logger.warning(
                        "第 %d 次生成校验失败（%s），剔除特征 %s 后重试",
                        attempt,
                        e,
                        dropped,
                    )
                    continue
                # 恢复策略 2：夹取退化尺寸
                if not sanitized_once:
                    sanitized = sanitize_dimensions(params)
                    if sanitized != params:
                        params = sanitized
                        sanitized_once = True
                        logger.warning("第 %d 次失败，夹取退化尺寸后重试: %s", attempt, params)
                        continue
                raise
            except Exception as e:  # noqa: BLE001 - OCCT 异常类型繁杂（Standard_DomainError 等）
                if feats:
                    dropped = feats.pop()
                    logger.warning("第 %d 次生成异常（%s），剔除特征 %s 后重试", attempt, e, dropped)
                    continue
                if not sanitized_once:
                    sanitized = sanitize_dimensions(params)
                    if sanitized != params:
                        params = sanitized
                        sanitized_once = True
                        logger.warning("第 %d 次生成异常，夹取退化尺寸后重试: %s", attempt, params)
                        continue
                raise CadQueryScriptError(f"CAD 模型生成失败（第 {attempt} 次）: {e}") from e

        raise CadQueryScriptError(
            f"重生成 {max_retries} 次后仍校验失败，最后一次报告: "
            f"{last_report.summary() if last_report is not None else '无报告'}"
        )
