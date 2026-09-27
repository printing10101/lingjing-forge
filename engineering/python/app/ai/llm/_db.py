"""Provider 注册表数据库路径（从 provider_registry 拆出）。"""

from __future__ import annotations

import os
from pathlib import Path


def _get_db_path() -> Path:
    """获取 Provider 注册表数据库路径。

    约定：python/data/llm_providers.db
    支持环境变量 LLM_PROVIDERS_DB 覆盖。
    """
    env_path = os.environ.get("LLM_PROVIDERS_DB")
    if env_path:
        return Path(env_path)

    # python/ 目录（与 app.db 同级）
    python_dir = Path(__file__).resolve().parent.parent.parent.parent
    data_dir = python_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "llm_providers.db"
