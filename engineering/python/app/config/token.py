"""MES 集成与 LNN 认证令牌配置。"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.config._utils import _ROOT_DIR, _bool_env, _env, _float_env, logger


@dataclass
class MESConfig:
    """MES/ERP 系统集成配置。"""

    base_url: str = field(default_factory=lambda: _env("MES_BASE_URL", ""))
    api_key: str = field(default_factory=lambda: _env("MES_API_KEY", ""))
    timeout: float = field(default_factory=lambda: _float_env("MES_TIMEOUT", 30.0))
    enabled: bool = field(default_factory=lambda: _bool_env("MES_ENABLED", False))


@dataclass
class TokenConfig:
    _TOKEN_FILE_NAME = ".lnn_token"
    _TOKEN_META_FILE_NAME = ".lnn_token_meta.json"
    _token_cache: str | None = field(default=None, repr=False, init=False)

    def _resolve_token(self) -> str:
        token = _env("LNN_TOKEN", "")
        if token:
            logger.info("Using token from LNN_TOKEN environment variable")
            return token

        token_file = Path(_env("LNN_TOKEN_FILE", self._TOKEN_FILE_NAME))
        if not token_file.is_absolute():
            token_file = Path(_ROOT_DIR) / token_file

        if token_file.exists():
            try:
                token = token_file.read_text().strip()
                if token:
                    logger.info("Loaded token from %s", token_file)
                    return token
            except (OSError, IOError, PermissionError):
                logger.warning("Failed to read token file", exc_info=True)

        new_token = str(uuid.uuid4())
        try:
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(new_token)
            logger.info("Generated and saved new token to %s", token_file)
        except (OSError, IOError, PermissionError):
            logger.warning(
                "Could not persist token. Token is ephemeral for this session.",
                exc_info=True,
            )

        self._print_setup_guidance(token_file, new_token)
        return new_token

    def _print_setup_guidance(self, token_file: Path, token: str) -> None:
        guidance = f"""
╔══════════════════════════════════════════════════════════════╗
║  LNN认证令牌配置                                            ║
╠══════════════════════════════════════════════════════════════╣
║  系统已生成新的认证令牌。请选择以下方式之一管理令牌：      ║
║                                                              ║
║  方式一（推荐）：设置环境变量                                ║
║    export LNN_TOKEN="你的令牌值"                             ║
║                                                              ║
║  方式二：将令牌写入文件                                      ║
║    文件路径: {str(token_file):<44}║
║                                                              ║
║  当前会话令牌: {token}  ║
║                                                              ║
║  安全须知：                                                  ║
║  - 切勿将令牌提交到版本控制系统                              ║
║  - 定期轮换令牌以保障安全性                                  ║
║  - 生产环境请使用环境变量方式                                ║
╚══════════════════════════════════════════════════════════════╝
"""
        sys.stderr.write(guidance)

    @property
    def token(self) -> str:
        if self._token_cache is None:
            self._token_cache = self._resolve_token()
        return self._token_cache

    def rotate(self) -> str:
        new_token = str(uuid.uuid4())
        self._token_cache = new_token
        token_file = Path(_env("LNN_TOKEN_FILE", self._TOKEN_FILE_NAME))
        if not token_file.is_absolute():
            token_file = Path(_ROOT_DIR) / token_file
        try:
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(new_token)
            logger.info("Token rotated and saved to %s", token_file)
        except (OSError, IOError, PermissionError):
            logger.warning("Could not persist rotated token", exc_info=True)
        return new_token
