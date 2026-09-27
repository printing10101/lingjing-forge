"""
工艺规则管理 API

提供规则的创建、读取、更新、删除操作，支持规则分组管理、
搜索筛选、导入导出和数据备份功能。所有操作本地完成。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol


from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.auth.dependencies import get_current_user
from app.auth.permissions import require_permission
from app.core.response import success, error, ErrorCode
from app.core.safe_errors import safe_error_message
from app.utils.utils import get_output_dir
from app.utils.upload_security import validate_upload
from app.database.rule_db import (
    get_rule_db,
    ProcessRule,
    RuleCondition,
    RuleResult,
    RuleGroup,
)
from app.rules.conflict_detector import detect_conflicts, ConflictReport
from app.config.limits import DEFAULT_QUERY_LIMIT

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rules", tags=["Process Rules"])

RULE_EXPORT_DIR = get_output_dir("rules")

# ``DEFAULT_QUERY_LIMIT`` 由 ``app.config.limits`` 集中管理，
# 与 database/rule_db.py / rag/knowledge_base.py 共享同一基准值。

VALID_OPERATORS = {"=", "<", ">", "<=", ">=", "!="}
VALID_LOGIC_OPERATORS = {"AND", "OR"}
VALID_STATUSES = {"active", "inactive", "draft"}

# [P0-16] sort_by 白名单：防止 SQL 注入（列名拼接）
# 与 rule_db.list_rules 的 valid_sort 保持一致
_ALLOWED_SORT_FIELDS = {
    "name",
    "created_at",
    "updated_at",
    "priority",
    "status",
}
_ALLOWED_SORT_ORDERS = {"ASC", "DESC"}


class ConditionItem(BaseModel):
    parameter: str
    operator: str
    value: str
    unit: str | None = None


class ResultItem(BaseModel):
    parameter: str
    operator: str
    value: str
    unit: str | None = None


class RuleCreateRequest(BaseModel):
    name: str
    description: str = ""
    group_id: int | None = None
    conditions: list[ConditionItem]
    logic_operator: str = "AND"
    result: ResultItem
    status: str = "active"
    priority: int = 0


class RuleUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    group_id: int | None = None
    conditions: list[ConditionItem] | None = None
    logic_operator: str | None = None
    result: ResultItem | None = None
    status: str | None = None
    priority: int | None = None


class GroupCreateRequest(BaseModel):
    name: str
    description: str = ""


class GroupUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None


# 创建路径传入请求模型（ConditionItem/ResultItem），更新路径传入 DB 模型
# （RuleCondition/RuleResult），两者共享 parameter/operator/value 字段，
# 用 Protocol 结构化约束，避免联合类型在迭代处退化。
class _FieldLike(Protocol):
    parameter: str
    operator: str
    value: str


def _validate_rule_data(
    conditions: Sequence[_FieldLike],
    result: _FieldLike,
    logic_operator: str,
) -> str | None:
    if not conditions:
        return "规则条件不能为空"

    for i, cond in enumerate(conditions):
        if not cond.parameter:
            return f"条件{i + 1}的参数名不能为空"
        if cond.operator not in VALID_OPERATORS:
            return f"条件{i + 1}的运算符'{cond.operator}'无效，支持: {', '.join(sorted(VALID_OPERATORS))}"
        if not cond.value:
            return f"条件{i + 1}的值不能为空"

    if logic_operator not in VALID_LOGIC_OPERATORS:
        return f"逻辑运算符'{logic_operator}'无效，仅支持 AND 或 OR"

    if not result.parameter:
        return "结果参数名不能为空"
    if result.operator not in VALID_OPERATORS:
        return f"结果运算符'{result.operator}'无效，支持: {', '.join(sorted(VALID_OPERATORS))}"
    if not result.value:
        return "结果值不能为空"

    return None


def _build_rule_from_request(req: RuleCreateRequest) -> ProcessRule:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return ProcessRule(
        name=req.name,
        description=req.description,
        group_id=req.group_id,
        conditions=[RuleCondition(**c.model_dump()) for c in req.conditions],
        logic_operator=req.logic_operator,
        result=RuleResult(**req.result.model_dump()),
        status=req.status,
        priority=req.priority,
        created_at=now,
        updated_at=now,
    )


def _rule_to_dict(rule: ProcessRule) -> dict:
    return {
        "id": rule.id,
        "name": rule.name,
        "description": rule.description,
        "group_id": rule.group_id,
        "conditions": [c.to_dict() for c in rule.conditions],
        "logic_operator": rule.logic_operator,
        "result": rule.result.to_dict() if rule.result else None,
        "status": rule.status,
        "priority": rule.priority,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
        "preview_text": rule.to_preview_text(),
    }


def _conflict_report_to_dict(report: ConflictReport) -> dict:
    """将冲突报告转换为可序列化的字典"""
    return {
        "conflicting_rule_ids": report.conflicting_rule_ids,
        "conflict_type": report.conflict_type.value,
        "severity": report.severity.value,
        "description": report.description,
        "conflicting_parameters": report.conflicting_parameters,
    }


def _run_conflict_check(rules_to_check: list[ProcessRule]) -> list[dict] | None:
    """
    执行冲突检测，返回警告列表（如果有冲突）
    冲突仅作为警告，不阻塞规则保存
    """
    try:
        conflicts = detect_conflicts(rules_to_check)
        if conflicts:
            return [_conflict_report_to_dict(c) for c in conflicts]
    except (ValueError, TypeError, KeyError) as e:
        logger.warning("冲突检测失败: %s", e, exc_info=True)
    return None


def _group_to_dict(group: RuleGroup, rule_count: int = 0) -> dict:
    return {
        "id": group.id,
        "name": group.name,
        "description": group.description,
        "created_at": group.created_at,
        "updated_at": group.updated_at,
        "rule_count": rule_count,
    }


@router.post("/create", dependencies=[Depends(require_permission("rule:write"))])
async def create_rule(request: RuleCreateRequest):
    err = _validate_rule_data(request.conditions, request.result, request.logic_operator)
    if err:
        return error(ErrorCode.INVALID_REQUEST, message=err, detail=err)

    if request.status not in VALID_STATUSES:
        return error(
            ErrorCode.INVALID_REQUEST,
            message=f"状态'{request.status}'无效，支持: {', '.join(sorted(VALID_STATUSES))}",
        )

    db = get_rule_db()

    if request.group_id:
        group = db.get_group(request.group_id)
        if group is None:
            return error(
                ErrorCode.INVALID_REQUEST,
                message=f"规则分组ID {request.group_id} 不存在",
            )

    rule = _build_rule_from_request(request)
    created = db.create_rule(rule)

    # 执行冲突检测（仅警告，不阻塞保存）
    all_rules = db.list_rules(status="active", limit=DEFAULT_QUERY_LIMIT)
    warnings = _run_conflict_check(all_rules)

    response_data = _rule_to_dict(created)
    if warnings:
        response_data["warnings"] = warnings

    return success(data=response_data, message="规则创建成功")


@router.get("/list", dependencies=[Depends(get_current_user)])
async def list_rules(
    group_id: int | None = Query(None, description="规则分组ID"),
    status: str | None = Query(None, description="规则状态"),
    keyword: str | None = Query(None, description="搜索关键词"),
    sort_by: str = Query("updated_at", description="排序字段"),
    sort_order: str = Query("DESC", description="排序方向"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
):
    # [P0-16] 白名单校验：防止 sort_by / sort_order 注入
    if sort_by not in _ALLOWED_SORT_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=(f"不支持的排序字段: {sort_by}，允许: {', '.join(sorted(_ALLOWED_SORT_FIELDS))}"),
        )
    if sort_order.upper() not in _ALLOWED_SORT_ORDERS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的排序方向: {sort_order}，仅支持 ASC / DESC",
        )

    db = get_rule_db()

    offset = (page - 1) * page_size
    rules = db.list_rules(
        group_id=group_id,
        status=status,
        keyword=keyword,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=page_size,
        offset=offset,
    )

    total = db.count_rules(group_id=group_id, status=status, keyword=keyword)

    return success(
        data={
            "rules": [_rule_to_dict(r) for r in rules],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
        }
    )


@router.get("/detail/{rule_id}", dependencies=[Depends(get_current_user)])
async def get_rule(rule_id: int):
    db = get_rule_db()
    rule = db.get_rule(rule_id)
    if rule is None:
        return error(ErrorCode.NOT_FOUND, message=f"规则ID {rule_id} 不存在")

    return success(data=_rule_to_dict(rule))


@router.put("/update/{rule_id}", dependencies=[Depends(require_permission("rule:write"))])
async def update_rule(rule_id: int, request: RuleUpdateRequest):
    db = get_rule_db()
    existing = db.get_rule(rule_id)
    if existing is None:
        return error(ErrorCode.NOT_FOUND, message=f"规则ID {rule_id} 不存在")

    updated_rule = ProcessRule(
        id=rule_id,
        name=request.name if request.name is not None else existing.name,
        description=request.description if request.description is not None else existing.description,
        group_id=request.group_id if request.group_id is not None else existing.group_id,
        conditions=[RuleCondition(**c.model_dump()) for c in request.conditions]
        if request.conditions is not None
        else existing.conditions,
        logic_operator=request.logic_operator if request.logic_operator is not None else existing.logic_operator,
        result=RuleResult(**request.result.model_dump()) if request.result is not None else existing.result,
        status=request.status if request.status is not None else existing.status,
        priority=request.priority if request.priority is not None else existing.priority,
        created_at=existing.created_at,
        updated_at=None,
    )

    if updated_rule.conditions and updated_rule.result:
        err = _validate_rule_data(updated_rule.conditions, updated_rule.result, updated_rule.logic_operator)
        if err:
            return error(ErrorCode.INVALID_REQUEST, message=err, detail=err)

    if updated_rule.status not in VALID_STATUSES:
        return error(ErrorCode.INVALID_REQUEST, message=f"状态'{updated_rule.status}'无效")

    if updated_rule.group_id and updated_rule.group_id != existing.group_id:
        group = db.get_group(updated_rule.group_id)
        if group is None:
            return error(
                ErrorCode.INVALID_REQUEST,
                message=f"规则分组ID {updated_rule.group_id} 不存在",
            )

    result = db.update_rule(rule_id, updated_rule)
    if result is None:
        return error(ErrorCode.INTERNAL_ERROR, message="规则更新失败")

    # 执行冲突检测（仅警告，不阻塞保存）
    all_rules = db.list_rules(status="active", limit=DEFAULT_QUERY_LIMIT)
    warnings = _run_conflict_check(all_rules)

    response_data = _rule_to_dict(result)
    if warnings:
        response_data["warnings"] = warnings

    return success(data=response_data, message="规则更新成功")


@router.delete("/delete/{rule_id}", dependencies=[Depends(require_permission("rule:write"))])
async def delete_rule(rule_id: int):
    db = get_rule_db()
    if not db.delete_rule(rule_id):
        return error(ErrorCode.NOT_FOUND, message=f"规则ID {rule_id} 不存在")

    return success(message="规则删除成功")


@router.get("/groups/list", dependencies=[Depends(get_current_user)])
async def list_groups():
    db = get_rule_db()
    groups = db.list_groups()

    result = []
    for g in groups:
        # g.id 为自增主键，持久化分组必非空；None 仅可能来自未落库对象，按 0 计数兜底
        count = db.get_group_rule_count(g.id) if g.id is not None else 0
        result.append(_group_to_dict(g, count))

    return success(data={"groups": result, "total": len(result)})


@router.post("/groups/create", dependencies=[Depends(require_permission("rule:write"))])
async def create_group(request: GroupCreateRequest):
    if not request.name:
        return error(ErrorCode.INVALID_REQUEST, message="分组名称不能为空")

    db = get_rule_db()
    existing = db._find_group_by_name(request.name)
    if existing:
        return error(ErrorCode.INVALID_REQUEST, message=f"分组名称'{request.name}'已存在")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    group = RuleGroup(
        name=request.name,
        description=request.description,
        created_at=now,
        updated_at=now,
    )
    created = db.create_group(group)

    return success(data=_group_to_dict(created), message="分组创建成功")


@router.put("/groups/update/{group_id}", dependencies=[Depends(require_permission("rule:write"))])
async def update_group(group_id: int, request: GroupUpdateRequest):
    db = get_rule_db()
    existing = db.get_group(group_id)
    if existing is None:
        return error(ErrorCode.NOT_FOUND, message=f"分组ID {group_id} 不存在")

    updated = RuleGroup(
        id=group_id,
        name=request.name if request.name is not None else existing.name,
        description=request.description if request.description is not None else existing.description,
        created_at=existing.created_at,
        updated_at=None,
    )

    result = db.update_group(group_id, updated)
    if result is None:
        return error(ErrorCode.INTERNAL_ERROR, message="分组更新失败")

    return success(data=_group_to_dict(result), message="分组更新成功")


@router.delete("/groups/delete/{group_id}", dependencies=[Depends(require_permission("rule:write"))])
async def delete_group(group_id: int):
    db = get_rule_db()
    count = db.get_group_rule_count(group_id)
    if count > 0:
        return error(
            ErrorCode.INVALID_REQUEST,
            message=f"分组下还有 {count} 条规则，请先删除或转移规则后再删除分组",
        )

    if not db.delete_group(group_id):
        return error(ErrorCode.NOT_FOUND, message=f"分组ID {group_id} 不存在")

    return success(message="分组删除成功")


@router.post("/import", dependencies=[Depends(require_permission("rule:write"))])
async def import_rules(file: UploadFile = File(...)):
    # P0-12/P0-13 修复：使用 validate_upload 统一校验
    # （扩展名 + magic bytes + 分块流式读取 + 大小限制）
    # JSON 为文本类扩展名，validate_upload 会跳过 magic 校验仅做扩展名 + 大小校验
    _RULE_IMPORT_MAX_SIZE = 20 * 1024 * 1024  # 20MB
    try:
        content = await validate_upload(
            file,
            max_size=_RULE_IMPORT_MAX_SIZE,
            allowed_extensions={".json"},
            allowed_mimes={"application/json"},
        )
    except HTTPException:
        # validate_upload 抛出的 413/415/400 透传
        raise

    try:
        json.loads(content)
    except json.JSONDecodeError:
        return error(ErrorCode.INVALID_REQUEST, message="无效的JSON文件格式")

    save_path = RULE_EXPORT_DIR / f"import_{uuid.uuid4().hex[:8]}_{file.filename}"
    with open(save_path, "wb") as f:
        f.write(content)

    db = get_rule_db()
    try:
        result = db.import_rules(str(save_path))
        if result.get("version_check") == "incompatible":
            return error(
                ErrorCode.INVALID_REQUEST,
                message=result.get("version_message", "版本不兼容"),
            )
        message = f"导入成功: {result['imported_rules']} 条规则, {result['imported_groups']} 个分组"
        if result.get("version_check") == "warning":
            message += f" | {result['version_message']}"
        return success(
            data=result,
            message=message,
        )
    except (OSError, ValueError, KeyError, TypeError) as e:
        logger.error("规则导入失败: %s", e, exc_info=True)
        # 修复：避免 str(e) 直接进入响应
        safe = safe_error_message(e, context="rules.import", fallback="规则导入失败")
        return error(
            ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail={"error_id": safe.get("error_id")} if safe.get("error_id") else None,
        )


@router.get("/export", dependencies=[Depends(get_current_user)])
async def export_rules():
    db = get_rule_db()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_path = RULE_EXPORT_DIR / f"rules_export_{timestamp}.json"

    try:
        db.export_rules(str(export_path))
        return FileResponse(
            path=str(export_path),
            filename=export_path.name,
            media_type="application/json",
        )
    except (OSError, ValueError, TypeError) as e:
        logger.error("规则导出失败: %s", e, exc_info=True)
        # 修复：避免 str(e) 直接进入响应
        safe = safe_error_message(e, context="rules.export", fallback="规则导出失败")
        return error(
            ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail={"error_id": safe.get("error_id")} if safe.get("error_id") else None,
        )


@router.post("/backup", dependencies=[Depends(require_permission("backup:read"))])
async def backup_database():
    db = get_rule_db()
    try:
        backup_path = db.backup_database()
        return success(data={"backup_path": backup_path}, message="数据库备份成功")
    except (OSError, RuntimeError, ValueError) as e:
        logger.error("数据库备份失败: %s", e, exc_info=True)
        # 修复：避免 str(e) 直接进入响应
        safe = safe_error_message(e, context="rules.backup", fallback="数据库备份失败")
        return error(
            ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail={"error_id": safe.get("error_id")} if safe.get("error_id") else None,
        )


@router.get("/stats", dependencies=[Depends(get_current_user)])
async def get_stats():
    db = get_rule_db()

    total = db.count_rules()
    active = db.count_rules(status="active")
    inactive = db.count_rules(status="inactive")
    draft = db.count_rules(status="draft")
    groups = len(db.list_groups())

    return success(
        data={
            "total_rules": total,
            "active_rules": active,
            "inactive_rules": inactive,
            "draft_rules": draft,
            "total_groups": groups,
        }
    )


@router.get("/preview", dependencies=[Depends(get_current_user)])
async def preview_rule_text(
    conditions: str = Query(..., description="条件JSON数组"),
    logic_operator: str = Query("AND", description="逻辑运算符"),
    result: str = Query(..., description="结果JSON对象"),
):
    try:
        cond_list = json.loads(conditions)
        result_obj = json.loads(result)
    except json.JSONDecodeError:
        return error(ErrorCode.INVALID_REQUEST, message="无效的JSON格式")

    rule = ProcessRule(
        conditions=[RuleCondition(**c) for c in cond_list],
        logic_operator=logic_operator,
        result=RuleResult(**result_obj),
    )

    return success(data={"preview_text": rule.to_preview_text()})
