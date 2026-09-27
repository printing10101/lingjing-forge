"""ProviderRouter 实现（从 router 拆出）。"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.ai.llm.provider_base import LLMProvider, ProviderCapability, ProviderConfig, ProviderStatus
from app.ai.llm.provider_registry import ProviderRegistry, get_registry
from app.ai.llm._latency_cache import LatencyCache
from app.ai.llm._router_models import RoutingRequest, RoutingResult, RoutingStrategy

logger = logging.getLogger(__name__)


class ProviderRouter:
    """LLM Provider 智能路由器。

    使用方式：
        router = get_router()
        result = await router.route(RoutingRequest(
            required_capabilities=[ProviderCapability.CHAT],
        ))
        if result.success:
            response = await result.provider.chat_completion(messages)
    """

    def __init__(
        self,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self._registry = registry or get_registry()
        self._latency_cache = LatencyCache()

    @property
    def latency_cache(self) -> LatencyCache:
        return self._latency_cache

    # 主路由入口

    async def route(self, request: RoutingRequest) -> RoutingResult:
        """根据请求选择最佳 Provider。

        路由优先级（从高到低）：
        1. 显式指定的 provider_id（如果存在且可用）
        2. 激活的 Provider（如果未 skip_active）
        3. 按策略（capability/priority/latency）选择的候选
        4. 按优先级降级列表
        """
        # 1. 显式指定
        if request.provider_id:
            provider, config = await self._try_get(request.provider_id, request.required_capabilities)
            if provider is not None:
                return RoutingResult(
                    provider=provider,
                    config=config,
                    candidates=[config] if config else [],
                    selected_id=request.provider_id,
                    reason="explicit_user_choice",
                )
            # 显式指定的不可用，继续按策略选择
            logger.warning(
                "显式指定的 Provider %s 不可用，按策略降级",
                request.provider_id,
            )

        # 2. 激活的 Provider
        if not request.skip_active:
            active = self._registry.get_active_provider()
            active_cfg = self._registry.get_active_provider_config()
            if active is not None and active_cfg is not None:
                if self._has_capabilities(active_cfg, request.required_capabilities):
                    return RoutingResult(
                        provider=active,
                        config=active_cfg,
                        candidates=[active_cfg],
                        selected_id=active_cfg.provider_id,
                        reason="active_provider",
                    )

        # 3. 按策略选择候选
        candidates = self._select_candidates(request)
        if not candidates:
            return RoutingResult(
                provider=None,
                config=None,
                candidates=[],
                selected_id=None,
                reason="no_available_provider",
            )

        # 4. 尝试候选列表
        for cfg in candidates:
            provider, _ = await self._try_get(cfg.provider_id, request.required_capabilities)
            if provider is not None:
                return RoutingResult(
                    provider=provider,
                    config=cfg,
                    candidates=candidates,
                    selected_id=cfg.provider_id,
                    reason=f"strategy_{request.strategy}",
                )

        return RoutingResult(
            provider=None,
            config=None,
            candidates=candidates,
            selected_id=None,
            reason="all_candidates_unavailable",
        )

    # 候选选择

    def _select_candidates(self, request: RoutingRequest) -> list[ProviderConfig]:
        """根据策略选择候选 Provider 列表。"""
        all_providers = self._registry.list_providers(include_disabled=False)
        if not all_providers:
            return []

        # 能力过滤
        if request.required_capabilities:
            all_providers = [p for p in all_providers if self._has_capabilities(p, request.required_capabilities)]

        if not all_providers:
            return []

        # 按策略排序
        strategy = request.strategy
        if strategy == RoutingStrategy.LOCAL_FIRST:
            sorted_list = sorted(
                all_providers,
                key=lambda p: (not p.provider_type.is_local, -p.priority, p.name),
            )
        elif strategy == RoutingStrategy.CLOUD_FIRST:
            sorted_list = sorted(
                all_providers,
                key=lambda p: (p.provider_type.is_local, -p.priority, p.name),
            )
        elif strategy == RoutingStrategy.LATENCY_FIRST:
            sorted_list = sorted(
                all_providers,
                key=lambda p: (
                    self._latency_cache.get_avg(p.provider_id) or 999999.0,
                    -p.priority,
                ),
            )
        elif strategy == RoutingStrategy.CAPABILITY_MATCH:
            # 能力多的优先（更多能力 = 更通用）
            sorted_list = sorted(
                all_providers,
                key=lambda p: (-len(p.capabilities), -p.priority, p.name),
            )
        else:  # PRIORITY_FALLBACK / ACTIVE_ONLY / default
            sorted_list = sorted(
                all_providers,
                key=lambda p: (-p.priority, p.name),
            )

        # 激活的排第一
        active_cfg = self._registry.get_active_provider_config()
        if active_cfg and not request.skip_active:
            active_id = active_cfg.provider_id
            sorted_list = [p for p in sorted_list if p.provider_id == active_id] + [
                p for p in sorted_list if p.provider_id != active_id
            ]

        return sorted_list[: request.max_candidates]

    # 工具方法

    @staticmethod
    def _has_capabilities(
        config: ProviderConfig,
        required: list[ProviderCapability] | None,
    ) -> bool:
        """检查 Provider 是否具备所有必需能力。"""
        if not required:
            return True
        provider_caps = set(config.capabilities)
        required_caps = set(required)
        return required_caps.issubset(provider_caps)

    async def _try_get(
        self,
        provider_id: str,
        required_caps: list[ProviderCapability] | None,
    ) -> tuple[LLMProvider | None, ProviderConfig | None]:
        """尝试获取 Provider 实例，并验证其可用性。

        Returns:
            (provider, config) — 如果不可用返回 (None, None)
        """
        config = self._registry.get_provider(provider_id)
        if config is None or not config.enabled:
            return None, None

        if not self._has_capabilities(config, required_caps):
            return None, None

        provider = self._registry.get_provider_instance(provider_id)
        if provider is None:
            return None, None

        # 健康检查（best-effort，不阻塞路由）
        try:
            status = await provider.health_check()
            if status == ProviderStatus.OFFLINE:
                logger.debug(
                    "Provider %s 健康检查返回 OFFLINE，跳过",
                    provider_id,
                )
                return None, config
        except Exception as e:
            logger.debug(
                "Provider %s 健康检查异常，仍尝试使用: %s",
                provider_id,
                e,
            )

        return provider, config

    # 调用便捷方法

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        model: str | None = None,
        provider_id: str | None = None,
        required_capabilities: list[ProviderCapability] | None = None,
    ) -> dict[str, Any]:
        """便捷调用：自动路由 + 对话补全。

        Returns:
            LLM 响应字典，包含 content/model/finish_reason/usage 字段。
            如果路由失败，附加 _routing_failed=True 和 _reason 字段。

        Raises:
            ProviderError: 所有 Provider 都不可用时
        """
        from app.ai.llm.provider_base import ProviderError, ProviderUnavailableError

        request = RoutingRequest(
            provider_id=provider_id,
            required_capabilities=required_capabilities or [ProviderCapability.CHAT],
            strategy=RoutingStrategy.PRIORITY_FALLBACK,
        )

        # 优先尝试显式/激活的 Provider
        result = await self.route(request)
        if not result.success:
            # 降级到优先级列表中的任意可用 Provider
            fallback_request = RoutingRequest(
                required_capabilities=required_capabilities or [ProviderCapability.CHAT],
                strategy=RoutingStrategy.PRIORITY_FALLBACK,
                skip_active=True,
            )
            result = await self.route(fallback_request)

        if not result.success or result.provider is None:
            raise ProviderUnavailableError(
                f"无可用 LLM Provider。原因: {result.reason}. 候选数: {len(result.candidates)}"
            )

        start = time.time()
        try:
            response = await result.provider.chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                model=model,
            )
            latency_ms = (time.time() - start) * 1000
            self._latency_cache.record(result.selected_id or "", latency_ms)
            response["_provider_id"] = result.selected_id
            response["_provider_name"] = result.config.name if result.config else None
            response["_latency_ms"] = latency_ms
            return response
        except ProviderError:
            raise
        except Exception as e:
            logger.error(
                "Provider %s 调用失败: %s",
                result.selected_id,
                e,
                exc_info=True,
            )
            # 失效缓存，下次重新创建实例
            self._registry.clear_instance_cache()
            raise ProviderUnavailableError(f"Provider {result.selected_id} 调用失败: {e}") from e

    # 状态查询

    def get_status(self) -> dict[str, Any]:
        """返回路由器状态。"""
        summary = self._registry.get_status_summary()
        latency_summary = {}
        for cfg in self._registry.list_providers(include_disabled=False):
            avg = self._latency_cache.get_avg(cfg.provider_id)
            latency_summary[cfg.provider_id] = {
                "name": cfg.name,
                "avg_latency_ms": avg,
                "is_active": cfg.is_active,
                "priority": cfg.priority,
            }
        return {
            "registry": summary,
            "latency": latency_summary,
        }
