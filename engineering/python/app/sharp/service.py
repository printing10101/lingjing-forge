"""SHARP 服务单例（M5.2）。

组装 SHARP 4 大组件（StrategicPlanner + ToolRegistry + MemoryAugmentor +
ReActLoop），对外暴露统一的 `verify()` / `batch_verify()` 接口。

设计原则
--------
- **懒加载**：所有重型依赖（LLMRouter / KnowledgeGraphQueryAPI /
  RagRetrievalEngine）在首次调用时才装配，避免在 import 时阻塞
- **线程安全**：双检锁保护单例创建，所有公开方法都是线程安全的
- **配置驱动**：消融模式与 max_react_steps 等可运行时切换
- **容错降级**：KG / RAG / LLM 任意依赖不可用时仍能启动（仅注册可用工具）
- **接口对齐**：直接复用现有 ``KnowledgeGraphQueryAPI`` /
  ``RagRetrievalEngine`` / ``LLMRouter`` 接口，零侵入

被路由层调用
------------
- ``SharpService.instance()`` 获取单例
- ``await service.verify(triple, ablation_mode=None, max_steps=None)``
- ``await service.batch_verify(triples, ...)``
- ``service.set_ablation_mode(mode)``
- ``service.list_trajectories(...)`` / ``service.clear_trajectories()``
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from app.sharp.memory import (
    MemoryAugmentor,
    SimilarityRetriever,
    TrajectoryStore,
)
from app.sharp.react import ReActLoop, VerificationResult
from app.sharp.schema import (
    DEFAULT_SCHEMA,
    StrategicPlanner,
    Triple,
)
from app.sharp.tools import ToolRegistry
import time
import uuid

logger = logging.getLogger(__name__)


# 默认配置（M6 会通过 config.py 注入运行时配置）

DEFAULT_MAX_REACT_STEPS: int = 8
DEFAULT_CONFIDENCE_THRESHOLD: float = 0.85
DEFAULT_EVIDENCE_CONVERGENCE_WINDOW: int = 2
DEFAULT_MEMORY_TOP_K: int = 3
DEFAULT_LLM_MAX_TOKENS: int = 768
DEFAULT_LLM_TEMPERATURE: float = 0.3
VALID_ABLATION_MODES: frozenset[str | None] = frozenset({None, "no_schema", "no_memory", "no_react", "no_toolset"})


# SHARP 服务


class SharpService:
    """SHARP 三元组验证服务单例。

    Usage::

        service = SharpService.instance()
        result = await service.verify(triple)
    """

    _singleton: "SharpService" | None = None
    _lock = threading.Lock()

    # 单例入口

    @classmethod
    def instance(cls) -> "SharpService":
        """获取 SharpService 单例（双检锁）。"""
        if cls._singleton is not None:
            return cls._singleton
        with cls._lock:
            if cls._singleton is None:
                cls._singleton = cls()
            return cls._singleton

    def __init__(self) -> None:
        """初始化服务。直接 ``__init__`` 会绕过单例，请使用 ``instance()``。"""
        # 配置（运行时可修改）—— 先用模块级默认值兜底
        self._ablation_mode: str | None = None
        self._max_react_steps: int = DEFAULT_MAX_REACT_STEPS
        self._confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
        self._evidence_convergence_window: int = DEFAULT_EVIDENCE_CONVERGENCE_WINDOW
        self._memory_top_k: int = DEFAULT_MEMORY_TOP_K

        # 重型依赖（懒加载）
        self._llm_router: Any = None
        self._query_api: Any = None
        self._rag_engine: Any = None
        self._tool_registry: ToolRegistry | None = None
        self._strategic_planner: StrategicPlanner | None = None
        self._trajectory_store: TrajectoryStore | None = None
        self._memory_augmentor: MemoryAugmentor | None = None
        self._react_loop: ReActLoop | None = None

        # 标记依赖是否已尝试加载（避免重复尝试失败的依赖）
        self._deps_loaded: bool = False
        self._deps_lock = threading.Lock()

        # 异步并发锁：串行化单次覆盖路径，避免 pipeline 重建期间的竞态
        # （asyncio.Lock 不跨事件循环共享，SharpService 单例生命周期与 app 一致）
        self._verify_lock: asyncio.Lock = asyncio.Lock()

        # 从 AppConfig.sharp 读取运行时配置（容错：失败则沿用模块级默认值）
        self._load_config()

    # 配置加载

    def _load_config(self) -> None:
        """从 ``AppConfig.sharp`` 读取运行时配置覆盖默认值。

        优先级：环境变量 > 模块级常量默认值。``ablation_mode`` 优先级高于
        单独的 ``enable_*`` 开关；若 ``ablation_mode`` 为空，则根据
        ``enable_*`` 反推消融模式（任一关闭即对应消融）。

        容错：配置加载失败时仅记录警告，沿用模块级默认值，不阻塞启动。
        """
        try:
            from app.config import config

            cfg = config.sharp
            self._max_react_steps = cfg.max_react_steps
            self._confidence_threshold = cfg.confidence_threshold
            self._evidence_convergence_window = cfg.evidence_convergence_window
            self._memory_top_k = cfg.memory_top_k

            # 消融模式：ablation_mode 优先，否则根据 enable_* 反推
            if cfg.resolved_ablation_mode is not None:
                self._ablation_mode = cfg.resolved_ablation_mode
            else:
                self._ablation_mode = self._derive_ablation_from_toggles(cfg)

            logger.info(
                "SHARP config loaded | max_steps=%d | conf_thresh=%.2f | "
                "evidence_window=%d | memory_top_k=%d | ablation=%s",
                self._max_react_steps,
                self._confidence_threshold,
                self._evidence_convergence_window,
                self._memory_top_k,
                self._ablation_mode,
            )
        except Exception as e:
            logger.warning("SHARP: load SharpConfig failed, use module defaults: %s", e)

    @staticmethod
    def _derive_ablation_from_toggles(cfg: Any) -> str | None:
        """根据 ``enable_*`` 开关反推消融模式。

        优先级：no_react > no_toolset > no_memory > no_schema
        （更激进的消融优先，对齐论文消融实验场景）。

        Args:
            cfg: ``SharpConfig`` 实例

        Returns:
            消融模式字符串或 None（完整 SHARP）
        """
        if not cfg.enable_react_loop:
            return "no_react"
        if not cfg.enable_hybrid_toolset:
            return "no_toolset"
        if not cfg.enable_memory_augment:
            return "no_memory"
        if not cfg.enable_schema_planner:
            return "no_schema"
        return None

    # 依赖加载

    def _ensure_dependencies(self) -> None:
        """懒加载所有重型依赖（线程安全）。"""
        if self._deps_loaded:
            return
        with self._deps_lock:
            if self._deps_loaded:
                return
            self._load_llm_router()
            self._load_query_api()
            self._load_rag_engine()
            self._build_pipeline()
            self._deps_loaded = True

    def _load_llm_router(self) -> None:
        """加载 LLMRouter 单例。"""
        try:
            from app.ai.llm.router import get_router

            self._llm_router = get_router()
            logger.info("SHARP: LLMRouter loaded")
        except Exception as e:
            logger.warning("SHARP: LLMRouter load failed: %s", e)
            self._llm_router = None

    def _load_query_api(self) -> None:
        """加载 KnowledgeGraphQueryAPI 单例（复用 v1/knowledge_graph 的预热逻辑）。"""
        try:
            from app.api.v1.knowledge_graph import _get_query_api

            self._query_api = _get_query_api()
            logger.info("SHARP: KnowledgeGraphQueryAPI loaded")
        except Exception as e:
            logger.warning("SHARP: KnowledgeGraphQueryAPI load failed: %s", e)
            self._query_api = None

    def _load_rag_engine(self) -> None:
        """加载 RagRetrievalEngine 单例（复用 rag/service 的懒加载逻辑）。"""
        try:
            # 2026-08-20 修复：_get_rag_engine 实际定义在 app.rag.service（原误写 app.rag.routes）
            from app.rag.service import _get_rag_engine

            self._rag_engine = _get_rag_engine()
            logger.info("SHARP: RagRetrievalEngine loaded")
        except Exception as e:
            logger.warning("SHARP: RagRetrievalEngine load failed: %s", e)
            self._rag_engine = None

    def _build_pipeline(self) -> None:
        """根据当前配置与依赖构建/重建 SHARP pipeline。"""
        # 1. 战略规划器
        self._strategic_planner = StrategicPlanner(
            schema=DEFAULT_SCHEMA,
            max_react_steps=self._max_react_steps,
            confidence_threshold=self._confidence_threshold,
            evidence_convergence_window=self._evidence_convergence_window,
            ablation_mode=self._ablation_mode,
        )

        # 2. 工具注册中心（按消融模式跳过特定工具）
        # no_react 模式下仍注册工具（供无 ReAct 时的简化路径使用）
        self._tool_registry = ToolRegistry.create_default_registry(
            query_api=self._query_api,
            rag_engine=self._rag_engine,
            llm_router=self._llm_router,
            ablation_mode=self._ablation_mode,
        )

        # 3. 轨迹存储（始终创建，即使消融为 no_memory 也保留存储能力）
        if self._trajectory_store is None:
            self._trajectory_store = TrajectoryStore()

        # 4. Memory 增强器（no_memory 模式下禁用）
        memory_enabled = self._ablation_mode != "no_memory"
        self._memory_augmentor = MemoryAugmentor(
            trajectory_store=self._trajectory_store,
            similarity_retriever=SimilarityRetriever(),
            top_k=self._memory_top_k,
            enabled=memory_enabled,
        )

        # 5. ReAct 循环（no_react 模式下不构建，verify 会走简化路径）
        if self._ablation_mode == "no_react":
            self._react_loop = None
            logger.info("SHARP: ReAct loop disabled (no_react mode)")
        else:
            self._react_loop = ReActLoop(
                llm_router=self._llm_router,
                tool_registry=self._tool_registry,
                strategic_planner=self._strategic_planner,
                max_react_steps=self._max_react_steps,
                llm_max_tokens=DEFAULT_LLM_MAX_TOKENS,
                llm_temperature=DEFAULT_LLM_TEMPERATURE,
                memory_augmentor=self._memory_augmentor,
            )

        logger.info(
            "SHARP pipeline built | ablation=%s | tools=%d | memory=%s | react=%s",
            self._ablation_mode,
            self._tool_registry.size if self._tool_registry else 0,
            memory_enabled,
            self._react_loop is not None,
        )

    # 配置管理

    def set_ablation_mode(self, mode: str | None) -> None:
        """切换消融模式并重建 pipeline。"""
        if mode not in VALID_ABLATION_MODES:
            raise ValueError(f"ablation_mode 必须是 {VALID_ABLATION_MODES} 之一，实际: {mode}")
        if mode == self._ablation_mode and self._deps_loaded:
            return
        self._ablation_mode = mode
        if self._deps_loaded:
            self._build_pipeline()
        logger.info("SHARP ablation mode set: %s", mode)

    def set_max_react_steps(self, max_steps: int) -> None:
        """更新默认最大 ReAct 步数。"""
        if max_steps < 1 or max_steps > 20:
            raise ValueError(f"max_steps 必须在 [1, 20]，实际: {max_steps}")
        self._max_react_steps = max_steps
        if self._deps_loaded:
            self._build_pipeline()

    def get_ablation_mode(self) -> str | None:
        return self._ablation_mode

    def get_status(self) -> dict[str, Any]:
        """返回服务状态摘要。"""
        from app.sharp import __version__

        return {
            "version": __version__,
            "ablation_mode": self._ablation_mode,
            "enabled_components": {
                "schema_planner": self._ablation_mode != "no_schema",
                "memory_augment": self._ablation_mode != "no_memory",
                "hybrid_toolset": self._ablation_mode != "no_toolset",
                "react_loop": self._ablation_mode != "no_react",
            },
            "tool_registry_size": (self._tool_registry.size if self._tool_registry else 0),
            "trajectory_count": (self._trajectory_store.count() if self._trajectory_store else 0),
        }

    # 验证入口

    async def verify(
        self,
        triple: Triple,
        ablation_mode: str | None = None,
        max_react_steps: int | None = None,
    ) -> VerificationResult:
        """验证单个三元组。

        Args:
            triple: 待验证的三元组
            ablation_mode: 单次消融模式覆盖（None 使用服务端默认）
            max_react_steps: 单次 max_steps 覆盖

        Returns:
            VerificationResult

        并发安全
        --------
        - 单次消融覆盖路径通过 ``self._verify_lock`` 串行化，避免 pipeline 重建
          期间其他协程读到中间态。代价是同一时刻只能跑 1 条覆盖请求。
        - max_steps 覆盖不修改共享实例，而是构造临时副本，无锁开销。
        - 默认路径（无覆盖）完全无锁，可并发执行。
        """
        self._ensure_dependencies()

        # 处理单次覆盖：加锁串行化，避免 pipeline 重建竞态
        if ablation_mode is not None and ablation_mode != self._ablation_mode:
            if ablation_mode not in VALID_ABLATION_MODES:
                raise ValueError(f"ablation_mode 必须是 {VALID_ABLATION_MODES} 之一")
            async with self._verify_lock:
                original_mode = self._ablation_mode
                try:
                    self._build_pipeline_with_override(ablation_mode, max_react_steps)
                    result = await self._run_verify(triple)
                finally:
                    self._build_pipeline_with_override(original_mode, None)
                return result

        # 单次 max_steps 覆盖：构造临时 pipeline 副本，不修改共享实例
        if max_react_steps is not None and self._react_loop is not None:
            return await self._run_verify_with_max_steps(triple, max_react_steps)

        return await self._run_verify(triple)

    async def _run_verify_with_max_steps(self, triple: Triple, max_react_steps: int) -> VerificationResult:
        """单次 max_steps 覆盖的隔离执行路径。

        通过 ``verify(max_steps_override=...)`` 参数传入覆盖值，无需构造
        临时 ReActLoop 副本——override 是单次调用参数，不会影响共享实例
        的 ``default_max_steps``，因此无并发竞态风险。
        Memory 存储复用 ``self._memory_augmentor``。
        """
        if self._react_loop is None:
            return await self._verify_without_react(triple)

        # 直接调用共享实例，通过 max_steps_override 控制本次循环上限
        result = await self._react_loop.verify(triple, max_steps_override=max_react_steps)

        # 存储到 Memory
        if self._memory_augmentor is not None:
            try:
                self._memory_augmentor.store(result)
            except Exception as e:
                logger.warning("SHARP: trajectory store failed: %s", e)

        return result

    async def _run_verify(self, triple: Triple) -> VerificationResult:
        """实际执行验证（含 no_react 降级路径）。"""
        if self._react_loop is None:
            # no_react 模式：直接调用 LLM 推理工具
            return await self._verify_without_react(triple)

        result = await self._react_loop.verify(triple)

        # 存储到 Memory（即使消融为 no_memory，存储本身不报错）
        if self._memory_augmentor is not None:
            try:
                self._memory_augmentor.store(result)
            except Exception as e:
                logger.warning("SHARP: trajectory store failed: %s", e)

        return result

    async def _verify_without_react(self, triple: Triple) -> VerificationResult:
        """no_react 模式的简化验证路径：直接调用 LLM 推理 + Schema 校验。

        对应论文消融实验 §5.3：去除 ReAct 循环后，仅靠单次 LLM 推理验证。
        """
        from app.sharp.tools.base import ToolCall

        verification_id = f"ver_{uuid.uuid4().hex[:12]}"
        start_time = time.perf_counter()

        # 1. Schema 校验
        schema_valid = DEFAULT_SCHEMA.is_valid_relation(triple.head_type, triple.relation, triple.tail_type)

        # 2. 构造 LLMReasonTool 所需的自然语言输入
        # 注意：LLMReasonTool._execute 期望参数 triple_text / evidence_summary / focus_dimensions
        # （非 triple / schema_valid），参数名不匹配会导致 ValueError
        triple_text = (
            f"{triple.head_type.value}({triple.head_id}) "
            f"{triple.relation.value} "
            f"{triple.tail_type.value}({triple.tail_id})"
        )
        evidence_summary = (
            f"Schema 校验: {'通过' if schema_valid else '不通过'}；无 ReAct 循环，未收集外部证据（KG/RAG 工具未触发）"
        )

        # 3. 调用 LLM 推理工具
        confidence = 0.0
        reasoning = "no_react 模式：未调用 LLM（无可用 LLM 路由器或工具未注册）"
        verdict = "uncertain"

        if self._llm_router is not None and self._tool_registry is not None:
            llm_tool = self._tool_registry.get("llm.reason")
            if llm_tool is not None:
                try:
                    tool_result = await llm_tool.execute(
                        ToolCall(
                            tool_name="llm.reason",
                            arguments={
                                "triple_text": triple_text,
                                "evidence_summary": evidence_summary,
                            },
                        )
                    )
                    if tool_result.success and isinstance(tool_result.output, dict):
                        confidence = float(tool_result.output.get("confidence", 0.0))
                        verdict = tool_result.output.get("verdict", "uncertain")
                        reasoning = tool_result.output.get("reasoning", "")
                    elif not tool_result.success:
                        # 工具内部异常（如 LLM 调用失败、JSON 解析失败）
                        reasoning = f"no_react 模式 LLM 推理失败: {tool_result.error}"
                        logger.warning(
                            "SHARP no_react: llm.reason tool failed: %s",
                            tool_result.error,
                        )
                except Exception as e:
                    # execute() 内部已捕获异常，此处兜底防御
                    reasoning = f"no_react 模式 LLM 推理异常: {type(e).__name__}: {e}"
                    logger.warning("SHARP no_react: llm.reason execute raised: %s", e)
            else:
                reasoning = "no_react 模式：llm.reason 工具未注册到 tool_registry"
                logger.warning("SHARP no_react: llm.reason tool not found in registry")
        else:
            # 无 LLM 可用时，仅依赖 Schema 校验
            if schema_valid:
                confidence = 0.5
                verdict = "uncertain"
                reasoning = "no_react 模式且无 LLM 可用，仅完成 Schema 校验"
            else:
                confidence = 0.1
                verdict = "refuted"
                reasoning = "Schema 校验不通过（无 LLM 复核）"

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        return VerificationResult(
            triple=triple,
            verdict=verdict,
            confidence=round(confidence, 4),
            reasoning=reasoning,
            trajectory=[],
            evidence_chain=[],
            strategy={"ablation_mode": "no_react", "schema_valid": schema_valid},
            stopping_decision={
                "trigger": "no_react_mode",
                "reason": "消融模式：no_react",
            },
            verification_id=verification_id,
            elapsed_ms=elapsed_ms,
            steps_taken=0,
        )

    def _build_pipeline_with_override(
        self,
        ablation_mode: str | None,
        max_react_steps: int | None,
    ) -> None:
        """临时切换消融模式与 max_steps 后重建 pipeline。"""
        original_mode = self._ablation_mode
        original_max = self._max_react_steps
        try:
            self._ablation_mode = ablation_mode
            if max_react_steps is not None:
                self._max_react_steps = max_react_steps
            self._build_pipeline()
        except Exception:
            # 出错时回滚
            self._ablation_mode = original_mode
            self._max_react_steps = original_max
            self._build_pipeline()
            raise

    async def batch_verify(
        self,
        triples: list[Triple],
        ablation_mode: str | None = None,
        max_react_steps: int | None = None,
    ) -> list[tuple[int, VerificationResult, str | None]]:
        """批量验证三元组。

        Returns:
            list of (index, result, error)。成功时 error 为 None。
        """
        results: list[tuple[int, VerificationResult, str | None]] = []
        for idx, triple in enumerate(triples):
            try:
                result = await self.verify(
                    triple,
                    ablation_mode=ablation_mode,
                    max_react_steps=max_react_steps,
                )
                results.append((idx, result, None))
            except Exception as e:
                logger.warning("SHARP batch verify[%d] failed: %s", idx, e)
                results.append((idx, None, str(e)))  # type: ignore[arg-type]
        return results

    # 轨迹管理

    def list_trajectories(
        self,
        limit: int = 50,
        verdict: str | None = None,
        relation: str | None = None,
    ) -> list[Any]:
        """查询历史轨迹（带过滤）。"""
        if self._trajectory_store is None:
            self._ensure_dependencies()
        if self._trajectory_store is None:
            return []
        records = self._trajectory_store.list_all()
        # 过滤
        if verdict:
            records = [r for r in records if r.verdict == verdict]
        if relation:
            records = [r for r in records if r.triple.get("relation") == relation]
        # 截断
        return records[-limit:] if limit < len(records) else list(records)

    def get_trajectory(self, verification_id: str) -> Any | None:
        """按 ID 取单条轨迹。"""
        if self._trajectory_store is None:
            self._ensure_dependencies()
        if self._trajectory_store is None:
            return None
        return self._trajectory_store.get(verification_id)

    def clear_trajectories(self) -> int:
        """清空轨迹库。返回被清除的记录数。"""
        if self._trajectory_store is None:
            self._ensure_dependencies()
        if self._trajectory_store is None:
            return 0
        return self._trajectory_store.clear()


__all__ = ["SharpService"]
