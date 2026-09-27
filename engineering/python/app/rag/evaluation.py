"""检索效果评估体系。

包含评估数据集、准确率计算、性能目标验证和评估报告生成功能。

v2 增强：
- 支持完整 RAG pipeline 评估（query_rewrite + hybrid_search + reranker）
- 支持 ablation study（逐项开关增强模块，量化各模块贡献）
- 支持 baseline vs enhanced A/B 对比报告
"""

import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


from ._eval_models import (
    EvaluationQuery,
    EvaluationResult,
    EvaluationReport,
    AblationResult,
    ComparisonReport,
    EvaluationDataset,
)


class RetrievalEvaluator:
    def __init__(self, knowledge_base, reranker_service=None, rag_engine=None):
        self.knowledge_base = knowledge_base
        self.reranker_service = reranker_service
        self.rag_engine = rag_engine
        self.dataset = EvaluationDataset()

    def calculate_precision_at_k(self, expected: list[str], retrieved: list[str], k: int) -> float:
        if k == 0 or not retrieved:
            return 0.0
        # 用 set 加速 in 查找（expected 通常很小，但 retrieved 较大时受益明显）
        expected_set = set(expected)
        relevant_retrieved = [doc_id for doc_id in retrieved[:k] if doc_id in expected_set]
        return len(relevant_retrieved) / k

    def calculate_recall_at_k(self, expected: list[str], retrieved: list[str], k: int) -> float:
        if not expected:
            return 0.0
        expected_set = set(expected)
        relevant_retrieved = [doc_id for doc_id in retrieved[:k] if doc_id in expected_set]
        return len(relevant_retrieved) / len(expected)

    def calculate_f1_score(self, precision: float, recall: float) -> float:
        if precision + recall == 0:
            return 0.0
        return 2 * (precision * recall) / (precision + recall)

    def calculate_mrr(self, expected: list[str], retrieved: list[str]) -> float:
        expected_set = set(expected)
        for i, doc_id in enumerate(retrieved):
            if doc_id in expected_set:
                return 1.0 / (i + 1)
        return 0.0

    def calculate_ndcg_at_k(self, expected: list[str], retrieved: list[str], k: int) -> float:
        """NDCG@k：标准 DCG 使用 log2(position+1) 折损。

        修复：原实现使用 ``rel / (i + 1)``（即 1/(rank)）折损，
        与 NDCG 学术标准公式 ``rel / log2(rank + 1)`` 不一致。
        标准公式更准确反映用户对靠前结果的偏好程度，对研究论文报告更严谨。
        """
        if k == 0 or not retrieved:
            return 0.0

        expected_set = set(expected)

        # DCG: 折损累积增益，position 从 1 开始
        # DCG@k = sum_{i=1}^{k} (rel_i / log2(i + 1))
        dcg = 0.0
        for i, doc_id in enumerate(retrieved[:k]):
            rel = 1.0 if doc_id in expected_set else 0.0
            if rel > 0:
                # i 是 0-indexed，对应 1-indexed 的 rank = i + 1
                # log2(rank + 1) = log2(i + 2)
                dcg += rel / (math.log2(i + 2))

        # IDCG: 理想排序下的 DCG
        ideal_hits = min(len(expected), k)
        idcg = 0.0
        for i in range(ideal_hits):
            idcg += 1.0 / (math.log2(i + 2))

        if idcg == 0:
            return 0.0
        return dcg / idcg

    def evaluate_single_query(self, query: EvaluationQuery, top_k: int = 3) -> EvaluationResult:
        """baseline 评估：直接调用 knowledge_base.query()，可选 reranker。

        为了同时支持 top-3 / top-5 准确率计算，这里始终检索
        ``max(top_k, 5) * 2`` 条结果，但 ``retrieved_doc_ids`` 保留
        前 ``max(top_k, 5)`` 条；``evaluate_all`` 会据此分别计算
        top3/top5 准确率，避免两个指标相互复用导致相同。
        """
        start_time = time.time()

        retrieve_k = max(top_k, 5)
        raw_results = self.knowledge_base.query(query_text=query.query_text, n_results=retrieve_k * 2)

        if self.reranker_service and raw_results.get("documents"):
            formatted_results = []
            docs = (
                raw_results["documents"][0]
                if raw_results["documents"] and isinstance(raw_results["documents"][0], list)
                else raw_results["documents"]
            )
            metas = (
                raw_results["metadatas"][0]
                if raw_results["metadatas"] and isinstance(raw_results["metadatas"][0], list)
                else raw_results["metadatas"]
            )
            dists = (
                raw_results["distances"][0]
                if raw_results["distances"] and isinstance(raw_results["distances"][0], list)
                else raw_results["distances"]
            )
            ids = (
                raw_results["ids"][0]
                if raw_results["ids"] and isinstance(raw_results["ids"][0], list)
                else raw_results["ids"]
            )

            for i, doc in enumerate(docs):
                formatted_results.append(
                    {
                        "id": ids[i] if i < len(ids) else f"idx_{i}",
                        "document": doc,
                        "metadata": metas[i] if i < len(metas) else {},
                        "distance": dists[i] if i < len(dists) else 1.0,
                    }
                )

            reranked_results = self.reranker_service.rerank(query=query.query_text, results=formatted_results)

            # 修复 bug：reranker 返回字段为 "id" 而非 "doc_id"
            # 保留 retrieve_k 条以便 evaluate_all 计算 top5 准确率
            retrieved_ids = [r.get("id") or r.get("doc_id") or "" for r in reranked_results[:retrieve_k]]
        else:
            raw_ids = raw_results.get("ids", [])
            if raw_ids and isinstance(raw_ids[0], list):
                retrieved_ids = list(raw_ids[0])[:retrieve_k]
            else:
                retrieved_ids = list(raw_ids)[:retrieve_k]

        elapsed_time = (time.time() - start_time) * 1000

        # 评估指标基于前 top_k 条，但 retrieved_doc_ids 保留 retrieve_k 条
        # 以便 evaluate_all 据此分别计算 top3 / top5 准确率
        precision = self.calculate_precision_at_k(query.expected_doc_ids, retrieved_ids[:top_k], top_k)
        recall = self.calculate_recall_at_k(query.expected_doc_ids, retrieved_ids[:top_k], top_k)
        f1 = self.calculate_f1_score(precision, recall)
        mrr = self.calculate_mrr(query.expected_doc_ids, retrieved_ids)
        ndcg = self.calculate_ndcg_at_k(query.expected_doc_ids, retrieved_ids[:top_k], top_k)

        hits = len([doc_id for doc_id in retrieved_ids[:top_k] if doc_id in query.expected_doc_ids])

        return EvaluationResult(
            query_id=query.query_id,
            query_text=query.query_text,
            expected_doc_ids=query.expected_doc_ids,
            retrieved_doc_ids=retrieved_ids,
            top_k=top_k,
            hits=hits,
            precision=precision,
            recall=recall,
            f1_score=f1,
            mrr=mrr,
            ndcg=ndcg,
            retrieval_time_ms=round(elapsed_time, 2),
        )

    def evaluate_with_rag_engine(self, query: EvaluationQuery, top_k: int = 3) -> EvaluationResult:
        """使用完整 RAG pipeline 评估单条查询。

        调用 RagRetrievalEngine.retrieve()，利用所有已启用的增强模块
        （query_rewrite + hybrid_search + reranker + parallel_retrieval + cache）。

        与 ``evaluate_single_query`` 一致，这里始终检索 ``max(top_k, 5)`` 条，
        以便 ``evaluate_all`` 据此分别计算 top3 / top5 准确率。
        """
        if self.rag_engine is None:
            raise RuntimeError("rag_engine not configured. Pass rag_engine to RetrievalEvaluator.")

        retrieve_k = max(top_k, 5)
        start_time = time.time()
        try:
            rag_result = self.rag_engine.retrieve(query=query.query_text, n_results=retrieve_k)
        except (OSError, RuntimeError, ValueError, KeyError) as e:
            logger.warning(
                "RAG engine retrieval failed for query %s: %s",
                query.query_id,
                e,
                exc_info=True,
            )
            rag_result = {"results": [], "enhancements": {}}

        elapsed_time = (time.time() - start_time) * 1000

        retrieved_ids = []
        for r in rag_result.get("results", [])[:retrieve_k]:
            doc_id = r.get("id") or r.get("doc_id")
            if doc_id:
                retrieved_ids.append(doc_id)

        # 评估指标基于前 top_k 条，retrieved_doc_ids 保留 retrieve_k 条
        precision = self.calculate_precision_at_k(query.expected_doc_ids, retrieved_ids[:top_k], top_k)
        recall = self.calculate_recall_at_k(query.expected_doc_ids, retrieved_ids[:top_k], top_k)
        f1 = self.calculate_f1_score(precision, recall)
        mrr = self.calculate_mrr(query.expected_doc_ids, retrieved_ids)
        ndcg = self.calculate_ndcg_at_k(query.expected_doc_ids, retrieved_ids[:top_k], top_k)

        hits = len([doc_id for doc_id in retrieved_ids[:top_k] if doc_id in query.expected_doc_ids])

        return EvaluationResult(
            query_id=query.query_id,
            query_text=query.query_text,
            expected_doc_ids=query.expected_doc_ids,
            retrieved_doc_ids=retrieved_ids,
            top_k=top_k,
            hits=hits,
            precision=precision,
            recall=recall,
            f1_score=f1,
            mrr=mrr,
            ndcg=ndcg,
            retrieval_time_ms=round(elapsed_time, 2),
        )

    def evaluate_all(
        self,
        top_k: int = 3,
        category: str | None = None,
        difficulty: str | None = None,
        use_rag_engine: bool = False,
    ) -> EvaluationReport:
        """批量评估所有查询。

        Args:
            top_k: 每条查询返回的文档数
            category: 仅评估指定类别
            difficulty: 仅评估指定难度
            use_rag_engine: True 时使用完整 RAG pipeline，False 使用 baseline
        """
        queries = self.dataset.get_queries(category=category, difficulty=difficulty)

        results = []
        for query in queries:
            try:
                if use_rag_engine and self.rag_engine is not None:
                    result = self.evaluate_with_rag_engine(query, top_k=top_k)
                else:
                    result = self.evaluate_single_query(query, top_k=top_k)
            except (RuntimeError, OSError, ValueError) as e:
                logger.warning(
                    "Evaluation failed for query %s: %s",
                    query.query_id,
                    e,
                    exc_info=True,
                )
                # 失败时生成零值结果，保持报告完整性
                result = EvaluationResult(
                    query_id=query.query_id,
                    query_text=query.query_text,
                    expected_doc_ids=query.expected_doc_ids,
                    retrieved_doc_ids=[],
                    top_k=top_k,
                    hits=0,
                    precision=0.0,
                    recall=0.0,
                    f1_score=0.0,
                    mrr=0.0,
                    ndcg=0.0,
                    retrieval_time_ms=0.0,
                )
            results.append(result)

        avg_precision = sum(r.precision for r in results) / len(results) if results else 0.0
        avg_recall = sum(r.recall for r in results) / len(results) if results else 0.0
        avg_f1 = sum(r.f1_score for r in results) / len(results) if results else 0.0
        avg_mrr = sum(r.mrr for r in results) / len(results) if results else 0.0
        avg_ndcg = sum(r.ndcg for r in results) / len(results) if results else 0.0
        avg_time = sum(r.retrieval_time_ms for r in results) / len(results) if results else 0.0

        # top-3 命中：基于前 top_k 条的命中数（hits 已基于 retrieved_doc_ids[:top_k] 计算）
        top3_correct = sum(1 for r in results if r.hits > 0)
        top3_accuracy = top3_correct / len(results) if results else 0.0

        # top-5 命中：独立计算，基于 retrieved_doc_ids[:5] 中是否命中 expected_doc_ids
        # 注意 retrieved_doc_ids 保留 max(top_k, 5) 条，足以支持 top-5 评估
        top5_correct = sum(
            1 for r in results if any(doc_id in r.expected_doc_ids for doc_id in r.retrieved_doc_ids[:5])
        )
        top5_accuracy = top5_correct / len(results) if results else 0.0

        category_perf = {}
        for r in results:
            query_row = self.dataset.get_query_by_id(r.query_id)
            if query_row:
                cat = query_row.category
                if cat not in category_perf:
                    category_perf[cat] = {"total": 0, "correct": 0, "accuracy": 0.0}
                category_perf[cat]["total"] += 1
                if r.hits > 0:
                    category_perf[cat]["correct"] += 1

        for cat in category_perf:
            total = category_perf[cat]["total"]
            correct = category_perf[cat]["correct"]
            category_perf[cat]["accuracy"] = round(correct / total, 4) if total > 0 else 0.0

        target_accuracy = 0.80
        performance_target_met = top3_accuracy >= target_accuracy

        # 记录本次评估使用的增强配置
        enhancement_config = self._get_enhancement_config(use_rag_engine)

        report = EvaluationReport(
            report_id=f"ER_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            evaluation_time=datetime.now(timezone.utc).isoformat(),
            total_queries=len(results),
            top_k=top_k,
            avg_precision=round(avg_precision, 4),
            avg_recall=round(avg_recall, 4),
            avg_f1_score=round(avg_f1, 4),
            avg_mrr=round(avg_mrr, 4),
            avg_ndcg=round(avg_ndcg, 4),
            top3_accuracy=round(top3_accuracy, 4),
            top5_accuracy=round(top5_accuracy, 4),
            category_performance=category_perf,
            query_results=[r.to_dict() for r in results],
            performance_target_met=performance_target_met,
            target_accuracy=target_accuracy,
            enhancement_config=enhancement_config,
            avg_retrieval_time_ms=round(avg_time, 2),
        )

        return report

    def _get_enhancement_config(self, use_rag_engine: bool) -> dict:
        """获取当前评估使用的增强配置。"""
        if use_rag_engine and self.rag_engine is not None:
            try:
                return self.rag_engine.get_enhancement_status()
            except (AttributeError, RuntimeError):
                return {"mode": "rag_engine", "error": "status_unavailable"}
        return {
            "mode": "baseline",
            "reranker_enabled": self.reranker_service is not None,
        }

    def run_ablation_study(
        self,
        top_k: int = 3,
        category: str | None = None,
        difficulty: str | None = None,
    ) -> list[AblationResult]:
        """运行 ablation study，逐项关闭增强模块，量化各模块贡献。

        实验配置：
        1. baseline: 所有增强关闭
        2. +reranker_only: 仅开启 reranker
        3. +hybrid_only: 仅开启 hybrid_search
        4. +rewrite_only: 仅开启 query_rewrite
        5. +all_parallel_cache: 开启 parallel + cache（不影响质量的性能优化）
        6. full_pipeline: 全部开启

        Returns:
            各配置下的评估结果列表
        """
        if self.rag_engine is None:
            logger.warning("Ablation study requires rag_engine. Returning baseline only.")
            baseline_report = self.evaluate_all(top_k=top_k, category=category, difficulty=difficulty)
            return [
                AblationResult(
                    config_name="baseline",
                    config_description="所有增强关闭（baseline）",
                    enhancements_enabled={"reranker": False, "hybrid": False, "rewrite": False},
                    avg_precision=baseline_report.avg_precision,
                    avg_recall=baseline_report.avg_recall,
                    avg_f1_score=baseline_report.avg_f1_score,
                    avg_mrr=baseline_report.avg_mrr,
                    avg_ndcg=baseline_report.avg_ndcg,
                    top3_accuracy=baseline_report.top3_accuracy,
                    avg_retrieval_time_ms=baseline_report.avg_retrieval_time_ms,
                    total_queries=baseline_report.total_queries,
                )
            ]

        # 定义各实验配置
        configs = [
            (
                "baseline",
                "所有增强关闭",
                {
                    "ENABLE_RERANKER": "0",
                    "ENABLE_HYBRID_SEARCH": "0",
                    "ENABLE_QUERY_REWRITE": "0",
                    "ENABLE_HYDE": "0",
                    "ENABLE_PARALLEL_RETRIEVAL": "0",
                    "ENABLE_RESULT_CACHE": "0",
                },
            ),
            (
                "reranker_only",
                "仅 Cross-Encoder 重排序",
                {
                    "ENABLE_RERANKER": "1",
                    "ENABLE_HYBRID_SEARCH": "0",
                    "ENABLE_QUERY_REWRITE": "0",
                    "ENABLE_HYDE": "0",
                    "ENABLE_PARALLEL_RETRIEVAL": "0",
                    "ENABLE_RESULT_CACHE": "0",
                },
            ),
            (
                "hybrid_only",
                "仅混合检索（BM25+Vector RRF）",
                {
                    "ENABLE_RERANKER": "0",
                    "ENABLE_HYBRID_SEARCH": "1",
                    "ENABLE_QUERY_REWRITE": "0",
                    "ENABLE_HYDE": "0",
                    "ENABLE_PARALLEL_RETRIEVAL": "0",
                    "ENABLE_RESULT_CACHE": "0",
                },
            ),
            (
                "rewrite_only",
                "仅查询改写",
                {
                    "ENABLE_RERANKER": "0",
                    "ENABLE_HYBRID_SEARCH": "0",
                    "ENABLE_QUERY_REWRITE": "1",
                    "ENABLE_HYDE": "0",
                    "ENABLE_PARALLEL_RETRIEVAL": "0",
                    "ENABLE_RESULT_CACHE": "0",
                },
            ),
            (
                "parallel_cache_only",
                "仅并行检索+缓存（性能优化）",
                {
                    "ENABLE_RERANKER": "0",
                    "ENABLE_HYBRID_SEARCH": "0",
                    "ENABLE_QUERY_REWRITE": "0",
                    "ENABLE_HYDE": "0",
                    "ENABLE_PARALLEL_RETRIEVAL": "1",
                    "ENABLE_RESULT_CACHE": "1",
                },
            ),
            (
                "full_pipeline",
                "全部增强开启",
                {
                    "ENABLE_RERANKER": "1",
                    "ENABLE_HYBRID_SEARCH": "1",
                    "ENABLE_QUERY_REWRITE": "1",
                    "ENABLE_HYDE": "0",
                    "ENABLE_PARALLEL_RETRIEVAL": "1",
                    "ENABLE_RESULT_CACHE": "1",
                },
            ),
        ]

        # 保存原始环境变量
        env_keys = [
            "ENABLE_RERANKER",
            "ENABLE_HYBRID_SEARCH",
            "ENABLE_QUERY_REWRITE",
            "ENABLE_HYDE",
            "ENABLE_PARALLEL_RETRIEVAL",
            "ENABLE_RESULT_CACHE",
        ]
        original_env: dict[str, str | None] = {k: os.environ.get(k) for k in env_keys}

        ablation_results: list[AblationResult] = []

        try:
            for config_name, description, env_overrides in configs:
                logger.info("Running ablation: %s", config_name)

                # 应用环境变量覆盖
                for key, value in env_overrides.items():
                    os.environ[key] = str(value)

                # 重置 RAG engine 的增强模块懒加载状态
                self._reset_rag_engine_enhancements()

                # 清空缓存避免污染
                if hasattr(self.rag_engine, "clear_cache"):
                    try:
                        self.rag_engine.clear_cache()
                    except (RuntimeError, OSError) as cache_err:
                        # clear_cache 失败不阻塞评估（可能产生少量过期命中），
                        # 记录便于排查：评估结果可能存在轻微污染
                        logger.debug("clear_cache failed during ablation: %s", cache_err, exc_info=True)

                # 运行评估
                report = self.evaluate_all(
                    top_k=top_k,
                    category=category,
                    difficulty=difficulty,
                    use_rag_engine=True,
                )

                enhancements_enabled = {
                    k: (env_overrides.get(k, "0") == "1")
                    for k in [
                        "ENABLE_RERANKER",
                        "ENABLE_HYBRID_SEARCH",
                        "ENABLE_QUERY_REWRITE",
                        "ENABLE_PARALLEL_RETRIEVAL",
                        "ENABLE_RESULT_CACHE",
                    ]
                }

                ablation_results.append(
                    AblationResult(
                        config_name=config_name,
                        config_description=description,
                        enhancements_enabled=enhancements_enabled,
                        avg_precision=report.avg_precision,
                        avg_recall=report.avg_recall,
                        avg_f1_score=report.avg_f1_score,
                        avg_mrr=report.avg_mrr,
                        avg_ndcg=report.avg_ndcg,
                        top3_accuracy=report.top3_accuracy,
                        avg_retrieval_time_ms=report.avg_retrieval_time_ms,
                        total_queries=report.total_queries,
                    )
                )
        finally:
            # 恢复原始环境变量
            for key, val in original_env.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = str(val)
            # 重置 RAG engine 以恢复原始配置
            self._reset_rag_engine_enhancements()

        return ablation_results

    def _reset_rag_engine_enhancements(self) -> None:
        """重置 RAG engine 的增强模块，使下次调用时重新加载。"""
        if self.rag_engine is None:
            return
        # 通过重新加载模块配置使环境变量生效
        try:
            # 重新导入配置常量
            import importlib
            import app.rag.rag_retrieval as rr_module

            importlib.reload(rr_module)
            # 重置 engine 的懒加载标志和模块引用
            self.rag_engine._enhancement_loaded = False
            self.rag_engine._query_rewriter = None
            self.rag_engine._hybrid_engine = None
            self.rag_engine._reranker = None
            # 更新 engine 引用的配置常量
            self.rag_engine._cache = rr_module._ResultCache()
        except (ImportError, AttributeError, RuntimeError) as e:
            logger.warning("Failed to reset RAG engine enhancements: %s", e)

    def generate_comparison_report(
        self,
        top_k: int = 3,
        category: str | None = None,
        difficulty: str | None = None,
        run_ablation: bool = True,
    ) -> ComparisonReport:
        """生成 baseline vs enhanced A/B 对比报告。

        Args:
            top_k: 每条查询返回的文档数
            category: 仅评估指定类别
            difficulty: 仅评估指定难度
            run_ablation: 是否运行 ablation study（更全面但更耗时）

        Returns:
            ComparisonReport 对比报告
        """
        logger.info("Starting baseline evaluation...")
        baseline_report = self.evaluate_all(top_k=top_k, category=category, difficulty=difficulty, use_rag_engine=False)

        logger.info("Starting enhanced (RAG pipeline) evaluation...")
        enhanced_report = self.evaluate_all(top_k=top_k, category=category, difficulty=difficulty, use_rag_engine=True)

        # 计算提升幅度
        def _improvement(baseline_val: float, enhanced_val: float) -> float:
            if baseline_val == 0:
                return 0.0 if enhanced_val == 0 else 100.0
            return round(((enhanced_val - baseline_val) / baseline_val) * 100, 2)

        improvement = {
            "precision_pct": _improvement(baseline_report.avg_precision, enhanced_report.avg_precision),
            "recall_pct": _improvement(baseline_report.avg_recall, enhanced_report.avg_recall),
            "f1_score_pct": _improvement(baseline_report.avg_f1_score, enhanced_report.avg_f1_score),
            "mrr_pct": _improvement(baseline_report.avg_mrr, enhanced_report.avg_mrr),
            "ndcg_pct": _improvement(baseline_report.avg_ndcg, enhanced_report.avg_ndcg),
            "top3_accuracy_pct": _improvement(baseline_report.top3_accuracy, enhanced_report.top3_accuracy),
            "retrieval_time_pct": _improvement(
                baseline_report.avg_retrieval_time_ms,
                enhanced_report.avg_retrieval_time_ms,
            ),
        }

        # 运行 ablation study
        ablation_data: list[dict] = []
        if run_ablation:
            logger.info("Running ablation study...")
            try:
                ablation_results = self.run_ablation_study(top_k=top_k, category=category, difficulty=difficulty)
                ablation_data = [r.to_dict() for r in ablation_results]
            except (RuntimeError, OSError, ValueError) as e:
                logger.warning("Ablation study failed: %s", e, exc_info=True)

        # 生成结论
        if improvement["f1_score_pct"] > 5:
            conclusion = f"RAG pipeline 显著提升检索质量（F1 +{improvement['f1_score_pct']}%），建议全面启用。"
        elif improvement["f1_score_pct"] > 0:
            conclusion = (
                f"RAG pipeline 有正向提升（F1 +{improvement['f1_score_pct']}%），"
                f"可根据 ablation 结果选择性启用增强模块。"
            )
        else:
            conclusion = "RAG pipeline 未带来明显质量提升，建议检查 enhancement 配置或评估数据集。"

        return ComparisonReport(
            report_id=f"CR_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            evaluation_time=datetime.now(timezone.utc).isoformat(),
            baseline=baseline_report.to_dict(),
            enhanced=enhanced_report.to_dict(),
            improvement=improvement,
            ablation_results=ablation_data,
            conclusion=conclusion,
        )

    def generate_report(self, report: EvaluationReport, output_path: str | None = None) -> str:
        report_content = {
            "report_id": report.report_id,
            "evaluation_time": report.evaluation_time,
            "summary": {
                "total_queries": report.total_queries,
                "top_k": report.top_k,
                "avg_precision": report.avg_precision,
                "avg_recall": report.avg_recall,
                "avg_f1_score": report.avg_f1_score,
                "avg_mrr": report.avg_mrr,
                "avg_ndcg": report.avg_ndcg,
                "top3_accuracy": report.top3_accuracy,
                "top5_accuracy": report.top5_accuracy,
                "avg_retrieval_time_ms": report.avg_retrieval_time_ms,
                "performance_target_met": report.performance_target_met,
                "target_accuracy": report.target_accuracy,
            },
            "enhancement_config": report.enhancement_config,
            "category_performance": report.category_performance,
            "query_results": report.query_results,
        }

        if output_path:
            output_path_obj = Path(output_path)
            output_path_obj.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path_obj, "w", encoding="utf-8") as f:
                json.dump(report_content, f, ensure_ascii=False, indent=2)

            return f"评估报告已保存至: {output_path}"

        return json.dumps(report_content, ensure_ascii=False, indent=2)

    def generate_comparison_report_output(self, comparison: ComparisonReport, output_path: str | None = None) -> str:
        """生成对比报告并保存到文件。"""
        report_content = {
            "report_id": comparison.report_id,
            "evaluation_time": comparison.evaluation_time,
            "conclusion": comparison.conclusion,
            "improvement": comparison.improvement,
            "baseline_summary": {
                "total_queries": comparison.baseline.get("total_queries", 0),
                "avg_precision": comparison.baseline.get("avg_precision", 0.0),
                "avg_recall": comparison.baseline.get("avg_recall", 0.0),
                "avg_f1_score": comparison.baseline.get("avg_f1_score", 0.0),
                "avg_mrr": comparison.baseline.get("avg_mrr", 0.0),
                "avg_ndcg": comparison.baseline.get("avg_ndcg", 0.0),
                "top3_accuracy": comparison.baseline.get("top3_accuracy", 0.0),
                "avg_retrieval_time_ms": comparison.baseline.get("avg_retrieval_time_ms", 0.0),
            },
            "enhanced_summary": {
                "total_queries": comparison.enhanced.get("total_queries", 0),
                "avg_precision": comparison.enhanced.get("avg_precision", 0.0),
                "avg_recall": comparison.enhanced.get("avg_recall", 0.0),
                "avg_f1_score": comparison.enhanced.get("avg_f1_score", 0.0),
                "avg_mrr": comparison.enhanced.get("avg_mrr", 0.0),
                "avg_ndcg": comparison.enhanced.get("avg_ndcg", 0.0),
                "top3_accuracy": comparison.enhanced.get("top3_accuracy", 0.0),
                "avg_retrieval_time_ms": comparison.enhanced.get("avg_retrieval_time_ms", 0.0),
            },
            "ablation_results": comparison.ablation_results,
        }

        if output_path:
            output_path_obj = Path(output_path)
            output_path_obj.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path_obj, "w", encoding="utf-8") as f:
                json.dump(report_content, f, ensure_ascii=False, indent=2)

            return f"对比报告已保存至: {output_path}"

        return json.dumps(report_content, ensure_ascii=False, indent=2)
