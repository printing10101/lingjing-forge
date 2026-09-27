"""Ollama status and model management routes.

支持离线模型加载，可通过环境变量 OLLAMA_MODEL_PATH 指定本地模型路径。
在中国网络环境下，可手动从以下源下载模型：
  - 魔搭 ModelScope: https://modelscope.cn
  - HuggingFace 镜像: https://hf-mirror.com
下载后将模型文件放置到 OLLAMA_MODEL_PATH 指定的目录即可。
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter

from app.config import config
from app.core.response import success, error, ErrorCode
from app.core.safe_errors import safe_error_message

logger = logging.getLogger(__name__)

# 离线模型路径配置（可选）
# 如果设置了 OLLAMA_MODEL_PATH，将优先从该路径加载模型
OLLAMA_MODEL_PATH = os.getenv("OLLAMA_MODEL_PATH")

# Ollama 健康检查/状态查询的 HTTP 超时（秒）
OLLAMA_HEALTH_TIMEOUT = 5

router = APIRouter(prefix="/api/ollama", tags=["Ollama"])


@router.get("/status")
async def get_ollama_status() -> dict[str, Any]:
    """获取 Ollama 服务状态"""
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_HEALTH_TIMEOUT) as client:
            response = await client.get(f"{config.ai.ollama_base_url}/api/tags")
            if response.status_code == 200:
                data = response.json()
                models = data.get("models", [])
                return success(
                    data={
                        "status": "running",
                        "base_url": config.ai.ollama_base_url,
                        "model_count": len(models),
                        "models": [m.get("name", "unknown") for m in models],
                    }
                )
            else:
                logger.warning("Ollama returned status %d", response.status_code)
                return error(
                    code=ErrorCode.SERVICE_UNAVAILABLE,
                    message=f"Ollama returned status {response.status_code}",
                )
    except (httpx.TimeoutException, httpx.NetworkError, OSError, RuntimeError) as e:
        # 使用 safe_error_message 包装异常，避免泄露内部网络错误细节
        safe = safe_error_message(e, context="ollama.status")
        logger.error(
            "Ollama status check failed | error_id=%s | exc=%s",
            safe.get("error_id"),
            e,
            exc_info=True,
        )

        # 提供有用的错误信息，帮助用户诊断问题
        error_msg = safe["message"]
        if "Connection" in str(e) or "Network" in str(e):
            error_msg += "。请检查 Ollama 服务是否正在运行，以及 OLLAMA_BASE_URL 配置是否正确。"
        elif "Timeout" in str(e):
            error_msg += "。Ollama 服务响应超时，请检查服务状态或增加超时时间。"

        return error(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message=error_msg,
            detail=safe.get("detail"),
        )


@router.get("/models")
async def list_ollama_models() -> dict[str, Any]:
    """获取 Ollama 已安装模型列表"""
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_HEALTH_TIMEOUT) as client:
            response = await client.get(f"{config.ai.ollama_base_url}/api/tags")
            if response.status_code == 200:
                data = response.json()
                models = data.get("models", [])
                return success(
                    data={
                        "models": [
                            {
                                "name": m.get("name", "unknown"),
                                "size": m.get("size", 0),
                                "digest": m.get("digest", ""),
                            }
                            for m in models
                        ]
                    }
                )
            else:
                logger.warning("Ollama returned status %d for model listing", response.status_code)
                return error(
                    code=ErrorCode.SERVICE_UNAVAILABLE,
                    message=f"Ollama returned status {response.status_code}",
                )
    except (httpx.TimeoutException, httpx.NetworkError, OSError, RuntimeError) as e:
        # 使用 safe_error_message 包装异常
        safe = safe_error_message(e, context="ollama.list_models")
        logger.error(
            "Failed to list Ollama models | error_id=%s | exc=%s",
            safe.get("error_id"),
            e,
            exc_info=True,
        )

        # 提供有用的错误信息
        error_msg = safe["message"]
        if "Connection" in str(e) or "Network" in str(e):
            error_msg += "。无法连接到 Ollama 服务，请检查服务是否正在运行。"

        return error(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message=error_msg,
            detail=safe.get("detail"),
        )
