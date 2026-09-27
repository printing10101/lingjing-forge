"""配置包共享的底层工具与常量。

从原 ``app/config/__init__.py`` 拆分而来，保持向后兼容。

本模块提供：
- ``PROJECT_ROOT`` / ``PYTHON_DIR`` / ``_ROOT_DIR`` 路径常量
- ``logger`` 日志器（名称固定为 ``app.config``，与原包级行为一致）
- ``_env`` / ``_path`` / ``_bool_env`` / ``_int_env`` / ``_float_env`` 环境变量读取辅助函数
"""

from __future__ import annotations

import os
import logging

# 注意：本模块原为 app/config.py 单文件，现已重构为 app/config/ 包。
# __file__ 路径由 app/config.py 变为 app/config/_utils.py，需多向上一级目录。
# _utils.py 与 __init__.py 同级，因此 parent 层数与原 __init__.py 一致。
PROJECT_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PYTHON_DIR: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT_DIR = PROJECT_ROOT

# 固定使用 "app.config" 作为 logger 名称，与原 __init__.py 中
# logging.getLogger(__name__) 的行为保持一致（__name__ 原为 app.config）。
logger = logging.getLogger("app.config")


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _path(key: str, default_rel: str) -> str:
    return _env(key, os.path.join(_ROOT_DIR, default_rel))


def _bool_env(key: str, default: bool = False) -> bool:
    return _env(key, "true" if default else "false").lower() == "true"


def _int_env(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError as e:
        logger.debug("环境变量 %s 转换整数失败，使用默认值: %s", key, e, exc_info=True)
        return default


def _float_env(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError as e:
        logger.debug("环境变量 %s 转换浮点数失败，使用默认值: %s", key, e, exc_info=True)
        return default
