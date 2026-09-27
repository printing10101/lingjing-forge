"""工艺规则库模块常量（从 rule_db 拆出）。"""

from __future__ import annotations

from pathlib import Path


# 数据格式版本（用于区分导出数据结构的版本）
CURRENT_FORMAT_VERSION = "1.0"

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
VERSION_FILE = PROJECT_ROOT / "VERSION"

DB_DIR = Path(__file__).parent.parent.parent / "data"
DB_PATH = DB_DIR / "process_rules.db"
