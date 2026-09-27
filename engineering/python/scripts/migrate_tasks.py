"""
Database migration script for PostgreSQL task persistence.

Run: python -m scripts.migrate_tasks
Creates the training_tasks table and all required indexes.
"""

import asyncio
import os
import sys
import logging
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from app.database.connection import DatabaseConfig  # noqa: E402
from sqlalchemy import text  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _mask_db_url(url: str) -> str:
    """脱敏数据库 URL 中的密码字段。

    S4 修复：原 ``logger.info("Connecting to: %s", url)`` 会将完整 URL
    （含明文密码）写入日志，被日志收集系统持久化后存在密码泄露风险。
    本函数将 ``postgresql://user:password@host/db`` 转为
    ``postgresql://user:***@host/db`` 形式输出。
    """
    if not url:
        return url
    try:
        parsed = urlparse(url)
        if parsed.password:
            # 替换 password 部分，保留其他字段
            masked_netloc = f"{parsed.username}:***@{parsed.hostname}"
            if parsed.port:
                masked_netloc += f":{parsed.port}"
            return parsed._replace(netloc=masked_netloc).geturl()
    except Exception:
        # URL 格式异常时，做正则兜底脱敏
        import re
        return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)
    return url


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS training_tasks (
    id              VARCHAR(64) PRIMARY KEY,
    task_type       VARCHAR(64) NOT NULL,
    status          VARCHAR(32) NOT NULL DEFAULT 'pending',
    progress        INTEGER NOT NULL DEFAULT 0,
    params          JSONB,
    result          JSONB,
    error           VARCHAR(2048),
    owner_id        VARCHAR(128),
    idempotency_key VARCHAR(256) UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_training_tasks_status
    ON training_tasks (status);

CREATE INDEX IF NOT EXISTS idx_training_tasks_task_type
    ON training_tasks (task_type);

CREATE INDEX IF NOT EXISTS idx_training_tasks_status_type
    ON training_tasks (status, task_type);

CREATE INDEX IF NOT EXISTS idx_training_tasks_created_at
    ON training_tasks (created_at);

CREATE INDEX IF NOT EXISTS idx_training_tasks_owner
    ON training_tasks (owner_id, created_at);

CREATE INDEX IF NOT EXISTS idx_training_tasks_idempotency
    ON training_tasks (idempotency_key);

COMMENT ON TABLE training_tasks IS '训练任务持久化表 - 存储任务全生命周期数据';
COMMENT ON COLUMN training_tasks.id IS '任务ID（UUID格式主键）';
COMMENT ON COLUMN training_tasks.task_type IS '任务类型分类标识';
COMMENT ON COLUMN training_tasks.status IS '任务状态：pending/running/completed/failed/cancelled';
COMMENT ON COLUMN training_tasks.progress IS '任务进度百分比(0-100)';
COMMENT ON COLUMN training_tasks.params IS '任务参数（JSON格式）';
COMMENT ON COLUMN training_tasks.result IS '任务结果数据（JSON格式）';
COMMENT ON COLUMN training_tasks.error IS '错误信息';
COMMENT ON COLUMN training_tasks.created_at IS '创建时间';
COMMENT ON COLUMN training_tasks.updated_at IS '最近更新时间';
COMMENT ON COLUMN training_tasks.started_at IS '任务开始执行时间';
COMMENT ON COLUMN training_tasks.completed_at IS '任务完成/失败/取消时间';
"""


async def run_migration():
    url = os.environ.get("DB_URL", "")
    if not url:
        logger.error("DB_URL environment variable not set")
        logger.info("Set DB_URL and try again, e.g.:")
        # S4 修复：示例 URL 不再使用易被复制粘贴的弱口令 "lnn_password"，
        # 改为占位符 <your_password>，避免被运维直接复用导致弱口令扩散。
        logger.info("  DB_URL=postgresql://lnn:<your_password>@localhost:5432/lnn_db python -m scripts.migrate_tasks")
        return 1

    _config = DatabaseConfig()  # noqa: F841
    # S4 修复：日志中输出脱敏后的 URL（密码替换为 ***），避免明文密码进入日志
    logger.info("Connecting to: %s", _mask_db_url(url))

    from app.database.connection import get_engine
    engine = get_engine()

    if engine is None:
        logger.error("Failed to create database engine")
        return 1

    try:
        async with engine.begin() as conn:
            statements = [s.strip() for s in CREATE_TABLE_SQL.split(";") if s.strip()]
            for stmt in statements:
                logger.info("Executing: %s", stmt[:80])
                await conn.execute(text(stmt + ";"))
            logger.info("Migration completed successfully")

        async with engine.begin() as conn:
            result = await conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_name = 'training_tasks'")
            )
            row = result.fetchone()
            if row:
                logger.info("Verified: table 'training_tasks' exists")

        await engine.dispose()
        return 0

    except Exception as e:
        logger.error("Migration failed: %s", e)
        return 1


if __name__ == "__main__":
    code = asyncio.run(run_migration())
    sys.exit(code)
