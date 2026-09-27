"""项目路径约定配置。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.config._utils import _env


@dataclass
class PathsConfig:
    backup_dir: str = field(default_factory=lambda: _env("BACKUP_DIR", "./backups"))
    db_path: str = field(default_factory=lambda: _env("DB_PATH", "./data/app.db"))
    vector_db_path: str = field(default_factory=lambda: _env("VECTOR_DB_PATH", "./data/chroma_db"))
    config_path: str = field(default_factory=lambda: _env("CONFIG_PATH", "./config.json"))
    gstack_dir: str = field(default_factory=lambda: _env("LNN_GSTACK_DIR", ".lingjing/.gstack"))
    skills_dir: str = field(
        default_factory=lambda: _env(
            "LNN_SKILLS_DIR",
            str(
                # 包转换后 __file__ 路径多一级，需多一层 .parent
                # paths.py 与 __init__.py 同级，因此 parent 层数与原 __init__.py 一致
                Path(__file__).resolve().parent.parent.parent.parent / ".lingjing" / "skills"
            ),
        )
    )
