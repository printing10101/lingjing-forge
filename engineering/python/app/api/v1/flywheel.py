"""飞轮状态 API 接口。

提供飞轮系统状态查询、指标获取和报告生成功能。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission

from app.metrics.flywheel_metrics import (
    get_flywheel_collector,
    save_report_to_file,
)

logger = logging.getLogger(__name__)

# 周报保存目录白名单基（项目根），防任意目录写入
_REPORT_BASE_DIR = Path(__file__).resolve().parents[3]


def _validate_report_dir(user_dir: str) -> Path:
    """校验周报输出目录，仅允许项目根下的相对路径。"""
    import os

    from app.utils.utils import validate_user_path

    try:
        return validate_user_path(
            user_path=user_dir,
            allowed_base_dirs=[Path(os.getenv("LNN_PROJECT_ROOT", str(_REPORT_BASE_DIR))).resolve()],
            allowed_extensions=None,
            project_root=_REPORT_BASE_DIR,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="报告输出目录不合法或超出允许范围（仅允许项目根下相对路径）",
        ) from exc


router = APIRouter(
    prefix="/api/v1/flywheel",
    tags=["Flywheel"],
    dependencies=[Depends(require_permission("flywheel:read"))],
)


# Response schemas


class MetricDefinition(BaseModel):
    """指标定义。"""

    name: str = Field(..., description="指标名称")
    description: str = Field(..., description="指标含义")
    unit: str = Field(..., description="单位")
    range: str = Field(..., description="取值范围")
    calculation: str = Field(..., description="计算方式")


class FlywheelStatusResponse(BaseModel):
    """飞轮状态响应。"""

    status: str = Field(..., description="飞轮状态: healthy / warning / critical")
    data_volume: int = Field(..., description="加工记录数（条）")
    model_quality: float = Field(..., description="模型质量（%，0-100）")
    adoption_rate: float = Field(..., description="用户采纳率（%，0-100）")
    uncertainty_mean: float = Field(..., description="不确定性均值（0-1）")
    feedback_delay: float = Field(..., description="回灌延迟（分钟）")
    health_score: float = Field(..., description="健康分数（0-100）")
    timestamp: str = Field(..., description="采集时间（ISO 8601）")


class FlywheelReportResponse(BaseModel):
    """飞轮报告响应。"""

    report_type: str
    generated_at: str
    period: dict[str, str]
    current_metrics: dict[str, Any]
    trends: dict[str, Any]
    summary: dict[str, Any]


class MetricDefinitionsResponse(BaseModel):
    """指标定义列表响应。"""

    metrics: list[MetricDefinition]


# Endpoints


@router.get(
    "/status",
    response_model=FlywheelStatusResponse,
    summary="获取飞轮当前状态",
    description="返回所有关键飞轮指标，包含加工记录数、模型质量、用户采纳率、不确定性均值和回灌延迟。",
)
async def get_flywheel_status() -> FlywheelStatusResponse:
    """获取飞轮当前状态。

    p4-4c: 改为调用异步方法 ``collect_current_metrics_async`` 与
    ``generate_weekly_report_async``，从真实数据源（IDatasetStore /
    ISnapshotStore）采集指标。无数据源时返回零值（兼容旧调用方）。
    """
    try:
        collector = get_flywheel_collector()
        metrics = await collector.collect_current_metrics_async()
        report = await collector.generate_weekly_report_async()
        health_score = report.get("summary", {}).get("health_score", 0)
        health_status = report.get("summary", {}).get("health_status", "unknown")

        status_map = {
            "excellent": "healthy",
            "good": "healthy",
            "fair": "warning",
            "poor": "critical",
        }

        return FlywheelStatusResponse(
            status=status_map.get(health_status, "warning"),
            data_volume=metrics.data_volume,
            model_quality=metrics.model_quality,
            adoption_rate=metrics.adoption_rate,
            uncertainty_mean=metrics.uncertainty_mean,
            feedback_delay=metrics.feedback_delay,
            health_score=health_score,
            timestamp=metrics.timestamp,
        )
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        logger.error("Failed to collect flywheel status: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "flywheel_status_collection_failed",
                "message": "飞轮状态采集失败，请稍后重试",
            },
        ) from e


@router.get(
    "/metrics",
    summary="获取飞轮指标详情（含历史数据）",
    description="返回当前指标及指定天数范围内的历史数据。",
)
async def get_flywheel_metrics(
    days: int = Query(default=7, ge=1, le=90, description="历史数据天数范围（1-90）"),
) -> dict[str, Any]:
    """获取飞轮指标详情。

    p4-4c: 改为调用异步方法 ``collect_current_metrics_async`` 与
    ``get_historical_metrics_async``，从真实数据源采集。
    """
    try:
        collector = get_flywheel_collector()
        current = await collector.collect_current_metrics_async()
        historical = await collector.get_historical_metrics_async(days=days)

        return {
            "current": current.to_dict(),
            "historical": [m.to_dict() for m in historical],
            "period_days": days,
        }
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        logger.error("Failed to get flywheel metrics: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "flywheel_metrics_fetch_failed",
                "message": "飞轮指标获取失败，请稍后重试",
            },
        ) from e


@router.get(
    "/report/weekly",
    response_model=FlywheelReportResponse,
    summary="生成每周飞轮报告",
    description="生成包含当前指标、历史趋势和改进建议的周度飞轮报告。",
)
async def generate_weekly_report(
    save: bool = Query(default=False, description="是否同时保存报告到文件"),
    output_dir: str = Query(default="reports", description="报告保存目录（仅允许项目根下相对路径）"),
) -> dict[str, Any]:
    """生成每周飞轮报告。

    p4-4c: 改为调用异步方法 ``generate_weekly_report_async``，从真实数据源采集。
    """
    try:
        collector = get_flywheel_collector()
        report = await collector.generate_weekly_report_async()

        if save:
            safe_dir = _validate_report_dir(output_dir)
            filepath = save_report_to_file(report, safe_dir)
            report["saved_to"] = str(filepath)

        return report
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as e:
        logger.error("Failed to generate weekly report: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "weekly_report_generation_failed",
                "message": "周报生成失败，请稍后重试",
            },
        ) from e


@router.get(
    "/definitions",
    response_model=MetricDefinitionsResponse,
    summary="获取指标定义说明",
    description="返回所有飞轮指标的定义，包含含义、单位、取值范围和计算方式。",
)
async def get_metric_definitions() -> MetricDefinitionsResponse:
    """获取指标定义。"""
    definitions = [
        MetricDefinition(
            name="data_volume",
            description="加工记录数：系统处理的数据记录总量",
            unit="条",
            range=">= 0",
            calculation="SELECT COUNT(*) FROM machining_records",
        ),
        MetricDefinition(
            name="model_quality",
            description="模型质量：模型预测准确率",
            unit="%",
            range="0 - 100",
            calculation="正确预测数 / 总预测数 × 100",
        ),
        MetricDefinition(
            name="adoption_rate",
            description="用户采纳率：用户接受模型建议的比例",
            unit="%",
            range="0 - 100",
            calculation="采纳建议次数 / 总建议次数 × 100",
        ),
        MetricDefinition(
            name="uncertainty_mean",
            description="不确定性均值：模型预测不确定性的平均值",
            unit="分数",
            range="0 - 1",
            calculation="AVG(uncertainty_score) FROM predictions",
        ),
        MetricDefinition(
            name="feedback_delay",
            description="回灌延迟：数据从产生到反馈回系统的平均时间",
            unit="分钟",
            range=">= 0",
            calculation="AVG(feedback_time - data_time) FROM feedback_loop",
        ),
    ]

    return MetricDefinitionsResponse(metrics=definitions)


# 模型热更新部署记录

# 候选模型存储目录（相对工作目录），依次扫描
_MODEL_STORAGE_CANDIDATES: tuple[Path, ...] = (
    Path("models/lnn"),
    Path("data/models"),
    Path("output/models"),
    Path("output/models/finetuned"),
)

# 识别为已部署模型的文件后缀
_MODEL_FILE_SUFFIXES: frozenset[str] = frozenset({".pt", ".pth", ".onnx", ".bin", ".safetensors", ".ckpt"})


@router.get(
    "/deployments",
    summary="获取模型热更新部署记录",
    description=(
        "扫描本地模型存储目录（models/lnn、data/models、output/models 等），"
        "返回已部署模型产物的真实文件记录（名称、路径、大小、更新时间、状态）。"
    ),
)
async def get_flywheel_deployments() -> dict[str, Any]:
    """获取已部署模型记录。

    数据来源为本地模型存储目录的真实文件系统扫描（非写死数据）。
    目录不存在或为空时返回空列表（兼容全新安装环境）。
    """
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for base in _MODEL_STORAGE_CANDIDATES:
        if not base.is_dir():
            continue
        try:
            for path in sorted(base.rglob("*")):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in _MODEL_FILE_SUFFIXES:
                    continue
                rel = str(path)
                if rel in seen:
                    continue
                seen.add(rel)
                stat = path.stat()
                records.append(
                    {
                        "model_name": path.stem,
                        "path": rel,
                        "size_bytes": stat.st_size,
                        "size_human": _format_size(stat.st_size),
                        "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                        "status": "active",
                        "version": path.stem,
                    }
                )
        except OSError as e:
            logger.warning("扫描模型目录失败 %s: %s", base, e)
            continue

    # 按更新时间倒序（最新部署在前）
    records.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return {"deployments": records, "count": len(records)}


def _format_size(num_bytes: int) -> str:
    """将字节数格式化为可读字符串。"""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


# ---------------------------------------------------------------------------
# W4.1 仿真合成数据（飞轮充能：不等真机也能动的数据源，2026-09 全量升格）
# ---------------------------------------------------------------------------


class SyntheticGenerateRequest(BaseModel):
    """合成数据生成请求（参数扫描网格）。"""

    material: str = Field("45steel", description="材料标识（透传切削力预测器）")
    tool: str = Field("endmill_d10", description="刀具标识")
    rpm_values: list[float] | None = Field(None, description="转速扫描档（默认 3 档）")
    feed_values: list[float] | None = Field(None, description="进给扫描档（默认 3 档）")
    depth_values: list[float] | None = Field(None, description="切深扫描档（默认 3 档）")
    stock: dict[str, float] | None = Field(None, description="毛坯尺寸 {length,width,height}")
    dataset_name: str = Field("synthetic_machining_params_v1", description="目标数据集名")
    use_pinn: bool = Field(False, description="切削力优先 PINN（无 torch 自动降级 Kienzle）")
    max_combinations: int = Field(200, ge=1, le=200, description="网格上限")


class SyntheticGenerateResponse(BaseModel):
    """合成数据生成结果。"""

    success: bool
    dataset_id: str | None = None
    version: str | None = None
    total: int = 0
    voxel_passed: int = 0
    voxel_failed: int = 0
    duration_seconds: float = 0.0
    combos_skipped: int = 0
    errors: list[str] = Field(default_factory=list)
    message: str = ""


# P2-13：重型副作用端点（200 组仿真 + 数据集写入）不适用 router 级
# flywheel:read——升级为 dataset:write（工程角色已持有该码）
@router.post(
    "/synthetic/generate",
    response_model=SyntheticGenerateResponse,
    dependencies=[Depends(require_permission("dataset:write"))],
)
async def generate_synthetic_dataset(req: SyntheticGenerateRequest):
    """体素仿真+切削力模型参数扫描，批量生成"参数→仿真结果"样本对落库。

    每个样本含合成 G 代码、体素校验报告（通过/过切/撞刀）与切削力预测；
    撞刀样本与通过样本同权重落库——voxel_passed=False 正是"敢上机"
    闸门分类器的正样本。数据集经 DatasetStore 提交为不可变版本（带血缘），
    写入即进入飞轮训练侧消费通道。
    """
    from app.pipelines.synthetic_data_gen import generate_synthetic_dataset as _gen
    from app.pipelines.synthetic_data_gen import synthetic_enabled

    if not synthetic_enabled():
        return SyntheticGenerateResponse(
            success=False,
            message="合成数据生成已关闭（LNN_FLYWHEEL_SYNTHETIC_ENABLED=0）",
        )
    summary = await _gen(
        material=req.material,
        tool=req.tool,
        rpm_values=req.rpm_values,
        feed_values=req.feed_values,
        depth_values=req.depth_values,
        stock=req.stock,
        dataset_name=req.dataset_name,
        use_pinn=req.use_pinn,
        max_combinations=req.max_combinations,
    )
    return SyntheticGenerateResponse(success=summary.total > 0, **summary.to_dict())


# ---------------------------------------------------------------------------
# 用户反馈提交（自进化 M0：把 data_flywheel 反馈采集层接入业务调用方）
# ---------------------------------------------------------------------------


class FeedbackSubmitRequest(BaseModel):
    """用户反馈提交请求（标注 / 采纳 / 修正三选一）。

    ``prediction_id`` 建议传编排器返回的 ``pipeline_id`` 或 G 代码任务
    ``task_id``，使反馈可与失败案例库 / trace 关联（自进化血缘）。
    ``metadata`` 可携带 ``prompt_id`` / ``prompt_version`` / 评分摘要，
    供提示词迭代按反馈质量分组。
    """

    feedback_type: str = Field(..., description="annotation | adoption | correction")
    prediction_id: str | None = Field(None, description="关联的预测/管线 ID（pipeline_id 或 task_id）")
    model_version: str | None = Field(None, description="被反馈的模型/提示词版本标识")
    original_output: dict[str, Any] | None = Field(None, description="原始模型输出（correction 必填）")
    corrected_output: dict[str, Any] | None = Field(None, description="用户修正后的输出（correction 必填）")
    accepted: bool | None = Field(None, description="是否采纳（adoption 必填）")
    notes: str = Field("", description="用户备注")
    user_id: str = Field("local", description="反馈用户 ID")
    metadata: dict[str, Any] | None = Field(None, description="扩展元数据（prompt_version 等）")
    flush: bool = Field(False, description="提交后立即落盘（默认入缓冲区批量提交）")


class FeedbackSubmitResponse(BaseModel):
    """用户反馈提交结果。"""

    feedback_id: str = Field(..., description="反馈唯一 ID")
    feedback_type: str = Field(..., description="annotation | adoption | correction")
    flushed: bool = Field(False, description="是否已立即落盘")
    buffer_size: int = Field(0, description="采集器当前缓冲区条数")


def _get_feedback_collector() -> Any:
    """从插件注册表取数据飞轮插件的反馈采集器（未加载返回 None）。"""
    from app.plugins.plugin_manager import PluginRegistry

    plugin = PluginRegistry.get_instance().get_plugin_instance("data_flywheel")
    if plugin is None or not hasattr(plugin, "get_feedback_collector"):
        return None
    return plugin.get_feedback_collector()


# 写反馈即写 IDatasetStore：沿用合成数据端点的 dataset:write 权限码（工程角色已持有）
@router.post(
    "/feedback",
    response_model=FeedbackSubmitResponse,
    dependencies=[Depends(require_permission("dataset:write"))],
)
async def submit_feedback(req: FeedbackSubmitRequest):
    """提交用户反馈到飞轮反馈数据集（annotation / adoption / correction）。

    自进化 M0：此前 ``FeedbackCollector`` 无任何业务调用方（孤立骨架），
    本端点补上 REST 提交入口——前端「采纳/修正」动作、MCP 客户端与
    回放评估均可把人工判定回流为训练数据。
    """
    collector = _get_feedback_collector()
    if collector is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "feedback_collector_unavailable",
                "message": "反馈采集器未就绪（数据飞轮插件未加载或数据集存储不可用）",
            },
        )

    user_id = req.user_id or "local"
    common: dict[str, Any] = {
        "user_id": user_id,
        "prediction_id": req.prediction_id,
        "model_version": req.model_version,
        "notes": req.notes,
        "metadata": req.metadata,
    }
    try:
        if req.feedback_type == "annotation":
            feedback_id = await collector.record_annotation(original_output=req.original_output, **common)
        elif req.feedback_type == "adoption":
            if not isinstance(req.accepted, bool):
                raise HTTPException(
                    status_code=400,
                    detail={"error": "invalid_feedback", "message": "adoption 反馈必须提供 accepted（bool）"},
                )
            feedback_id = await collector.record_adoption(
                accepted=req.accepted, original_output=req.original_output, **common
            )
        elif req.feedback_type == "correction":
            if not req.original_output or not req.corrected_output:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "invalid_feedback",
                        "message": "correction 反馈必须同时提供 original_output 与 corrected_output",
                    },
                )
            feedback_id = await collector.record_correction(
                original_output=req.original_output, corrected_output=req.corrected_output, **common
            )
        else:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_feedback_type",
                    "message": f"feedback_type 不合法: {req.feedback_type}（合法值: annotation/adoption/correction）",
                },
            )

        flushed = False
        if req.flush:
            version = await collector.flush()
            flushed = version is not None
        return FeedbackSubmitResponse(
            feedback_id=feedback_id,
            feedback_type=req.feedback_type,
            flushed=flushed,
            buffer_size=int(getattr(collector, "buffer_size", 0)),
        )
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError) as e:
        logger.warning("反馈提交参数非法: %s", e)
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_feedback", "message": f"反馈内容不合法: {e}"},
        ) from e
    except (RuntimeError, AttributeError, OSError) as e:
        logger.error("反馈提交失败: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "feedback_submit_failed", "message": "反馈提交失败，请稍后重试"},
        ) from e
