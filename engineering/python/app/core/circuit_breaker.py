"""
熔断器模式实现

提供服务健康检查、自动熔断、半开状态探测等机制，防止级联故障。
"""

from __future__ import annotations

import threading
from enum import Enum
from typing import Any
from collections.abc import Callable

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta

from .exceptions import CircuitBreakerHalfOpenException, CircuitBreakerOpenException


class CircuitState(Enum):
    """熔断器状态"""

    CLOSED = "closed"  # 闭合：正常调用
    OPEN = "open"  # 打开：拒绝调用
    HALF_OPEN = "half_open"  # 半开：健康探测


@dataclass
class CircuitBreakerConfig:
    """熔断器配置"""

    failure_threshold: int = 5  # 连续失败次数阈值
    recovery_timeout: float = 30  # 熔断恢复超时 (秒)
    half_open_max_calls: int = 3  # 半开状态最大探测次数
    success_threshold: int = 2  # 半开成功后恢复次数


class CircuitBreaker:
    """
    熔断器实现

    用法示例:
        cb = CircuitBreaker("llm_service", CircuitBreakerConfig())
        cb.execute(lambda: call_ai_service())

    状态流转:
        CLOSED -> (失败>= threshold) -> OPEN
        OPEN -> (等待 recovery_timeout) -> HALF_OPEN
        HALF_OPEN -> (成功>= threshold) -> CLOSED
        HALF_OPEN -> (失败) -> OPEN
    """

    def __init__(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
        fallback: Callable[[Exception], Any] | None = None,
    ):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.fallback = fallback

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: datetime | None = None
        self._opened_at: datetime | None = None
        self._last_success_time: datetime | None = None
        # P2-1 修复：半开探测并发闸门计数（此前 half_open_max_calls 是
        # 死配置——恢复窗口一开，全部积压请求同时打到刚恢复的服务，
        # 极易立即再熔断）
        self._half_open_in_flight = 0
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        """获取当前状态，如果处于 OPEN 状态且超时，自动进入 HALF_OPEN"""
        with self._lock:
            if self._state == CircuitState.OPEN and self._is_recovery_timeout_expired():
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
                self._half_open_in_flight = 0
        return self._state

    @property
    def is_available(self) -> bool:
        """服务是否可用（不包括半开探测中）"""
        return self.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def _is_recovery_timeout_expired(self) -> bool:
        """是否已达到恢复超时时间"""
        if not self._opened_at:
            return False
        return datetime.now() - self._opened_at > timedelta(seconds=self.config.recovery_timeout)

    def _record_success(self):
        """记录成功调用"""
        with self._lock:
            self._failure_count = 0

            if self.state == CircuitState.HALF_OPEN:
                self._success_count += 1
                self._last_success_time = datetime.now()

                if self._success_count >= self.config.success_threshold:
                    self._transition_to_closed()
            else:
                self._last_success_time = datetime.now()

    def _record_failure(self, exception: Exception):
        """记录失败调用"""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = datetime.now()

            if self.state == CircuitState.HALF_OPEN:
                # 半开状态下失败，直接回到 OPEN 状态
                self._transition_to_open()
            elif self._failure_count >= self.config.failure_threshold:
                # 连续失败达到阈值，熔断
                self._transition_to_open()

    def _transition_to_open(self):
        """转换到 OPEN 状态"""
        self._state = CircuitState.OPEN
        self._opened_at = datetime.now()
        self._success_count = 0
        self._half_open_in_flight = 0

    def _transition_to_closed(self):
        """转换到 CLOSED 状态"""
        self._state = CircuitState.CLOSED
        self._opened_at = None
        self._failure_count = 0
        self._half_open_in_flight = 0

    def _transition_to_half_open(self):
        """转换到 HALF_OPEN 状态"""
        self._state = CircuitState.HALF_OPEN
        self._success_count = 0

    def execute(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """
        执行受保护的服务调用

        如果熔断器打开，则调用 fallback 或抛出异常
        如果服务调用成功，记录成功
        如果服务调用失败，记录失败

        Args:
            func: 需要熔断保护的可调用函数
            *args: 函数参数
            **kwargs: 函数关键字参数

        Returns:
            函数执行结果

        Raises:
            CircuitBreakerOpenException: 熔断器已打开，拒绝调用
            Exception: 函数执行失败，但已记录
        """
        with self._lock:
            current_state = self.state

            if current_state == CircuitState.OPEN:
                raise CircuitBreakerOpenException(  # type: ignore[call-arg]
                    service=self.name,
                    opened_at=self._opened_at.isoformat() if self._opened_at else None,
                )

        try:
            result = func(*args, **kwargs)
            self._record_success()
            return result
        except Exception as e:
            self._record_failure(e)

            # 调用 fallback
            if self.fallback and isinstance(e, Exception):
                return self.fallback(e)

            raise

    async def async_execute(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """执行受保护的异步服务调用（await 版 execute）。

        ``execute`` 直接调用 ``func`` 只会得到协程对象——协程从未被 await
        就被记为成功，这是同步版不能用于 async 调用的原因。LLM Provider
        等异步链路必须使用本方法。

        状态机语义与 ``execute`` 完全一致：
        - OPEN 时抛出 :class:`CircuitBreakerOpenException`（不执行 func）；
        - 成功记录成功，失败记录失败并 re-raise（不吞异常，fallback 由调用方决定）。

        Raises:
            CircuitBreakerOpenException: 熔断器已打开，拒绝调用
            Exception: func 执行失败，但已记录
        """
        self.try_acquire_half_open()
        try:
            result = await func(*args, **kwargs)
            self._record_success()
            return result
        except Exception as e:
            self._record_failure(e)
            raise
        finally:
            self.release_half_open()

    def record_success(self) -> None:
        """手动记录一次成功（供无法包裹调用体的异步流式场景使用）。"""
        self._record_success()

    def record_failure(self, exception: Exception) -> None:
        """手动记录一次失败（供无法包裹调用体的异步流式场景使用）。"""
        self._record_failure(exception)

    def check_available(self) -> None:
        """检查熔断器是否放行，打开时抛出 CircuitBreakerOpenException。

        供「先检查、后执行」的异步调用模式使用（执行体自身负责回填
        record_success / record_failure）。
        """
        with self._lock:
            if self.state == CircuitState.OPEN:
                raise CircuitBreakerOpenException(  # type: ignore[call-arg]
                    service=self.name,
                    opened_at=self._opened_at.isoformat() if self._opened_at else None,
                )

    def try_acquire_half_open(self) -> None:
        """半开探测并发闸门（P2-1：让 half_open_max_calls 真正生效）。

        - OPEN：抛 CircuitBreakerOpenException（快速失败）；
        - HALF_OPEN 且在途探测数已达 ``half_open_max_calls``：抛
          CircuitBreakerHalfOpenException（多余的探测请求被拒）；
        - 其余情况：在途计数 +1 并放行。

        调用方必须在调用完成后执行 :meth:`release_half_open`（成功/
        失败路径都要释放，建议 try/finally）。
        """
        with self._lock:
            state = self.state
            if state == CircuitState.OPEN:
                raise CircuitBreakerOpenException(  # type: ignore[call-arg]
                    service=self.name,
                    opened_at=self._opened_at.isoformat() if self._opened_at else None,
                )
            if state == CircuitState.HALF_OPEN:
                if self._half_open_in_flight >= self.config.half_open_max_calls:
                    raise CircuitBreakerHalfOpenException(  # type: ignore[call-arg]
                        service=self.name,
                        attempts=self.config.half_open_max_calls,
                    )
                self._half_open_in_flight += 1

    def release_half_open(self) -> None:
        """释放一个半开探测在途名额（与 try_acquire_half_open 配对）。"""
        with self._lock:
            if self._half_open_in_flight > 0:
                self._half_open_in_flight -= 1

    def reset(self):
        """重置熔断器状态"""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._opened_at = None
            self._last_failure_time = None
            self._last_success_time = None

    # 上下文管理器协议：使 ``with circuit_breaker_context(...) as cb`` 成立
    def __enter__(self) -> "CircuitBreaker":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        return None

    def get_status(self) -> dict:
        """获取熔断器状态信息"""
        with self._lock:
            return {
                "name": self.name,
                "state": self.state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "failure_threshold": self.config.failure_threshold,
                "last_failure_time": self._last_failure_time.isoformat() if self._last_failure_time else None,
                "opened_at": self._opened_at.isoformat() if self._opened_at else None,
            }


class CircuitBreakerRegistry:
    """
    熔断器注册中心

    提供全局熔断器管理，按服务名缓存实例
    """

    _instance: "CircuitBreakerRegistry" | None = None
    _lock = threading.Lock()

    def __new__(cls) -> "CircuitBreakerRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._breakers: dict[str, CircuitBreaker] = {}
        return cls._instance

    def get_or_create(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
        fallback: Callable[[Exception], Any] | None = None,
    ) -> CircuitBreaker:
        """获取或创建设备名的熔断器"""
        if name not in self._breakers:
            # 双重检查锁
            with self._lock:
                if name not in self._breakers:
                    self._breakers[name] = CircuitBreaker(
                        name=name,
                        config=config,
                        fallback=fallback,
                    )
        return self._breakers[name]

    def get(self, name: str) -> CircuitBreaker | None:
        """获取指定服务名的熔断器"""
        return self._breakers.get(name)

    def reset_all(self):
        """重置所有熔断器"""
        for breaker in self._breakers.values():
            breaker.reset()

    def get_all_status(self) -> dict[str, dict]:
        """获取所有熔断器状态"""
        return {name: breaker.get_status() for name, breaker in self._breakers.items()}


# 快捷上下文管理器


def circuit_breaker_context(
    name: str,
    config: CircuitBreakerConfig | None = None,
    fallback: Callable[[Exception], Any] | None = None,
) -> AbstractContextManager[CircuitBreaker]:
    """
    熔断器上下文管理器。

    用法：进入 with 得到熔断器实例后，用它的 execute 方法包裹受保护调用
    （异步链路请用 async_execute）。

    历史 bug 修复：原实现把 ``yield breaker`` 写成了 ``return breaker``，
    函数根本不是生成器，``with`` 语句一进入就抛 AttributeError。
    """
    registry = CircuitBreakerRegistry()
    return registry.get_or_create(name, config, fallback)
