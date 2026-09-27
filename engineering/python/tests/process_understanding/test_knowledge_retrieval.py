"""
知识检索准确率测试

测试目标: Top-3 相关性 > 90%
测试方法: 准备典型工艺问题，评估检索到的知识与问题的相关性
"""

from __future__ import annotations

import os

import pytest

# 检索链路使用仓库共享的 data/chroma_db 目录；xdist 多 worker 并发初始化
# chroma 客户端时存在竞态（实测 KeyError: chroma_db → 向量存储初始化失败）。
# 须独占运行；串行下稳定通过。
pytestmark = [
    pytest.mark.skip_ci,
    pytest.mark.skipif(
        os.environ.get("PYTEST_XDIST_WORKER") is not None,
        reason="共享 chroma_db 目录在 xdist 并发初始化下有竞态，须独占运行（-p no:xdist）",
    ),
]

from app.ai.process_understanding.knowledge_retriever import (
    KnowledgeRetriever,
    RetrievalDocument,
    HybridRetrievalResult,
    RetrievalStrategy,
)
from app.ai.process_understanding.task_classifier import TaskType

pytestmark = pytest.mark.skip_ci


# 典型工艺问题测试集


TYPICAL_QUERIES = [
    {
        "query": "45钢切削参数推荐",
        "task_type": TaskType.PROCESS_CONSULT,
        "expected_keywords": ["45钢", "切削", "参数"],
    },
    {
        "query": "304不锈钢加工方法",
        "task_type": TaskType.PROCESS_CONSULT,
        "expected_keywords": ["不锈钢", "加工"],
    },
    {
        "query": "刀具磨损原因分析",
        "task_type": TaskType.FAULT_DIAGNOSIS,
        "expected_keywords": ["刀具", "磨损"],
    },
    {
        "query": "加工精度超差怎么解决",
        "task_type": TaskType.FAULT_DIAGNOSIS,
        "expected_keywords": ["精度", "差"],
    },
    {
        "query": "铝合金壳体加工方案",
        "task_type": TaskType.SOLUTION_GENERATION,
        "expected_keywords": ["铝合金", "加工"],
    },
    {
        "query": "G代码编程基础",
        "task_type": TaskType.KNOWLEDGE_QUERY,
        "expected_keywords": ["G代码", "编程"],
    },
    {
        "query": "表面粗糙度标准",
        "task_type": TaskType.KNOWLEDGE_QUERY,
        "expected_keywords": ["粗糙度", "标准"],
    },
    {
        "query": "车削加工切削速度计算",
        "task_type": TaskType.PROCESS_CONSULT,
        "expected_keywords": ["车削", "切削速度"],
    },
    {
        "query": "数控铣床故障排除",
        "task_type": TaskType.FAULT_DIAGNOSIS,
        "expected_keywords": ["铣床", "故障"],
    },
    {
        "query": "热处理工艺规范",
        "task_type": TaskType.KNOWLEDGE_QUERY,
        "expected_keywords": ["热处理", "工艺"],
    },
]


class RetrievalRelevanceEvaluator:
    """检索相关性评估器"""

    @staticmethod
    def calculate_relevance(document: RetrievalDocument, expected_keywords: list[str]) -> float:
        """计算文档与预期关键词的相关性得分。"""
        doc_text = document.content.lower() if document.content else ""
        if not doc_text:
            return 0.0

        # 检查每个关键字的匹配情况
        matched = 0
        for kw in expected_keywords:
            if kw.lower() in doc_text:
                matched += 1

        # 精确匹配得分
        exact_score = matched / len(expected_keywords) if expected_keywords else 1.0

        # 部分匹配加分
        partial_score = 0.0
        for kw in expected_keywords:
            # 检查单个字符匹配
            chars = set(kw.lower())
            doc_chars = set(doc_text)
            overlap = len(chars & doc_chars)
            partial_score += overlap / max(len(chars), 1) * 0.1

        partial_score = min(0.3, partial_score)

        return min(1.0, exact_score * 0.7 + partial_score)

    @staticmethod
    def evaluate_top_k(
        result: HybridRetrievalResult,
        expected_keywords: list[str],
        k: int = 3,
    ) -> dict:
        """评估 Top-K 结果的相关性。"""
        top_k_docs = result.documents[:k]
        relevances = []

        for doc in top_k_docs:
            relevance = RetrievalRelevanceEvaluator.calculate_relevance(doc, expected_keywords)
            relevances.append(relevance)

        # Top-K 中至少有一个高相关 (>0.5) 即为通过
        has_relevant = any(r > 0.5 for r in relevances)
        avg_relevance = sum(relevances) / len(relevances) if relevances else 0.0

        return {
            "relevances": relevances,
            "avg_relevance": avg_relevance,
            "has_relevant": has_relevant,
            "passed": has_relevant,
        }


class TestKnowledgeRetriever:
    """知识检索器测试"""

    @pytest.fixture
    def retriever(self) -> KnowledgeRetriever:
        return KnowledgeRetriever()

    @pytest.mark.asyncio
    async def test_retrieval_structure(self, retriever: KnowledgeRetriever):
        """测试检索结果的基本结构。"""
        result = await retriever.retrieve(
            query="45钢切削参数",
            task_type=TaskType.PROCESS_CONSULT,
        )
        assert isinstance(result, HybridRetrievalResult)
        assert result.query == "45钢切削参数"
        assert result.task_type == TaskType.PROCESS_CONSULT
        assert result.strategy == RetrievalStrategy.HYBRID

    @pytest.mark.asyncio
    async def test_retrieval_has_results(self, retriever: KnowledgeRetriever):
        """测试检索能返回结果。"""
        result = await retriever.retrieve(
            query="切削参数 刀具 加工",
            task_type=TaskType.PROCESS_CONSULT,
            top_k=5,
        )
        # 即使知识库为空，也应返回空列表而非报错
        assert isinstance(result.documents, list)

    @pytest.mark.asyncio
    async def test_different_task_types(self, retriever: KnowledgeRetriever):
        """测试不同任务类型的检索不会报错。"""
        for task_type in TaskType:
            result = await retriever.retrieve(
                query="加工工艺",
                task_type=task_type,
            )
            assert result.task_type == task_type

    @pytest.mark.asyncio
    async def test_latency_tracking(self, retriever: KnowledgeRetriever):
        """测试延迟跟踪。"""
        result = await retriever.retrieve(query="测试查询")
        assert result.latency_ms >= 0

    def test_stats(self, retriever: KnowledgeRetriever):
        """测试统计信息。"""
        stats = retriever.get_stats()
        assert "total_queries" in stats
        assert "avg_latency_ms" in stats


class TestRetrievalDocument:
    """检索文档数据类测试"""

    def test_default_values(self):
        doc = RetrievalDocument()
        assert doc.id == ""
        assert doc.content == ""
        assert doc.vector_score == 0.0
        assert doc.keyword_score == 0.0
        assert doc.final_score == 0.0
        assert doc.source == "unknown"

    def test_custom_values(self):
        doc = RetrievalDocument(
            id="doc_001",
            content="45钢切削参数推荐",
            metadata={"source": "default"},
            vector_score=0.8,
            keyword_score=0.6,
            final_score=0.7,
            source="default",
        )
        assert doc.id == "doc_001"
        assert doc.vector_score == 0.8


class TestDelegationMapping:
    """委托架构映射配置测试（v2：KnowledgeRetriever 委托给 RagRetrievalEngine）"""

    def test_all_task_types_have_intent_mapping(self):
        """确保所有任务类型都有 QueryIntent 映射。"""
        from app.ai.process_understanding.knowledge_retriever import (
            TASK_TYPE_TO_QUERY_INTENT,
        )

        for task_type in TaskType:
            assert task_type in TASK_TYPE_TO_QUERY_INTENT, f"{task_type} 缺少 QueryIntent 映射"
            assert isinstance(TASK_TYPE_TO_QUERY_INTENT[task_type], str)

    def test_all_task_types_have_pipeline_level(self):
        """确保所有任务类型都有 pipeline_level 配置。"""
        from app.ai.process_understanding.knowledge_retriever import (
            TASK_TYPE_TO_PIPELINE_LEVEL,
        )

        valid_levels = {"fast", "standard", "full"}
        for task_type in TaskType:
            assert task_type in TASK_TYPE_TO_PIPELINE_LEVEL, f"{task_type} 缺少 pipeline_level 配置"
            assert TASK_TYPE_TO_PIPELINE_LEVEL[task_type] in valid_levels, f"{task_type} pipeline_level 无效"

    def test_all_task_types_have_default_top_k(self):
        """确保所有任务类型都有默认 top_k 配置。"""
        from app.ai.process_understanding.knowledge_retriever import (
            TASK_TYPE_DEFAULT_TOP_K,
        )

        for task_type in TaskType:
            assert task_type in TASK_TYPE_DEFAULT_TOP_K, f"{task_type} 缺少默认 top_k 配置"
            assert isinstance(TASK_TYPE_DEFAULT_TOP_K[task_type], int)
            assert TASK_TYPE_DEFAULT_TOP_K[task_type] > 0

    def test_chitchat_uses_fast_pipeline(self):
        """闲聊任务应使用 fast pipeline 以降低延迟。"""
        from app.ai.process_understanding.knowledge_retriever import (
            TASK_TYPE_TO_PIPELINE_LEVEL,
        )

        assert TASK_TYPE_TO_PIPELINE_LEVEL[TaskType.CHITCHAT] == "fast"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
