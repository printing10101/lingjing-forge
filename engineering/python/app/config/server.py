"""HTTP 服务器绑定配置。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config._utils import _bool_env, _env


@dataclass
class ServerConfig:
    host: str = field(default_factory=lambda: _env("SERVER_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("SERVER_PORT", "8765")))
    debug: bool = field(default_factory=lambda: _bool_env("DEBUG", False))
