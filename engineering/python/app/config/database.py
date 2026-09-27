"""数据库路径和连接配置。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config._utils import PYTHON_DIR, _env, _path


@dataclass
class DatabaseConfig:
    cad_db_path: str = field(default_factory=lambda: _path("CAD_DB_PATH", "cad_tasks.db"))
    model_library_path: str = field(default_factory=lambda: _path("MODEL_LIBRARY_PATH", "model_library.db"))
    db_url: str = field(
        default_factory=lambda: _env(
            "DATABASE_URL",
            # 默认路径与原 main.py 中 Path(__file__).parent.parent / "data" / "app.db" 一致
            # 使用 PYTHON_DIR 以保持向后兼容（现有 DB 位于 python/data/app.db）
            f"sqlite+aiosqlite:///{PYTHON_DIR}/data/app.db",
        )
    )
