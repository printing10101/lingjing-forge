"""B-rep 拓扑校验器（借鉴 Pointer-CAD 的生成后几何校验思路）。

为 NL2CAD 生成结果提供"几何合法性门禁"：

- 实体存在性检查（产物必须包含 ≥1 个 Solid；Compound 含多实体仅告警）
- OCCT ``isValid()`` 检查（破面 / 非流形 / 自相交等）
- 体积必须 > 0 且有限（防止空实体 / 退化实体）
- 包围盒尺寸必须在 [min_dimension, max_dimension] 内
- 最小边/特征尺寸检查（退化特征预警）

校验结果以 :class:`BrepValidationReport` 返回，调用方（CadQueryGenerator）
据此决定放行 / 拦截 / 触发重生成，构成"生成 → 校验 → 失败重生成"闭环。

注意（cadquery 2.7 实测）：布尔 cut 后的产物 ShapeType 为 ``Compound``
而非 ``Solid``，故本校验器按"包含实体的闭合体积"判定，而非死板要求
ShapeType == SOLID。
"""

from __future__ import annotations

import copy
import logging
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import cadquery as cq

logger = logging.getLogger(__name__)

# 默认阈值（单位：毫米）
DEFAULT_MIN_DIMENSION = 1e-3  # 任何包围盒边低于此值视为退化尺寸
DEFAULT_MAX_DIMENSION = 10000.0  # 超过此值视为异常超大尺寸
DEFAULT_MIN_VOLUME = 1e-6  # mm^3，低于此视为无效实体
DEFAULT_MIN_EDGE_LENGTH = 1e-4  # 最短边长低于此值视为退化几何

# 最小实体拓扑元素数（四面体为最小固体：4 面 / 6 边 / 4 顶点）
MIN_FACES_SOLID = 4
MIN_EDGES_SOLID = 6
MIN_VERTICES_SOLID = 4

# 错误码常量（稳定字符串，测试与日志依赖）
ERR_NOT_SOLID = "NOT_SOLID"
ERR_INVALID_SHAPE = "INVALID_SHAPE"
ERR_ZERO_VOLUME = "ZERO_VOLUME"
ERR_OVERSIZED_DIMENSION = "OVERSIZED_DIMENSION"
ERR_DEGENERATE_DIMENSION = "DEGENERATE_DIMENSION"
ERR_DEGENERATE_EDGE = "DEGENERATE_EDGE"
WARN_SUSPICIOUS_TOPOLOGY = "SUSPICIOUS_TOPOLOGY"
WARN_MULTI_SOLID = "MULTI_SOLID"


@dataclass
class BrepIssue:
    """单条校验问题。"""

    code: str
    severity: str  # "error" | "warning"
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class BrepValidationReport:
    """B-rep 校验报告。"""

    is_valid: bool = False
    issues: list[BrepIssue] = field(default_factory=list)
    shape_type: str | None = None
    volume: float | None = None
    bbox: dict[str, float] | None = None
    num_faces: int | None = None
    num_edges: int | None = None
    num_vertices: int | None = None
    min_edge_length: float | None = None

    @property
    def errors(self) -> list[BrepIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[BrepIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def error_codes(self) -> list[str]:
        return [i.code for i in self.errors]

    @property
    def warning_codes(self) -> list[str]:
        return [i.code for i in self.warnings]

    def summary(self) -> str:
        if self.is_valid:
            return f"B-rep 校验通过（type={self.shape_type}, V={self.volume if self.volume is not None else '?'} mm^3）"
        return f"B-rep 校验失败: {self.error_codes}"

    def __str__(self) -> str:  # pragma: no cover - 仅调试用
        lines = [self.summary()]
        for issue in self.issues:
            lines.append(f"  [{issue.severity}] {issue.code}: {issue.message}")
        return "\n".join(lines)


class BrepValidationError(Exception):
    """校验存在 error 级问题时抛出。"""

    def __init__(self, report: BrepValidationReport) -> None:
        self.report = report
        super().__init__(report.summary())


# 内部工具
def _to_shape(obj: Any) -> cq.Shape:
    """把 Workplane / Shape 统一归一化为单个 cq.Shape。

    Raises:
        TypeError: 不支持的几何类型。
        ValueError: 空 Workplane。
    """
    if isinstance(obj, cq.Shape):
        return obj
    if isinstance(obj, cq.Workplane):
        vals = obj.vals()
        if not vals:
            raise ValueError("Workplane 为空，无法校验")
        return cast(cq.Shape, vals[0])
    # 兼容其它包含 .val() 的封装
    val = getattr(obj, "val", None)
    if callable(val):
        result = val()
        if isinstance(result, cq.Shape):
            return result
    raise TypeError(f"不支持的几何类型: {type(obj)!r}")


def _safe_attr(shape: Any, name: str, default: Any = None) -> Any:
    """容错读取几何属性/方法：属性不存在或调用抛异常时返回 default。

    解决 getattr 在属性访问阶段即抛 AttributeError 的问题（如 Workplane
    没有 ShapeType），以及 OCCT 方法异常繁杂的问题。
    """
    try:
        attr = getattr(shape, name, None)
        if callable(attr):
            return attr()
        return attr
    except Exception as e:  # noqa: BLE001 - OCCT 异常类型繁杂，统一兜底
        logger.debug("geom attr %s(%s) failed: %s", name, type(shape).__name__, e)
        return default


def _solids_of(shape: cq.Shape) -> list[cq.Shape]:
    """提取 shape 中的全部 Solid 子实体（SOLID 本身返回自身，COMPOUND 递归收集）。"""
    stype = _safe_attr(shape, "ShapeType")
    if stype is None:
        return []
    stype = str(stype).upper()
    if stype == "SOLID":
        return [shape]
    if stype == "COMPOUND":
        solids: list[cq.Shape] = []
        sub = _safe_attr(shape, "Solids", default=[])
        if sub:
            for s in sub:
                if isinstance(s, cq.Shape):
                    solids.append(s)
        return solids
    return []


def _volume_of(shape: cq.Shape) -> float | None:
    """安全读取单个实体体积（非 SOLID 返回 None）。"""
    vol = _safe_attr(shape, "Volume")
    if vol is None:
        return None
    try:
        vol = float(vol)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(vol):
        return None
    return vol


def _edge_lengths(shape: cq.Shape) -> list[float]:
    """收集 shape 全部边长度（容错）。"""
    lengths: list[float] = []
    edges = _safe_attr(shape, "Edges", default=None)
    if not edges:
        return lengths
    for e in edges:
        ln = _safe_attr(e, "Length")
        if ln is None:
            continue
        try:
            ln = float(ln)
        except (TypeError, ValueError):
            continue
        if math.isfinite(ln):
            lengths.append(ln)
    return lengths


# 核心校验
def validate_brep(
    shape: cq.Shape,
    *,
    min_dimension: float = DEFAULT_MIN_DIMENSION,
    max_dimension: float = DEFAULT_MAX_DIMENSION,
    min_volume: float = DEFAULT_MIN_VOLUME,
    min_edge_length: float = DEFAULT_MIN_EDGE_LENGTH,
    strict: bool = False,
) -> BrepValidationReport:
    """对几何体执行 B-rep 拓扑校验（自动归一化 Workplane/Shape/Compound）。

    Args:
        shape: 待校验几何体（Workplane / Shape / Compound 均可）。
        min_dimension: 包围盒边长下限，低于视为退化尺寸。
        max_dimension: 包围盒边长上限，超过视为 error。
        min_volume: 体积下限，低于视为 error。
        min_edge_length: 最短边长下限，低于视为退化几何。
        strict: True 时退化尺寸/退化边/可疑拓扑也升级为 error。

    Returns:
        BrepValidationReport。
    """
    shape = _to_shape(shape)
    issues: list[BrepIssue] = []
    report = BrepValidationReport()

    # 1. 实体类型与存在性（Compound 含 ≥1 Solid 视为有效闭合体积）
    stype = _safe_attr(shape, "ShapeType")
    report.shape_type = str(stype) if stype else None
    solids = _solids_of(shape)
    if not solids:
        issues.append(
            BrepIssue(
                ERR_NOT_SOLID,
                "error",
                f"生成产物不含实体（Solid），实际类型为 {report.shape_type}。"
                "期望参数化建模输出闭合实体（布尔操作产物可为 Compound，但必须含实体）。",
                {"shape_type": report.shape_type},
            )
        )
    elif len(solids) > 1:
        issues.append(
            BrepIssue(
                WARN_MULTI_SOLID,
                "error" if strict else "warning",
                f"产物包含 {len(solids)} 个独立实体：单零件 NL2CAD 场景下应为一个整体，请检查是否发生意外的布尔分割。",
                {"num_solids": len(solids)},
            )
        )

    # 2. OCCT isValid
    if solids:
        invalid_solids = [s for s in solids if _safe_attr(s, "isValid") is not True]
        if invalid_solids:
            issues.append(
                BrepIssue(
                    ERR_INVALID_SHAPE,
                    "error",
                    f"{len(invalid_solids)}/{len(solids)} 个实体 isValid() 返回 False："
                    "存在破面 / 非流形 / 自相交等拓扑错误，无法用于后续 CAM 加工。",
                )
            )
    elif _safe_attr(shape, "isValid") is not True:
        issues.append(BrepIssue(ERR_INVALID_SHAPE, "error", "OCCT isValid() 返回 False：几何存在拓扑错误。"))

    # 3. 体积（Compound 按各 Solid 体积求和）
    volume: float | None = None
    if solids:
        vols = [v for v in (_volume_of(s) for s in solids) if v is not None]
        if vols:
            volume = sum(vols)
    report.volume = volume
    if solids and (volume is None or volume <= min_volume):
        issues.append(
            BrepIssue(
                ERR_ZERO_VOLUME,
                "error",
                f"实体总体积 {volume if volume is not None else 'N/A'} mm^3 低于下限 {min_volume:g} mm^3：实体为空或已退化。",
                {"volume": volume},
            )
        )

    # 4. 包围盒尺寸
    bb = _safe_attr(shape, "BoundingBox")
    if bb is not None:
        try:
            dims = {
                "xlen": float(bb.xlen),
                "ylen": float(bb.ylen),
                "zlen": float(bb.zlen),
            }
        except (AttributeError, TypeError, ValueError):
            dims = {}
        if dims:
            report.bbox = {
                "xmin": float(bb.xmin),
                "ymin": float(bb.ymin),
                "zmin": float(bb.zmin),
                "xmax": float(bb.xmax),
                "ymax": float(bb.ymax),
                "zmax": float(bb.zmax),
                **dims,
            }
            for axis, length in dims.items():
                if not math.isfinite(length):
                    issues.append(BrepIssue(ERR_INVALID_SHAPE, "error", f"包围盒 {axis} 长度非有限值"))
                elif length > max_dimension:
                    issues.append(
                        BrepIssue(
                            ERR_OVERSIZED_DIMENSION,
                            "error",
                            f"包围盒 {axis}={length:.4g} mm 超过上限 {max_dimension:g} mm：尺寸异常，疑似参数错误。",
                            {"axis": axis, "length": length},
                        )
                    )
                elif length < min_dimension:
                    issues.append(
                        BrepIssue(
                            ERR_DEGENERATE_DIMENSION,
                            "error" if strict else "warning",
                            f"包围盒 {axis}={length:.6g} mm 低于下限 {min_dimension:g} mm：存在退化尺寸。",
                            {"axis": axis, "length": length},
                        )
                    )

    # 5. 拓扑元素计数（可疑实体预警）
    faces = _safe_attr(shape, "Faces", default=None)
    edges = _safe_attr(shape, "Edges", default=None)
    vertices = _safe_attr(shape, "Vertices", default=None)
    report.num_faces = len(faces) if faces is not None else None
    report.num_edges = len(edges) if edges is not None else None
    report.num_vertices = len(vertices) if vertices is not None else None
    if solids and (
        (report.num_faces is not None and report.num_faces < MIN_FACES_SOLID)
        or (report.num_edges is not None and report.num_edges < MIN_EDGES_SOLID)
        or (report.num_vertices is not None and report.num_vertices < MIN_VERTICES_SOLID)
    ):
        issues.append(
            BrepIssue(
                WARN_SUSPICIOUS_TOPOLOGY,
                "error" if strict else "warning",
                "实体拓扑元素数异常偏少"
                f"（faces={report.num_faces}, edges={report.num_edges}, vertices={report.num_vertices}），"
                "可能存在退化拓扑。",
            )
        )

    # 6. 最小边/特征尺寸
    lengths = _edge_lengths(shape)
    if lengths:
        min_len = min(lengths)
        report.min_edge_length = min_len
        if min_len < min_edge_length:
            issues.append(
                BrepIssue(
                    ERR_DEGENERATE_EDGE,
                    "error" if strict else "warning",
                    f"最短边 {min_len:.6g} mm 低于下限 {min_edge_length:g} mm：存在退化/微特征几何。",
                    {"min_edge_length": min_len},
                )
            )

    report.issues = issues
    report.is_valid = not report.errors
    return report


def validate_workplane(
    workplane: cq.Workplane | Any,
    **kwargs: Any,
) -> BrepValidationReport:
    """校验 Workplane（或其 .val() 产物）。

    Args:
        workplane: cq.Workplane 或包含 .val() 的对象。
        **kwargs: 透传 validate_brep 参数。

    Returns:
        BrepValidationReport。
    """
    shape = _to_shape(workplane)

    # 多顶层实体提示（Workplane.vals() 返回多个独立 Shape）
    if isinstance(workplane, cq.Workplane):
        vals = workplane.vals()
        if len(vals) > 1:
            report = validate_brep(shape, **kwargs)
            if not any(i.code == WARN_MULTI_SOLID for i in report.issues):
                report.issues.append(
                    BrepIssue(
                        WARN_MULTI_SOLID,
                        "warning",
                        f"Workplane 包含 {len(vals)} 个顶层实体，仅对首个实体执行完整校验。",
                        {"num_solids": len(vals)},
                    )
                )
            report.is_valid = not report.errors
            return report

    return validate_brep(shape, **kwargs)


def _validate_stl_file(path: Path) -> BrepValidationReport:
    """STL 文件级解析校验（cadquery 2.7 无 importStl，改走文件解析）。

    二进制：80 字节头 + uint32 三角面数 + N×50 字节；ASCII：facet 关键字。
    几何正确性由导出前的内存校验（validate_workplane）兜底。
    """
    report = BrepValidationReport()
    try:
        data = path.read_bytes()
    except OSError as e:
        report.issues.append(BrepIssue(ERR_INVALID_SHAPE, "error", f"STL 文件读取失败: {e}"))
        return report

    if len(data) < 84:
        report.issues.append(BrepIssue(ERR_INVALID_SHAPE, "error", "STL 文件过小（<84 字节），疑似损坏或为空。"))
        return report

    count = struct.unpack("<I", data[80:84])[0]
    expected = 84 + count * 50
    if expected == len(data):
        if count <= 0:
            report.issues.append(BrepIssue(ERR_INVALID_SHAPE, "error", "STL 无三角面（triangle count = 0）。"))
            return report
        report.is_valid = True
        return report

    # ASCII STL 兜底
    head = data[:4096].decode("ascii", errors="ignore").lstrip().lower()
    if head.startswith("solid") and "facet" in head:
        report.is_valid = True
        return report

    report.issues.append(
        BrepIssue(
            ERR_INVALID_SHAPE,
            "error",
            f"STL 文件解析异常：声明 {count} 个三角面但文件大小 {len(data)} 与预期 {expected} 不符，文件损坏。",
        )
    )
    return report


def validate_exported_model(
    path: str | Path,
    output_format: str,
    **kwargs: Any,
) -> BrepValidationReport | None:
    """对已导出的模型文件做回读校验。

    - ``step``：STEP 回读，执行完整 B-rep 校验。
    - ``stl``：文件级解析校验（三角形数/大小一致性）。
    - ``obj`` / ``gltf``：跳过，返回 None（调用方视为通过）。

    Returns:
        BrepValidationReport（跳过时为 None）。
    """
    fmt = (output_format or "").lower()
    path = Path(path)
    if fmt == "step":
        try:
            shape = cq.importers.importStep(str(path))
        except Exception as e:  # noqa: BLE001
            logger.error("STEP 回读失败 %s: %s", path, e)
            report = BrepValidationReport(is_valid=False)
            report.issues.append(BrepIssue(ERR_INVALID_SHAPE, "error", f"STEP 文件回读失败，文件可能损坏或不完整: {e}"))
            return report
        return validate_brep(_to_shape(shape), **kwargs)

    if fmt == "stl":
        return _validate_stl_file(path)

    return None


def raise_if_invalid(report: BrepValidationReport | None) -> None:
    """report 存在 error 级问题时抛出 BrepValidationError。"""
    if report is not None and not report.is_valid:
        raise BrepValidationError(report)


def sanitize_dimensions(params: dict[str, Any]) -> dict[str, Any]:
    """把尺寸中的退化值夹取到有效范围，返回新 dict（供重生成使用）。

    1. 各维度夹取到 [min_dimension, max_dimension]；
    2. 若长宽高乘积仍低于体积下限，三轴等比放大到乘积 ≥ min_volume。
    """
    new_params = copy.deepcopy(params)
    dims = new_params.get("dimensions")
    if not isinstance(dims, dict):
        return new_params

    for key in ("length", "width", "height"):
        if key not in dims:
            continue
        try:
            val = float(dims[key])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(val):
            val = 50.0
        val = max(val, DEFAULT_MIN_DIMENSION)
        val = min(val, DEFAULT_MAX_DIMENSION)
        dims[key] = round(val, 6)

    # 体积下限兜底：三轴等比缩放（目标取 10× 下限，留足数值余量避免边界误判）
    try:
        product = float(dims.get("length", 50)) * float(dims.get("width", 30)) * float(dims.get("height", 20))
    except (TypeError, ValueError):
        product = 0.0
    if product < DEFAULT_MIN_VOLUME:
        target = DEFAULT_MIN_VOLUME * 10.0
        factor = (target / max(product, 1e-30)) ** (1 / 3)
        for key in ("length", "width", "height"):
            if key in dims:
                dims[key] = round(float(dims[key]) * factor, 6)
    return new_params


__all__ = [
    "BrepIssue",
    "BrepValidationReport",
    "BrepValidationError",
    "validate_brep",
    "validate_workplane",
    "validate_exported_model",
    "raise_if_invalid",
    "sanitize_dimensions",
    "ERR_NOT_SOLID",
    "ERR_INVALID_SHAPE",
    "ERR_ZERO_VOLUME",
    "ERR_OVERSIZED_DIMENSION",
    "ERR_DEGENERATE_DIMENSION",
    "ERR_DEGENERATE_EDGE",
    "WARN_SUSPICIOUS_TOPOLOGY",
    "WARN_MULTI_SOLID",
]
