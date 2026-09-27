"""ai/llm 纯逻辑单元测试（Provider 枚举/API Key 脱敏/延迟缓存/路由结果/能力匹配/模型解析）。"""

from __future__ import annotations

import pytest

from app.ai.llm.auto_detect import AutoDetector
from app.ai.llm.provider_base import (
    ProviderCapability,
    ProviderConfig,
    ProviderType,
    _mask_api_key,
)
from app.ai.llm.router import LatencyCache, ProviderRouter, RoutingResult

pytestmark = pytest.mark.unit


def _config(**kw) -> ProviderConfig:
    return ProviderConfig(
        provider_id=kw.get("provider_id", "ollama"),
        name=kw.get("name", "Ollama"),
        provider_type=kw.get("provider_type", ProviderType.OLLAMA),
        base_url=kw.get("base_url", "http://localhost:11434"),
        capabilities=kw.get("capabilities", [ProviderCapability.CHAT]),
    )


class TestProviderType:
    def test_is_local(self):
        assert ProviderType.OLLAMA.is_local is True
        assert ProviderType.LMSTUDIO.is_local is True
        assert ProviderType.OPENAI.is_local is False

    def test_is_cloud(self):
        assert ProviderType.OPENAI.is_cloud is True
        assert ProviderType.ANTHROPIC.is_cloud is True
        assert ProviderType.OLLAMA.is_cloud is False


class TestMaskApiKey:
    def test_empty(self):
        assert _mask_api_key("") == ""

    def test_short_key(self):
        assert _mask_api_key("short") == "*****"

    def test_exactly_eight(self):
        assert _mask_api_key("12345678") == "********"

    def test_long_key(self):
        assert _mask_api_key("1234567890") == "1234**7890"


class TestLatencyCache:
    def test_record_and_avg(self):
        cache = LatencyCache(max_entries=10)
        cache.record("p1", 100.0)
        cache.record("p1", 200.0)
        assert cache.get_avg("p1") == 150.0

    def test_unknown_provider(self):
        cache = LatencyCache(max_entries=10)
        assert cache.get_avg("nope") is None

    def test_max_bucket_size(self):
        cache = LatencyCache(max_entries=2)
        cache.record("p1", 100.0)
        cache.record("p1", 200.0)
        cache.record("p1", 300.0)
        # 只保留最近 2 条 avg = (200+300)/2 = 250
        assert cache.get_avg("p1") == 250.0

    def test_clear(self):
        cache = LatencyCache(max_entries=10)
        cache.record("p1", 100.0)
        cache.clear()
        assert cache.get_avg("p1") is None


class TestRoutingResult:
    def test_success_with_provider(self):
        r = RoutingResult(provider=object(), config=None, candidates=[], selected_id="p1", reason="ok")
        assert r.success is True

    def test_failure_without_provider(self):
        r = RoutingResult(provider=None, config=None, candidates=[], selected_id=None, reason="none")
        assert r.success is False

    def test_to_dict(self):
        cfg = _config()
        r = RoutingResult(provider=None, config=cfg, candidates=[cfg], selected_id="ollama", reason="ok")
        d = r.to_dict()
        assert d["selected_id"] == "ollama"
        assert d["selected_name"] == "Ollama"


class TestParseModels:
    def test_openai_format(self):
        data = {"data": [{"id": "gpt-4"}, {"id": "gpt-3.5"}]}
        assert AutoDetector._parse_models(data) == ["gpt-4", "gpt-3.5"]

    def test_ollama_format(self):
        data = {"models": [{"name": "qwen2.5:7b"}, {"name": "llama3"}]}
        assert AutoDetector._parse_models(data) == ["qwen2.5:7b", "llama3"]

    def test_empty(self):
        assert AutoDetector._parse_models({}) == []


class TestHasCapabilities:
    def test_empty_required_returns_true(self):
        assert ProviderRouter._has_capabilities(_config(), None) is True
        assert ProviderRouter._has_capabilities(_config(), []) is True

    def test_subset_returns_true(self):
        cfg = _config(capabilities=[ProviderCapability.CHAT, ProviderCapability.EMBEDDING])
        assert ProviderRouter._has_capabilities(cfg, [ProviderCapability.CHAT]) is True

    def test_superset_returns_false(self):
        cfg = _config(capabilities=[ProviderCapability.CHAT])
        assert ProviderRouter._has_capabilities(cfg, [ProviderCapability.CHAT, ProviderCapability.VISION]) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
