"""Dreaming 反思核心：去重 / 过时更新 / 洞察浮现。

反思任务（Dream Job）的输入输出约定：
    输入：Memory Store + 最多 100 个 Sessions
    输出：全新 Memory Store（不可变） + Reflection Report

本地化实现：
    - LLM 反思通过 ProviderRouter 路由到本地 LLM（Ollama/LM Studio）
    - 反思决策写入 GraphStore + Git 不可变版本
    - 硬约束：CAM 二次验证始终 True、SUCCEEDED 禁删、HRC52 降置信

反思三阶段：
    1. 去重（deduplicate）：合并重复 memory 条目
    2. 过时更新（update stale）：用新 Session 修正旧 memory
    3. 洞察浮现（surface insights）：跨 Session 发现潜在规律

本模块为门面：实现已拆分至 _reflector_models / _dedup_mixin / _update_mixin / _insights_mixin。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

# H12 修复：原代码在模块导入期临时篡改全局 sys.platform="linux" 以绕过
# Windows asyncio Proactor 限制，但这是线程不安全的——其他线程在此时读取
# sys.platform 会得到错误值。正确做法是显式设置事件循环策略。
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except (AttributeError, RuntimeError):
        # 在已存在事件循环的上下文中调用可能失败，忽略即可。
        pass

from app.dreaming.memory_store import LocalMemoryStore
from app.dreaming.session_extractor import ProjectSession
from app.dreaming._dedup_mixin import _DedupMixin
from app.dreaming._insights_mixin import _InsightsMixin
from app.dreaming._reflector_models import (  # noqa: F401
    DeduplicationResult,
    InsightItem,
    ReflectionResult,
    UpdateResult,
)
from app.dreaming._update_mixin import _UpdateMixin

logger = logging.getLogger(__name__)


class DreamReflector(_DedupMixin, _UpdateMixin, _InsightsMixin):
    """离线反思核心引擎。

    用法：
        reflector = DreamReflector(memory_store=..., repo_root="...")
        result = await reflector.reflect(sessions, instructions="...")
        # result.new_memory_version 是 Git commit hash
    """

    def __init__(
        self,
        memory_store: LocalMemoryStore,
        repo_root: str | None = None,
        enable_llm: bool = True,
    ) -> None:
        self.store = memory_store
        self.repo_root = repo_root
        self.enable_llm = enable_llm
        self._llm_router: Any = None  # 延迟初始化，避免启动时强依赖

    def _get_llm_router(self):
        """延迟获取 ProviderRouter 单例。"""
        if self._llm_router is not None:
            return self._llm_router
        try:
            from app.ai.llm.router import get_router

            self._llm_router = get_router()
        except ImportError as e:
            logger.warning("ProviderRouter 不可用，LLM 反思将降级为规则模式: %s", e)
            self._llm_router = None
        return self._llm_router

    # 主入口

    async def reflect(
        self,
        sessions: list[ProjectSession],
        instructions: str | None = None,
    ) -> ReflectionResult:
        """执行完整反思流程。

        Args:
            sessions: 待反思的 Session 列表（已归一化）
            instructions: 反思指令（如 "重点关注 HRC52 的进给速率异常"）

        Returns:
            ReflectionResult，包含去重/更新/洞察三阶段结果
        """
        logger.info(
            "Dreaming 反思启动：sessions=%d, instructions=%s",
            len(sessions),
            instructions or "(默认)",
        )

        # 阶段 1：去重
        dedup_result = self._deduplicate_memories()

        # 阶段 2：过时更新
        update_result = self._update_stale_entries(sessions)

        # 阶段 3：洞察浮现
        insights, llm_used, llm_model = await self._surface_insights(sessions, instructions)

        # 生成不可变版本
        new_version = None
        try:
            version = self.store.commit_version(
                message=f"dream: reflect {len(sessions)} sessions, {len(insights)} insights"
            )
            new_version = version.version_id
        except Exception as e:
            logger.warning("Memory version 提交失败: %s", e)

        # 生成摘要
        summary = self._generate_summary(sessions, dedup_result, update_result, insights)

        result = ReflectionResult(
            deduplicated=dedup_result,
            updated=update_result,
            insights=insights,
            new_memory_version=new_version,
            summary=summary,
            llm_used=llm_used,
            llm_model=llm_model,
        )

        logger.info(
            "Dreaming 反思完成：dedup=%d, updated=%d, insights=%d, version=%s",
            dedup_result.merged_count,
            len(update_result.updated_node_ids),
            len(insights),
            new_version or "(none)",
        )

        return result


# 阶段 1：去重
