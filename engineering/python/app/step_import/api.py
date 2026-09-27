"""STEP文件导入API接口。

提供符合RESTful规范的STEP文件上传、解析和格式转换接口。
支持标准HTTP文件上传协议、分块上传、格式验证和错误处理。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission

from app.core.response import success, error, ErrorCode
from app.core.safe_errors import safe_error_message
from app.utils.utils import get_output_dir, get_upload_dir, sanitize_filename
from app.utils.upload_security import validate_upload
from app.step_import.step_parser import StepParser, StepParseError
from app.step_import.step_converter import (
    StepConverter,
)
from app.step_import.step_cache import get_step_cache
from app.config.limits import MAX_FILE_SIZE

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/import/step",
    tags=["STEP Import"],
    dependencies=[Depends(require_permission("step:read"))],
)

OUTPUT_DIR = get_output_dir("step_import")
TEMP_DIR = get_upload_dir("step_import")

ALLOWED_EXTENSIONS = {".step", ".stp"}

_step_parser = StepParser()
_step_converter = StepConverter(output_dir=OUTPUT_DIR)


class StepImportResponse(BaseModel):
    file_name: str = ""
    file_size: int = 0
    parse_time_ms: float = 0.0
    conversion_time_ms: float = 0.0
    model_info: dict = Field(default_factory=dict)
    entities: list[dict] = Field(default_factory=list)
    is_assembly: bool = False
    stl_files: list[dict] = Field(default_factory=list)
    brep_files: list[dict] = Field(default_factory=list)
    status: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    cached: bool = False
    import_id: str = ""
    format: str = ""


def _validate_step_file(file: UploadFile) -> None:
    """验证上传的STEP文件格式和大小。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {ext}。请上传 .step 或 .stp 格式的STEP文件。",
        )


def _save_temp_file(content: bytes, original_name: str) -> Path:
    """将上传内容保存到临时文件。"""
    unique_id = uuid.uuid4().hex[:12]
    ext = Path(original_name).suffix.lower()
    temp_path = TEMP_DIR / f"step_{unique_id}{ext}"
    temp_path.write_bytes(content)
    return temp_path


def _parse_and_convert(
    temp_path: Path,
    file_name: str,
    file_size: int,
    output_format: str = "stl",
    precision: str = "medium",
    use_cache: bool = True,
) -> dict:
    """执行STEP解析和格式转换(在线程池中运行)。"""
    cache = get_step_cache()

    if use_cache:
        cached = cache.get(temp_path)
        if cached and cached.parse_result_data:
            logger.info("使用缓存结果: %s", file_name)
            data = dict(cached.parse_result_data)
            data["cached"] = True
            return data

    parse_result = _step_parser.parse(temp_path)
    shape = _step_parser.get_cadquery_shape(temp_path)

    batch_result = _step_converter.convert_all_entities(
        shape=shape,
        file_name=file_name,
        parse_result=parse_result,
        output_format=output_format,
        precision=precision,
    )

    conversion_ms = batch_result.total_time_ms

    status = {
        "success": batch_result.success,
        "message": "解析和转换完成" if batch_result.success else "部分转换失败",
        "entity_count": parse_result.model_info.entity_count,
        "face_count": parse_result.model_info.face_count,
        "vertex_count": parse_result.model_info.vertex_count,
        "errors": batch_result.errors,
    }

    stl_files: list[dict[str, Any]] = []
    brep_files: list[dict[str, Any]] = []
    for f in batch_result.files:
        file_info = {
            "file_name": f.file_name,
            "stl_url": f.stl_url,
            "stl_path": f.stl_path,
            "format": f.format,
            "face_count": f.face_count,
            "vertex_count": f.vertex_count,
            "file_size": f.file_size,
            "entity_index": f.entity_index,
            "entity_name": f.entity_name,
            "precision_used": f.precision_used,
        }
        if f.format == "brep":
            brep_files.append(file_info)
        else:
            stl_files.append(file_info)

    model_info_dict = {
        "volume": parse_result.model_info.volume,
        "surface_area": parse_result.model_info.surface_area,
        "bounding_box": {
            "length": parse_result.model_info.bounding_box.length,
            "width": parse_result.model_info.bounding_box.width,
            "height": parse_result.model_info.bounding_box.height,
            "min_point": list(parse_result.model_info.bounding_box.min_point),
            "max_point": list(parse_result.model_info.bounding_box.max_point),
        },
        "center_of_mass": {
            "x": parse_result.model_info.center_of_mass[0],
            "y": parse_result.model_info.center_of_mass[1],
            "z": parse_result.model_info.center_of_mass[2],
        },
        "entity_count": parse_result.model_info.entity_count,
        "face_count": parse_result.model_info.face_count,
        "vertex_count": parse_result.model_info.vertex_count,
        "edge_count": parse_result.model_info.edge_count,
        "shell_count": parse_result.model_info.shell_count,
        "solid_count": parse_result.model_info.solid_count,
    }

    entities_list = []
    for ent in parse_result.entities:
        entities_list.append(
            {
                "name": ent.name,
                "entity_index": ent.entity_index,
                "volume": ent.volume,
                "surface_area": ent.surface_area,
                "bounding_box": {
                    "length": ent.bounding_box.length,
                    "width": ent.bounding_box.width,
                    "height": ent.bounding_box.height,
                    "min_point": list(ent.bounding_box.min_point),
                    "max_point": list(ent.bounding_box.max_point),
                },
                "center_of_mass": list(ent.center_of_mass),
                "face_count": ent.face_count,
                "vertex_count": ent.vertex_count,
            }
        )

    import_id = uuid.uuid4().hex[:12]

    data = {
        "file_name": file_name,
        "file_size": file_size,
        "parse_time_ms": parse_result.parse_time_ms,
        "conversion_time_ms": conversion_ms,
        "model_info": model_info_dict,
        "entities": entities_list,
        "is_assembly": parse_result.is_assembly,
        "stl_files": stl_files,
        "brep_files": brep_files,
        "status": status,
        "warnings": parse_result.warnings,
        "cached": False,
        "import_id": import_id,
        "format": output_format,
    }

    if use_cache:
        cache.put(
            file_path=temp_path,
            stl_files=[f["file_name"] for f in stl_files],
            brep_files=[f["file_name"] for f in brep_files],
            parse_result_data=data,
        )

    return data


@router.post("")
@router.post("/")
async def import_step_file(
    request: Request,
    file: UploadFile = File(..., description="STEP文件(.step/.stp)"),
    output_format: str = Form(default="stl", description="输出格式: stl 或 brep"),
    precision: str = Form(default="medium", description="精度级别: low/medium/high"),
    use_cache: bool = Form(default=True, description="是否使用缓存"),
) -> dict:
    """导入STEP文件并进行解析和格式转换。

    接受STEP格式的CAD文件，解析几何数据并转换为系统兼容格式。

    Args:
        file: STEP文件(.step/.stp)
        output_format: 输出格式(stl/brep)
        precision: 精度级别(low/medium/high)
        use_cache: 是否启用缓存

    Returns:
        标准API响应，data中包含模型信息和STL文件URL
    """
    try:
        _validate_step_file(file)
    except HTTPException as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=e.detail, detail=e.detail)

    # P0-12/P0-13 修复：使用 validate_upload 异步分块流式读取 + 大小限制 +
    # magic bytes 签名校验，避免原 ``file.file.read()`` 全量入内存导致 OOM/DoS。
    try:
        content = await validate_upload(
            file,
            max_size=MAX_FILE_SIZE,
            allowed_extensions=ALLOWED_EXTENSIONS,
            allowed_mimes={"application/step"},
        )
    except HTTPException as e:
        # validate_upload 抛出的 413/415/400 转为业务错误响应（保持原 API 响应格式）
        return error(code=ErrorCode.INVALID_REQUEST, message=e.detail)

    temp_path = None
    try:
        temp_path = await asyncio.to_thread(_save_temp_file, content, file.filename or "unknown.step")

        result_data = await asyncio.to_thread(
            _parse_and_convert,
            temp_path,
            file.filename or "unknown.step",
            len(content),
            output_format,
            precision,
            use_cache,
        )

        return success(data=result_data, message="STEP文件导入成功")

    except StepParseError as e:
        # StepParseError 是业务级解析错误（用户可理解），但仍通过
        # safe_error_message 包装以便审计/脱敏统一管理。
        safe = safe_error_message(e, context="step_import.parse")
        logger.warning(
            "STEP 解析失败 | error_id=%s | exc=%s",
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=safe["message"],
            suggestion="请确认文件为有效的STEP格式(AP203/AP214/AP242)，且未被损坏。",
        )
    except MemoryError:
        logger.exception("内存不足")
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="服务器内存不足，无法处理该文件。请尝试降低精度设置或使用更小的文件。",
            recoverable=True,
        )
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as e:
        # 兜底：API 入口必须捕获所有异常以避免 5xx 直接抛给客户端
        safe = safe_error_message(e, context="step_import.import_unexpected")
        logger.exception("STEP导入未预期错误 | error_id=%s", safe.get("error_id"))
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.get("/output/{file_name}")
async def get_output_file(file_name: str):
    """获取转换后的输出文件(STL/BREP)。

    Args:
        file_name: 输出文件名

    Returns:
        FileResponse: 文件流

    Raises:
        HTTPException: 400 当文件路径净化或验证失败时；
                       404 当文件不存在时。

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
        raise HTTPException(status_code=404, detail=f"输出文件未找到: {safe_name}")

    # 保留原有的媒体类型判断逻辑
    media_type = "application/sla" if safe_name.endswith(".stl") else "application/octet-stream"
    # 保留原有的 FileResponse 返回机制
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=safe_name,
    )


@router.get("/cache/stats")
async def get_cache_stats() -> dict:
    """获取STEP解析缓存统计信息。

    Returns:
        标准API响应，data中包含缓存命中率等统计
    """
    cache = get_step_cache()
    return success(data=cache.stats, message="缓存统计获取成功")


@router.delete("/cache")
async def clear_cache() -> dict:
    """清空STEP解析缓存。

    Returns:
        标准API响应
    """
    cache = get_step_cache()
    prev_size = cache.size
    cache.clear()
    return success(
        data={"cleared_entries": prev_size},
        message=f"缓存已清空，移除 {prev_size} 个条目",
    )


@router.get("/history")
async def get_import_history(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    """获取STEP导入历史记录。

    扫描输出目录中的STL文件，按时间倒序返回最近导入的记录。

    Args:
        limit: 返回记录数量限制

    Returns:
        标准API响应，data中包含导入历史列表
    """

    history = []
    try:
        stl_files = sorted(
            OUTPUT_DIR.glob("*.stl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        brep_files = {p.stem: p for p in OUTPUT_DIR.glob("*.brep")}

        seen = set()
        for stl_path in stl_files[:limit]:
            try:
                stat = stl_path.stat()
                base = stl_path.stem
                parts = base.rsplit("_", 1)
                original_name = parts[0] if len(parts) >= 2 else base

                entry = {
                    "file_name": stl_path.name,
                    "original_name": original_name,
                    "file_size": stat.st_size,
                    "created_at": stat.st_mtime,
                    "stl_url": f"/api/import/step/output/{stl_path.name}",
                    "has_brep": base in brep_files,
                }

                if base not in seen:
                    seen.add(base)
                    history.append(entry)
            except (OSError, ValueError, KeyError, AttributeError, TypeError):
                # 单个历史文件解析失败不应阻塞整体列表展示
                continue

    except (OSError, ValueError, RuntimeError) as e:
        # 获取历史涉及文件系统遍历，捕获核心错误
        logger.warning("获取导入历史失败: %s", e)

    return success(data={"history": history, "total": len(history)}, message="导入历史获取成功")


@router.delete("/history/{file_name}")
async def delete_import_file(file_name: str) -> dict:
    """删除指定导入文件。

    Args:
        file_name: 要删除的文件名

    Returns:
        标准API响应
    """
    file_path = OUTPUT_DIR / file_name
    if not file_path.exists():
        return error(code=ErrorCode.FILE_NOT_FOUND, message=f"文件未找到: {file_name}")

    try:
        file_path.unlink()
        base = file_path.stem

        for related in OUTPUT_DIR.glob(f"{base}*"):
            try:
                related.unlink()
            except (OSError, PermissionError) as e:
                # 关联产物清理失败不应阻塞主删除流程
                logger.warning(
                    f"Failed to remove related output file {related}: {e}",
                    exc_info=True,
                )

        cache = get_step_cache()
        cache.clear()

        return success(message=f"文件 {file_name} 已删除")
    except (OSError, ValueError, RuntimeError, TypeError) as e:
        # 删除涉及文件 IO 与缓存清理，捕获核心错误；
        # 使用 safe_error_message 包装。
        safe = safe_error_message(e, context="step_import.delete_file")
        logger.error(
            "STEP 文件删除失败 | error_id=%s | exc=%s",
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as e:
        # 兜底：API 入口必须捕获所有异常以避免 5xx 直接抛给客户端
        safe = safe_error_message(e, context="step_import.delete_unexpected")
        logger.exception("删除文件未预期错误 | error_id=%s", safe.get("error_id"))
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )
