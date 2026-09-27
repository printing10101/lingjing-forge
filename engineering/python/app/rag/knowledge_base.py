"""Knowledge base management for RAG system.

Provides ChromaDB-backed semantic search with CollectionProxy adapter
for backward compatibility.

v2 增强：
- 集成 EntityIndex 倒排索引，支持 entity → chunk_ids 检索
- add() 方法新增 entities 参数，自动维护倒排索引
- 新增 query_by_entities() 方法，支持基于实体的跨源检索
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from app.dependencies import get_embedding_service
from app.rag.entity_index import EntityIndex, get_entity_index
from app.dependencies import get_vector_store
from app.config.limits import DEFAULT_QUERY_LIMIT

logger = logging.getLogger(__name__)

DEFAULT_KNOWLEDGE_JSON_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "knowledge", "default_knowledge.json"
)

# ``DEFAULT_QUERY_LIMIT`` 由 ``app.config.limits`` 集中管理，
# 与 database/rule_db.py / rules/api.py 共享同一基准值。


class CollectionProxy:
    """Proxy object that mimics ChromaDB collection API for the KnowledgeStore."""

    def __init__(self, store: KnowledgeStore):
        self._store = store

    def get(self, ids=None, include=None, **kwargs):
        if include is None:
            include = ["documents", "metadatas"]
        records = self._store.get_all()
        if ids:
            records = [r for r in records if r["id"] in ids]
        result: dict[str, list] = {
            "ids": [],
            "documents": [],
            "metadatas": [],
        }
        for r in records:
            result["ids"].append(r["id"])
            result["documents"].append(r.get("document", ""))
            result["metadatas"].append(r.get("metadata", {}))
        return result

    def query(self, query_texts=None, n_results=5, where=None, **kwargs):
        if query_texts is None:
            query_texts = [""]
        query = query_texts[0] if isinstance(query_texts, list) else str(query_texts)
        records = self._store.query(query, n_results, filters=where)
        result: dict[str, list] = {
            "ids": [[]],
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }
        for r in records:
            result["ids"][0].append(r.get("id", ""))
            result["documents"][0].append(r.get("document", ""))
            result["metadatas"][0].append(r.get("metadata", {}))
            result["distances"][0].append(r.get("distance", 0.0))
        return result

    def add(self, ids=None, documents=None, metadatas=None, **kwargs):
        documents = documents or []
        metadatas = metadatas or []
        ids = ids or []
        embeddings = kwargs.get("embeddings")
        entities_list = kwargs.get("entities_list") or []
        for i, doc in enumerate(documents):
            doc_id = ids[i] if i < len(ids) else None
            meta = metadatas[i] if i < len(metadatas) else {}
            emb = embeddings[i] if embeddings and i < len(embeddings) else None
            ents = entities_list[i] if i < len(entities_list) else None
            self._store.add(doc, metadata=meta, doc_id=doc_id, embedding=emb, entities=ents)

    def count(self):
        return self._store.count()

    def delete(self, ids=None, where=None, **kwargs):
        if ids:
            for doc_id in ids:
                self._store.delete(doc_id)
        elif where:
            source = where.get("source")
            if source:
                self._store.delete_by_source(source)


class KnowledgeStore:
    """ChromaDB-backed document store with semantic vector search.

    v2 增强：集成 EntityIndex 倒排索引，支持 entity → chunk_ids 检索。
    """

    def __init__(self):
        self._vs = get_vector_store()
        self._emb = get_embedding_service()
        self._entity_index: EntityIndex = get_entity_index()
        self._next_id = 1

    def add(
        self,
        document: str,
        metadata: dict | None = None,
        doc_id: str | None = None,
        embedding: list[float] | None = None,
        entities: list[str] | None = None,
    ) -> str:
        """添加文档到知识库。

        Args:
            document: 文档内容
            metadata: 元数据
            doc_id: 文档 ID（不传则自动生成）
            embedding: 预计算的 embedding（不传则自动计算）
            entities: 实体列表（会写入倒排索引，支持基于实体的检索）
        """
        doc_id = doc_id or f"doc_{self._next_id}"
        self._next_id += 1
        if embedding is None:
            embedding = self._emb.embed(document)
        metadata = metadata or {}
        metadata["updated_at"] = time.time()
        self._vs.add(
            ids=doc_id,
            documents=document,
            embeddings=embedding,
            metadatas=metadata,
        )

        # 维护 entity 倒排索引
        if entities:
            try:
                self._entity_index.add(doc_id, entities)
            except (OSError, RuntimeError, ValueError) as e:
                logger.debug(
                    "EntityIndex add failed for doc %s: %s",
                    doc_id,
                    e,
                    exc_info=True,
                )

        return doc_id

    def query_by_entities(
        self,
        entities: list[str],
        mode: str = "union",
    ) -> list[dict[str, Any]]:
        """通过实体倒排索引检索文档。

        Args:
            entities: 实体名列表
            mode: "union" 取并集；"intersection" 取交集

        Returns:
            匹配的文档列表（含 id/document/metadata/distance 字段）
        """
        chunk_ids = self._entity_index.get_chunks(entities, mode=mode)
        if not chunk_ids:
            return []

        results: list[dict[str, Any]] = []
        for chunk_id in chunk_ids:
            doc = self.get_by_id(chunk_id)
            if doc:
                doc["distance"] = 0.0  # 实体精确匹配，距离为 0
                results.append(doc)
        return results

    def query(self, text: str, top_k: int = 5, filters: dict | None = None) -> list[dict[str, Any]]:
        query_embedding = self._emb.embed(text)
        chroma_filters = None
        if filters:
            chroma_filters = filters
        result = self._vs.query(
            query_embedding=query_embedding,
            n_results=top_k,
            where=chroma_filters,
        )
        records: list[dict[str, Any]] = []
        ids_list = result.get("ids", [[]])[0]
        docs_list = result.get("documents", [[]])[0]
        metas_list = result.get("metadatas", [[]])[0]
        dists_list = result.get("distances", [[]])[0]
        for i in range(len(ids_list)):
            records.append(
                {
                    "id": ids_list[i],
                    "document": docs_list[i] if i < len(docs_list) else "",
                    "metadata": metas_list[i] if i < len(metas_list) else {},
                    "distance": dists_list[i] if i < len(dists_list) else 1.0,
                }
            )
        return records

    def query_by_source(
        self,
        source: str,
        query: str = "",
        n_results: int = 5,
        extra_filters: dict | None = None,
    ) -> dict:
        # 合并 source 过滤与额外元数据过滤（如 cluster_tag、category 等）
        if extra_filters:
            where: dict = {"$and": [{"source": source}, extra_filters]}
        else:
            where = {"source": source}
        if query:
            query_embedding = self._emb.embed(query)
            result = self._vs.query(
                query_embedding=query_embedding,
                n_results=n_results,
                where=where,
            )
        else:
            result = self._vs.get(where=where, limit=n_results)
            result.setdefault("distances", [[0.0] * len(result.get("ids", [[]])[0])])

        return result

    def get_all(self) -> list[dict[str, Any]]:
        return self._vs.list_documents(limit=DEFAULT_QUERY_LIMIT)

    def get_by_id(self, doc_id: str) -> dict[str, Any] | None:
        result = self._vs.get(ids=[doc_id], limit=1)
        ids_list = result.get("ids", [])
        if not ids_list:
            return None
        docs_list = result.get("documents", [])
        metas_list = result.get("metadatas", [])
        return {
            "id": ids_list[0],
            "document": docs_list[0] if docs_list else "",
            "metadata": metas_list[0] if metas_list else {},
        }

    def delete(self, doc_id: str) -> bool:
        before = self._vs.count()
        self._vs.delete(ids=[doc_id])
        # 同步清理 entity 倒排索引
        try:
            self._entity_index.remove_chunk(doc_id)
        except (OSError, RuntimeError, ValueError) as e:
            logger.debug(
                "EntityIndex remove failed for doc %s: %s",
                doc_id,
                e,
                exc_info=True,
            )
        return self._vs.count() < before

    def delete_by_source(self, source: str) -> int:
        # 先获取该 source 下所有 doc_id，用于清理 entity 索引
        docs_to_clean: list[str] = []
        try:
            all_docs = self._vs.list_documents(limit=DEFAULT_QUERY_LIMIT)
            docs_to_clean = [d["id"] for d in all_docs if d.get("metadata", {}).get("source") == source]
        except (OSError, RuntimeError, ValueError, KeyError) as e:
            logger.debug(
                "Failed to list docs for entity index cleanup: %s",
                e,
                exc_info=True,
            )

        before = self._vs.count()
        self._vs.delete(where={"source": source})
        deleted_count = before - self._vs.count()

        # 批量清理 entity 倒排索引
        if docs_to_clean:
            try:
                for doc_id in docs_to_clean:
                    self._entity_index.remove_chunk(doc_id)
            except (OSError, RuntimeError, ValueError) as e:
                logger.debug(
                    "EntityIndex batch remove failed: %s",
                    e,
                    exc_info=True,
                )

        return deleted_count

    def count(self) -> int:
        return self._vs.count()

    def load_default_knowledge(self) -> int:
        default_entries = [
            {
                "id": "default_machining_001",
                "document": (
                    "45钢推荐切削参数：粗车切削速度80-150m/min，"
                    "进给量0.2-0.5mm/r，切削深度1-4mm。"
                    "精车切削速度120-200m/min，进给量0.05-0.15mm/r，"
                    "切削深度0.1-0.5mm。"
                ),
                "metadata": {
                    "category": "切削参数",
                    "type": "guideline",
                    "source": "default",
                },
            },
            {
                "id": "default_machining_002",
                "document": "铝合金6061推荐切削参数：粗车切削速度200-400m/min，进给量0.3-0.8mm/r，切削深度1-5mm。",
                "metadata": {
                    "category": "切削参数",
                    "type": "guideline",
                    "source": "default",
                },
            },
            {
                "id": "default_machining_003",
                "document": "304不锈钢切削参数：粗车切削速度60-120m/min，进给量0.15-0.3mm/r，切削深度1-3mm。由于加工硬化特性，需保持连续切削避免刀具在已加工表面停留。",
                "metadata": {
                    "category": "切削参数",
                    "type": "guideline",
                    "source": "default",
                },
            },
            {
                "id": "default_machining_004",
                "document": "工艺路线规划原则：粗加工→半精加工→精加工→超精加工。各阶段应选择不同机床和刀具以保证精度和效率。",
                "metadata": {
                    "category": "工艺规划",
                    "type": "guideline",
                    "source": "default",
                },
            },
            {
                "id": "default_machining_005",
                "document": "G代码编程基础：G00快速定位，G01直线插补，G02/G03圆弧插补，G90绝对编程，G91增量编程。",
                "metadata": {
                    "category": "NC编程",
                    "type": "reference",
                    "source": "default",
                },
            },
        ]
        documents = [e["document"] for e in default_entries]
        embeddings = self._emb.embed_batch(documents)
        ids = [e["id"] for e in default_entries]
        metadatas = [e["metadata"] for e in default_entries]
        self._vs.add(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)
        return len(default_entries)

    def load_rag_json_knowledge(self, json_path: str | None = None) -> dict[str, int]:
        if json_path is None:
            json_path = DEFAULT_KNOWLEDGE_JSON_PATH
        stats = {"success": 0, "skipped": 0, "errors": 0}
        try:
            if not os.path.exists(json_path):
                stats["errors"] += 1
                return stats
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = data if isinstance(data, list) else data.get("entries", [])
            batch_docs = []
            batch_ids = []
            batch_metas = []
            for item in items:
                try:
                    doc_id = item.get("id") or item.get("doc_id")
                    document = item.get("document") or item.get("content") or item.get("text", "")
                    metadata = item.get("metadata") or item.get("meta", {})
                    if not document:
                        stats["skipped"] += 1
                        continue
                    batch_docs.append(document)
                    batch_ids.append(doc_id)
                    batch_metas.append(metadata)
                except (AttributeError, KeyError, TypeError) as parse_err:
                    # 单条 JSON 项目字段缺失/类型错误时跳过，不阻塞整体导入
                    logger.debug(
                        "Skipped malformed item in batch import: %s",
                        parse_err,
                        exc_info=True,
                    )
                    stats["skipped"] += 1
            if batch_docs:
                embeddings = self._emb.embed_batch(batch_docs)
                self._vs.add(
                    ids=batch_ids,
                    documents=batch_docs,
                    embeddings=embeddings,
                    metadatas=batch_metas,
                )
                stats["success"] = len(batch_docs)
        except json.JSONDecodeError:
            stats["errors"] += 1
        except (OSError, RuntimeError, ValueError, TypeError) as store_err:
            # 向量库写入或 embedding 计算失败（IO/运行时/类型错误）
            logger.debug(
                "Failed to import knowledge batch: %s",
                store_err,
                exc_info=True,
            )
            stats["errors"] += 1
        return stats

    def get_stats(self) -> dict[str, Any]:
        return self._vs.get_stats()


class KnowledgeBase:
    """KnowledgeBase with ChromaDB-compatible API surface."""

    def __init__(self):
        self._store = KnowledgeStore()
        self.collection = CollectionProxy(self._store)
        # 暴露 entity index 供 RagRetrievalEngine 使用
        self._entity_index = self._store._entity_index

    def add_knowledge(
        self,
        document: str,
        metadata: dict | None = None,
        doc_id: str | None = None,
        entities: list[str] | None = None,
    ) -> dict:
        """添加知识文档。

        Args:
            document: 文档内容
            metadata: 元数据
            doc_id: 文档 ID
            entities: 实体列表（写入倒排索引）
        """
        doc_id = self._store.add(document, metadata=metadata, doc_id=doc_id, entities=entities)
        return {"doc_id": doc_id}

    def query(self, query_text: str = "", n_results: int = 5, top_k: int = 5) -> dict[str, Any]:
        results = self._store.query(query_text, top_k=n_results or top_k)
        return {"documents": results, "total_results": len(results)}

    def query_by_entities(
        self,
        entities: list[str],
        mode: str = "union",
    ) -> dict[str, Any]:
        """通过实体倒排索引检索文档。

        Args:
            entities: 实体名列表
            mode: "union" 取并集；"intersection" 取交集

        Returns:
            {"documents": [...], "total_results": int}
        """
        results = self._store.query_by_entities(entities, mode=mode)
        return {"documents": results, "total_results": len(results)}

    @property
    def entity_index(self) -> EntityIndex:
        """暴露 EntityIndex 实例供外部使用。"""
        return self._entity_index

    def delete(self, doc_id: str) -> bool:
        return self._store.delete(doc_id)

    def query_by_source(
        self,
        source: str,
        query: str = "",
        n_results: int = 5,
        extra_filters: dict | None = None,
    ) -> dict:
        return self._store.query_by_source(source, query, n_results, extra_filters=extra_filters)

    def delete_by_source(self, source: str) -> int:
        return self._store.delete_by_source(source)

    def count(self) -> int:
        return self._store.count()

    def list_documents(self, limit: int = 50) -> list[dict]:
        return [
            {
                "id": d["id"],
                "metadata": d.get("metadata", {}),
                "created_at": d["metadata"].get("created_at"),
            }
            for d in self._store.get_all()[:limit]
        ]

    def get_stats(self) -> dict[str, Any]:
        return self._store.get_stats()

    def load_default_knowledge(self) -> int:
        return self._store.load_default_knowledge()

    def load_rag_json_knowledge(self, json_path: str | None = None) -> dict[str, int]:
        return self._store.load_rag_json_knowledge(json_path)


class _KnowledgeBaseHolder:
    """Thread-safe lazy holder for the :class:`KnowledgeBase` singleton."""

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self._instance: KnowledgeBase | None = None

    def get(self) -> KnowledgeBase:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            # 双重检查：可能在获取锁的过程中其他线程已创建实例
            if self._instance is not None:
                return self._instance
            self._instance = KnowledgeBase()
            logger.info("Initialized ChromaDB-backed knowledge base")
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _KnowledgeBaseHolder()


def get_knowledge_base() -> KnowledgeBase:
    """获取共享的 :class:`KnowledgeBase` 单例；首次访问时懒初始化。

    Returns:
        :class:`KnowledgeBase` 实例（应用生命周期内同一实例）。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_knowledge_base)``。
        实现是线程安全的，行为与重构前完全一致。
    """
    return _holder.get()


__all__ = [
    "CollectionProxy",
    "KnowledgeStore",
    "KnowledgeBase",
    "get_knowledge_base",
    "DEFAULT_KNOWLEDGE_JSON_PATH",
]
