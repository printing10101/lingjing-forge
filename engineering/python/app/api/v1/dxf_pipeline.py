"""DXF 端到端批处理 API。

提供：
    - POST /api/v1/dxf/process    端到端处理单个 DXF
    - POST /api/v1/dxf/batch      批量处理（最多 20 个）
    - POST /api/v1/dxf/e2e-fixture   用 fixtures 跑端到端 smoke test

用于：CI、研发自测、研究模块的 shadow mode 触发。
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.auth.permissions import require_permission
from app.utils.utils import validate_user_path
from app.core.safe_errors import safe_error_message

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/dxf",
    tags=["dxf"],
    dependencies=[Depends(require_permission("dxf:read"))],
)


# 路径安全：防止路径遍历攻击

# 允许的 DXF 根目录（项目根 + data/ 子目录）。生产环境可通过环境变量覆盖。
_ALLOWED_DXF_BASE_DIRS: list[Path] = [
    (Path(os.getenv("LNN_PROJECT_ROOT", Path(__file__).resolve().parents[4]))).resolve(),
    (Path(os.getenv("LNN_DATA_DIR", "data"))).resolve(),
]


def _validate_dxf_path(user_path: str) -> Path:
    """校验用户提供的 DXF 路径，防止路径遍历攻击。

    规则：
        1. 必须是 .dxf 扩展名
        2. 解析后的绝对路径必须位于 _ALLOWED_DXF_BASE_DIRS 之一之下
        3. 拒绝包含 .. 或绝对路径逃逸的输入

    委托给统一的 ``app.utils.utils.validate_user_path`` 实现。

    Args:
        user_path: 用户提交的 DXF 文件路径（相对或绝对）

    Returns:
        校验通过后的 Path 对象

    Raises:
        HTTPException: 400 当路径不合法或逃逸允许范围
    """
    try:
        return validate_user_path(
            user_path=user_path,
            allowed_base_dirs=_ALLOWED_DXF_BASE_DIRS,
            allowed_extensions={".dxf"},
            project_root=_ALLOWED_DXF_BASE_DIRS[0],
        )
    except ValueError as exc:
        # 包装异常消息，避免直接回显内部错误细节
        safe = safe_error_message(exc, context="dxf_pipeline.validate_user_path", fallback="DXF 路径校验失败")
        raise HTTPException(
            status_code=400,
            detail=safe["message"],
            headers={"X-Error-ID": safe["error_id"]},
        ) from exc


# 请求/响应模型


def _validate_output_dir(user_path: str) -> Path:
    """校验用户提供的输出目录，防止任意目录写入（与 DXF 输入同源白名单，无扩展名限制）。

    Raises:
        HTTPException: 400 当路径逃逸允许范围。
    """
    try:
        return validate_user_path(
            user_path=user_path,
            allowed_base_dirs=_ALLOWED_DXF_BASE_DIRS,
            allowed_extensions=None,
            project_root=_ALLOWED_DXF_BASE_DIRS[0],
        )
    except ValueError as exc:
        safe = safe_error_message(exc, context="dxf_pipeline.validate_output_dir", fallback="输出目录校验失败")
        raise HTTPException(
            status_code=400,
            detail=safe["message"],
            headers={"X-Error-ID": safe["error_id"]},
        ) from exc


class DxfProcessRequest(BaseModel):
    dxf_path: str
    output_dir: str | None = None
    postprocessor: str | None = "fanuc_0i"
    user_id: str | None = None
    material: str = "45钢"

    @field_validator("dxf_path")
    @classmethod
    def _validate_dxf_path_field(cls, v: str) -> str:
        """在模型层校验 DXF 路径，防止路径遍历。"""
        _validate_dxf_path(v)  # 抛出 HTTPException 即终止
        return v

    @field_validator("output_dir")
    @classmethod
    def _validate_output_dir_field(cls, v: str | None) -> str | None:
        """输出目录同样不得逃逸白名单（防任意目录写入）。"""
        if v is not None:
            _validate_output_dir(v)
        return v


class DxfBatchRequest(BaseModel):
    dxf_paths: list[str] = Field(..., min_length=1, max_length=20)
    output_dir: str | None = None
    postprocessor: str | None = "fanuc_0i"
    user_id: str | None = None
    material: str = "45钢"

    @field_validator("dxf_paths")
    @classmethod
    def _validate_dxf_paths_field(cls, v: list[str]) -> list[str]:
        """批量校验每个 DXF 路径。"""
        for p in v:
            _validate_dxf_path(p)
        return v

    @field_validator("output_dir")
    @classmethod
    def _validate_output_dir_field(cls, v: str | None) -> str | None:
        if v is not None:
            _validate_output_dir(v)
        return v


class DxfE2EFixtureRequest(BaseModel):
    fixtures_dir: str = "data/test_fixtures"
    output_dir: str = "data/outputs/e2e"
    postprocessor: str = "fanuc_0i"
    user_id: str | None = "e2e_runner"

    @field_validator("fixtures_dir", "output_dir")
    @classmethod
    def _validate_dirs_field(cls, v: str) -> str:
        return str(_validate_output_dir(v))


# 端点


@router.post("/process")
def process_dxf(req: DxfProcessRequest) -> dict[str, Any]:
    """处理单个 DXF 文件（端到端）。"""
    from app.dxf.process_service import DxfProcessService

    svc = DxfProcessService()
    r = svc.process(
        dxf_path=req.dxf_path,
        output_dir=req.output_dir,
        postprocessor=req.postprocessor,
        user_id=req.user_id,
        material=req.material,
    )
    return r.to_dict()


@router.post("/batch")
def process_batch(req: DxfBatchRequest) -> dict[str, Any]:
    """批量处理多个 DXF（最多 20 个）。"""
    from app.dxf.process_service import DxfProcessService

    svc = DxfProcessService()
    results = []
    for p in req.dxf_paths:
        r = svc.process(
            dxf_path=p,
            output_dir=req.output_dir,
            postprocessor=req.postprocessor,
            user_id=req.user_id,
            material=req.material,
        )
        results.append(r.to_dict())
    success_count = sum(1 for x in results if x["success"])
    return {
        "total": len(results),
        "success": success_count,
        "failed": len(results) - success_count,
        "results": results,
    }


# ── 批量异步任务（避免 20 文件串行长请求占用 worker）──

# 进程级批量任务注册表：仅保留最近 _BATCH_JOBS_MAX 个，防内存泄漏
_batch_jobs: dict[str, dict[str, Any]] = {}
_batch_jobs_order: list[str] = []
_BATCH_JOBS_MAX = 50
# 防止后台任务被 GC（asyncio.create_task 文档要求外部保留强引用）
_batch_tasks: set[asyncio.Task] = set()


def _prune_batch_jobs() -> None:
    while len(_batch_jobs_order) > _BATCH_JOBS_MAX:
        stale = _batch_jobs_order.pop(0)
        _batch_jobs.pop(stale, None)


async def _run_batch_job(job_id: str, req: DxfBatchRequest) -> None:
    """逐文件执行批量任务；每文件结果实时写入注册表供轮询。"""
    from app.dxf.process_service import DxfProcessService

    job = _batch_jobs.get(job_id)
    if job is None:
        return
    svc = DxfProcessService()
    try:
        for p in req.dxf_paths:
            r = await asyncio.to_thread(
                lambda pp=p: svc.process(
                    dxf_path=pp,
                    output_dir=req.output_dir,
                    postprocessor=req.postprocessor,
                    user_id=req.user_id,
                )
            )
            rd = r.to_dict()
            job["results"].append(rd)
            job["done"] += 1
            if rd.get("success"):
                job["success"] += 1
            else:
                job["failed"] += 1
        job["status"] = "completed"
    except Exception as e:
        safe = safe_error_message(e, context="dxf_pipeline.batch_job")
        job["status"] = "failed"
        job["error"] = safe.get("message", str(e))
        logger.error("批量任务 %s 失败: %s", job_id, safe.get("error_id"), exc_info=True)


@router.post("/batch-async")
async def process_batch_async(req: DxfBatchRequest) -> dict[str, Any]:
    """批量处理多个 DXF（异步任务模式）。

    立即返回 job_id，处理在后台进行；用 GET /batch-status/{job_id} 轮询进度。
    """
    job_id = f"batch_{uuid.uuid4().hex[:12]}"
    now = datetime.now().isoformat()
    _batch_jobs[job_id] = {
        "job_id": job_id,
        "status": "running",
        "total": len(req.dxf_paths),
        "done": 0,
        "success": 0,
        "failed": 0,
        "results": [],
        "created_at": now,
    }
    _batch_jobs_order.append(job_id)
    _prune_batch_jobs()

    task = asyncio.create_task(_run_batch_job(job_id, req))
    _batch_tasks.add(task)
    task.add_done_callback(_batch_tasks.discard)

    return {
        "job_id": job_id,
        "status": "running",
        "total": len(req.dxf_paths),
        "status_url": "/api/v1/dxf/batch-status/" + job_id,
    }


@router.get("/batch-status/{job_id}")
def batch_job_status(job_id: str) -> dict[str, Any]:
    """查询批量处理任务进度。"""
    job = _batch_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return dict(job)


@router.post("/e2e-fixture")
def e2e_fixture(req: DxfE2EFixtureRequest) -> dict[str, Any]:
    """用 data/test_fixtures/ 下的所有 DXF 跑端到端测试。

    通常有 5+ 个 fixture，可以扩展到 20+。
    """
    fixtures_dir = Path(req.fixtures_dir)
    if not fixtures_dir.exists():
        raise HTTPException(
            status_code=404,
            detail="指定的 fixtures 目录不存在，请检查路径配置",
        )
    dxf_files = sorted(fixtures_dir.glob("*.dxf"))
    if not dxf_files:
        raise HTTPException(
            status_code=404,
            detail="指定目录中未找到任何 DXF 文件",
        )
    if len(dxf_files) > 20:
        dxf_files = dxf_files[:20]

    from app.dxf.process_service import DxfProcessService

    svc = DxfProcessService()
    results = []
    out_root = Path(req.output_dir)
    for dxf in dxf_files:
        # 每个 fixture 用自己的子目录
        sub = out_root / dxf.stem
        r = svc.process(
            dxf_path=dxf,
            output_dir=sub,
            postprocessor=req.postprocessor,
            user_id=req.user_id,
        )
        results.append(r.to_dict())
    success = sum(1 for x in results if x["success"])
    return {
        "fixtures_dir": str(fixtures_dir),
        "output_dir": str(out_root),
        "total": len(results),
        "success": success,
        "failed": len(results) - success,
        "postprocessor": req.postprocessor,
        "results": results,
    }


__all__ = ["router"]
