"""Provider 工厂与默认模板（从 provider_registry 拆出）。"""

from __future__ import annotations

import logging
import os

from app.ai.llm.provider_base import (
    LLMProvider,
    ProviderCapability,
    ProviderConfig,
    ProviderType,
)

logger = logging.getLogger(__name__)

# ProviderType -> Provider 类 的映射
_PROVIDER_CLASS_MAP: dict[ProviderType, type[LLMProvider]] = {}


def _register_provider_class(provider_type: ProviderType, cls: type[LLMProvider]) -> None:
    """注册 Provider 类。"""
    _PROVIDER_CLASS_MAP[provider_type] = cls


def _load_all_provider_classes() -> None:
    """延迟加载所有 Provider 类，避免循环导入。"""
    if _PROVIDER_CLASS_MAP:
        return

    try:
        from app.ai.llm.providers import (
            OllamaProvider,
            LMStudioProvider,
            LlamaCppProvider,
            LlamaProvider,
            VllmProvider,
            TGIProvider,
            KoboldCppProvider,
        )
        from app.ai.llm.providers.cloud import (
            OpenAIProvider,
            AnthropicProvider,
            DeepSeekProvider,
            QwenProvider,
            GeminiProvider,
            OpenAICompatibleProvider,
        )

        _register_provider_class(ProviderType.OLLAMA, OllamaProvider)
        _register_provider_class(ProviderType.LMSTUDIO, LMStudioProvider)
        _register_provider_class(ProviderType.LLAMACPP, LlamaCppProvider)
        _register_provider_class(ProviderType.LLAMA, LlamaProvider)
        _register_provider_class(ProviderType.VLLM, VllmProvider)
        _register_provider_class(ProviderType.TGI, TGIProvider)
        _register_provider_class(ProviderType.KOBOLDCPP, KoboldCppProvider)
        _register_provider_class(ProviderType.OPENAI, OpenAIProvider)
        _register_provider_class(ProviderType.ANTHROPIC, AnthropicProvider)
        _register_provider_class(ProviderType.DEEPSEEK, DeepSeekProvider)
        _register_provider_class(ProviderType.QWEN, QwenProvider)
        _register_provider_class(ProviderType.GEMINI, GeminiProvider)
        _register_provider_class(ProviderType.OPENAI_COMPATIBLE, OpenAICompatibleProvider)
    except ImportError as e:
        logger.error("加载 Provider 类失败: %s", e, exc_info=True)


def create_provider(config: ProviderConfig) -> LLMProvider:
    """根据配置创建 Provider 实例。"""
    _load_all_provider_classes()
    cls = _PROVIDER_CLASS_MAP.get(config.provider_type)
    if cls is None:
        raise ValueError(f"未知的 Provider 类型: {config.provider_type}")
    return cls(config)


# 默认 Provider 模板

# Provider 默认 base_url 模板。
# 安全修复 [P1-BE-4]：提取为模块级常量，并支持环境变量覆盖。
# - 本地服务使用 127.0.0.1（统一约定）
# - 云服务允许通过 LLM_<PROVIDER>_BASE_URL 环境变量切换代理或兼容 API（如 Azure OpenAI）
# - 与前端 src/utils/llmProviders.ts 的 PROVIDER_DEFAULT_BASE_URLS 保持同步
_PROVIDER_DEFAULT_BASE_URLS: dict[str, str] = {
    "ollama": "http://127.0.0.1:11434",
    "lmstudio": "http://127.0.0.1:1234/v1",
    "llamacpp": "http://127.0.0.1:8080/v1",
    "llama": "http://127.0.0.1:8081/v1",
    "vllm": "http://127.0.0.1:8000/v1",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
}


def _provider_base_url(provider: str) -> str:
    """读取 Provider 默认 base_url，优先环境变量 LLM_<PROVIDER>_BASE_URL。"""
    env_key = f"LLM_{provider.upper()}_BASE_URL"
    return os.getenv(env_key, _PROVIDER_DEFAULT_BASE_URLS.get(provider, ""))


def _default_provider_templates() -> list[ProviderConfig]:
    """生成默认 Provider 模板（全部 disabled，等待用户配置）。"""
    return [
        ProviderConfig(
            provider_id="ollama-default",
            name="Ollama (本地)",
            provider_type=ProviderType.OLLAMA,
            base_url=_provider_base_url("ollama"),
            default_model="qwen3.5:35b-128k",
            enabled=False,
            priority=10,
            capabilities=[ProviderCapability.CHAT, ProviderCapability.STREAMING],
        ),
        ProviderConfig(
            provider_id="lmstudio-default",
            name="LM Studio (本地)",
            provider_type=ProviderType.LMSTUDIO,
            base_url=_provider_base_url("lmstudio"),
            default_model="",
            enabled=False,
            priority=9,
            capabilities=[ProviderCapability.CHAT, ProviderCapability.STREAMING],
        ),
        ProviderConfig(
            provider_id="llamacpp-default",
            name="llama.cpp (本地)",
            provider_type=ProviderType.LLAMACPP,
            base_url=_provider_base_url("llamacpp"),
            default_model="qwen3-instruct-30b",
            # llama.cpp llama-server / model-proxy 的 API Key（Bearer）。
            # 从 LLAMA_API_KEY 环境变量读取，避免把 key 硬编码进源码；
            # 未设置且该 server 启用了 --api-key 时，调用会被 401 拒绝。
            api_key=os.environ.get("LLAMA_API_KEY", ""),
            enabled=True,
            is_active=True,
            priority=8,
            capabilities=[ProviderCapability.CHAT],
        ),
        ProviderConfig(
            provider_id="llama-default",
            name="Llama (本地 llama-server)",
            provider_type=ProviderType.LLAMA,
            base_url=_provider_base_url("llama"),
            default_model="",
            enabled=False,
            priority=8,
            capabilities=[ProviderCapability.CHAT, ProviderCapability.STREAMING],
        ),
        ProviderConfig(
            provider_id="vllm-default",
            name="vLLM (本地)",
            provider_type=ProviderType.VLLM,
            base_url=_provider_base_url("vllm"),
            default_model="",
            enabled=False,
            priority=8,
            capabilities=[ProviderCapability.CHAT, ProviderCapability.STREAMING],
        ),
        ProviderConfig(
            provider_id="openai-default",
            name="OpenAI (云端)",
            provider_type=ProviderType.OPENAI,
            base_url=_provider_base_url("openai"),
            default_model="gpt-4o-mini",
            enabled=False,
            priority=7,
            capabilities=[
                ProviderCapability.CHAT,
                ProviderCapability.STREAMING,
                ProviderCapability.FUNCTION_CALLING,
                ProviderCapability.VISION,
            ],
        ),
        ProviderConfig(
            provider_id="anthropic-default",
            name="Anthropic Claude (云端)",
            provider_type=ProviderType.ANTHROPIC,
            base_url=_provider_base_url("anthropic"),
            default_model="claude-3-5-sonnet-20241022",
            enabled=False,
            priority=7,
            capabilities=[
                ProviderCapability.CHAT,
                ProviderCapability.STREAMING,
                ProviderCapability.VISION,
            ],
        ),
        ProviderConfig(
            provider_id="deepseek-default",
            name="DeepSeek (云端)",
            provider_type=ProviderType.DEEPSEEK,
            base_url=_provider_base_url("deepseek"),
            default_model="deepseek-chat",
            enabled=False,
            priority=6,
            capabilities=[ProviderCapability.CHAT, ProviderCapability.STREAMING],
        ),
        ProviderConfig(
            provider_id="qwen-default",
            name="通义千问 (云端)",
            provider_type=ProviderType.QWEN,
            base_url=_provider_base_url("qwen"),
            default_model="qwen-plus",
            enabled=False,
            priority=6,
            capabilities=[ProviderCapability.CHAT, ProviderCapability.STREAMING],
        ),
        ProviderConfig(
            provider_id="gemini-default",
            name="Google Gemini (云端)",
            provider_type=ProviderType.GEMINI,
            base_url=_provider_base_url("gemini"),
            default_model="gemini-1.5-flash",
            enabled=False,
            priority=5,
            capabilities=[ProviderCapability.CHAT, ProviderCapability.VISION],
        ),
        ProviderConfig(
            provider_id="openai-compatible-default",
            name="OpenAI 兼容 (自定义)",
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            base_url="",
            default_model="",
            enabled=False,
            priority=4,
            capabilities=[ProviderCapability.CHAT],
        ),
    ]
