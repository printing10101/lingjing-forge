"""文件存储路径配置。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config._utils import _path


@dataclass
class StorageConfig:
    output_dir: str = field(default_factory=lambda: _path("OUTPUT_DIR", "output"))
    temp_dir: str = field(default_factory=lambda: _path("TEMP_DIR", "temp"))
