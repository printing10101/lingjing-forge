"""应用启动后置钩子（从 ``app/main.py`` 拆分）。

将原 ``main.py`` 中内联定义的启动后置任务集中到本模块，便于：

1. ``main.py`` 仅保留应用装配逻辑，单文件行数从 898 行降至 ~720 行；
2. 启动钩子可独立测试（无需启动完整 FastAPI 应用）；
3. 后续若需新增启动后置任务（如预热缓存、加载模型），仅修改本模块。

设计约束：
- 所有钩子均为 ``async`` 函数，由 ``startup_event`` 在 ``init_db`` 之后调用；
- 失败仅告警不阻断启动（保持与现有容错策略一致）；
- ``run_alembic_upgrade`` 在子线程中同步执行 alembic，避免阻塞事件循环；
- ``verify_critical_dependencies`` 验证 DB / Redis 连通性，避免应用以「僵尸态」启动。
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys

# Alembic 迁移日志截断长度（防止 stdout/stderr 过长污染日志）
# P5-2 优化：从 200/500 增加到 5000，避免长迁移输出被截断，便于诊断问题
ALEMBIC_STDOUT_LOG_LIMIT: int = 5000
ALEMBIC_STDERR_LOG_LIMIT: int = 5000
ALEMBIC_TIMEOUT_SEC: int = 120


async def run_alembic_upgrade(logger: logging.Logger) -> None:
    """P0-3 修复：执行 ``alembic upgrade head``，保证 schema 版本一致。

    设计：
    - 失败仅告警不阻断启动（``init_db`` 已通过 ``create_all`` 保证基础表存在）；
    - 仅在 ``LNN_ALEMBIC_ENABLED != "false"`` 时执行（默认开启）；
    - 在子线程中同步执行，避免阻塞事件循环。
    """
    if os.environ.get("LNN_ALEMBIC_ENABLED", "true").lower() == "false":
        logger.info("[startup] Alembic migration skipped (LNN_ALEMBIC_ENABLED=false)")
        return

    python_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        # WindowsSelectorEventLoop（start_server.py 为
        # 绕 _overlapped 坑强制切换）不支持 asyncio subprocess create_subprocess_exec
        # 抛 NotImplementedError 且不在 except 范围 破坏「不阻断启动」语义。
        # 用 asyncio.to_thread 在独立线程中跑同步 subprocess.run（2026-08-19 改进：
        # 原实现直接阻塞事件循环，与 docstring 声称的"子线程执行"不符；to_thread
        # 兼容所有事件循环且不阻塞其他启动钩子）。
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=python_dir,
            capture_output=True,
            timeout=ALEMBIC_TIMEOUT_SEC,
        )
        if result.returncode == 0:
            logger.info(
                "[startup] Alembic upgrade head done: %s",
                result.stdout.decode("utf-8", "replace").strip()[:ALEMBIC_STDOUT_LOG_LIMIT],
            )
        else:
            logger.warning(
                "[startup] Alembic upgrade returned non-zero (rc=%s): %s",
                result.returncode,
                result.stderr.decode("utf-8", "replace").strip()[:ALEMBIC_STDERR_LOG_LIMIT],
            )
    except FileNotFoundError:
        logger.warning("[startup] alembic not installed, skip migration")
    except Exception as e:  # noqa: BLE001 - 迁移失败绝不阻断启动
        logger.warning("[startup] Alembic migration failed (non-fatal): %s", e, exc_info=True)


async def verify_critical_dependencies(logger: logging.Logger) -> None:
    """P1-15 修复：启动后验证关键依赖（DB / Redis）连通性。

    设计：
    - DB 不可达：warning（``init_db`` 已通过 ``create_all`` 建表，但连接可能因
      配置错误或网络分区失败；运行时查询会 500，需提前告警）；
    - Redis 不可达：debug（Redis 为可选依赖，未配置时返回 None 属正常）；
    - 任一失败仅记录日志，不阻断启动（保持与现有容错策略一致）。
    """
    # DB 连通性
    try:
        from app.database.connection import check_db_health

        db_status = await check_db_health()
        if db_status.get("status") == "unhealthy":
            logger.warning(
                "[startup] DB 连通性自检失败: %s（运行时查询可能 500）",
                db_status.get("error", "unknown"),
            )
        else:
            logger.info(
                "[startup] DB 连通性自检通过: status=%s",
                db_status.get("status"),
            )
    except (
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        ImportError,
        asyncio.TimeoutError,
    ) as e:
        # Q1 修复：收窄为可预期的连接/解析/导入异常。
        # OSError 覆盖 socket/PostgreSQL 连接错误；
        # ImportError 覆盖驱动缺失场景；
        # asyncio.TimeoutError 覆盖连接超时。
        logger.warning("[startup] DB 连通性自检异常: %s", e, exc_info=True)

    # Redis 连通性（可选依赖）
    try:
        from app.services.redis_client import check_redis_health

        redis_status = await check_redis_health()
        if redis_status.get("status") == "unhealthy":
            logger.warning(
                "[startup] Redis 连通性自检失败: %s（任务进度/取消标志缓存不可用）",
                redis_status.get("error", "unknown"),
            )
        else:
            logger.info(
                "[startup] Redis 连通性自检通过: status=%s",
                redis_status.get("status"),
            )
    except (
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        ImportError,
        asyncio.TimeoutError,
    ) as e:
        # Q1 修复：Redis 为可选依赖，连接失败/模块缺失都不应阻断启动
        logger.debug("[startup] Redis 连通性自检跳过: %s", e, exc_info=True)
