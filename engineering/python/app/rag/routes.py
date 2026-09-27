"""RAG knowledge base API routes."""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, UploadFile, File, Form

from app.auth.dependencies import get_current_user
from app.auth.permissions import require_permission
from .service import (  # noqa: E402
    query_knowledge as query_knowledge_service,
    get_stats as get_stats_service,
    add_knowledge as add_knowledge_service,
    delete_knowledge as delete_knowledge_service,
    list_documents as list_documents_service,
    load_default_knowledge as load_default_knowledge_service,
    load_rag_json as load_rag_json_service,
    search_by_source as search_by_source_service,
    delete_by_source as delete_by_source_service,
    import_document as import_document_service,
    export_backup as export_backup_service,
    import_backup as import_backup_service,
    optimize_index as optimize_index_service,
    cleanup_orphaned as cleanup_orphaned_service,
    get_enhancement_status as get_enhancement_status_service,
    get_cache_stats as get_cache_stats_service,
    clear_cache as clear_cache_service,
    retrieve_from_signal_fusion as retrieve_from_signal_fusion_service,
    run_evaluation as run_evaluation_service,
    run_ablation_study as run_ablation_study_service,
    generate_comparison_report as generate_comparison_report_service,
    recommend_process as recommend_process_service,
    find_similar_quadruples as find_similar_quadruples_service,
    add_process_quadruple as add_process_quadruple_service,
    list_features as list_features_service,
    get_processes_for_feature as get_processes_for_feature_service,
    get_process_stats as get_process_stats_service,
    seed_default_process_knowledge as seed_default_process_knowledge_service,
    flush_process_index as flush_process_index_service,
    get_related_documents as get_related_documents_service,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rag", tags=["RAG 知识库"])

# 注意：不得在模块级初始化 ChromaDB/知识库（曾导致桌面运行时 import 期 panic）；
# 重型组件一律经 FastAPI Depends 或懒加载单例在请求期构建。


# v2 增强：懒加载单例（避免在导入时初始化重型组件）

_rag_engine_instance = None
_rag_engine_lock = threading.Lock()


@router.get("/query", dependencies=[Depends(get_current_user)])
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
        增强检索结果，包含 results / detected_intent / enhancements 等字段
    """
    return await query_knowledge_service(q, n_results, intent, use_enhanced)


@router.get("/stats", dependencies=[Depends(get_current_user)])
async def get_stats():
    return await get_stats_service()


@router.post("/add", dependencies=[Depends(require_permission("rag:write"))])
async def add_knowledge(request: dict[str, Any]):
    return await add_knowledge_service(request)


@router.delete("/{doc_id}", dependencies=[Depends(require_permission("rag:write"))])
async def delete_knowledge(doc_id: str):
    return await delete_knowledge_service(doc_id)


@router.get("/list", dependencies=[Depends(get_current_user)])
async def list_documents(limit: int = Query(50, ge=1, le=500)):
    return await list_documents_service(limit)


@router.post("/load/default", dependencies=[Depends(require_permission("rag:write"))])
async def load_default_knowledge():
    return await load_default_knowledge_service()


@router.post("/load/json", dependencies=[Depends(require_permission("rag:write"))])
async def load_rag_json():
    return await load_rag_json_service()


@router.get("/search", dependencies=[Depends(get_current_user)])
async def search_by_source(
    source: str = Query(..., description="知识来源"),
    query: str = Query("", description="可选搜索查询"),
    n_results: int = Query(5, ge=1, le=50),
):
    return await search_by_source_service(source, query, n_results)


@router.delete("/source/{source}", dependencies=[Depends(require_permission("rag:write"))])
async def delete_by_source(source: str):
    return await delete_by_source_service(source)


@router.post("/import/file", dependencies=[Depends(require_permission("rag:write"))])
async def import_document(
    file: UploadFile = File(...),
    chunk_size: int = Form(400, ge=100, le=10000),
    chunk_overlap: int = Form(60, ge=0, le=1000),
):
    return await import_document_service(file, chunk_size, chunk_overlap)


@router.post("/backup/export", dependencies=[Depends(require_permission("backup:export"))])
async def export_backup(backup_dir: str = Query("./backups/rag")):
    return await export_backup_service(backup_dir)


@router.post("/backup/import", dependencies=[Depends(require_permission("backup:import"))])
async def import_backup(backup_dir: str = Query(..., description="备份目录路径")):
    return await import_backup_service(backup_dir)


@router.post("/maintenance/optimize", dependencies=[Depends(require_permission("rag:write"))])
async def optimize_index():
    return await optimize_index_service()


@router.post("/maintenance/cleanup", dependencies=[Depends(require_permission("rag:write"))])
async def cleanup_orphaned():
    return await cleanup_orphaned_service()


# v2 增强 API 端点
# 以下端点暴露 RAG pipeline 的诊断、评估与 ablation study 能力，
# 便于运维与研发团队量化各增强模块的贡献度。


@router.get("/v2/enhancement/status", dependencies=[Depends(get_current_user)])
async def get_enhancement_status():
    """获取 RAG 增强模块的实时状态与性能指标。

    返回各模块（parallel_retrieval / hybrid_search / reranker /
    query_rewrite / hyde / result_cache）的启用状态与统计信息，
    用于运维诊断与灰度发布验证。
    """
    return await get_enhancement_status_service()


@router.get("/v2/cache/stats", dependencies=[Depends(get_current_user)])
async def get_cache_stats():
    """获取检索结果 LRU 缓存的命中统计。

    用于判断缓存效果与容量是否需要调整。
    """
    return await get_cache_stats_service()


@router.delete("/v2/cache", dependencies=[Depends(require_permission("rag:write"))])
async def clear_cache():
    """清空检索结果 LRU 缓存。

    在知识库内容更新后调用，避免返回过期的缓存结果。
    """
    return await clear_cache_service()


@router.post("/v2/signal-fusion/retrieve", dependencies=[Depends(require_permission("rag:write"))])
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
    return await retrieve_from_signal_fusion_service(payload)


@router.post("/v2/evaluation", dependencies=[Depends(require_permission("rag:write"))])
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
    return run_evaluation_service(top_k, category, difficulty, use_rag_engine)


@router.post("/v2/ablation", dependencies=[Depends(require_permission("rag:write"))])
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
    return run_ablation_study_service(top_k, category, difficulty)


@router.post("/v2/comparison", dependencies=[Depends(require_permission("rag:write"))])
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
    return generate_comparison_report_service(top_k, category, difficulty, run_ablation)


# 工艺决策四元组 API（CAMWorks TechDB 思路落地）
# 落地竞品分析识别的核心补强点：Feature Process Tool Parameter 四元组建模。
# 通过 chunk_ids 字段与 EntityIndex 互查，实现 quadruple 原始文档溯源。


@router.post("/process/recommend", dependencies=[Depends(require_permission("rag:write"))])
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
    return await recommend_process_service(request)


@router.post("/process/similar", dependencies=[Depends(require_permission("rag:write"))])
async def find_similar_quadruples(request: dict[str, Any]):
    """查找相似工艺记录（3 层匹配：精确 / 同特征 / 材料迁移）。

    请求体：
        {
            "feature": "pocket",
            "material": "aluminum",
            "top_k": 10
        }
    """
    return await find_similar_quadruples_service(request)


@router.post("/process/add", dependencies=[Depends(require_permission("rag:write"))])
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
    return await add_process_quadruple_service(request)


@router.get("/process/features", dependencies=[Depends(get_current_user)])
async def list_features():
    """列出所有已建模的特征类型。"""
    return await list_features_service()


@router.get("/process/{feature}/processes", dependencies=[Depends(get_current_user)])
async def get_processes_for_feature(feature: str):
    """获取指定特征对应的所有工艺方法。"""
    return await get_processes_for_feature_service(feature)


@router.get("/process/stats", dependencies=[Depends(get_current_user)])
async def get_process_stats():
    """获取工艺四元组索引统计信息。"""
    return await get_process_stats_service()


@router.post("/process/seed", dependencies=[Depends(require_permission("rag:write"))])
async def seed_default_process_knowledge():
    """注入默认工艺知识库（覆盖常见特征的典型工艺方案）。

    包含 12 条默认四元组，覆盖 pocket/slot/hole/thread/profile/face/chamfer
    等特征，以及 aluminum/steel/titanium 三种材料。
    """
    return await seed_default_process_knowledge_service()


@router.post("/process/flush", dependencies=[Depends(require_permission("rag:write"))])
async def flush_process_index():
    """强制将工艺四元组索引落盘。"""
    return await flush_process_index_service()


@router.post("/process/related-documents", dependencies=[Depends(require_permission("rag:write"))])
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
    return await get_related_documents_service(request)
