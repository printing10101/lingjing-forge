from logging.config import fileConfig
import os
import sys
from pathlib import Path

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Override sqlalchemy.url with environment variable if set
# P1 修复：同时支持 DB_URL 和 DATABASE_URL。原代码仅读 DB_URL，
# 但 config.py 和运维惯例用 DATABASE_URL，可能导致迁移连错库。
# 防复发：环境变量回退链必须覆盖所有运维命名约定。
env_db_url = os.environ.get("DB_URL") or os.environ.get("DATABASE_URL")
if env_db_url:
    config.set_main_option("sqlalchemy.url", env_db_url)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import all models for autogenerate support
from app.database.models import (
    Base as TaskBase,
    CuttingExperienceBase,  # noqa: E402 P2-1 数据飞轮
)
from app.database.rule_models import Base as RuleBase  # noqa: E402
from app.database.models.machining_record import (  # noqa: E402
    Base as MachiningRecordBase,
)
from app.knowledge_graph.models import Base as KnowledgeGraphBase  # noqa: E402

# Merge metadata from all model bases
from sqlalchemy import MetaData  # noqa: E402
target_metadata = MetaData()
for base in (TaskBase, RuleBase, MachiningRecordBase, KnowledgeGraphBase, CuttingExperienceBase):
    for table in base.metadata.tables.values():
        table.tometadata(target_metadata)

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
