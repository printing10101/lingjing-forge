"""统一制造语义嵌入检索 API（W8：unified_embedding 出口）。

``app/ai/unified_embedding``（约 2.4k 行，编码器/对齐器/检索器，测试齐全
且 Top-5 召回 >80% 达标）此前是全仓最大"遗珠"——零 API、零调用方，纯库
状态。本模块把它包成最小 REST 面：

- ``POST /api/v1/embedding/encode``  三模态特征 → 512 维统一嵌入
  （纯 NumPy 投影，零模型加载、零 torch 依赖）
- ``POST /api/v1/embedding/index``   为指定层构建 kd-tree ANN 索引
- ``POST /api/v1/embedding/query``   跨层检索（支持语义轴加权 / 模态过滤）
- ``GET  /api/v1/embedding/status``  已建索引统计

层（layer）为自由键名，约定取 cognitive（知识/文本）/ perception（感知
信号）/ execution（工艺执行）三层的子集。索引为进程内状态，服务重启后
需重建（检索场景秒级）；生产化持久化属后续迭代，不在本最小面范围。

消费路径（对齐《产品叙事》W8）：
1. process_understanding 方案生成路径的相似案例 A/B 检索源；
2. "越用越懂你"在检索侧的相似工艺案例推荐。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.core.exceptions import ValidationException
from app.core.response import success

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/embedding", tags=["Unified Embedding"])

_RETRIEVER: Any = None
_RETRIEVER_LOCK = threading.Lock()
# P2-11：CrossLayerRetriever 内部无锁——build/query 并发有读到半构建
# 索引的窗口；REST 层用进程级互斥串行化全部检索操作（检索为毫秒级，
# 串行化开销可忽略）
_RETRIEVER_IO_LOCK = threading.Lock()
#: 单次索引构建/查询的向量规模上限（防认证后大 payload 打爆内存/事件循环）
_MAX_VECTORS = 5000


def _get_retriever() -> Any:
    """模块级单例（CrossLayerRetriever 为进程内索引状态）。"""
    global _RETRIEVER
    if _RETRIEVER is None:
        with _RETRIEVER_LOCK:
            if _RETRIEVER is None:
                from app.ai.unified_embedding.retriever import CrossLayerRetriever

                _RETRIEVER = CrossLayerRetriever()
    return _RETRIEVER


def reset_retriever_for_test() -> None:
    """测试隔离：清空模块级单例。"""
    global _RETRIEVER
    with _RETRIEVER_LOCK:
        _RETRIEVER = None


def _as_array(value: list[float] | list[list[float]], name: str, dim: int) -> np.ndarray:
    try:
        arr = np.asarray(value, dtype=np.float32)
    except (TypeError, ValueError) as e:
        raise ValidationException(f"{name} 不是合法的数值数组", detail={"error": str(e)}) from e
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.shape[-1] != dim:
        raise ValidationException(
            f"{name} 维度不匹配：期望 {dim}，实际 {arr.shape[-1]}",
            detail={"expected_dim": dim, "actual_dim": arr.shape[-1]},
        )
    return arr


class EmbeddingEncodeRequest(BaseModel):
    """三模态编码请求（至少提供一种模态特征）。"""

    llm_text_features: list[list[float]] | None = Field(None, description="文本嵌入 [n,768]")
    lnn_state_features: list[list[float]] | None = Field(None, description="状态特征 [n,18]")
    jepa_visual_features: list[list[float]] | None = Field(None, description="视觉特征 [n,1024]")
    modality_weights: dict[str, float] | None = Field(None, description='融合权重 {"llm":0.6,...}')


class EmbeddingIndexRequest(BaseModel):
    """索引构建请求。"""

    layer: str = Field(..., min_length=1, max_length=64, description="层名 cognitive/perception/execution")
    vectors: list[list[float]] = Field(..., min_length=1, description="512 维嵌入 [n,512]")
    metadata: list[dict[str, Any]] | None = Field(None, description="与向量行数对齐的元数据")


class EmbeddingQueryRequest(BaseModel):
    """跨层检索请求。"""

    layer: str = Field(..., min_length=1, max_length=64)
    query_vector: list[float] = Field(..., description="512 维查询嵌入")
    k: int = Field(5, ge=1, le=100)
    axis_weights: dict[str, float] | None = Field(None, description='语义轴权重 {"material":0.8,...}')
    modality_filter: str | None = Field(None, description="按元数据 modality 字段过滤")


@router.post("/encode", dependencies=[Depends(require_permission("model:predict"))])
async def encode_embeddings(req: EmbeddingEncodeRequest):
    """三模态特征 → 512 维统一嵌入（纯 NumPy 投影，无模型加载）。"""
    if not any([req.llm_text_features, req.lnn_state_features, req.jepa_visual_features]):
        raise ValidationException(
            "至少提供一种模态特征（llm_text_features / lnn_state_features / jepa_visual_features）"
        )
    try:
        from app.ai.unified_embedding.encoder import MultiModalEncoder

        encoder = MultiModalEncoder()
        per_modality: dict[str, list[float]] = {}
        embeddings: list[np.ndarray] = []
        weights: list[float] = []
        default_weights = {"llm": 1.0, "lnn": 1.0, "jepa": 1.0}
        effective_weights = {**default_weights, **(req.modality_weights or {})}

        if req.llm_text_features:
            emb = encoder.encode_llm(_as_array(req.llm_text_features, "llm_text_features", 768))
            per_modality["llm"] = emb.mean(axis=0).tolist()
            embeddings.append(emb)
            weights.append(float(effective_weights["llm"]))
        if req.lnn_state_features:
            emb = encoder.encode_lnn(_as_array(req.lnn_state_features, "lnn_state_features", 18))
            per_modality["lnn"] = emb.mean(axis=0).tolist()
            embeddings.append(emb)
            weights.append(float(effective_weights["lnn"]))
        if req.jepa_visual_features:
            emb = encoder.encode_jepa(_as_array(req.jepa_visual_features, "jepa_visual_features", 1024))
            per_modality["jepa"] = emb.mean(axis=0).tolist()
            embeddings.append(emb)
            weights.append(float(effective_weights["jepa"]))

        fused = encoder.fuse(embeddings, weights=weights)
        return success(
            {
                "fused": fused.tolist(),
                "dim": int(fused.shape[-1]),
                "per_modality_mean": per_modality,
            }
        )
    except ValidationException:
        raise
    except (ValueError, TypeError, KeyError) as e:
        raise ValidationException(f"嵌入编码失败：{e}") from e


@router.post("/index", dependencies=[Depends(require_permission("model:predict"))])
async def build_embedding_index(req: EmbeddingIndexRequest):
    """为指定层构建 kd-tree ANN 索引（进程内，重启后需重建）。"""
    vectors = _as_array(req.vectors, "vectors", 512)
    if vectors.shape[0] > _MAX_VECTORS:
        raise ValidationException(
            f"单次索引构建向量数超上限 {_MAX_VECTORS}",
            detail={"limit": _MAX_VECTORS, "actual": vectors.shape[0]},
        )
    if req.metadata is not None and len(req.metadata) != vectors.shape[0]:
        raise ValidationException(
            "metadata 行数必须与 vectors 行数对齐",
            detail={"vectors": vectors.shape[0], "metadata": len(req.metadata)},
        )
    retriever = _get_retriever()

    def _build() -> dict[str, Any]:
        with _RETRIEVER_IO_LOCK:
            retriever.build_index(req.layer, vectors, req.metadata)
            return retriever.get_layer_stats(req.layer)

    # P2-11：kd-tree 构建是 CPU 密集操作，移出事件循环
    try:
        stats = await asyncio.to_thread(_build)
    except ValueError as e:
        raise ValidationException(str(e)) from e
    return success({"layer": req.layer, "size": stats.get("size", int(vectors.shape[0])), "dim": 512})


@router.post("/query", dependencies=[Depends(require_permission("model:predict"))])
async def query_embeddings(req: EmbeddingQueryRequest):
    """跨层检索（kd-tree ANN，支持语义轴加权与模态过滤）。"""
    query_vec = _as_array(req.query_vector, "query_vector", 512)
    retriever = _get_retriever()

    def _query():
        with _RETRIEVER_IO_LOCK:
            return retriever.query(
                req.layer,
                query_vec[0],
                k=req.k,
                axis_weights=req.axis_weights,
                modality_filter=req.modality_filter,
            )

    try:
        results = await asyncio.to_thread(_query)
    except ValueError as e:
        raise ValidationException(str(e)) from e
    return success({"layer": req.layer, "results": [r.to_dict() for r in results], "count": len(results)})


@router.get("/status", dependencies=[Depends(require_permission("model:predict"))])
async def embedding_status():
    """已建索引统计（层名 / 规模 / 维度）。"""
    retriever = _get_retriever()
    return success({"layers": retriever.get_all_layer_stats()})
