"""rag 纯逻辑单元测试（tokenizer / RRF 融合）。"""

from __future__ import annotations

import pytest

from app.rag.hybrid_search import reciprocal_rank_fusion
from app.rag.tokenizer import (
    DOMAIN_LEXICON,
    _char_level_tokenize,
    tokenize,
    tokenize_batch,
)

pytestmark = pytest.mark.unit


class TestTokenizer:
    def test_empty_string(self):
        assert tokenize("") == []

    def test_none_or_whitespace(self):
        assert tokenize("   ") == []

    def test_english_lowercase(self):
        assert tokenize("Hello World") == ["hello", "world"]

    def test_batch(self):
        assert tokenize_batch(["a b", "c"]) == [["a", "b"], ["c"]]

    def test_char_level_chinese(self):
        # fallback：中文按字符，英文按词
        assert _char_level_tokenize("TC4 切削") == ["tc4", "切", "削"]

    def test_domain_lexicon_non_empty(self):
        assert len(DOMAIN_LEXICON) > 0
        assert "TC4" in DOMAIN_LEXICON
        assert "6061-T6" in DOMAIN_LEXICON


class TestReciprocalRankFusion:
    def test_basic_fusion(self):
        vector = [{"id": "a"}, {"id": "b"}]
        bm25 = [{"id": "b"}, {"id": "c"}]
        result = reciprocal_rank_fusion(vector, bm25, k=60, top_k=10)
        ids = [r["id"] for r in result]
        # b 同时出现在两路，RRF 分数最高，应排第一
        assert ids[0] == "b"
        assert set(ids) == {"a", "b", "c"}

    def test_rrf_score_added(self):
        vector = [{"id": "a"}]
        bm25 = []
        result = reciprocal_rank_fusion(vector, bm25, k=60, top_k=10)
        assert "rrf_score" in result[0]
        assert result[0]["rrf_score"] > 0

    def test_top_k_truncation(self):
        vector = [{"id": f"d{i}"} for i in range(5)]
        bm25 = []
        result = reciprocal_rank_fusion(vector, bm25, k=60, top_k=3)
        assert len(result) == 3

    def test_empty_inputs(self):
        assert reciprocal_rank_fusion([], [], k=60, top_k=10) == []

    def test_doc_id_from_document_field(self):
        # 无 id 字段时用 document 字段前 100 字符
        vector = [{"document": "doc-alpha"}]
        bm25 = [{"document": "doc-beta"}]
        result = reciprocal_rank_fusion(vector, bm25, k=60, top_k=10)
        assert len(result) == 2

    def test_weights(self):
        # vector_weight=0 时，仅 bm25 贡献分数
        vector = [{"id": "a"}, {"id": "b"}]
        bm25 = [{"id": "b"}]
        result = reciprocal_rank_fusion(vector, bm25, k=60, top_k=10, vector_weight=0.0)
        # b 在 bm25 rank 0 1/61；a 无分数 排最后
        assert result[0]["id"] == "b"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
