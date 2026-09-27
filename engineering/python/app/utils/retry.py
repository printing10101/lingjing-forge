"""通用指数退避重试工具。

历史上 ``app/dnc/opcu_client.py`` 与 ``app/dnc/mtconnect_client.py`` 各自
实现了字节级几乎完全一致的 ``_retry_with_backoff`` 方法，仅在属性名上不同
（``max_reconnect_attempts`` vs ``max_retries`` 等）。本模块提供模块级
``retry_with_backoff`` 协程函数统一两条实现，消除维护时一处修改、另一处
遗漏不同步的风险。

设计约束
---------
- **不破坏现有对外 API**：两个客户端类的 ``_retry_with_backoff`` 方法保留
  为单行委托，使任何外部调用仍可用。
- **失败回调签名不变**：``failure_callback(operation_name, error, attempt)``
  保持原样。
- **退避算法不变**：``delay = min(backoff_base * (2 ** attempt), backoff_max)``
  外加 0~1 秒 jitter，与原实现完全一致。
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


async def retry_with_backoff(
    operation: Callable[[], Awaitable[Any]],
    operation_name: str,
    *,
    max_retries: int,
    backoff_base: float = 1.0,
    backoff_max: float = 60.0,
    failure_callback: Callable[[str, BaseException, int], None] | None = None,
) -> Any:
    """通用异步指数退避重试。

    Args:
        operation: 无参数的协程工厂（返回 awaitable 的可调用对象）。
        operation_name: 操作名称（用于日志）。
        max_retries: 最大重试次数（不含首次尝试）。
        backoff_base: 指数退避基数（秒）。
        backoff_max: 最大退避时间（秒）。
        failure_callback: 失败告警回调，签名
            ``callback(operation_name, error, attempt)``。

    Returns:
        操作的成功返回值。

    Raises:
        RuntimeError: 达到最大重试次数后仍失败。
    """
    last_error: BaseException | None = None
    # 总尝试次数 = 1 (初始) + max_retries (重试)
    total_attempts = max_retries + 1
    for attempt in range(total_attempts):
        try:
            return await operation()
        except Exception as e:
            last_error = e
            if attempt >= max_retries:
                # 达到最大重试次数
                logger.error(
                    "%s 失败，已达最大重试次数 %d: %s",
                    operation_name,
                    max_retries,
                    e,
                )
                if failure_callback:
                    try:
                        failure_callback(operation_name, e, attempt + 1)
                    except Exception as cb_err:
                        logger.error("failure_callback 执行失败: %s", cb_err)
                raise RuntimeError(f"{operation_name} 失败，已达最大重试次数 {max_retries}: {e}") from e
            # 指数退避 + jitter（避免惊群）
            delay = min(
                backoff_base * (2**attempt),
                backoff_max,
            )
            jitter = random.uniform(0, 1.0)
            wait_time = delay + jitter
            logger.warning(
                "%s 第 %d/%d 次尝试失败: %s，%.2f 秒后重试",
                operation_name,
                attempt + 1,
                total_attempts,
                e,
                wait_time,
            )
            await asyncio.sleep(wait_time)
    # 理论上不应到达
    raise RuntimeError(f"{operation_name} 失败: {last_error}")


__all__ = ["retry_with_backoff"]
