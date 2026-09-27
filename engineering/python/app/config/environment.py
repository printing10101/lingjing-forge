"""运行环境配置。

集中管理跨模块共享的环境变量读取逻辑，避免同一环境变量名在多处
直接调用 ``os.environ.get`` 导致的：

1. 默认值不一致（有的地方默认 ``"production"``，有的默认 ``"development"``）；
2. 校验逻辑不一致（有的地方接受任意值，有的地方仅接受 ``development``/``production``）；
3. 解析逻辑不一致（CORS 来源列表的逗号分隔 + ``strip`` 处理在 6+ 处重复实现）。

.. note::
    本模块仅收录 **语义完全一致** 的环境变量读取（依据 ``limits.py``
    设计原则 #4）。``auth/security.py::_is_production_env()`` 因语义不同
    （安全决策需要多环境变量回退 + ``TESTING`` 覆盖）不在本模块收录范围；
    ``simulation/test_server.py`` 的开发模式断言也因默认值不同
    （``"development"`` 而非 ``"production"``）不在本模块收录范围。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Final

from app.config._utils import _env


# LINGJING_ENV —— CORS / 安全配置专用环境标识

#: : ``LINGJING_ENV`` 的两个合法取值。
_DEVELOPMENT: Final[str] = "development"
_PRODUCTION: Final[str] = "production"
_VALID_LINGJING_ENVS: Final[frozenset[str]] = frozenset({_DEVELOPMENT, _PRODUCTION})


def get_lingjing_env() -> str:
    """返回当前 ``LINGJING_ENV`` 标识。

    读取 ``LINGJING_ENV`` 环境变量并归一化为小写。仅接受
    ``"development"`` 与 ``"production"`` 两个合法值；未设置或取值
    非法时回退到 ``"production"``（安全优先）。

    本函数统一了以下历史重复实现（语义完全一致）：

    - ``app/middleware/cors_config.py::_resolve_environment()``
    - ``app/config/safety.py::SecurityConfig.lingjing_env`` 字段默认值

    Returns:
        ``"development"`` 或 ``"production"``。
    """
    env = os.environ.get("LINGJING_ENV", _PRODUCTION).lower()
    return env if env in _VALID_LINGJING_ENVS else _PRODUCTION


# ALLOWED_ORIGINS —— CORS 允许来源列表

#: : ``ALLOWED_ORIGINS`` 环境变量的合法取值——以逗号分隔的 Origin 列表。
#: : 空字符串或未设置时返回空列表，由调用方决定回退策略。


def parse_allowed_origins() -> list[str]:
    """解析 ``ALLOWED_ORIGINS`` 环境变量为 Origin 列表。

    读取 ``ALLOWED_ORIGINS`` 环境变量（逗号分隔），对每一项执行
    ``strip`` 并过滤空串。未设置或全为空白时返回空列表。

    本函数统一了以下历史重复实现（语义完全一致，均为
    "ALLOWED_ORIGINS 优先，逗号分隔，strip 空白"）：

    - ``app/middleware/cors_config.py`` 中 5 处
      ``os.environ.get("ALLOWED_ORIGINS", "")`` + split + strip 内联代码
    - ``app/config/safety.py::_resolve_cors_origins()`` 中
      ``ALLOWED_ORIGINS`` 分支

    .. note::
        ``CORS_ORIGINS`` 作为向后兼容回退字段由 ``safety.py`` 自行处理，
        本函数 **不** 读取 ``CORS_ORIGINS``，以保持 ``cors_config.py``
        原有行为不变（仅识别 ``ALLOWED_ORIGINS``）。

    Returns:
        Origin 字符串列表；未设置时返回 ``[]``。
    """
    raw = os.environ.get("ALLOWED_ORIGINS", "")
    if not raw:
        return []
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


# 通用 EnvironmentConfig 数据类（保留向后兼容）


@dataclass
class EnvironmentConfig:
    """通用运行环境配置（读取 ``ENVIRONMENT`` 环境变量）。

    .. note::
        本数据类读取的是 ``ENVIRONMENT`` 环境变量，**不是** ``LINGJING_ENV``。
        二者语义不同：

        - ``ENVIRONMENT``：通用环境标识，可取任意值（如 ``"testing"``），
          供 ``app_config.py`` 聚合配置使用；
        - ``LINGJING_ENV``：CORS / 安全配置专用标识，仅取
          ``"development"`` / ``"production"``，参见 :func:`get_lingjing_env`。

        如需获取 CORS 用的环境标识，请直接调用 :func:`get_lingjing_env`。
    """

    environment: str = field(default_factory=lambda: _env("ENVIRONMENT", "development").lower())

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        return self.environment in ("development", "dev")
