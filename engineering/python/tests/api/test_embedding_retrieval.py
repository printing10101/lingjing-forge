"""统一嵌入检索 API 测试（W8 出口）。

直接调用路由函数（跳过 RBAC 依赖），覆盖：
- encode：三模态编码 / 维度校验 / 空输入拒绝
- index + query 往返（kd-tree ANN 召回）
- status
"""

from __future__ import annotations

import numpy as np
import pytest

from app.api.v1.embedding_retrieval import (
    EmbeddingEncodeRequest,
    EmbeddingIndexRequest,
    EmbeddingQueryRequest,
    build_embedding_index,
    embedding_status,
    encode_embeddings,
    query_embeddings,
    reset_retriever_for_test,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolated_retriever():
    reset_retriever_for_test()
    yield
    reset_retriever_for_test()


def _rand(dim: int, n: int = 1, seed: int = 42) -> list[list[float]]:
    rng = np.random.RandomState(seed)
    return rng.rand(n, dim).astype(float).tolist()


async def test_encode_requires_at_least_one_modality():
    with pytest.raises(Exception):
        await encode_embeddings(EmbeddingEncodeRequest())


async def test_encode_llm_modality_and_dim_validation():
    resp = await encode_embeddings(
        EmbeddingEncodeRequest(llm_text_features=_rand(768, n=2))
    )
    body = resp["data"] if "data" in resp else resp
    assert len(body["fused"]) == 512
    assert "llm" in body["per_modality_mean"]


async def test_encode_rejects_wrong_dim():
    with pytest.raises(Exception):
        await encode_embeddings(EmbeddingEncodeRequest(llm_text_features=_rand(128)))


async def test_index_and_query_roundtrip():
    n = 20
    rng = np.random.RandomState(7)
    base = rng.rand(n, 512).astype(float).tolist()
    idx_resp = await build_embedding_index(
        EmbeddingIndexRequest(
            layer="cognitive",
            vectors=base,
            metadata=[{"modality": "llm", "doc_id": f"d{i}"} for i in range(n)],
        )
    )
    status = await embedding_status()
    status_body = status["data"] if "data" in status else status
    assert status_body["layers"]["cognitive"]["size"] == n

    # 用第 3 条向量作为查询 → top1 应命中自身
    query_vec = base[3]
    resp = await query_embeddings(
        EmbeddingQueryRequest(layer="cognitive", query_vector=query_vec, k=3)
    )
    body = resp["data"] if "data" in resp else resp
    assert body["count"] == 3
    assert body["results"][0]["metadata"]["doc_id"] == "d3"
    assert body["results"][0]["similarity"] > 0.99


async def test_query_unbuilt_layer_raises():
    with pytest.raises(Exception):
        await query_embeddings(
            EmbeddingQueryRequest(layer="nonexistent", query_vector=_rand(512)[0], k=1)
        )
