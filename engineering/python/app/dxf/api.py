"""DXF文件处理API接口。

提供符合RESTful规范的DXF文件上传、解析、特征提取、
3D模型转换和端到端工艺规划接口。
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, Depends
from fastapi.responses import FileResponse

from app.auth.dependencies import get_current_user
from app.core.response import success, error, ErrorCode
from app.core.safe_errors import safe_error_message
from app.utils.utils import get_output_dir, get_upload_dir, cleanup_temp_file, sanitize_filename
from app.utils.upload_security import validate_upload
from app.dxf.dxf_parser import DxfParser
from app.dxf.feature_extractor import FeatureExtractor
from app.dxf.dxf_to_model import DxfToModelConverter
from app.dxf.pipeline import DxfProcessPipeline
from app.process_planning.gcode_generator import GCodeGenerator
from app.xmaker.integration import XmakerIntegration
from app.config.limits import MAX_FILE_SIZE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dxf", tags=["DXF Processing"])

OUTPUT_DIR = get_output_dir("dxf_import")
TEMP_DIR = get_upload_dir("dxf_import")

ALLOWED_EXTENSIONS = {".dxf"}


@lru_cache(maxsize=1)
def get_dxf_parser() -> DxfParser:
    """FastAPI 依赖：提供 DxfParser 实例（进程级单例）。"""
    return DxfParser()


@lru_cache(maxsize=1)
def get_feature_extractor() -> FeatureExtractor:
    """FastAPI 依赖：提供 FeatureExtractor 实例（进程级单例）。"""
    return FeatureExtractor()


@lru_cache(maxsize=1)
def get_model_converter() -> DxfToModelConverter:
    """FastAPI 依赖：提供 DxfToModelConverter 实例（进程级单例）。"""
    return DxfToModelConverter()


@lru_cache(maxsize=1)
def get_dxf_pipeline() -> DxfProcessPipeline:
    """FastAPI 依赖：提供 DxfProcessPipeline 实例（进程级单例）。"""
    return DxfProcessPipeline()


@lru_cache(maxsize=1)
def get_xmaker_client() -> XmakerIntegration:
    """FastAPI 依赖：提供 XmakerIntegration 实例（进程级单例）。"""
    return XmakerIntegration()


def _validate_dxf_file(file: UploadFile) -> None:
    """验证上传的DXF文件格式和大小。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {ext}。请上传 .dxf 格式的DXF文件。",
        )


async def _save_upload(file: UploadFile) -> Path:
    """保存上传文件到临时目录并返回路径。

    P0-12/P0-13 修复：使用 ``validate_upload`` 分块流式读取 + 大小限制 +
    magic bytes 签名校验，替代原 ``file.file.read()`` 全量入内存。
    """
    content = await validate_upload(
        file,
        max_size=MAX_FILE_SIZE,
        allowed_extensions=ALLOWED_EXTENSIONS,
        allowed_mimes={"application/dxf"},
    )

    temp_path = TEMP_DIR / f"{uuid.uuid4().hex}_{file.filename}"
    temp_path.write_bytes(content)
    return temp_path


@asynccontextmanager
async def _dxf_upload_context(file: UploadFile):
    """DXF上传生命周期管理：验证 → 保存 → 用毕清理。

    替代各端点中重复的 try/finally 清理模式。
    """
    _validate_dxf_file(file)
    temp_path = await _save_upload(file)
    try:
        yield temp_path
    finally:
        cleanup_temp_file(temp_path)


def _dxf_error_response(
    logger_obj: logging.Logger,
    exc: BaseException,
    context: str,
    log_fmt: str = "DXF 操作失败: %s",
) -> dict:
    """标准DXF错误响应（替代各端点中重复的 except 块）。"""
    logger_obj.error(log_fmt, exc, exc_info=True)
    safe = safe_error_message(exc, context=context)
    return error(
        code=ErrorCode.INTERNAL_ERROR,
        message=safe["message"],
        detail={"error_id": safe.get("error_id")} if safe.get("detail") else None,
    )


@router.post("/parse", response_model=dict)
async def parse_dxf(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    dxf_parser: DxfParser = Depends(get_dxf_parser),
):
    """解析DXF文件，提取几何实体和尺寸标注。

    上传DXF文件，返回提取的直线、圆、圆弧、文字和尺寸标注列表。
    支持AutoCAD R12至2021版本的DXF格式。
    """
    try:
        async with _dxf_upload_context(file) as temp_path:
            result = dxf_parser.parse(temp_path)
            lines_data = [
                {
                    "start": list(line.start),
                    "end": list(line.end),
                    "layer": line.layer,
                    "color": line.color,
                    "handle": line.handle,
                }
                for line in result.lines
            ]
            circles_data = [
                {
                    "center": list(c.center),
                    "radius": c.radius,
                    "layer": c.layer,
                    "color": c.color,
                    "handle": c.handle,
                }
                for c in result.circles
            ]
            dims_data = [
                {
                    "dim_type": d.dim_type,
                    "measurement": d.measurement,
                    "text": d.text,
                    "position": list(d.position),
                    "layer": d.layer,
                }
                for d in result.dimensions
            ]

            return success(
                data={
                    "file_name": result.file_name,
                    "file_size": result.file_size,
                    "dxf_version": result.dxf_version,
                    "parse_time_ms": round(result.parse_time_ms, 2),
                    "entity_counts": result.entity_counts,
                    "total_entities": result.total_entities,
                    "lines_count": len(result.lines),
                    "circles_count": len(result.circles),
                    "arcs_count": len(result.arcs),
                    "texts_count": len(result.texts),
                    "dimensions_count": len(result.dimensions),
                    "extents": result.extents,
                    "lines": lines_data[:200],
                    "circles": circles_data[:200],
                    "dimensions": dims_data[:100],
                    "warnings": result.warnings,
                }
            )
    except (ValueError, TypeError, AttributeError, OSError, OverflowError) as e:
        return _dxf_error_response(logger, e, "dxf.parse", "DXF 解析失败: %s")


@router.post("/features", response_model=dict)
async def extract_features(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    dxf_parser: DxfParser = Depends(get_dxf_parser),
    feature_extractor: FeatureExtractor = Depends(get_feature_extractor),
):
    """从DXF文件中提取加工特征。

    上传DXF文件，返回孔特征和平面特征列表。
    包含孔径、位置、深度等参数信息。
    """
    try:
        async with _dxf_upload_context(file) as temp_path:
            parse_result = dxf_parser.parse(temp_path)
            feature_result = feature_extractor.extract(parse_result)
            return success(data=feature_result.to_dict())
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, OSError, RuntimeError) as e:
        return _dxf_error_response(logger, e, "dxf.features", "DXF 特征提取失败: %s")


@router.post("/pipeline", response_model=dict)
async def run_dxf_pipeline(
    file: UploadFile = File(...),
    material: str = Form(default="45#钢"),
    controller_type: str = Form(default="fanuc_0i"),
    part_type: str = Form(default="general"),
    safe_z: float = Form(default=50.0),
    current_user: dict = Depends(get_current_user),
    pipeline: DxfProcessPipeline = Depends(get_dxf_pipeline),
):
    """执行完整的DXF端到端处理流水线。

    上传DXF文件，自动完成解析→特征提取→3D模型转换→工艺规划→G代码生成。
    返回包含G代码的完整处理结果。
    """
    try:
        async with _dxf_upload_context(file) as temp_path:
            result = pipeline.run(
                file_path=temp_path,
                material=material,
                controller_type=controller_type,
                part_type=part_type,
                safe_z=safe_z,
            )
            return success(data=result.to_dict())
    except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError, TimeoutError) as e:
        return _dxf_error_response(logger, e, "dxf.pipeline", "DXF 管道处理失败: %s")


@router.post("/model/stl", response_model=dict)
async def convert_to_stl(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    dxf_parser: DxfParser = Depends(get_dxf_parser),
    feature_extractor: FeatureExtractor = Depends(get_feature_extractor),
    model_converter: DxfToModelConverter = Depends(get_model_converter),
):
    """将DXF文件转换为STL 3D模型。

    上传DXF文件，返回生成的STL模型文件。
    """
    try:
        async with _dxf_upload_context(file) as temp_path:
            parse_result = dxf_parser.parse(temp_path)
            feature_result = feature_extractor.extract(parse_result)
            model_result = model_converter.convert(feature_result)

            if not model_result.success:
                return error(
                    code=ErrorCode.INTERNAL_ERROR,
                    message=f"模型转换失败: {'; '.join(model_result.errors)}",
                )

            output_path = OUTPUT_DIR / f"{uuid.uuid4().hex}.stl"
            model_converter.export_stl(model_result, output_path)

            return success(
                data={
                    "file_name": output_path.name,
                    "file_size": output_path.stat().st_size,
                    "download_url": f"/api/dxf/model/download/{output_path.name}",
                }
            )
    except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError, OverflowError) as e:
        return _dxf_error_response(logger, e, "dxf.model.stl", "DXF 转 STL 模型失败: %s")


@router.get("/model/download/{file_name}")
async def download_model(
    file_name: str,
    current_user: dict = Depends(get_current_user),
):
    """下载生成的3D模型文件。

    [路径遍历修复] 增加了双重路径验证：
    1. 通过 sanitize_filename 拒绝包含路径分隔符或 ".." 的输入；
    2. 通过 resolve() + is_relative_to() 确保最终路径严格位于 OUTPUT_DIR 内。
    """
    # [路径遍历修复] 第一层：用户输入净化
    safe_name = sanitize_filename(file_name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="无效的文件名")

    # [路径遍历修复] 第二层：解析为绝对路径并验证在允许目录内
    allowed_dir = OUTPUT_DIR.resolve()
    file_path = (OUTPUT_DIR / safe_name).resolve()
    if not file_path.is_relative_to(allowed_dir):
        raise HTTPException(status_code=400, detail="无效的文件路径")

    # 保留原有的文件存在性检查
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {safe_name}")

    # 保留原有的媒体类型判断逻辑
    media_type = "application/octet-stream"
    if safe_name.endswith(".stl"):
        media_type = "model/stl"
    elif safe_name.endswith(".step"):
        media_type = "model/step"

    # 保留原有的 FileResponse 返回机制
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=safe_name,
    )


@router.post("/validate", response_model=dict)
async def validate_dxf(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    dxf_parser: DxfParser = Depends(get_dxf_parser),
):
    """验证DXF文件格式和内容。"""
    try:
        async with _dxf_upload_context(file) as temp_path:
            result = dxf_parser.parse(temp_path)
            issues = []

            if result.total_entities == 0:
                issues.append("DXF文件中未发现几何实体")

            if len(result.circles) == 0:
                issues.append("未发现圆实体，可能无法识别孔特征")

            if len(result.dimensions) == 0:
                issues.append("未发现尺寸标注，特征参数将使用默认值")

            if len(result.lines) == 0 and len(result.circles) == 0:
                issues.append("文件中无线条或圆，可能为空文件或仅含文字")

            return success(
                data={
                    "valid": len(issues) == 0 or all("可能" in i or "建议" in i for i in issues),
                    "dxf_version": result.dxf_version,
                    "entity_counts": result.entity_counts,
                    "total_entities": result.total_entities,
                    "issues": issues,
                }
            )
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
        logger.warning("DXF 校验失败: %s", e, exc_info=True)
        safe = safe_error_message(e, context="dxf.validate")
        return success(
            data={
                "valid": False,
                "issues": ["文件解析失败，请检查DXF格式是否正确"],
                "error_id": safe.get("error_id"),
            }
        )


# XM-100 五轴加工端点


@router.post("/xm100/generate", response_model=dict)
async def generate_xm100_gcode(
    file: UploadFile = File(...),
    material: str = Form(default="45#钢"),
    part_type: str = Form(default="general"),
    enable_five_axis: bool = Form(default=True),
    strategy: str = Form(default="lead_angle"),
    current_user: dict = Depends(get_current_user),
    dxf_parser: DxfParser = Depends(get_dxf_parser),
    feature_extractor: FeatureExtractor = Depends(get_feature_extractor),
):
    """为 XM-100 五轴机床生成 G 代码。

    上传 DXF 文件，使用 xmachine_xm100 后处理器生成五轴联动 G 代码。
    支持三种五轴策略：lead_angle（引导角）、tilt_angle（倾斜角）、interpolation（插值）。
    """
    try:
        async with _dxf_upload_context(file) as temp_path:
            parse_result = dxf_parser.parse(temp_path)
            feature_result = feature_extractor.extract(parse_result)

            generator = GCodeGenerator()
            from app.process_planning.process_planner import ProcessPlanner

            planner = ProcessPlanner()
            plan_result = planner.plan(
                features=feature_result,
                material=material,
                part_type=part_type,
            )

            gcode_result = generator.generate(
                operation_plan=plan_result.operation_plan,
                controller_type="xmachine_xm100",
                material_name=str(material),
            )

            if gcode_result.errors:
                return error(
                    code=ErrorCode.INTERNAL_ERROR,
                    message=f"G 代码生成失败: {'; '.join(gcode_result.errors)}",
                )

            output_path = OUTPUT_DIR / f"{uuid.uuid4().hex}_xm100.gcode"
            output_path.write_text(gcode_result.program_text, encoding="utf-8")

            return success(
                data={
                    "file_name": output_path.name,
                    "file_size": output_path.stat().st_size,
                    "controller_type": "xmachine_xm100",
                    "five_axis_enabled": enable_five_axis,
                    "strategy": strategy,
                    "total_lines": gcode_result.total_lines,
                    "estimated_time_min": gcode_result.estimated_cycle_time_min,
                    "download_url": f"/api/dxf/model/download/{output_path.name}",
                }
            )
    except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError) as e:
        return _dxf_error_response(logger, e, "dxf.xm100.generate", "XM-100 G代码生成失败: %s")


@router.post("/xm100/upload", response_model=dict)
async def upload_to_xmaker(
    file: UploadFile = File(...),
    job_name: str = Form(default=""),
    current_user: dict = Depends(get_current_user),
    xmaker_client: XmakerIntegration = Depends(get_xmaker_client),
):
    """上传 G 代码到 Xmaker 平台。

    上传 G 代码文件到 Xmaker 云平台，返回文件 ID 和下载链接。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    ext = Path(file.filename).suffix.lower()
    if ext not in {".gcode", ".nc", ".tap"}:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {ext}。请上传 .gcode/.nc/.tap 格式文件。",
        )

    # P0-12/P0-13 修复：使用 validate_upload 分块流式读取 + 大小限制 + magic bytes 校验
    # gcode/nc/tap 为文本类扩展名，validate_upload 会跳过 magic 校验仅做扩展名 + 大小校验
    content = await validate_upload(
        file,
        max_size=MAX_FILE_SIZE,
        allowed_extensions={".gcode", ".nc", ".tap"},
        allowed_mimes={"text/plain"},
    )

    # 写入临时文件
    temp_path = TEMP_DIR / f"{uuid.uuid4().hex}_{file.filename}"
    temp_path.write_bytes(content)

    try:
        upload_result = xmaker_client.upload_gcode(
            file_path=temp_path,
            job_name=job_name or temp_path.stem,
        )

        if not upload_result.success:
            return error(
                code=ErrorCode.INTERNAL_ERROR,
                message=f"上传失败: {upload_result.error_message}",
            )

        return success(
            data={
                "file_id": upload_result.file_id,
                "file_url": upload_result.file_url,
                "upload_time_ms": upload_result.upload_time_ms,
            }
        )
    except HTTPException:
        raise
    except (OSError, RuntimeError, TimeoutError, ValueError, TypeError) as e:
        return _dxf_error_response(logger, e, "dxf.xm100.upload", "XM-100 上传失败: %s")
    finally:
        cleanup_temp_file(temp_path)


@router.get("/xm100/status", response_model=dict)
async def get_xm100_status(
    machine_id: str = "default",
    current_user: dict = Depends(get_current_user),
    xmaker_client: XmakerIntegration = Depends(get_xmaker_client),
):
    """获取 XM-100 机床状态。

    查询指定机床的实时状态，包括加工进度、剩余时间、错误信息等。
    """
    try:
        status_info = xmaker_client.get_machine_status(machine_id)

        return success(
            data={
                "machine_id": machine_id,
                "status": status_info.status.value,
                "current_job_id": status_info.current_job_id,
                "progress_percent": status_info.progress_percent,
                "elapsed_time_sec": status_info.elapsed_time_sec,
                "remaining_time_sec": status_info.remaining_time_sec,
                "current_line": status_info.current_line,
                "total_lines": status_info.total_lines,
                "error_code": status_info.error_code,
                "error_message": status_info.error_message,
            }
        )
    except (OSError, RuntimeError, TimeoutError, ValueError, TypeError, KeyError, AttributeError) as e:
        return _dxf_error_response(logger, e, "dxf.xm100.status", "获取 XM-100 机床状态失败: %s")
