"""通知 / 系统状态 / 活动简报聚合 API。

提供三个聚合端点，供前端顶栏通知与首页状态使用：
- ``GET /api/v1/notifications``   顶栏通知（任务状态 + 设备告警 + 库存预警 + 质量异常）
- ``GET /api/v1/system/status``   系统状态（版本 + 运行时长 + 核心组件健康）
- ``GET /api/v1/activity/brief``  首页活动简报（生产 / 任务 / 告警 / 质量统计）

所有数据均来自真实后端状态（数据库查询 + 任务管理器），无写死数据。
单个数据源失败时降级为空，不影响其余部分（接口始终成功返回）。
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter

from app.core.response import success
from app.tasks.task_system import AsyncTaskManager
from app.services import (
    equipment_service,
    materials_service,
    production_service,
    quality_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Dashboard Aggregation"])

task_manager = AsyncTaskManager()

# 进程启动时间（用于计算 uptime）
_START_TIME = datetime.now()

# 通知优先级映射：状态值 -> 前端 badge 类型
_PRIORITY_MAP: dict[str, str] = {
    "failed": "danger",
    "故障": "danger",
    "缺货": "danger",
    "待处理": "danger",
    "running": "primary",
    "queued": "warning",
    "pending": "warning",
    "低库存": "warning",
    "处理中": "warning",
    "已确认": "warning",
}


def _priority_for(status: str) -> str:
    return _PRIORITY_MAP.get(status, "info")


@router.get("/notifications")
async def get_notifications():
    """聚合顶栏通知：进行中/失败任务 + 未处理告警 + 低库存物料 + 待处理质量异常。"""
    notifications: list[dict] = []

    # 1. 任务状态（进行中 / 失败 / 排队）
    try:
        tasks = await task_manager.list_tasks(limit=50)
        for t in tasks[:10]:
            d = t.to_dict()
            status = d.get("status", "")
            if status in ("failed", "running", "pending", "queued"):
                notifications.append(
                    {
                        "notification_id": f"task-{d.get('job_id', '')}",
                        "title": f"任务 {d.get('job_id', '')} 状态：{status}",
                        "created_at": d.get("created_at_iso")
                        or datetime.fromtimestamp(float(d.get("created_at", 0) or 0)).isoformat(),
                        "priority": _priority_for(status),
                    }
                )
    except Exception as e:
        logger.warning("聚合任务通知失败: %s", e, exc_info=True)

    # 2. 设备告警（未解决）
    try:
        alarms = await equipment_service.list_alarms(status=None, page=1, page_size=20)
        for a in (alarms.get("items") or [])[:10]:
            severity = str(a.get("severity", ""))
            alarms_status = str(a.get("status", ""))
            if alarms_status in ("待处理", "已确认", "处理中"):
                notifications.append(
                    {
                        "notification_id": f"alarm-{a.get('id', '')}",
                        "title": f"设备告警：{a.get('message', '未知告警')}",
                        "created_at": str(a.get("created_at", "")),
                        "priority": _priority_for(severity),
                    }
                )
    except Exception as e:
        logger.warning("聚合设备告警通知失败: %s", e, exc_info=True)

    # 3. 物料库存预警（缺货 / 低库存）
    try:
        for status_key in ("缺货", "低库存"):
            mats = await materials_service.list_materials(status=status_key, page=1, page_size=10)
            for m in (mats.get("items") or [])[:5]:
                notifications.append(
                    {
                        "notification_id": f"material-{m.get('id', '')}",
                        "title": f"物料 {m.get('name', '')} 库存状态：{status_key}（当前 {m.get('quantity', 0)}）",
                        "created_at": str(m.get("updated_at", "")),
                        "priority": _priority_for(status_key),
                    }
                )
    except Exception as e:
        logger.warning("聚合物料预警通知失败: %s", e, exc_info=True)

    # 4. 质量异常（待处理）
    try:
        anomalies = await quality_service.list_anomalies(status=None, limit=20, offset=0)
        for a in (anomalies.get("anomalies") or [])[:10]:
            a_status = str(a.get("status", ""))
            if a_status in ("待处理", "处理中"):
                notifications.append(
                    {
                        "notification_id": f"quality-{a.get('id', '')}",
                        "title": f"质量异常：{a.get('anomaly_type', '未知')} - {a.get('description', '')}",
                        "created_at": str(a.get("created_at", "")),
                        "priority": _priority_for(a_status),
                    }
                )
    except Exception as e:
        logger.warning("聚合质量异常通知失败: %s", e, exc_info=True)

    # 按时间倒序（未解析成功的时间排最后）
    def _sort_key(n: dict) -> str:
        try:
            return datetime.fromisoformat(str(n["created_at"])).isoformat()
        except (ValueError, TypeError):
            return "0000"

    notifications.sort(key=_sort_key, reverse=True)
    return success(data=notifications[:20])


@router.get("/system/status")
async def get_system_status():
    """系统状态：版本、运行时长、核心组件健康（真实查询）。"""
    from app.version import get_version_info

    info = get_version_info()
    uptime_seconds = max(0, int((datetime.now() - _START_TIME).total_seconds()))

    components: dict[str, str] = {}
    # 数据库
    try:
        from app.database.connection import get_sessionmaker

        sessionmaker = get_sessionmaker()
        components["database"] = "ok" if sessionmaker is not None else "unavailable"
    except Exception:
        logger.warning("[system/status] database health check failed", exc_info=True)
        components["database"] = "error"

    # 任务管理器
    try:
        await task_manager.list_tasks(limit=1)
        components["tasks"] = "ok"
    except Exception:
        logger.warning("[system/status] task manager health check failed", exc_info=True)
        components["tasks"] = "error"

    # 插件系统
    try:
        from app.dependencies import get_plugin_manager

        get_plugin_manager()
        components["plugins"] = "ok"
    except Exception:
        logger.warning("[system/status] plugin manager health check failed", exc_info=True)
        components["plugins"] = "error"

    return success(
        data={
            "version": info.get("version", "unknown"),
            "uptime": uptime_seconds,
            "components": components,
            "server_time": datetime.now().isoformat(),
        }
    )


@router.get("/activity/brief")
async def get_activity_brief():
    """首页活动简报：今日生产 / 任务 / 告警 / 质量统计（真实聚合）。"""
    brief: dict[str, int] = {}

    # 生产仪表盘（今日产量）
    try:
        dash = await production_service.get_dashboard()
        if dash:
            brief["today_output"] = int(dash.get("total_output") or 0)
            brief["active_orders"] = int(dash.get("active_orders") or 0)
    except Exception as e:
        logger.warning("聚合生产简报失败: %s", e, exc_info=True)

    # 任务统计
    try:
        tasks = await task_manager.list_tasks(limit=100)
        brief["task_total"] = len(tasks)
        brief["task_running"] = sum(
            1 for t in tasks if getattr(t, "status", None) is not None and getattr(t, "status").value == "running"
        )
        brief["task_failed"] = sum(
            1 for t in tasks if getattr(t, "status", None) is not None and getattr(t, "status").value == "failed"
        )
    except Exception as e:
        logger.warning("聚合任务简报失败: %s", e, exc_info=True)

    # 设备告警统计
    try:
        eq_stats = await equipment_service.get_equipment_stats()
        if eq_stats:
            brief["equipment_total"] = int(eq_stats.get("total") or 0)
            brief["equipment_fault"] = int(eq_stats.get("fault") or 0)
    except Exception as e:
        logger.warning("聚合设备简报失败: %s", e, exc_info=True)

    # 质量统计
    try:
        q_stats = await quality_service.get_quality_stats()
        if q_stats:
            brief["quality_today"] = int(q_stats.get("today_count") or 0)
            brief["quality_anomaly"] = int(q_stats.get("anomaly_count") or 0)
    except Exception as e:
        logger.warning("聚合质量简报失败: %s", e, exc_info=True)

    return success(data=brief)
