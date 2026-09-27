"""W8.2 RAG 引用溯源（sources 字段）测试。

知识建议必须带出处：/query 两种管线（enhanced / baseline）的响应都必须
包含 ``sources``（rank/source/doc_id/similarity/preview），doc_id 可反查
来源文档（与 W2 血缘体系对齐）。
"""

from __future__ import annotations

import pytest

from app.rag import service as rag_service

# 仅标 unit：本文件 8 个同步用例挂 asyncio 标记只会产生 PytestWarning；
# 异步用例逐个显式标注（strict 模式下 async 测试必须有 asyncio 标记才会执行）。
pytestmark = [pytest.mark.unit]


class TestNormalizeRagItems:
    def test_enhanced_flat_list_passthrough(self):
        items = rag_service._normalize_rag_items(
            [{"document": "doc1", "metadata": {"source": "bosch"}, "distance": 0.2, "id": "c1"}]
        )
        assert len(items) == 1
        assert items[0]["id"] == "c1"

    def test_enhanced_dict_shape_unwrapped(self):
        """增强管线实际返回 dict（缓存条目形状，results 键下为条目列表）——
        W 引擎验证发现此前只处理 list 形状导致 sources 恒为 0。"""
        enhanced = {
            "query": "q",
            "results": [
                {"document": "d1", "metadata": {"source": "s"}, "distance": 0.2, "id": "c1"},
                {"document": "d2", "metadata": {"source": "s"}, "distance": 0.4, "id": "c2"},
            ],
            "detected_intent": "cutting_params",
            "_cache_hit": False,
        }
        items = rag_service._normalize_rag_items(enhanced)
        assert [i["id"] for i in items] == ["c1", "c2"]
        citations = rag_service._extract_citations(items)
        assert [c["rank"] for c in citations] == [1, 2]
        assert citations[0]["doc_id"] == "c1"

    def test_baseline_nested_chroma_normalized(self):
        nested = {
            "documents": [["docA", "docB"]],
            "metadatas": [[{"source": "s1"}, {"source": "s2"}]],
            "distances": [[0.1, 0.4]],
            "ids": [["id-a", "id-b"]],
        }
        items = rag_service._normalize_rag_items(nested)
        assert [i["id"] for i in items] == ["id-a", "id-b"]
        assert items[1]["metadata"]["source"] == "s2"

    def test_baseline_missing_rows_defensive(self):
        items = rag_service._normalize_rag_items({"documents": [["only-doc"]]})
        assert items[0]["document"] == "only-doc"
        assert items[0]["metadata"] == {}
        assert items[0]["distance"] is None

    def test_none_and_garbage_return_empty(self):
        assert rag_service._normalize_rag_items(None) == []
        assert rag_service._normalize_rag_items("junk") == []


class TestExtractCitations:
    def test_fields_extracted_from_enhanced_item(self):
        citations = rag_service._extract_citations(
            [
                {
                    "document": "钛合金 TC4 铣削参数推荐……" + "x" * 200,
                    "metadata": {"source": "bosch_cnc"},
                    "distance": 0.25,
                    "id": "chunk-001",
                }
            ]
        )
        c = citations[0]
        assert c["rank"] == 1
        assert c["source"] == "bosch_cnc"
        assert c["doc_id"] == "chunk-001"
        assert c["similarity"] == 0.75  # 1 - distance
        assert len(c["preview"]) == 120  # 截断

    def test_source_falls_back_to_retrieval_filter(self):
        citations = rag_service._extract_citations(
            [{"document": "d", "metadata": {}, "distance": 0.5, "id": "x", "_retrieval_source_filter": "uniwear"}]
        )
        assert citations[0]["source"] == "uniwear"

    def test_score_field_preferred_over_distance(self):
        citations = rag_service._extract_citations(
            [{"document": "d", "metadata": {}, "distance": 0.9, "id": "x", "score": 0.87}]
        )
        assert citations[0]["similarity"] == 0.87

    def test_no_score_nor_distance_gives_none(self):
        citations = rag_service._extract_citations([{"document": "d", "metadata": {}, "id": "x"}])
        assert citations[0]["similarity"] is None


class TestQueryKnowledgeIncludesSources:
    @staticmethod
    async def _call(monkeypatch, *, use_enhanced: bool):
        return await rag_service.query_knowledge(
            q="钛合金铣削",
            n_results=3,
            intent=None,
            use_enhanced=use_enhanced,
        )

    @pytest.mark.asyncio
    async def test_enhanced_pipeline_response_has_sources(self, monkeypatch):
        class _StubEngine:
            def retrieve(self, q, intent, n_results, override_source):
                return [
                    {"document": "d1", "metadata": {"source": "s"}, "distance": 0.2, "id": "c1"},
                    {"document": "d2", "metadata": {"source": "s"}, "distance": 0.4, "id": "c2"},
                ]

        monkeypatch.setattr(rag_service, "_get_rag_engine", lambda: _StubEngine())
        result = await self._call(monkeypatch, use_enhanced=True)
        assert result["pipeline"] == "enhanced"
        assert [s["doc_id"] for s in result["sources"]] == ["c1", "c2"]
        assert result["sources"][0]["rank"] == 1

    @pytest.mark.asyncio
    async def test_baseline_pipeline_response_has_sources(self, monkeypatch):
        class _StubKB:
            def query(self, query_text: str, n_results: int):
                return {
                    "documents": [["doc1"]],
                    "metadatas": [[{"source": "manual"}]],
                    "distances": [[0.3]],
                    "ids": [["id-1"]],
                }

        monkeypatch.setattr(rag_service, "kb", _StubKB())
        result = await self._call(monkeypatch, use_enhanced=False)
        assert result["pipeline"] == "baseline"
        assert result["sources"] == [
            {
                "rank": 1,
                "source": "manual",
                "doc_id": "id-1",
                "similarity": 0.7,
                "preview": "doc1",
            }
        ]
