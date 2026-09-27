"""RAG 路由业务逻辑（从 routes.py 抽取，波次2 S4）。"""

from __future__ import annotations

from __future__ import annotations
import asyncio
import json
import logging
import os
import threading
from typing import Any
from fastapi import Body, HTTPException, Query, UploadFile, File, Form
from app.core.safe_errors import safe_error_message
from app.dependencies import get_knowledge_base
from app.dependencies import get_vector_store
from app.utils.upload_security import validate_upload


logger = logging.getLogger(__name__)

kb = get_knowledge_base()

_rag_engine_instance = None

_rag_engine_lock = threading.Lock()


def _get_rag_engine():
    """懒加载 RagRetrievalEngine 单例。

    RAG engine 依赖 knowledge_base，且其增强模块（reranker / hybrid_search /
    query_rewriter）在首次调用 ``_load_enhancements`` 时才会触发模型加载，
    避免在服务启动时阻塞。
    """
    global _rag_engine_instance
    if _rag_engine_instance is not None:
        return _rag_engine_instance
    with _rag_engine_lock:
        if _rag_engine_instance is not None:
            return _rag_engine_instance
        from app.rag.rag_retrieval import RagRetrievalEngine
        from app.rag.signal_fusion_kb import get_signal_fusion_kb

        # 集成点 2：显式注入 signal_fusion_kb，打通 RagRetrievalEngine
        # SignalFusionKnowledgeBase（之前仅通过共享 VectorStore 单例隐式耦合）
        _rag_engine_instance = RagRetrievalEngine(
            knowledge_base=kb,
            signal_fusion_kb=get_signal_fusion_kb(),
        )
        logger.info("RagRetrievalEngine singleton initialized (signal_fusion_kb injected)")
    return _rag_engine_instance


def _get_evaluator(use_rag_engine: bool = False):
    """构建 RetrievalEvaluator 实例。

    Args:
        use_rag_engine: True 时注入 RAG engine，启用完整 pipeline 评估
    """
    from app.rag.evaluation import RetrievalEvaluator

    reranker = None
    try:
        from app.rag.reranker import get_reranker_service

        reranker = get_reranker_service()
    except (ImportError, RuntimeError) as e:
        logger.debug("Reranker service unavailable: %s", e)

    rag_engine = _get_rag_engine() if use_rag_engine else None
    return RetrievalEvaluator(
        knowledge_base=kb,
        reranker_service=reranker,
        rag_engine=rag_engine,
    )


def _raise_internal(
    exc: BaseException,
    *,
    context: str,
    fallback: str,
    status_code: int = 500,
) -> None:
    """统一的 HTTPException 5xx 包装：避免将内部异常细节泄露给客户端。"""
    safe = safe_error_message(exc, context=context, fallback=fallback)
    headers = {"X-Error-ID": safe.get("error_id", "")}
    # debug 模式下保留 detail，便于本地排错
    detail: Any = safe.get("message")
    if "detail" in safe:
        detail = f"{safe['message']} ({safe['detail']})"
    raise HTTPException(status_code=status_code, detail=detail, headers=headers)


def _get_process_index():
    """懒加载工艺四元组索引单例。

    集成点 4：注入 ``knowledge_base`` 以启用 ``get_related_documents()``
    的完整文档反查能力（EntityIndex 已在 ``get_process_quadruple_index()``
    内部默认注入）。
    """
    from app.rag.process_quadruple import (
        get_process_quadruple_index,
        seed_default_quadruples,
    )

    index = get_process_quadruple_index(knowledge_base=kb)
    # 首次访问时自动注入默认知识库（仅当索引为空）
    if index.get_stats()["total_quadruples"] == 0:
        try:
            seeded = seed_default_quadruples(index)
            logger.info("已注入 %d 条默认工艺四元组", seeded)
        except (ValueError, KeyError, RuntimeError) as e:
            logger.warning("注入默认工艺四元组失败: %s", e)
    return index


def _normalize_rag_items(results: Any) -> list[dict[str, Any]]:
    """把两条管线的检索结果统一为扁平条目（W8.2 引用抽取用）。

    - enhanced（RagRetrievalEngine.retrieve）：字典，``results`` 键下为
      扁平条目列表 [{document, metadata, distance, id, ...}]（W 引擎验证
      发现此前只处理 list 形状导致增强管线 sources 恒为 0）
    - baseline（kb.query）：ChromaDB 嵌套结构 {ids/documents/metadatas/distances}
    """
    if isinstance(results, dict):
        if isinstance(results.get("results"), list):
            return [r for r in results["results"] if isinstance(r, dict)]
        docs_row = (results.get("documents") or [[]])[0] if results.get("documents") else []
        metas = results.get("metadatas") or []
        metas_row = metas[0] if metas else []
        dists = results.get("distances") or []
        dists_row = dists[0] if dists else []
        ids = results.get("ids") or []
        ids_row = ids[0] if ids else []
        return [
            {
                "document": docs_row[i] if i < len(docs_row) else "",
                "metadata": metas_row[i] if i < len(metas_row) else {},
                "distance": dists_row[i] if i < len(dists_row) else None,
                "id": ids_row[i] if i < len(ids_row) else None,
            }
            for i in range(len(docs_row))
        ]
    if isinstance(results, list):
        return [r for r in results if isinstance(r, dict)]
    return []


def _extract_citations(items: list[dict[str, Any]], max_preview: int = 120) -> list[dict[str, Any]]:
    """抽取引用溯源字段（W8.2：知识建议必须带出处）。

    与 W2 血缘体系对齐：``doc_id`` 即知识条目 ID，可反查来源文档。
    """
    citations: list[dict[str, Any]] = []
    for rank, item in enumerate(items, start=1):
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        score = item.get("score")
        distance = item.get("distance")
        if isinstance(score, (int, float)):
            similarity: float | None = round(float(score), 6)
        elif isinstance(distance, (int, float)):
            similarity = round(max(0.0, 1.0 - float(distance)), 6)
        else:
            similarity = None
        document = item.get("document") or item.get("content") or ""
        citations.append(
            {
                "rank": rank,
                "source": metadata.get("source") or item.get("_retrieval_source_filter") or "",
                "doc_id": item.get("id") or metadata.get("id") or "",
                "similarity": similarity,
                "preview": str(document)[:max_preview],
            }
        )
    return citations


async def query_knowledge(
    q: str = Query(..., description="查询文本"),
    n_results: int = Query(5, ge=1, le=50, description="返回结果数量"),
    intent: str | None = Query(
        None,
        description="查询意图（material_wear/cutting_params/vibration_wear/"
        "material_compare/cross_source/general），不传则自动检测",
    ),
    use_enhanced: bool = Query(
        True,
        description="True 启用完整增强 pipeline（reranker/hybrid_search/"
        "query_rewrite），False 仅使用 baseline 向量检索",
    ),
):
    """RAG 知识库查询（v2 增强）。

    启用完整 pipeline：查询改写 → HyDE → 意图检测 → 多源并行检索 →
    混合检索融合（RRF）→ Cross-Encoder 重排序 → 关键词 boost。

    Args:
        q: 查询文本
        n_results: 返回结果数量
        intent: 可选查询意图，不传则自动检测
        use_enhanced: 是否启用增强 pipeline（默认 True）

    Returns:
        增强检索结果，包含 results / detected_intent / enhancements 等字段；
        W8.2 起额外返回 ``sources``（引用溯源：rank/source/doc_id/similarity/
        preview）——每条答案建议均可回溯到知识出处。
    """
    try:
        if use_enhanced:
            # 使用增强 pipeline（RagRetrievalEngine）
            engine = _get_rag_engine()

            # 解析 intent 字符串到 QueryIntent 枚举
            intent_enum = None
            if intent:
                from app.rag.rag_retrieval import QueryIntent

                try:
                    intent_enum = QueryIntent(intent)
                except ValueError:
                    valid_intents = [i.value for i in QueryIntent]
                    raise HTTPException(
                        status_code=400,
                        detail=f"无效的 intent: {intent}，可选值: {valid_intents}",
                    )

            # ChromaDB 同步调用通过线程池执行，避免阻塞事件循环
            result = await asyncio.to_thread(
                engine.retrieve,
                q,
                intent_enum,
                n_results,
                None,  # override_source
            )
            return {
                "query": q,
                "results": result,
                "sources": _extract_citations(_normalize_rag_items(result)),
                "pipeline": "enhanced",
                "n_results": n_results,
            }
        else:
            # baseline 模式：直接使用 kb.query()
            result = kb.query(query_text=q, n_results=n_results)
            return {
                "query": q,
                "results": result,
                "sources": _extract_citations(_normalize_rag_items(result)),
                "pipeline": "baseline",
                "n_results": n_results,
            }
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError, RuntimeError, OSError) as e:
        # 捕获 RAG 查询相关异常：embedding 生成、向量检索、ChromaDB 操作
        _raise_internal(e, context="rag.query", fallback="知识库查询失败")


async def get_stats():
    try:
        return kb.get_stats()
    except (ValueError, TypeError, RuntimeError, OSError) as e:
        # 捕获知识库状态查询异常：ChromaDB 计数、集合访问
        _raise_internal(e, context="rag.stats", fallback="获取知识库状态失败")


async def add_knowledge(request: dict[str, Any]):
    try:
        document = request.get("document", "")
        metadata = request.get("metadata")
        if not document:
            raise HTTPException(status_code=400, detail="文档内容不能为空")
        result = kb.add_knowledge(document=document, metadata=metadata)
        return result
    except HTTPException:
        raise
    except (ValueError, TypeError, RuntimeError, OSError) as e:
        # 捕获知识库写入异常：embedding 推理、ChromaDB 写入
        _raise_internal(e, context="rag.add", fallback="添加知识失败")


async def delete_knowledge(doc_id: str):
    try:
        deleted = kb.delete(doc_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
        return {"deleted": True, "doc_id": doc_id}
    except HTTPException:
        raise
    except (ValueError, TypeError, RuntimeError, OSError) as e:
        # 捕获知识库删除异常：ChromaDB 删除操作、文档查找
        _raise_internal(e, context="rag.delete", fallback="删除知识失败")


async def list_documents(limit: int = Query(50, ge=1, le=500)):
    try:
        return {"documents": kb.list_documents(limit=limit)}
    except (ValueError, TypeError, KeyError, RuntimeError, OSError) as e:
        # 捕获知识库列表查询异常：ChromaDB 集合访问、文档计数
        logger.exception("Failed to list documents")
        _raise_internal(e, context="rag.list", fallback="获取文档列表失败")


async def load_default_knowledge():
    try:
        count = kb.load_default_knowledge()
        return {"loaded": count, "message": f"已加载 {count} 条默认知识"}
    except (OSError, ValueError, RuntimeError) as e:
        # 捕获默认知识加载异常：文件读取、JSON 解析、ChromaDB 写入
        logger.exception("Failed to load default knowledge")
        _raise_internal(e, context="rag.load_default", fallback="加载默认知识失败")


async def load_rag_json():
    try:
        stats = kb.load_rag_json_knowledge()
        return {"stats": stats}
    except (json.JSONDecodeError, OSError, ValueError, RuntimeError) as e:
        # 捕获 JSON 知识加载异常：JSON 解析、文件读取、ChromaDB 写入
        logger.exception("Failed to load RAG JSON knowledge")
        _raise_internal(e, context="rag.load_json", fallback="加载JSON知识失败")


async def search_by_source(
    source: str = Query(..., description="知识来源"),
    query: str = Query("", description="可选搜索查询"),
    n_results: int = Query(5, ge=1, le=50),
):
    try:
        result = kb.query_by_source(source=source, query=query, n_results=n_results)
        return {"source": source, "results": result}
    except (ValueError, TypeError, KeyError, RuntimeError, OSError) as e:
        # 捕获按来源搜索异常：ChromaDB where 过滤、embedding 推理
        logger.exception("Failed to search by source")
        _raise_internal(e, context="rag.search", fallback="搜索失败")


async def delete_by_source(source: str):
    try:
        count = kb.delete_by_source(source)
        return {"deleted_count": count, "source": source}
    except (ValueError, TypeError, KeyError, RuntimeError, OSError) as e:
        # 捕获按来源删除异常：ChromaDB where 过滤、批量删除
        logger.exception("Failed to delete by source")
        _raise_internal(e, context="rag.delete_by_source", fallback="删除失败")


async def import_document(
    file: UploadFile = File(...),
    chunk_size: int = Form(400, ge=100, le=10000),
    chunk_overlap: int = Form(60, ge=0, le=1000),
):
    try:
        from app.rag.document_importer import DocumentImportService

        import tempfile

        # P0-12/P0-13 修复：使用 validate_upload 统一校验
        # （扩展名 + magic bytes + 分块流式读取 + 大小限制）
        # 替代原 ``await file.read()`` 全量入内存 + 事后校验的模式
        _ALLOWED_RAG_EXTENSIONS = {
            ".txt",
            ".pdf",
            ".docx",
            ".doc",
            ".md",
            ".json",
            ".csv",
            ".html",
            ".xml",
            ".rtf",
        }
        _ALLOWED_RAG_MIMES = {
            "text/plain",
            "text/csv",
            "application/json",
            "application/pdf",
            "application/zip",  # docx 为 zip 容器
            "application/octet-stream",  # doc/html/xml/rtf 无固定签名
        }
        content = await validate_upload(
            file,
            max_size=50 * 1024 * 1024,
            allowed_extensions=_ALLOWED_RAG_EXTENSIONS,
            allowed_mimes=_ALLOWED_RAG_MIMES,
        )

        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename or "doc.txt")[1]) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            service = DocumentImportService(kb)
            result = service.import_document(
                tmp_path,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            return result
        finally:
            os.unlink(tmp_path)
    except HTTPException:
        # validate_upload 抛出的 413/415/400 透传
        raise
    except (OSError, ValueError, UnicodeDecodeError, RuntimeError) as e:
        # 捕获文档导入异常：临时文件 IO、文本解析、embedding 推理、ChromaDB 写入
        logger.exception("Failed to import document")
        _raise_internal(e, context="rag.import", fallback="文档导入失败")


async def export_backup(backup_dir: str = Query("./backups/rag")):
    try:
        vs = get_vector_store()
        path = vs.export_backup(backup_dir)
        return {"backup_path": path, "message": "备份导出成功"}
    except (OSError, RuntimeError) as e:
        # 捕获备份导出异常：目录创建、文件写入、ChromaDB 导出
        logger.exception("Failed to export backup")
        _raise_internal(e, context="rag.backup_export", fallback="备份导出失败")


async def import_backup(backup_dir: str = Query(..., description="备份目录路径")):
    try:
        vs = get_vector_store()
        success = vs.import_backup(backup_dir)
        if not success:
            raise HTTPException(status_code=400, detail="备份目录不存在或无效")
        return {"message": "备份导入成功", "document_count": vs.count()}
    except HTTPException:
        raise
    except (OSError, ValueError, RuntimeError) as e:
        # 捕获备份导入异常：目录读取、文件解析、ChromaDB 恢复
        logger.exception("Failed to import backup")
        _raise_internal(e, context="rag.backup_import", fallback="备份导入失败")


async def optimize_index():
    try:
        vs = get_vector_store()
        success = vs.optimize_index()
        return {"optimized": success, "document_count": vs.count()}
    except (RuntimeError, OSError) as e:
        # 捕获索引优化异常：ChromaDB compaction、磁盘 IO
        logger.exception("Failed to optimize index")
        _raise_internal(e, context="rag.optimize", fallback="索引优化失败")


async def cleanup_orphaned():
    try:
        vs = get_vector_store()
        before = vs.count()
        vs.optimize_index()
        after = vs.count()
        return {
            "before": before,
            "after": after,
            "removed": before - after,
        }
    except (RuntimeError, OSError) as e:
        # 捕获清理异常：ChromaDB compaction、计数操作
        logger.exception("Failed to cleanup orphaned documents")
        _raise_internal(e, context="rag.cleanup", fallback="清理失败")


async def get_enhancement_status():
    """获取 RAG 增强模块的实时状态与性能指标。

    返回各模块（parallel_retrieval / hybrid_search / reranker /
    query_rewrite / hyde / result_cache）的启用状态与统计信息，
    用于运维诊断与灰度发布验证。
    """
    try:
        engine = _get_rag_engine()
        return engine.get_enhancement_status()
    except (RuntimeError, OSError, ValueError, AttributeError) as e:
        logger.exception("Failed to get enhancement status")
        _raise_internal(e, context="rag.v2.enhancement_status", fallback="获取增强状态失败")


async def get_cache_stats():
    """获取检索结果 LRU 缓存的命中统计。

    用于判断缓存效果与容量是否需要调整。
    """
    try:
        engine = _get_rag_engine()
        return engine._cache.stats()
    except (RuntimeError, AttributeError) as e:
        logger.exception("Failed to get cache stats")
        _raise_internal(e, context="rag.v2.cache_stats", fallback="获取缓存统计失败")


async def clear_cache():
    """清空检索结果 LRU 缓存。

    在知识库内容更新后调用，避免返回过期的缓存结果。
    """
    try:
        engine = _get_rag_engine()
        engine.clear_cache()
        return {"cleared": True, "message": "检索结果缓存已清空"}
    except (RuntimeError, AttributeError) as e:
        logger.exception("Failed to clear cache")
        _raise_internal(e, context="rag.v2.cache_clear", fallback="清空缓存失败")


async def retrieve_from_signal_fusion(
    payload: dict = Body(...),
):
    """集成点 2：通过 RagRetrievalEngine 委托 SignalFusionKnowledgeBase 检索。

    请求体字段（全部可选，但至少需要 features 或 query 之一）::

        {
          "features": [0.12, 0.45, ...],   # 9 维特征向量
          "signal_type": "vibration",       # 可选过滤
          "machine_id": "vmc_850",          # 可选过滤
          "material": "aluminum_6061",      # 可选过滤
          "tool_id": 3,                     # 可选过滤
          "top_k": 10,
          "query": "振动信号样本"            # 可选文本（降级时使用）
        }

    Returns:
        samples 列表（SignalSample.to_dict），含 degraded 标记
        （True 表示 signal_fusion_kb 未注入或检索失败，已降级到通用 RAG）
    """
    try:
        engine = _get_rag_engine()
        result = engine.retrieve_from_signal_fusion(
            features=payload.get("features"),
            signal_type=payload.get("signal_type"),
            machine_id=payload.get("machine_id"),
            material=payload.get("material"),
            tool_id=payload.get("tool_id"),
            top_k=int(payload.get("top_k", 10)),
            query=payload.get("query"),
        )
        return result
    except (RuntimeError, OSError, ValueError, TypeError) as e:
        logger.exception("Signal fusion retrieval failed")
        _raise_internal(
            e,
            context="rag.v2.signal_fusion.retrieve",
            fallback="信号融合检索失败",
        )


def run_evaluation(
    top_k: int = Query(3, ge=1, le=10, description="每条查询返回的文档数"),
    category: str | None = Query(None, description="仅评估指定类别"),
    difficulty: str | None = Query(None, description="仅评估指定难度"),
    use_rag_engine: bool = Query(
        False,
        description="True 使用完整 RAG pipeline，False 使用 baseline",
    ),
):
    """运行检索质量评估。

    评估 60 条标准查询的 precision / recall / F1 / MRR / nDCG /
    top3 / top5 准确率，并按类别汇总性能。

    注意：此端点为同步阻塞操作（底层 ChromaDB 调用非异步），
    FastAPI 会自动将普通 ``def`` 路由放到线程池执行，不会阻塞事件循环。
    60 条查询的 baseline 评估约耗时 5-15 秒，启用 RAG engine 后可能
    因 reranker 推理增加 30-90 秒。
    """
    try:
        evaluator = _get_evaluator(use_rag_engine=use_rag_engine)
        report = evaluator.evaluate_all(top_k=top_k, category=category, difficulty=difficulty)
        return report.to_dict()
    except (RuntimeError, OSError, ValueError) as e:
        logger.exception("Evaluation failed")
        _raise_internal(e, context="rag.v2.evaluation", fallback="评估运行失败")


def run_ablation_study(
    top_k: int = Query(3, ge=1, le=10, description="每条查询返回的文档数"),
    category: str | None = Query(None, description="仅评估指定类别"),
    difficulty: str | None = Query(None, description="仅评估指定难度"),
):
    """运行 ablation study，逐项关闭增强模块，量化各模块贡献。

    实验配置（6 组）：
    1. baseline            - 所有增强关闭
    2. reranker_only       - 仅 Cross-Encoder 重排序
    3. hybrid_only         - 仅混合检索（BM25+Vector RRF）
    4. rewrite_only        - 仅查询改写
    5. parallel_cache_only - 仅并行检索+缓存（性能优化）
    6. full_pipeline       - 全部增强开启

    返回各组配置下的评估指标，用于判断各模块的边际贡献。

    注意：此端点非常耗时（6 组 × 60 条查询），可能需要 5-15 分钟。
    建议在低峰期或离线场景调用。
    """
    try:
        evaluator = _get_evaluator(use_rag_engine=True)
        results = evaluator.run_ablation_study(top_k=top_k, category=category, difficulty=difficulty)
        return {
            "total_configs": len(results),
            "results": [r.to_dict() for r in results],
        }
    except (RuntimeError, OSError, ValueError) as e:
        logger.exception("Ablation study failed")
        _raise_internal(e, context="rag.v2.ablation", fallback="消融研究运行失败")


def generate_comparison_report(
    top_k: int = Query(3, ge=1, le=10, description="每条查询返回的文档数"),
    category: str | None = Query(None, description="仅评估指定类别"),
    difficulty: str | None = Query(None, description="仅评估指定难度"),
    run_ablation: bool = Query(True, description="是否运行 ablation study（更全面但更耗时）"),
):
    """生成 baseline vs enhanced A/B 对比报告。

    同时运行 baseline（纯向量检索）与 enhanced（完整 RAG pipeline）评估，
    计算各指标的提升幅度，并可选运行 ablation study 量化各模块贡献。

    返回 ComparisonReport，包含：
    - baseline / enhanced 的完整评估报告
    - 各指标的提升百分比
    - ablation study 结果（可选）
    - 自动生成的结论

    注意：启用 ablation 时总耗时可能超过 10 分钟。
    """
    try:
        evaluator = _get_evaluator(use_rag_engine=True)
        comparison = evaluator.generate_comparison_report(
            top_k=top_k,
            category=category,
            difficulty=difficulty,
            run_ablation=run_ablation,
        )
        return comparison.to_dict()
    except (RuntimeError, OSError, ValueError) as e:
        logger.exception("Comparison report generation failed")
        _raise_internal(e, context="rag.v2.comparison", fallback="对比报告生成失败")


async def recommend_process(request: dict[str, Any]):
    """根据加工特征推荐工艺方案（CAMWorks TechDB 式自动决策）。

    请求体：
        {
            "feature": "pocket",         # 加工特征
            "material": "aluminum",      # 工件材料（可选，默认 general）
            "top_k": 5                   # 返回前 K 条（可选，默认 5）
        }

    返回按 confidence 降序排列的推荐方案，每项含完整四元组 + 评分。
    """
    try:
        feature = (request.get("feature") or "").strip()
        if not feature:
            raise HTTPException(status_code=400, detail="feature 不能为空")
        material = (request.get("material") or "general").strip()
        top_k = int(request.get("top_k", 5))
        if not 1 <= top_k <= 50:
            raise HTTPException(status_code=400, detail="top_k 必须在 [1, 50]")

        index = _get_process_index()
        recommendations = index.recommend_process(
            feature=feature,
            material=material,
            top_k=top_k,
        )
        return {
            "feature": feature.lower(),
            "material": material.lower(),
            "count": len(recommendations),
            "recommendations": recommendations,
        }
    except HTTPException:
        raise
    except (ValueError, TypeError, RuntimeError) as e:
        logger.exception("Failed to recommend process")
        _raise_internal(e, context="rag.process.recommend", fallback="工艺推荐失败")


async def find_similar_quadruples(request: dict[str, Any]):
    """查找相似工艺记录（3 层匹配：精确 / 同特征 / 材料迁移）。

    请求体：
        {
            "feature": "pocket",
            "material": "aluminum",
            "top_k": 10
        }
    """
    try:
        feature = (request.get("feature") or "").strip()
        if not feature:
            raise HTTPException(status_code=400, detail="feature 不能为空")
        material = (request.get("material") or "general").strip()
        top_k = int(request.get("top_k", 10))
        if not 1 <= top_k <= 100:
            raise HTTPException(status_code=400, detail="top_k 必须在 [1, 100]")

        index = _get_process_index()
        results = index.find_similar(
            feature=feature,
            material=material,
            top_k=top_k,
        )
        return {
            "feature": feature.lower(),
            "material": material.lower(),
            "count": len(results),
            "results": results,
        }
    except HTTPException:
        raise
    except (ValueError, TypeError, RuntimeError) as e:
        logger.exception("Failed to find similar quadruples")
        _raise_internal(e, context="rag.process.similar", fallback="相似工艺查询失败")


async def add_process_quadruple(request: dict[str, Any]):
    """添加工艺四元组到索引。

    请求体示例：
        {
            "feature": "pocket",
            "process": "rough_mill",
            "tool": "endmill_d10",
            "parameters": {
                "spindle_rpm": 6000,
                "feed_rate_mm_per_min": 800,
                "depth_of_cut_mm": 2.0,
                "width_of_cut_mm": 5.0
            },
            "material": "aluminum",
            "confidence": 0.9,
            "source": "experiment",
            "chunk_ids": ["chunk_001"],
            "tags": ["hsm"]
        }
    """
    try:
        from app.rag.process_quadruple import ProcessQuadruple

        required = ["feature", "process", "tool", "parameters"]
        for k in required:
            if not request.get(k):
                raise HTTPException(
                    status_code=400,
                    detail=f"缺少必填字段: {k}",
                )

        quad = ProcessQuadruple.from_dict(request)
        index = _get_process_index()
        index.add(quad)
        return {
            "added": True,
            "quadruple": quad.to_dict(),
            "stats": index.get_stats(),
        }
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning("Invalid process quadruple: %s", e)
        raise HTTPException(status_code=400, detail="工艺四元组参数无效或格式错误") from e
    except (TypeError, KeyError, RuntimeError) as e:
        logger.exception("Failed to add process quadruple")
        _raise_internal(e, context="rag.process.add", fallback="添加工艺四元组失败")


async def list_features():
    """列出所有已建模的特征类型。"""
    try:
        index = _get_process_index()
        features = index.get_features()
        return {"features": features, "count": len(features)}
    except (RuntimeError, OSError) as e:
        logger.exception("Failed to list features")
        _raise_internal(e, context="rag.process.features", fallback="获取特征列表失败")


async def get_processes_for_feature(feature: str):
    """获取指定特征对应的所有工艺方法。"""
    try:
        index = _get_process_index()
        processes = index.get_processes_for_feature(feature)
        return {
            "feature": feature.lower(),
            "processes": processes,
            "count": len(processes),
        }
    except (RuntimeError, OSError) as e:
        logger.exception("Failed to get processes for feature")
        _raise_internal(e, context="rag.process.processes", fallback="获取工艺方法列表失败")


async def get_process_stats():
    """获取工艺四元组索引统计信息。"""
    try:
        index = _get_process_index()
        return index.get_stats()
    except (RuntimeError, OSError) as e:
        logger.exception("Failed to get process stats")
        _raise_internal(e, context="rag.process.stats", fallback="获取工艺索引统计失败")


async def seed_default_process_knowledge():
    """注入默认工艺知识库（覆盖常见特征的典型工艺方案）。

    包含 12 条默认四元组，覆盖 pocket/slot/hole/thread/profile/face/chamfer
    等特征，以及 aluminum/steel/titanium 三种材料。
    """
    try:
        from app.rag.process_quadruple import seed_default_quadruples

        index = _get_process_index()
        # 先清空再注入，避免重复
        before = index.get_stats()["total_quadruples"]
        count = seed_default_quadruples(index)
        index.flush(force=True)
        return {
            "seeded": count,
            "before_total": before,
            "after_total": index.get_stats()["total_quadruples"],
            "message": f"已注入 {count} 条默认工艺四元组",
        }
    except (RuntimeError, OSError, ValueError) as e:
        logger.exception("Failed to seed default process knowledge")
        _raise_internal(e, context="rag.process.seed", fallback="注入默认知识失败")


async def flush_process_index():
    """强制将工艺四元组索引落盘。"""
    try:
        index = _get_process_index()
        ok = index.flush(force=True)
        return {"flushed": ok, "stats": index.get_stats()}
    except (RuntimeError, OSError) as e:
        logger.exception("Failed to flush process index")
        _raise_internal(e, context="rag.process.flush", fallback="落盘失败")


async def get_related_documents(request: dict[str, Any]):
    """集成点 4：通过 chunk_ids + EntityIndex 反向查询原始文档。

    请求体：
        {
            "feature": "pocket",          # 加工特征
            "material": "aluminum",       # 可选工件材料过滤
            "top_k": 10,                  # 可选，默认 10
            "include_documents": true     # 可选，默认 true（拉取完整文档内容）
        }

    返回：
        - chunk_ids_direct: 四元组直接关联的 chunk_ids
        - chunk_ids_extended: 通过 EntityIndex 扩展查找的 chunk_ids
        - chunk_ids_all: 合并去重后的全部 chunk_ids
        - documents: 完整文档内容列表（include_documents=true 时）
        - entity_index_injected / knowledge_base_injected: 软依赖注入状态
    """
    try:
        feature = (request.get("feature") or "").strip()
        if not feature:
            raise HTTPException(status_code=400, detail="feature 不能为空")
        material = (request.get("material") or "").strip() or None
        top_k = int(request.get("top_k", 10))
        if not 1 <= top_k <= 100:
            raise HTTPException(status_code=400, detail="top_k 必须在 [1, 100]")
        include_documents = bool(request.get("include_documents", True))

        index = _get_process_index()
        result = index.get_related_documents(
            feature=feature,
            material=material,
            top_k=top_k,
            include_documents=include_documents,
        )
        return result
    except HTTPException:
        raise
    except (ValueError, TypeError, RuntimeError) as e:
        logger.exception("Failed to get related documents")
        _raise_internal(
            e,
            context="rag.process.related_documents",
            fallback="关联文档查询失败",
        )
