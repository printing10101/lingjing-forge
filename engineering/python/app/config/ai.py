"""AI 推理/训练模型配置、模型路由与微调设置。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.config._utils import _bool_env, _env, _int_env, _path, logger


@dataclass
class AIConfig:
    mode: str = field(default_factory=lambda: _env("AI_MODE", "local"))
    ollama_base_url: str = field(default_factory=lambda: _env("OLLAMA_BASE_URL", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: _env("OLLAMA_MODEL", "qwen3.5:35b-128k"))
    cloud_api_key: str = field(default_factory=lambda: _env("CLOUD_API_KEY", ""))
    cloud_base_url: str = field(default_factory=lambda: _env("CLOUD_BASE_URL", "https://api.openai.com/v1"))
    cloud_model: str = field(default_factory=lambda: _env("CLOUD_MODEL", "gpt-3.5-turbo"))
    timeout: int = field(default_factory=lambda: _int_env("AI_TIMEOUT", 60))
    max_retries: int = field(default_factory=lambda: _int_env("AI_MAX_RETRIES", 3))

    def __post_init__(self) -> None:
        """P2-14 修复：启动时校验云端模式关键配置。

        当 ``AI_MODE=cloud`` 但 ``CLOUD_API_KEY`` 为空时，云端调用必然
        鉴权失败；此时仅记录 WARNING 不阻断启动，保持与现有容错策略一致
        （本地 Ollama 可能仍可用作回退）。测试环境下静默。
        """
        if _env("ENVIRONMENT", "development").lower() == "testing":
            return
        if self.mode == "cloud" and not self.cloud_api_key.strip():
            logger.warning(
                "AI_MODE=cloud 但 CLOUD_API_KEY 为空，云端 API 调用将失败。"
                "请设置 CLOUD_API_KEY 环境变量，或切换 AI_MODE=local 使用本地 Ollama。"
            )


@dataclass
class ModelRouterSettings:
    local_model: str = field(default_factory=lambda: _env("LOCAL_MODEL", "qwen3.5:35b-128k"))
    cloud_provider: str = field(default_factory=lambda: _env("CLOUD_PROVIDER", "openai"))
    cloud_model: str = field(default_factory=lambda: _env("CLOUD_MODEL_ROUTER", "gpt-4o"))
    fallback_threshold: int = field(default_factory=lambda: _int_env("FALLBACK_THRESHOLD", 3))
    local_timeout: int = field(default_factory=lambda: _int_env("LOCAL_TIMEOUT", 30))


@dataclass
class FineTuneSettings:
    finetune_auto_trigger: bool = field(default_factory=lambda: _bool_env("FINETUNE_AUTO_TRIGGER", False))
    finetune_min_samples: int = field(default_factory=lambda: _int_env("FINETUNE_MIN_SAMPLES", 50))
    finetune_interval_days: int = field(default_factory=lambda: _int_env("FINETUNE_INTERVAL_DAYS", 7))
    finetune_output_dir: str = field(
        default_factory=lambda: _path("FINETUNE_OUTPUT_DIR", os.path.join("output", "models", "finetuned"))
    )
