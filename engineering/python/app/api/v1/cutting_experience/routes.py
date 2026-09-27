"""Cutting Experience 采集 API（P2-3，数据飞轮数据采集闭环）。

数据流：
- POST /capture      单条采集（手工录入 / 现场实测）
- POST /batch        批量采集（MTConnect 管道落库 / CSV 导入）
- GET  /             分页查询（多条件筛选）
- GET  /stats        聚合统计（节拍/粗糙度/磨损均值、合格率、异常率）
- GET  /{id}         单条详情
- DELETE /{id}       删除（管理用途）

权限：
- 写操作 require_permission("experience:write")
- 读操作 require_permission("experience:read")
权限码注册于 ``app/database/models/_presets.py``，admin 角色默认授权。

错误处理：
统一使用 ``app.core.exceptions`` 分级异常（由全局 handler 渲染为
``{code, message, detail}`` 统一响应体），不再直接抛 HTTPException：
- RuntimeError（数据库未配置，repository 层约定）→ ServiceUnavailableException (503/2002)
- 记录不存在 → RecordNotFoundException (404/3002)
- 参数非法（时间格式/批量超限）→ ValidationException (422/1002)
- 未预期异常 → InternalServerException (500/2001)，detail 带上下文
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Path, Query

from app.auth.permissions import require_permission
from app.contracts.cutting_experience import (
    CuttingExperience,
    ExperienceQuery,
    MachiningResult,
    MachiningType,
)
from app.core.exceptions import (
    InternalServerException,
    RecordNotFoundException,
    ServiceUnavailableException,
    ValidationException,
)
from app.services.domain.cutting_experience_repository import (
    aggregate_experience_stats,
    create_cutting_experience,
    create_many_cutting_experiences,
    delete_cutting_experience,
    get_cutting_experience,
    list_cutting_experiences,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/experience", tags=["cutting-experience"])

#: : 单次批量采集上限（超过应走 CSV 导入的持久化管道）
BATCH_LIMIT = 1000


def _db_unavailable(exc: RuntimeError) -> ServiceUnavailableException:
    """将 repository 层约定的 RuntimeError（数据库未配置）转为统一 503。"""
    return ServiceUnavailableException(
        "数据库未配置，数据采集暂不可用",
        detail={"reason": str(exc)},
    )


def _parse_dt(value: str | None, field: str) -> datetime | None:
    """解析 ISO8601 时间查询参数，非法格式抛统一 422。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationException(
            f"非法时间格式: {value}",
            detail={"field": field, "expected": "ISO 8601"},
        ) from exc


@router.post("/capture", status_code=201)
async def capture_experience(
    payload: CuttingExperience,
    _: None = Depends(require_permission("experience:write")),
) -> dict:
    """单条切削实测采集。"""
    try:
        return await create_cutting_experience(payload)
    except RuntimeError as exc:
        raise _db_unavailable(exc) from exc
    except Exception as exc:
        logger.exception("capture experience error: %s", exc)
        raise InternalServerException("采集服务暂时不可用") from exc


@router.post("/batch", status_code=201)
async def batch_capture_experiences(
    payloads: list[CuttingExperience],
    _: None = Depends(require_permission("experience:write")),
) -> dict:
    """批量采集（MTConnect 管道 / CSV 导入）。

    请求体为 CuttingExperience 数组，全部校验通过后一次性落库。
    """
    if len(payloads) > BATCH_LIMIT:
        raise ValidationException(
            f"单次批量上限 {BATCH_LIMIT} 条",
            detail={"limit": BATCH_LIMIT, "received": len(payloads)},
        )
    try:
        count = await create_many_cutting_experiences(payloads)
        return {"inserted": count, "requested": len(payloads)}
    except RuntimeError as exc:
        raise _db_unavailable(exc) from exc
    except Exception as exc:
        logger.exception("batch capture experience error: %s", exc)
        raise InternalServerException("批量采集服务暂时不可用") from exc


@router.get("")
async def query_experiences(
    machine_id: str | None = Query(default=None, max_length=64),
    tool_id: str | None = Query(default=None, max_length=64),
    material: str | None = Query(default=None, max_length=64),
    machining_type: MachiningType | None = Query(default=None),
    result: MachiningResult | None = Query(default=None),
    has_anomaly: bool | None = Query(default=None),
    start_time: str | None = Query(default=None, description="ISO8601 起始时间"),
    end_time: str | None = Query(default=None, description="ISO8601 结束时间"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _: None = Depends(require_permission("experience:read")),
) -> dict:
    """分页查询切削实测记录。"""
    query = ExperienceQuery(
        machine_id=machine_id,
        tool_id=tool_id,
        material=material,
        machining_type=machining_type,
        result=result,
        has_anomaly=has_anomaly,
        start_time=_parse_dt(start_time, "start_time"),
        end_time=_parse_dt(end_time, "end_time"),
        limit=limit,
        offset=offset,
    )
    try:
        return await list_cutting_experiences(query)
    except RuntimeError as exc:
        raise _db_unavailable(exc) from exc
    except Exception as exc:
        logger.exception("list experiences error: %s", exc)
        raise InternalServerException("查询服务暂时不可用") from exc


@router.get("/stats")
async def experience_stats(
    machine_id: str | None = Query(default=None, max_length=64),
    tool_id: str | None = Query(default=None, max_length=64),
    _: None = Depends(require_permission("experience:read")),
) -> dict:
    """聚合统计（节拍/粗糙度/磨损均值、合格率、异常率）。"""
    query = ExperienceQuery(machine_id=machine_id, tool_id=tool_id, limit=1)
    try:
        stats = await aggregate_experience_stats(query)
        return stats.model_dump()
    except RuntimeError as exc:
        raise _db_unavailable(exc) from exc
    except Exception as exc:
        logger.exception("experience stats error: %s", exc)
        raise InternalServerException("统计服务暂时不可用") from exc


@router.get("/{record_id}")
async def experience_detail(
    record_id: str = Path(min_length=1, max_length=64),
    _: None = Depends(require_permission("experience:read")),
) -> dict:
    """单条详情。

    ``record_id`` 接受采集接口返回的 ORM 主键（``exp_`` 前缀）或契约 UUID，
    repository 层统一归一化后查询。
    """
    try:
        record = await get_cutting_experience(record_id)
    except RuntimeError as exc:
        raise _db_unavailable(exc) from exc
    except Exception as exc:
        logger.exception("get experience error: %s", exc)
        raise InternalServerException("查询服务暂时不可用") from exc
    if record is None:
        raise RecordNotFoundException(f"记录不存在: {record_id}")
    return record


@router.delete("/{record_id}")
async def remove_experience(
    record_id: str = Path(min_length=1, max_length=64),
    _: None = Depends(require_permission("experience:write")),
) -> dict:
    """删除记录（管理用途）。"""
    try:
        deleted = await delete_cutting_experience(record_id)
    except RuntimeError as exc:
        raise _db_unavailable(exc) from exc
    except Exception as exc:
        logger.exception("delete experience error: %s", exc)
        raise InternalServerException("删除服务暂时不可用") from exc
    if not deleted:
        raise RecordNotFoundException(f"记录不存在: {record_id}")
    return {"deleted": True, "id": str(record_id)}
