"""路由注册中心：集中管理所有 API 路由的导入与 ``include_router`` 调用.

阶段 2 重构后职责：
- 定义 ``_OLLAMA_AVAILABLE`` 等条件导入标志位（main.py 重新导出，测试依赖）
- 调用 ``app.api.routers.register_all_domain_routers`` 完成全部路由注册
- 接收 ADR 阶段 1-7 条件模块的导入可用状态，回填到本模块全局变量

设计目标：
- ``main.py`` 仅负责应用实例创建、生命周期事件、中间件装配
- ``router_registry.py`` 仅负责条件标志位定义与领域注册调度
- ``app/api/routers/`` 各领域模块负责具体路由的导入与注册（按业务域聚合）
- 物理文件位置不变（``app/api/v1/*.py`` 保持扁平，向后兼容）

条件标志位说明：
- ``_OLLAMA_AVAILABLE``：Ollama 模块是否可用（依赖 ollama 包且
  ``config.hardware.skip_ollama=False``）。在模块加载时即确定，传递给
  ``register_all_domain_routers`` 控制 Ollama 路由是否注册
- ``_IMAGE_TO_3D_AVAILABLE`` / ``_FEATURE_EXTRACTION_AVAILABLE`` /
  ``_PARAMETRIC_GEOMETRY_AVAILABLE`` / ``_CUTTING_PARAMETERS_AVAILABLE`` /
  ``_CHATTER_PREDICTION_AVAILABLE`` / ``_GCODE_GENERATION_AVAILABLE`` /
  ``_CAM_VALIDATION_AVAILABLE``：ADR 阶段 1-7 各阶段条件模块的导入可用状态，
  初始为 False，在 ``register_routers`` 调用后由
  ``register_all_domain_routers`` 返回的 flags dict 回填
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.config import config
from app.api.routers import register_all_domain_routers

logger = logging.getLogger(__name__)


# 条件导入标志位

# Ollama：桌面版可能未安装 ollama 包，且 config.hardware.skip_ollama 可显式跳过
# [U-P0-2] 防复发：尊重 config.hardware.skip_ollama 标志
# - 轻量模式 / minimal 档位下显式跳过 Ollama 加载，降低内存占用
# - 避免 Ollama 启动探测失败导致应用卡死（老旧硬件常见问题）
_OLLAMA_AVAILABLE = False
if config.hardware.skip_ollama:
    logging.info(
        "Ollama 模块已跳过加载（LNN_SKIP_OLLAMA=true 或硬件档位=minimal）。"
        "轻量模式下将仅使用规则引擎 + 云端 API。"
        "如需启用本地 LLM，请设置 LNN_SKIP_OLLAMA=false 并提高硬件档位。"
    )
else:
    try:
        from app.ai import ollama_routes  # noqa: F401  # 探测导入：路由注册在 app/api/routers/ai.py

        _OLLAMA_AVAILABLE = True
    except ImportError as e:
        logging.warning(
            f"ollama 模块导入失败: {e}。"
            "影响: Ollama AI 模型集成功能将不可用。"
            "修复: 请安装 ollama Python 包，运行 'pip install ollama'"
        )

# ADR 阶段 1-7 条件模块标志位
# 初始为 False，在 ``register_routers`` 调用后由
# ``register_all_domain_routers`` 返回的 flags dict 回填实际值。
# 此处仅占位定义，确保 ``from app.router_registry import _FLAG`` 在
# 任何时刻都不抛 AttributeError（main.py 重新导出，测试文件依赖）。
_IMAGE_TO_3D_AVAILABLE: bool = False
_FEATURE_EXTRACTION_AVAILABLE: bool = False
_PARAMETRIC_GEOMETRY_AVAILABLE: bool = False
_CUTTING_PARAMETERS_AVAILABLE: bool = False
_CHATTER_PREDICTION_AVAILABLE: bool = False
_GCODE_GENERATION_AVAILABLE: bool = False
_CAM_VALIDATION_AVAILABLE: bool = False


def register_routers(app: FastAPI) -> None:
    """注册所有 API 路由到 FastAPI 应用实例.

    阶段 2 重构后，本函数仅做三件事：
    1. 调用 ``register_all_domain_routers`` 按领域顺序注册全部路由
    2. 传递 ``_OLLAMA_AVAILABLE`` 给 AI 域控制 Ollama 路由注册
    3. 接收返回的 flags dict 并回填到本模块全局变量

    注册顺序约定（由 ``register_all_domain_routers`` 实现）：
    - system 域最先注册（健康检查端点必须先于业务路由）
    - identity 域次之（认证依赖系统端点）
    - 业务域按依赖顺序注册（ai → tasks → governance → manufacturing →
      engineering → dnc_mes → templates → workflows → plugins）
    - adr_pipeline 域最后注册（依赖可选库，失败仅告警不阻断启动）
    """
    flags = register_all_domain_routers(app, ollama_available=_OLLAMA_AVAILABLE)

    # 回填 ADR 阶段 1-7 条件模块标志位到本模块全局变量
    # 供 main.py 重新导出与测试文件 ``from app.router_registry import _FLAG`` 使用
    globals().update(flags)

    # 日志摘要：哪些阶段模块成功加载，哪些被跳过
    loaded = [k for k, v in flags.items() if v]
    skipped = [k for k, v in flags.items() if not v]
    if loaded:
        logger.info("ADR 条件模块已加载: %s", ", ".join(loaded))
    if skipped:
        logger.info("ADR 条件模块已跳过（依赖缺失或被禁用）: %s", ", ".join(skipped))


__all__ = [
    # 注册函数
    "register_routers",
    # 领域注册聚合（供 main.py 或测试直接调用）
    "register_all_domain_routers",
    # 条件导入标志位（供 main.py 重新导出，保持向后兼容）
    "_OLLAMA_AVAILABLE",
    "_IMAGE_TO_3D_AVAILABLE",
    "_FEATURE_EXTRACTION_AVAILABLE",
    "_PARAMETRIC_GEOMETRY_AVAILABLE",
    "_CUTTING_PARAMETERS_AVAILABLE",
    "_CHATTER_PREDICTION_AVAILABLE",
    "_GCODE_GENERATION_AVAILABLE",
    "_CAM_VALIDATION_AVAILABLE",
]
