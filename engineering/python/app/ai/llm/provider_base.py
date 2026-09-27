"""LLM Provider 抽象基类。

定义所有 LLM Provider（本地/云端）必须实现的统一接口。
通过抽象基类实现多态，支持 Ollama / LM Studio / llama.cpp / vLLM /
OpenAI / Anthropic / DeepSeek 等多种后端的统一管理。
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ProviderType(str, Enum):
    """Provider 类型枚举。"""

    # 本地部署
    OLLAMA = "ollama"
    LMSTUDIO = "lmstudio"
    LLAMACPP = "llamacpp"
    LLAMA = "llama"  # Meta Llama 系模型（llama-server / llama.cpp 推理栈直连）
    VLLM = "vllm"
    TGI = "tgi"
    KOBOLDCPP = "koboldcpp"

    # 云端 API
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    GEMINI = "gemini"
    OPENAI_COMPATIBLE = "openai_compatible"  # 通用 OpenAI 兼容兜底

    @property
    def is_local(self) -> bool:
        """是否为本地部署。"""
        return self in {
            ProviderType.OLLAMA,
            ProviderType.LMSTUDIO,
            ProviderType.LLAMACPP,
            ProviderType.LLAMA,
            ProviderType.VLLM,
            ProviderType.TGI,
            ProviderType.KOBOLDCPP,
        }

    @property
    def is_cloud(self) -> bool:
        """是否为云端 API。"""
        return not self.is_local


class ProviderStatus(str, Enum):
    """Provider 健康状态。"""

    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"
    UNCONFIGURED = "unconfigured"


class ProviderCapability(str, Enum):
    """Provider 能力标签。"""

    CHAT = "chat"  # 对话补全
    EMBEDDING = "embedding"  # 向量嵌入
    FUNCTION_CALLING = "function_calling"  # 函数调用
    VISION = "vision"  # 视觉理解
    STREAMING = "streaming"  # 流式输出


@dataclass
class ProviderConfig:
    """Provider 配置数据类。

    统一的配置结构，适用于本地和云端 Provider。
    """

    provider_id: str  # 唯一标识
    name: str  # 显示名称
    provider_type: ProviderType
    base_url: str  # API 端点
    api_key: str = ""  # 云端 API Key（本地为空）
    default_model: str = ""  # 默认模型
    timeout: int = 60  # 超时秒数
    # 注意语义：max_retries 是「总尝试次数」而非「重试次数」（韧性层
    # 实现为 `for attempt in range(1, max_retries + 1)`）——3 表示最多
    # 打 3 次上游（重试 2 次），与字段名直觉相反，改动前先读
    # app/ai/llm/_resilience.py 的包装逻辑
    max_retries: int = 3
    retry_delay: float = 1.0
    enabled: bool = True  # 是否启用
    is_active: bool = False  # 是否为当前激活 Provider
    priority: int = 0  # 路由优先级（数字越大优先级越高）
    capabilities: list[ProviderCapability] = field(default_factory=lambda: [ProviderCapability.CHAT])
    extra: dict[str, Any] = field(default_factory=dict)  # 扩展配置

    def to_dict(self) -> dict[str, Any]:
        """转换为字典（API Key 脱敏）。"""
        return {
            "provider_id": self.provider_id,
            "name": self.name,
            "provider_type": self.provider_type.value,
            "base_url": self.base_url,
            "api_key_masked": _mask_api_key(self.api_key),
            "has_api_key": bool(self.api_key),
            "default_model": self.default_model,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "enabled": self.enabled,
            "is_active": self.is_active,
            "priority": self.priority,
            "capabilities": [c.value for c in self.capabilities],
            "is_local": self.provider_type.is_local,
            "extra": self.extra,
        }


def _mask_api_key(key: str) -> str:
    """API Key 脱敏显示。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


class LLMProvider:
    """LLM Provider 抽象基类。

    所有具体 Provider（Ollama / OpenAI / ...）必须继承此类并实现
    `detect() / health_check() / list_models() / chat_completion()` 方法。

    设计原则：
    - 统一接口：所有 Provider 暴露相同的方法签名
    - 优雅降级：探测/健康检查失败时返回明确状态，不抛异常
    - 连接复用：复用共享 httpx.AsyncClient 连接池
    """

    # 子类必须覆盖：该 Provider 类型的默认端口（本地）或默认 API 端点（云端）
    DEFAULT_PORT: int | None = None
    DEFAULT_BASE_URL: str = ""
    # 本地推理服务置 True：请求绕过系统代理环境变量（HTTP(S)_PROXY），
    # 避免 127.0.0.1 请求被 Clash 等系统代理劫持。云端 Provider 保持
    # 默认 False（可能需要代理出境）。
    _bypass_env_proxy: bool = False

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self._last_health_check: float = 0.0
        self._last_status: ProviderStatus = ProviderStatus.UNKNOWN
        self._last_latency_ms: float | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # 韧性层自动安装：子类自定义的 chat_completion / chat_completion_stream
        # 一次性包装上「熔断 + 重试 + 6xxx 异常桥接」。惰性导入避免模块循环
        # （_resilience 顶层 import 本模块的异常类，此处调用时本模块已加载完毕）。
        try:
            from app.ai.llm._resilience import install_resilience
        except ImportError:  # pragma: no cover - 仅在异常裁剪环境触发
            return
        install_resilience(cls)

    @property
    def provider_id(self) -> str:
        return self.config.provider_id

    @property
    def provider_type(self) -> ProviderType:
        return self.config.provider_type

    @property
    def is_local(self) -> bool:
        return self.config.provider_type.is_local

    @property
    def status(self) -> ProviderStatus:
        return self._last_status

    @property
    def latency_ms(self) -> float | None:
        return self._last_latency_ms

    # 抽象方法（子类必须实现）

    async def detect(self) -> bool:
        """探测该 Provider 是否在当前主机可用。

        本地 Provider：探测端口是否开放
        云端 Provider：验证 API Key 是否有效

        Returns:
            True 如果可用
        """
        raise NotImplementedError

    async def health_check(self) -> ProviderStatus:
        """健康检查，返回当前状态。"""
        raise NotImplementedError

    async def list_models(self) -> list[str]:
        """列出该 Provider 可用的模型列表。"""
        raise NotImplementedError

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> dict[str, Any]:
        """调用 LLM 对话补全 API。

        Args:
            messages: 消息列表，格式 [{"role": "user", "content": "..."}]
            max_tokens: 最大生成 token 数
            temperature: 采样温度 [0.0, 2.0]
            model: 模型名称，None 时使用 default_model

        Returns:
            统一格式的响应字典：
            {
                "content": str,        # 生成内容
                "model": str,          # 实际使用的模型
                "finish_reason": str,  # 完成原因
                "usage": dict,         # token 使用统计
            }
        """
        raise NotImplementedError

    # 共享工具方法

    async def chat_completion_stream(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式对话补全（统一 chunk 契约）。

        chunk 格式：``{"content": str, "model": str, "done": bool,
        "stream_mode": "native"|"pseudo", "usage": dict(仅 done 时)}``。

        默认实现为 **pseudo 流**：调用非流式 chat_completion 后整体作为
        单个 chunk 输出——保证所有 Provider 在 API 契约层面都"可流式"，
        与能力标签 ``ProviderCapability.STREAMING`` 不再自相矛盾。支持
        原生流式的 Provider（Ollama NDJSON / OpenAI 兼容 SSE）覆盖本方法
        （stream_mode="native"）。子类覆盖会被韧性层自动包装（熔断 +
        异常桥接；流式不做重试，避免重复输出）。
        """
        result = await self.chat_completion(messages, max_tokens=max_tokens, temperature=temperature, model=model)
        yield {
            "content": result.get("content", ""),
            "model": result.get("model", self._resolve_model(model)),
            "done": True,
            "stream_mode": "pseudo",
            "usage": result.get("usage", {}),
        }

    async def _get_http_client(self) -> httpx.AsyncClient:
        """按 Provider 类别选取共享客户端（本地服务绕过系统代理）。"""
        from app.ai.llm_client import get_shared_http_client, get_shared_http_client_no_proxy

        if self._bypass_env_proxy:
            return await get_shared_http_client_no_proxy()
        return await get_shared_http_client()

    async def _http_get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        """发起 GET 请求（复用共享连接池）。"""
        client = await self._get_http_client()
        return await client.get(url, headers=headers, timeout=self.config.timeout)

    async def _http_post(
        self,
        url: str,
        json_payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """发起 POST 请求（复用共享连接池）。"""
        client = await self._get_http_client()
        return await client.post(
            url,
            headers=headers,
            json=json_payload,
            timeout=self.config.timeout,
        )

    def _measure_latency(self, start: float) -> None:
        """记录请求延迟。"""
        self._last_latency_ms = (time.time() - start) * 1000

    def _update_status(self, status: ProviderStatus) -> None:
        """更新状态并记录健康检查时间。"""
        self._last_status = status
        self._last_health_check = time.time()

    def _build_auth_headers(self) -> dict[str, str]:
        """构建认证头（云端 Provider 用）。"""
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _resolve_model(self, model: str | None) -> str:
        """解析实际使用的模型名称。"""
        return model or self.config.default_model

    def to_dict(self) -> dict[str, Any]:
        """返回 Provider 概要信息（含运行时状态）。"""
        info = self.config.to_dict()
        info.update(
            {
                "status": self._last_status.value,
                "latency_ms": self._last_latency_ms,
                "last_health_check": self._last_health_check,
            }
        )
        return info


class ProviderError(Exception):
    """Provider 通用错误。"""


class ProviderHTTPError(ProviderError):
    """带 HTTP 状态码的 Provider 错误。

    韧性层（``app.ai.llm._resilience``）依赖 ``status_code`` 区分瞬态错误
    （429/5xx，可重试、计入熔断）与永久错误（401/403 等，直接上抛）。
    Provider 实现在 ``response.status_code != 200`` 时必须抛本类而不是
    无状态码的 ``ProviderError``。
    """

    def __init__(self, message: str, status_code: int = 0, body: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class ProviderUnavailableError(ProviderError):
    """Provider 不可用错误（用于降级判断）。"""
