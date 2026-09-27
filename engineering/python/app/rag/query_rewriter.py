"""查询改写与扩展模块。

支持两种查询增强策略：
1. 查询改写（Query Rewriting）：将口语化查询改写为更适合检索的关键词组合
2. HyDE（Hypothetical Document Embeddings）：让 LLM 生成假设性答案，
   用答案的 embedding 进行检索，弥补 query 与 document 之间的语义鸿沟

通过环境变量控制：
- ENABLE_QUERY_REWRITE: 是否启用查询改写（默认 "1"）
- ENABLE_HYDE: 是否启用 HyDE（默认 "0"，需要 LLM 调用，有额外延迟）
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)


def _run_async(coro):
    """在同步上下文中安全运行协程。

    - 无运行中的事件循环：直接 ``asyncio.run``
    - 已有事件循环（如 FastAPI async 路由内）：抛 ``RuntimeError``，
      提示调用方改为 ``await``。

    修复 P0-7：原实现在检测到事件循环时新建线程执行协程，高并发下
    线程数线性增长导致 OOM，且 ``thread.join()`` 会阻塞当前协程。
    现改为直接抛异常，由调用方捕获后降级（规则改写 / HyDE 返回 None）。

    注意：调用方（``_get_llm_client`` / ``rewrite_query`` /
    ``generate_hyde_document``）均已捕获 ``Exception`` 并降级处理，
    因此在 async 上下文中调用会安全降级而非崩溃。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 无运行中的事件循环，安全使用 asyncio.run
        return asyncio.run(coro)
    # 有运行中的事件循环，调用方应直接 await 协程
    raise RuntimeError("_run_async 不能在异步上下文中调用，请直接 await 协程或使用 asyncio.create_task")


# 功能开关
ENABLE_QUERY_REWRITE = os.getenv("ENABLE_QUERY_REWRITE", "1") == "1"
ENABLE_HYDE = os.getenv("ENABLE_HYDE", "0") == "1"

# HyDE 缓存大小
HYDE_CACHE_SIZE = 200
# HyDE 生成的假设文档最大长度
HYDE_MAX_TOKENS = 256


# 制造领域查询改写提示词
QUERY_REWRITE_PROMPT = """你是一个机械制造领域的查询改写专家。
请将用户的口语化查询改写为更适合向量检索的关键词组合，保留核心语义。

改写规则：
1. 提取关键技术术语（材料牌号、工艺类型、刀具类型等）
2. 补充相关同义词和近义词
3. 保持简洁，不超过 50 字
4. 不要回答问题，只输出改写后的查询

示例：
- 输入："钛合金怎么加工比较好"
  输出："TC4 钛合金切削参数 加工工艺 刀具选择 切削速度 进给量"

- 输入："钻头老断什么原因"
  输出："钻头断裂原因 钻孔工艺 切削力 刀具磨损 进给速度"

用户查询：{query}

改写后的查询："""

# HyDE 提示词
HYDE_PROMPT = """你是一个机械制造领域专家。
请根据用户问题，写一段简短的技术说明（约200字）作为假设性答案。
这个假设答案将用于语义检索，所以应该包含关键术语和技术细节。

用户问题：{query}

请直接输出技术说明，不要加前缀："""


class QueryRewriter:
    """查询改写与扩展服务。

    提供查询改写和 HyDE 两种增强方式，支持缓存。
    """

    def __init__(
        self,
        enable_rewrite: bool | None = None,
        enable_hyde: bool | None = None,
    ):
        if enable_rewrite is None:
            self.enable_rewrite = ENABLE_QUERY_REWRITE
        else:
            self.enable_rewrite = enable_rewrite
        if enable_hyde is None:
            self.enable_hyde = ENABLE_HYDE
        else:
            self.enable_hyde = enable_hyde

        self._rewrite_cache: dict[str, str] = {}
        self._rewrite_cache_keys: list[str] = []
        self._hyde_cache: dict[str, str] = {}
        self._hyde_cache_keys: list[str] = []
        self._lock = threading.Lock()
        self._llm_client = None
        # 命中统计（用于诊断）
        self._rewrite_hits = 0
        self._rewrite_misses = 0
        self._hyde_hits = 0
        self._hyde_misses = 0
        # 规则改写使用次数（fallback 频率监控）
        self._rule_rewrite_count = 0
        self._llm_rewrite_count = 0

    def _get_llm_client(self):
        """懒加载 LLM 客户端。

        注意：``get_llm_client`` 是协程函数（async def），必须通过
        ``_run_async`` 在同步上下文中执行，否则只会返回 coroutine 对象
        而非真正的 LLM 客户端实例。
        """
        if self._llm_client is not None:
            return self._llm_client
        try:
            from app.ai.llm_client import get_llm_client

            # get_llm_client 是 async，需在同步上下文中用 _run_async 解包
            client = _run_async(get_llm_client())
            self._llm_client = client
        except (ImportError, RuntimeError, OSError, ValueError) as e:
            logger.warning("Failed to load LLM client: %s", e)
            self._llm_client = None
        return self._llm_client

    def _cache_key(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _cache_get(self, cache: dict, keys: list, key: str) -> str | None:
        """LRU 读：命中时把 key 移到末尾。

        修复并发竞态：原实现在锁外执行 ``keys.remove`` / ``keys.append``，
        多线程并发读时可能抛 ``ValueError``（list.remove 找不到元素）
        或破坏 LRU 顺序。现统一在 ``self._lock`` 保护下操作。
        """
        with self._lock:
            if key in cache:
                keys.remove(key)
                keys.append(key)
                return cache[key]
        return None

    def _cache_set(self, cache: dict, keys: list, key: str, value: str, max_size: int):
        """LRU 写：已存在则更新值，否则淘汰最旧后插入。"""
        if key in cache:
            # 已存在则更新值（不需要重复添加到 keys）
            cache[key] = value
            return
        if len(keys) >= max_size:
            oldest = keys.pop(0)
            cache.pop(oldest, None)
        cache[key] = value
        keys.append(key)

    def rewrite_query(self, query: str) -> str:
        """查询改写：将口语化查询改写为关键词组合。

        Args:
            query: 原始查询

        Returns:
            改写后的查询；如果改写失败或未启用，返回原始查询
        """
        if not self.enable_rewrite or not query.strip():
            return query

        # 检查缓存
        key = self._cache_key(query)
        cached = self._cache_get(self._rewrite_cache, self._rewrite_cache_keys, key)
        if cached is not None:
            self._rewrite_hits += 1
            return cached
        self._rewrite_misses += 1

        # 尝试 LLM 改写
        llm = self._get_llm_client()
        if llm is None:
            # LLM 不可用时，使用规则改写
            rewritten = self._rule_based_rewrite(query)
            self._rule_rewrite_count += 1
        else:
            try:
                prompt = QUERY_REWRITE_PROMPT.format(query=query)
                result = _run_async(self._call_llm(llm, prompt))
                rewritten = result.strip() if result else query
                if not rewritten:
                    rewritten = query
                self._llm_rewrite_count += 1
            except Exception as e:  # LLM 调用可能抛出 LLMError/网络异常等，降级为规则改写
                logger.warning("LLM query rewrite failed: %s", e)
                rewritten = self._rule_based_rewrite(query)
                self._rule_rewrite_count += 1

        # 缓存
        with self._lock:
            self._cache_set(
                self._rewrite_cache,
                self._rewrite_cache_keys,
                key,
                rewritten,
                HYDE_CACHE_SIZE,
            )
        return rewritten

    def generate_hyde_document(self, query: str) -> str | None:
        """HyDE：让 LLM 生成假设性答案文档用于检索。

        Args:
            query: 用户查询

        Returns:
            LLM 生成的假设文档；如果未启用或失败，返回 None
        """
        if not self.enable_hyde or not query.strip():
            return None

        # 检查缓存
        key = self._cache_key(query)
        cached = self._cache_get(self._hyde_cache, self._hyde_cache_keys, key)
        if cached is not None:
            self._hyde_hits += 1
            return cached
        self._hyde_misses += 1

        llm = self._get_llm_client()
        if llm is None:
            return None

        try:
            prompt = HYDE_PROMPT.format(query=query)
            result = _run_async(self._call_llm(llm, prompt, max_tokens=HYDE_MAX_TOKENS))
            hyde_doc = result.strip() if result else None

            if hyde_doc:
                with self._lock:
                    self._cache_set(
                        self._hyde_cache,
                        self._hyde_cache_keys,
                        key,
                        hyde_doc,
                        HYDE_CACHE_SIZE,
                    )
            return hyde_doc

        except Exception as e:  # LLM 调用可能抛出 LLMError/网络异常等，HyDE 降级为 None
            logger.warning("HyDE generation failed: %s", e)
            return None

    async def _call_llm(self, llm: Any, prompt: str, max_tokens: int = 128) -> str:
        """异步调用 LLM。"""
        messages = [{"role": "user", "content": prompt}]
        response = await llm.chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.3,  # 低温度保证稳定性
        )
        # 兼容不同 LLM 客户端返回格式
        if isinstance(response, dict):
            return response.get("content", "") or response.get("response", "")
        return str(response) if response else ""

    @staticmethod
    def _rule_based_rewrite(query: str) -> str:
        """基于规则的查询改写（LLM 不可用时的 fallback）。

        1. 保留原文中的英文缩写和数字
        2. 为中文术语补充通用关键词
        """
        # 制造领域关键词映射
        keyword_map = {
            "钛合金": "TC4 钛合金 切削参数",
            "不锈钢": "不锈钢 HRC 切削速度",
            "铝合金": "铝合金 6061 切削参数",
            "刀具": "刀具 磨损 寿命",
            "磨损": "刀具磨损 寿命 监测",
            "振动": "振动 颤振 频域",
            "切削": "切削速度 进给量 切削深度",
            "钻孔": "钻孔 钻头 进给速度",
            "铣削": "铣削 立铣刀 面铣刀",
            "车削": "车削 车刀 切削参数",
        }

        result = query
        for keyword, expansion in keyword_map.items():
            if keyword in query and expansion not in result:
                result = f"{result} {expansion}"

        return result.strip()

    def get_stats(self) -> dict[str, Any]:
        """获取查询改写服务状态。"""
        rewrite_total = self._rewrite_hits + self._rewrite_misses
        hyde_total = self._hyde_hits + self._hyde_misses
        return {
            "enable_rewrite": self.enable_rewrite,
            "enable_hyde": self.enable_hyde,
            "rewrite_cache_size": len(self._rewrite_cache),
            "rewrite_cache_capacity": HYDE_CACHE_SIZE,
            "hyde_cache_size": len(self._hyde_cache),
            "hyde_cache_capacity": HYDE_CACHE_SIZE,
            "llm_available": self._llm_client is not None,
            # 命中统计
            "rewrite_hits": self._rewrite_hits,
            "rewrite_misses": self._rewrite_misses,
            "rewrite_hit_rate": (round(self._rewrite_hits / rewrite_total, 4) if rewrite_total > 0 else 0.0),
            "hyde_hits": self._hyde_hits,
            "hyde_misses": self._hyde_misses,
            "hyde_hit_rate": (round(self._hyde_hits / hyde_total, 4) if hyde_total > 0 else 0.0),
            # Fallback 频率监控
            "llm_rewrite_count": self._llm_rewrite_count,
            "rule_rewrite_count": self._rule_rewrite_count,
            "rule_fallback_rate": (
                round(
                    self._rule_rewrite_count / max(self._llm_rewrite_count + self._rule_rewrite_count, 1),
                    4,
                )
            ),
        }


# 线程安全懒加载单例


class _QueryRewriterHolder:
    """Thread-safe lazy holder for the :class:`QueryRewriter` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: QueryRewriter | None = None

    def get(self) -> QueryRewriter:
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is not None:
                return self._instance
            self._instance = QueryRewriter()
            logger.info("Initialized query rewriter service")
            return self._instance

    def reset(self) -> None:
        with self._lock:
            self._instance = None


_holder = _QueryRewriterHolder()


def get_query_rewriter() -> QueryRewriter:
    """获取共享的 :class:`QueryRewriter` 单例。"""
    return _holder.get()


__all__ = [
    "QueryRewriter",
    "get_query_rewriter",
    "ENABLE_QUERY_REWRITE",
    "ENABLE_HYDE",
]
