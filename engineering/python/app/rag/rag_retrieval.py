"""
RAG 检索规则引擎

实现多源知识库的分层检索策略：
1. 本地项目数据（Bosch CNC）优先
2. Uniwear 数据集其次
3. ChromaDB 向量库最终检索

增强功能（v2）：
- 多源并行检索（ThreadPoolExecutor，避免串行阻塞）
- 检索结果 LRU 缓存（query → results）
- 集成查询改写（Query Rewriting / HyDE）
- 集成混合检索（BM25 + Vector RRF 融合）
- 集成 Cross-Encoder 重排序
"""

import hashlib
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# 配置开关（通过环境变量控制，便于灰度发布与 A/B 测试）

# 是否启用多源并行检索（默认开启）
ENABLE_PARALLEL_RETRIEVAL = os.getenv("ENABLE_PARALLEL_RETRIEVAL", "1") == "1"
# 并行检索最大线程数
PARALLEL_RETRIEVAL_WORKERS = int(os.getenv("PARALLEL_RETRIEVAL_WORKERS", "4"))
# 是否启用检索结果缓存（默认开启）
ENABLE_RESULT_CACHE = os.getenv("ENABLE_RESULT_CACHE", "1") == "1"
# 结果缓存大小（条目数）
RESULT_CACHE_SIZE = int(os.getenv("RESULT_CACHE_SIZE", "200"))
# 是否启用混合检索（BM25 + Vector 融合）
ENABLE_HYBRID_SEARCH = os.getenv("ENABLE_HYBRID_SEARCH", "1") == "1"
# 是否启用 Cross-Encoder 重排序
ENABLE_RERANKER = os.getenv("ENABLE_RERANKER", "1") == "1"
# 是否启用查询改写
ENABLE_QUERY_REWRITE = os.getenv("ENABLE_QUERY_REWRITE", "1") == "1"
# 是否启用 HyDE（默认关闭，需要 LLM 调用，有额外延迟）
ENABLE_HYDE = os.getenv("ENABLE_HYDE", "0") == "1"


# 修复：拆分后 INTENT_KEYWORDS / RETRIEVAL_RULES 迁入 _retrieval_models.py，
# 此处必须一并导入（本文件 L77/L81/L86 引用，缺失会 ImportError——2026-08-03 安装验证发现）
from ._retrieval_models import (
    INTENT_KEYWORDS,
    RETRIEVAL_RULES,
    QueryIntent,
    RetrievalRule,
    _ResultCache,
)


class RagRetrievalEngine:
    """RAG 检索规则引擎

    根据用户查询意图，自动选择检索策略，按优先级分层检索。

    v2 增强：
    - 多源并行检索（ThreadPoolExecutor）
    - 检索结果 LRU 缓存
    - 集成查询改写、混合检索、重排序
    """

    def __init__(self, knowledge_base, signal_fusion_kb=None):
        """初始化 RAG 检索引擎。

        Args:
            knowledge_base: KnowledgeBase 实例（ChromaDB 后端文档检索）
            signal_fusion_kb: 可选的 SignalFusionKnowledgeBase 实例。
                集成点 2：显式依赖注入，避免之前通过共享 VectorStore 单例的隐式耦合。
                提供后，检索路径可路由到 signal_fusion source 的样本，
                并支持 retrieve_from_signal_fusion() 直接委托检索。
                未提供时降级为旧行为（仅靠 source_filter 触发 ChromaDB 检索）。
        """
        self.kb = knowledge_base
        self.signal_fusion_kb = signal_fusion_kb
        self.rules = RETRIEVAL_RULES
        # 预计算小写关键词，避免每次查询都转换
        self._intent_keywords_lower = {
            intent: [kw.lower() for kw in keywords] for intent, keywords in INTENT_KEYWORDS.items()
        }
        # 预计算 keyword_boost 的小写版本
        self._keyword_boost_lower = {
            intent: {kw.lower(): boost for kw, boost in rule.keyword_boost.items()}
            for intent, rule in RETRIEVAL_RULES.items()
        }
        # 结果缓存
        self._cache = _ResultCache()

        # 增强模块（懒加载，避免在导入时初始化重型组件）
        self._query_rewriter = None
        self._hybrid_engine = None
        self._reranker = None
        self._enhancement_loaded = False

    def _load_enhancements(self):
        """懒加载增强模块，避免在 __init__ 中触发重型依赖。"""
        if self._enhancement_loaded:
            return
        self._enhancement_loaded = True

        # 查询改写器
        if ENABLE_QUERY_REWRITE or ENABLE_HYDE:
            try:
                from app.rag.query_rewriter import get_query_rewriter

                self._query_rewriter = get_query_rewriter()
            except (ImportError, RuntimeError) as e:
                logger.warning("Failed to load query rewriter: %s", e)
                self._query_rewriter = None

        # 混合检索引擎
        if ENABLE_HYBRID_SEARCH:
            try:
                from app.rag.hybrid_search import get_hybrid_search_engine

                self._hybrid_engine = get_hybrid_search_engine()
            except (ImportError, RuntimeError) as e:
                logger.warning("Failed to load hybrid search engine: %s", e)
                self._hybrid_engine = None

        # 重排序器
        if ENABLE_RERANKER:
            try:
                from app.rag.reranker import get_reranker_service

                self._reranker = get_reranker_service()
            except (ImportError, RuntimeError) as e:
                logger.warning("Failed to load reranker service: %s", e)
                self._reranker = None

    def detect_intent(self, query: str) -> QueryIntent:
        query_lower = query.lower()

        scored_intents: dict[QueryIntent, int] = {}
        for intent, keywords_lower in self._intent_keywords_lower.items():
            score = sum(1 for kw in keywords_lower if kw in query_lower)
            if score > 0:
                scored_intents[intent] = score

        if not scored_intents:
            return QueryIntent.GENERAL

        return max(scored_intents, key=lambda k: scored_intents[k])

    def retrieve(
        self,
        query: str,
        intent: QueryIntent | None = None,
        n_results: int = 5,
        override_source: str | None = None,
    ) -> dict:
        """执行增强检索 pipeline（v3：分级路由 + entity 索引扩展 + cluster_tag 过滤）。

        Pipeline 分级：
        - fast: 跳过查询改写/HyDE/混合检索/重排序，仅向量检索+关键词 boost
        - standard: 向量检索 + 混合检索融合 + 重排序
        - full: 全部增强（含 HyDE）

        Pipeline 流程：
        1. 意图检测（先于改写，用于决定 pipeline_level）
        2. 查询改写（standard/full）
        3. HyDE 文档生成（full only）
        4. 多源并行检索（含 cluster_tag 元数据过滤）
        5. entity 倒排索引扩展检索
        6. 混合检索融合 RRF（standard/full）
        7. Cross-Encoder 重排序（standard/full）
        8. 关键词 boost 调整（fast 或 reranker 不可用时）
        """
        self._load_enhancements()

        # 检查缓存
        cached = self._cache.get(query, intent, n_results, override_source)
        if cached is not None:
            return {**cached, "_cache_hit": True}

        # 1. 意图检测（先于改写，以便根据 rule.pipeline_level 决定后续步骤）
        if intent is None:
            intent = self.detect_intent(query)

        rule = self.rules.get(intent, self.rules[QueryIntent.GENERAL])
        actual_n = rule.n_results or n_results
        pipeline_level = rule.pipeline_level
        use_enhancements = pipeline_level != "fast"

        # 2. 查询改写（fast 跳过）
        search_query = query
        rewrite_info: dict = {"original": query, "rewritten": None, "hyde": None}
        if use_enhancements and self._query_rewriter is not None:
            try:
                rewritten = self._query_rewriter.rewrite_query(query)
                if rewritten and rewritten.strip() and rewritten.strip() != query.strip():
                    search_query = rewritten
                    rewrite_info["rewritten"] = rewritten
            except Exception as e:  # 增强功能可降级，不阻断主检索
                logger.debug("Query rewrite skipped: %s", e)

        # 3. HyDE：生成假设文档用于向量检索（仅 full pipeline）
        hyde_doc = None
        if pipeline_level == "full" and self._query_rewriter is not None and ENABLE_HYDE:
            try:
                hyde_doc = self._query_rewriter.generate_hyde_document(query)
                if hyde_doc:
                    rewrite_info["hyde"] = hyde_doc[:200]
            except Exception as e:  # 增强功能可降级，不阻断主检索
                logger.debug("HyDE generation skipped: %s", e)

        # 构建 metadata 过滤（rule.metadata_filters + cluster_tag 映射）
        metadata_where = self._build_metadata_filter(rule)

        # 复制 source_filters，避免修改原 rule
        source_filters = list(rule.source_filters)
        if override_source:
            source_filters = [override_source]

        # HyDE 文档作为向量查询输入（如果有）
        vector_query = hyde_doc if hyde_doc else search_query

        # 4. 多源并行检索（带 cluster_tag 元数据过滤）
        results: list[dict] = []
        if source_filters:
            if ENABLE_PARALLEL_RETRIEVAL and len(source_filters) > 1:
                results = self._parallel_source_query(
                    vector_query,
                    source_filters,
                    actual_n,
                    where_filters=metadata_where,
                )
            else:
                for source in source_filters:
                    try:
                        source_results = self._query_source(
                            query=vector_query,
                            source=source,
                            n_results=actual_n,
                            where_filters=metadata_where,
                        )
                        for r in source_results:
                            r["_retrieval_source_filter"] = source
                        results.extend(source_results)
                    except (OSError, RuntimeError, ValueError, KeyError) as e:
                        logger.warning(
                            "Source query failed for %s: %s",
                            source,
                            e,
                            exc_info=True,
                        )

        # 5. entity 倒排索引扩展检索（跨源补充精确匹配的 chunk）
        entity_expansion_used = False
        if rule.use_entity_index:
            query_entities = _extract_query_entities(query)
            if query_entities:
                try:
                    entity_results = self._query_by_entities(query_entities)
                    for r in entity_results:
                        r["_retrieval_source_filter"] = "entity_index"
                    results.extend(entity_results)
                    entity_expansion_used = True
                except (RuntimeError, OSError, ValueError, KeyError) as e:
                    logger.debug("Entity index expansion skipped: %s", e)

        # fallback：结果不足时补充通用检索
        if not results or len(results) < 3:
            fallback = self._query_general(vector_query, n_results=actual_n)
            for r in fallback:
                r["_retrieval_source_filter"] = "fallback"
            results.extend(fallback)

        # 去重
        deduplicated = self._deduplicate(results)

        # 6. 混合检索融合（RRF）（fast 跳过）
        hybrid_used = False
        if use_enhancements and self._hybrid_engine is not None and ENABLE_HYBRID_SEARCH:
            try:
                # 用原始查询（非 HyDE）做 BM25 检索
                fused = self._hybrid_engine.search(
                    query=search_query,
                    vector_results=deduplicated,
                    top_k=actual_n * 2,
                )
                if fused:
                    deduplicated = fused
                    hybrid_used = True
            except (RuntimeError, ValueError, OSError) as e:
                logger.debug("Hybrid search fusion skipped: %s", e)

        # 截取到 2 倍 actual_n，留给 reranker 处理
        candidates = deduplicated[: actual_n * 2]

        # 7. Cross-Encoder 重排序（fast 跳过）
        rerank_used = False
        if use_enhancements and self._reranker is not None and ENABLE_RERANKER and candidates:
            try:
                reranked = self._reranker.rerank(
                    query=search_query,
                    results=candidates,
                    top_k=actual_n,
                )
                if reranked:
                    candidates = reranked
                    rerank_used = True
            except (RuntimeError, ValueError, OSError) as e:
                logger.debug("Reranking skipped: %s", e)

        # 8. 关键词 boost 调整（fast 或 reranker 不可用时）
        if not rerank_used:
            candidates = self._rerank_by_keywords(candidates, search_query, rule.keyword_boost)

        final_results = candidates[:actual_n]

        result = {
            "query": query,
            "search_query_used": search_query,
            "detected_intent": intent.value,
            "rule_priority": rule.priority,
            "pipeline_level": pipeline_level,
            "source_filters_applied": source_filters,
            "metadata_filters_applied": metadata_where,
            "total_found": len(results),
            "results_returned": len(final_results),
            "results": final_results,
            "enhancements": {
                "query_rewrite": rewrite_info["rewritten"] is not None,
                "hyde": rewrite_info["hyde"] is not None,
                "hybrid_search": hybrid_used,
                "reranker": rerank_used,
                "entity_index": entity_expansion_used,
                "pipeline_level": pipeline_level,
            },
            "rewrite_info": rewrite_info,
            "_cache_hit": False,
        }

        # 写入缓存
        self._cache.put(query, intent, n_results, override_source, result)

        return result

    @staticmethod
    def _build_metadata_filter(rule: RetrievalRule) -> dict | None:
        """合并 rule.metadata_filters 与 cluster_tag 映射，生成 ChromaDB where 子句。

        Returns:
            None 表示无过滤；dict 为 ChromaDB where 条件。
        """
        filters: dict = {}
        # rule.metadata_filters（修复历史遗漏：之前定义了但未使用）
        if rule.metadata_filters:
            filters.update(rule.metadata_filters)
        # cluster_tag 映射
        if rule.cluster_tag:
            tag_filter = _CLUSTER_TAG_FILTERS.get(rule.cluster_tag, {})
            filters.update(tag_filter)
        return filters if filters else None

    def _query_by_entities(self, entities: list[str]) -> list[dict]:
        """通过 entity 倒排索引检索文档，返回格式与 _parse_chroma_result 一致。

        entity 精确匹配的 chunk 作为向量检索的补充，提升跨源召回率。
        """
        try:
            raw = self.kb.query_by_entities(entities, mode="union")
        except (OSError, RuntimeError, ValueError, KeyError) as e:
            logger.debug("Entity query failed: %s", e, exc_info=True)
            return []
        # KnowledgeBase.query_by_entities 返回 {"documents": [...], "total_results": int}
        docs = raw.get("documents", []) if isinstance(raw, dict) else (raw or [])
        # 每个 doc 已经是 {id, document, metadata, distance} 格式，直接返回
        return list(docs)

    def _parallel_source_query(
        self,
        query: str,
        sources: list[str],
        n_results: int,
        where_filters: dict | None = None,
    ) -> list[dict]:
        """使用线程池并行查询多个 source。

        ChromaDB 的 query_by_source 是同步阻塞调用，
        使用 ThreadPoolExecutor 可真正并行执行 I/O。
        """
        results: list[dict] = []
        errors: list[tuple[str, Exception]] = []

        with ThreadPoolExecutor(max_workers=min(len(sources), PARALLEL_RETRIEVAL_WORKERS)) as executor:
            future_to_source = {
                executor.submit(self._query_source, query, source, n_results, where_filters): source
                for source in sources
            }
            for future in as_completed(future_to_source):
                source = future_to_source[future]
                try:
                    source_results = future.result(timeout=RAG_SOURCE_QUERY_TIMEOUT_SEC)
                    for r in source_results:
                        r["_retrieval_source_filter"] = source
                    results.extend(source_results)
                except (OSError, RuntimeError, ValueError, KeyError, TimeoutError) as e:
                    errors.append((source, e))
                    logger.warning(
                        "Parallel source query failed for %s: %s",
                        source,
                        e,
                        exc_info=True,
                    )

        if errors:
            logger.info(
                "Parallel retrieval completed with %d/%d source failures",
                len(errors),
                len(sources),
            )

        return results

    def retrieve_by_material(self, material: str, query: str, n_results: int = 5) -> dict:
        # 学术诚信修复：统一子串匹配，原 material.upper()=="TC4" 精确匹配漏匹配"TC4钛合金"等
        if "TC4" in material.upper() or "钛" in material:
            intent = QueryIntent.MATERIAL_WEAR
            override = "uniwear-nuaa"
        elif "HRC52" in material.upper() or "不锈钢" in material:
            intent = QueryIntent.CUTTING_PARAMS
            override = "uniwear-phm2010"
        else:
            intent = QueryIntent.GENERAL
            override = None

        return self.retrieve(query=query, intent=intent, n_results=n_results, override_source=override)

    def retrieve_by_signal_type(self, signal_type: str, query: str, n_results: int = 5) -> dict:
        if "vibration" in signal_type.lower() or "振动" in signal_type:
            intent = QueryIntent.VIBRATION_WEAR
        else:
            intent = QueryIntent.GENERAL

        return self.retrieve(query=query, intent=intent, n_results=n_results)

    def retrieve_from_signal_fusion(
        self,
        features: list[float] | None = None,
        signal_type: str | None = None,
        machine_id: str | None = None,
        material: str | None = None,
        tool_id: int | None = None,
        top_k: int = 10,
        query: str | None = None,
    ) -> dict:
        """集成点 2：直接委托 SignalFusionKnowledgeBase 检索多源信号样本。

        之前 RagRetrievalEngine 与 SignalFusionKnowledgeBase 通过共享
        VectorStore 单例（同一 ``knowledge_base`` ChromaDB 集合）隐式耦合：
        - 两者使用同一 VectorStore，但 RagRetrievalEngine 不知道后者存在；
        - signal_fusion source 的样本对 RAG 检索完全不可见。

        本方法通过显式依赖注入（构造时传入 signal_fusion_kb）打通两条路径：
        1. 提供 9 维 features 时走 SignalFusionKnowledgeBase.retrieve_similar
           （向量相似度检索，返回 SignalSample 列表）；
        2. 仅提供 query 文本时，从 query 中提取信号相关实体（vibration/RMS 等），
           再调用 retrieve_similar（features=None 时返回最近样本）。

        Args:
            features: 9 维信号特征向量（与 FeatureExtractor 对齐）
            signal_type: 可选信号类型过滤
                (vibration/cutting_force/temperature/acoustic_emission/current)
            machine_id: 可选机床 ID 过滤
            material: 可选材料过滤
            tool_id: 可选刀具 ID 过滤
            top_k: 返回前 K 个
            query: 可选文本查询（用于日志与降级时走通用 RAG 检索）

        Returns:
            dict，含 samples（SignalSample.to_dict 列表）、source、query 等
        """
        # 降级路径：未注入 signal_fusion_kb 时走通用 RAG 检索
        if self.signal_fusion_kb is None:
            logger.debug("signal_fusion_kb 未注入，retrieve_from_signal_fusion 降级到通用 RAG 检索")
            fallback = self.retrieve(
                query=query or "signal_fusion",
                intent=QueryIntent.SIGNAL_FUSION,
                n_results=top_k,
                override_source="signal_fusion",
            )
            return {
                **fallback,
                "degraded": True,
                "degradation_reason": "signal_fusion_kb not injected",
            }

        # 显式依赖路径：委托给 SignalFusionKnowledgeBase
        try:
            # features 缺失时构造零向量占位（retrieve_similar 仍可基于 metadata 过滤返回样本）
            query_features = features if features else [0.0] * 9
            samples = self.signal_fusion_kb.retrieve_similar(
                features=query_features,
                signal_type=signal_type,
                machine_id=machine_id,
                material=material,
                tool_id=tool_id,
                top_k=top_k,
            )
        except (RuntimeError, OSError, ValueError, KeyError) as e:
            logger.warning(
                "SignalFusionKnowledgeBase.retrieve_similar 失败，降级到通用 RAG: %s",
                e,
                exc_info=True,
            )
            fallback = self.retrieve(
                query=query or "signal_fusion",
                intent=QueryIntent.SIGNAL_FUSION,
                n_results=top_k,
                override_source="signal_fusion",
            )
            return {
                **fallback,
                "degraded": True,
                "degradation_reason": f"retrieve_similar failed: {e}",
            }

        return {
            "query": query or "",
            "source": "signal_fusion",
            "signal_type_filter": signal_type,
            "machine_id_filter": machine_id,
            "material_filter": material,
            "tool_id_filter": tool_id,
            "features_query_provided": features is not None,
            "total_found": len(samples),
            "results_returned": len(samples),
            "samples": [s.to_dict() for s in samples],
            "degraded": False,
        }

    def retrieve_cross_source(
        self,
        query: str,
        sources: list[str] | None = None,
        n_results: int = 10,
    ) -> dict:
        if sources is None:
            sources = ["bosch_cnc", "uniwear-nuaa", "uniwear-phm2010"]

        self._load_enhancements()

        # 查询改写
        search_query = query
        if self._query_rewriter is not None:
            try:
                rewritten = self._query_rewriter.rewrite_query(query)
                if rewritten and rewritten.strip():
                    search_query = rewritten
            except (RuntimeError, OSError, ValueError) as e:
                logger.debug("Cross-source query rewrite skipped: %s", e)

        if ENABLE_PARALLEL_RETRIEVAL and len(sources) > 1:
            all_results = self._parallel_source_query(search_query, sources, n_results)
        else:
            all_results = []
            for source in sources:
                try:
                    source_results = self._query_source(query=search_query, source=source, n_results=n_results)
                    for r in source_results:
                        r["_retrieval_source_filter"] = source
                    all_results.extend(source_results)
                except (OSError, RuntimeError, ValueError, KeyError) as e:
                    logger.warning(
                        "Cross-source query failed for %s: %s",
                        source,
                        e,
                        exc_info=True,
                    )

        deduplicated = self._deduplicate(all_results)

        # 重排序
        if self._reranker is not None and ENABLE_RERANKER and deduplicated:
            try:
                deduplicated = self._reranker.rerank(
                    query=search_query,
                    results=deduplicated,
                    top_k=n_results,
                )
            except (RuntimeError, ValueError, OSError) as e:
                logger.debug("Cross-source reranking skipped: %s", e)

        final_results = deduplicated[:n_results]

        return {
            "query": query,
            "search_query_used": search_query,
            "sources_queried": sources,
            "total_found": len(all_results),
            "results_returned": len(final_results),
            "results": final_results,
        }

    def _query_source(
        self,
        query: str,
        source: str,
        n_results: int,
        where_filters: dict | None = None,
    ) -> list[dict]:
        try:
            raw = self.kb.query_by_source(
                source=source,
                query=query,
                n_results=n_results,
                extra_filters=where_filters,
            )
        except (OSError, RuntimeError, ValueError, KeyError) as kb_err:
            # 单源查询失败时回退到通用检索，记录失败原因
            logger.debug(
                "Source query failed for %s, falling back to general: %s",
                source,
                kb_err,
                exc_info=True,
            )
            return self._query_general(query, n_results)
        return self._parse_chroma_result(raw)

    def _query_general(self, query: str, n_results: int = 5) -> list[dict]:
        try:
            raw = self.kb.query(query_text=query, n_results=n_results)
        except (OSError, RuntimeError, ValueError, KeyError) as kb_err:
            # 通用查询失败时返回空列表，记录以便后续排查
            logger.debug(
                "General query failed: %s",
                kb_err,
                exc_info=True,
            )
            return []
        return self._parse_chroma_result(raw)

    @staticmethod
    def _parse_chroma_result(raw: dict) -> list[dict]:
        """统一解析 ChromaDB 查询返回结构。

        ChromaDB 的返回形如::
            {
                "documents": [[doc1, doc2, ...]],
                "metadatas": [[meta1, meta2, ...]],
                "distances":  [[d1, d2, ...]],
                "ids":        [[id1, id2, ...]],
            }
        长度可能不齐（缺字段），统一防御式处理。
        """
        results: list[dict] = []
        if not raw.get("documents") or not raw["documents"][0]:
            return results

        docs = raw["documents"][0]
        metas = raw.get("metadatas", [None])
        metas_row = metas[0] if metas and metas[0] else []
        dists = raw.get("distances", [None])
        dists_row = dists[0] if dists and dists[0] else []
        ids = raw.get("ids", [None])
        ids_row = ids[0] if ids and ids[0] else []

        for i, doc in enumerate(docs):
            item = {
                "document": doc,
                "metadata": metas_row[i] if i < len(metas_row) else {},
                "distance": dists_row[i] if i < len(dists_row) else None,
                "id": ids_row[i] if i < len(ids_row) else None,
            }
            results.append(item)
        return results

    @staticmethod
    def _deduplicate(results: list[dict]) -> list[dict]:
        """去重：优先用 id，无 id 时用文档内容 SHA256（避免前 100 字碰撞）。"""
        seen: set = set()
        unique: list[dict] = []
        for r in results:
            doc_id = r.get("id")
            if doc_id:
                key = doc_id
            else:
                doc_text = r.get("document", "")
                # 用 SHA256 避免前 100 字相同导致误判
                key = hashlib.sha256(doc_text.encode("utf-8")).hexdigest()
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique

    def _rerank_by_keywords(
        self,
        results: list[dict],
        query: str,
        keyword_boost: dict,
    ) -> list[dict]:
        """基于关键词加权的轻量级重排序（reranker 不可用时的 fallback）。

        优化：
        - 关键词小写形式只计算一次，避免每文档重复转换
        - 文档与元数据的小写形式只计算一次
        - 命中关键词集合预计算（query 中包含哪些 boost 关键词）
        """
        query_lower = query.lower()
        # 预计算：keyword_boost 的小写形式 + 是否在 query 中命中
        # 这避免了每文档都重新计算 kw.lower() 与 kw_lower in query_lower
        active_boosts: list[tuple[str, float]] = []
        for kw, boost in keyword_boost.items():
            kw_lower = kw.lower()
            if kw_lower in query_lower:
                active_boosts.append((kw_lower, boost))

        scored: list[tuple[dict, float]] = []

        for r in results:
            # 优先使用 rerank_score（如果已由 reranker 计算过）
            if "rerank_score" in r:
                scored.append((r, float(r["rerank_score"])))
                continue
            # 其次使用 rrf_score（如果已由 hybrid search 计算过）
            if "rrf_score" in r:
                scored.append((r, float(r["rrf_score"])))
                continue

            distance = r.get("distance", 1.0) or 1.0
            # 学术诚信修复：ChromaDB 使用 cosine 距离（distance ∈ [0,2]），
            # 原 1.0 - min(distance, 1.0) 在 distance>1 时截断为 0，丢失区分度；
            # 改为标准 cosine 归一化 1.0 - distance/2.0，映射 [0,2] [1,0]
            semantic_score = 1.0 - distance / 2.0
            score = semantic_score
            # 文档与元数据小写只计算一次
            doc_lower = r.get("document", "").lower()
            meta_str_lower = str(r.get("metadata", {})).lower()

            for kw_lower, boost in active_boosts:
                if kw_lower in doc_lower:
                    score += boost * 0.05
                if kw_lower in meta_str_lower:
                    score += boost * 0.03

            scored.append((r, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [item for item, _ in scored]

    def get_enhancement_status(self) -> dict:
        """获取各增强模块的启用状态（用于诊断端点）。"""
        self._load_enhancements()
        return {
            "parallel_retrieval": ENABLE_PARALLEL_RETRIEVAL,
            "parallel_workers": PARALLEL_RETRIEVAL_WORKERS,
            "result_cache": ENABLE_RESULT_CACHE,
            "result_cache_stats": self._cache.stats(),
            "hybrid_search": ENABLE_HYBRID_SEARCH and self._hybrid_engine is not None,
            "hybrid_search_stats": (self._hybrid_engine.get_stats() if self._hybrid_engine is not None else None),
            "reranker": ENABLE_RERANKER and self._reranker is not None,
            "reranker_stats": (self._reranker.get_performance_metrics() if self._reranker is not None else None),
            "query_rewrite": ENABLE_QUERY_REWRITE and self._query_rewriter is not None,
            "hyde": ENABLE_HYDE and self._query_rewriter is not None,
            "query_rewriter_stats": (self._query_rewriter.get_stats() if self._query_rewriter is not None else None),
        }

    def clear_cache(self) -> None:
        """清空检索结果缓存。"""
        self._cache.clear()


# 以下符号由安装验证（2026-08-03）发现拆分/迁移丢失，从 592aedb 恢复

RAG_SOURCE_QUERY_TIMEOUT_SEC = float(os.getenv("RAG_SOURCE_QUERY_TIMEOUT_SEC", "15"))

_CLUSTER_TAG_FILTERS: dict[str, dict] = {
    "material_wear": {"category": "tool_wear"},
    "cutting_params": {"category": "tool_wear"},
    "vibration_wear": {"has_vibration": True},
    "material_compare": {},  # 依赖 source_filters 即可
    "cross_source": {},  # 跨源检索不额外限制
    "signal_fusion": {},  # signal_fusion source 已通过 source_filters 过滤
}

# 查询实体提取模式（历史缺失，2026-08-03 安装验证补齐）：
# 覆盖中文制造术语词组与英文/数字 token
_QUERY_ENTITY_PATTERNS: list[re.Pattern] = [
    re.compile(r"[\u4e00-\u9fff]{2,6}"),
    re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{1,31}"),
]


def _extract_query_entities(query: str) -> list[str]:
    """从查询文本中提取制造领域实体（小写形式）。

    用于在向量检索之外，通过 entity 倒排索引补充精确匹配的 chunk。
    """
    if not query or not query.strip():
        return []
    found: set[str] = set()
    for pattern in _QUERY_ENTITY_PATTERNS:
        for match in pattern.finditer(query):
            entity = match.group(0).strip().lower()
            if len(entity) >= 2:
                found.add(entity)
    return sorted(found)
