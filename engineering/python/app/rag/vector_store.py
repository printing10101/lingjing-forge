"""ChromaDB vector store operations wrapper.

Provides persistent vector storage with cosine similarity search.

Refactored to use a thread-safe lazy singleton holder instead of the
``global _`` pattern.  :func:`get_vector_store` is also exposed as a
FastAPI dependency factory so it can be used with ``Depends(get_vector_store)``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_COLLECTION = "knowledge_base"
# W 引擎验证修复：必须 normpath——chromadb 1.0.0 的 Rust sqlite 层处理
# 含 ``..`` 段的路径会 panic（rust\sqlite\src\db.rs:157 slice 越界），
# 未规范化的 ``app/rag/../../data/chroma_db`` 曾导致 RAG 向量库整库不可用。
DEFAULT_PERSIST_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "chroma_db"))

# HNSW 索引调优参数（参考 ChromaDB 官方文档与 HNSW 论文最佳实践）
# 这些参数显著影响向量检索的召回率、索引构建质量和查询延迟。
# 默认值经过中等规模知识库（1万~100万向量）场景调优，可通过环境变量覆盖。
#
# 参考：
# - HNSW 论文: Malkov & Yashunin, "Efficient and robust approximate nearest
# neighbor search using Hierarchical Navigable Small World graphs" (2018)
# - ChromaDB 文档: https://docs.trychroma.com/usage-guide#changing-the-distance-method
#
# M (每层最大连接数):
# - 控制图的度数，影响索引大小与召回率的平衡
# - 默认 16，取 32 适合中等规模知识库，召回率更优
# - 增大 M 提升召回但增加内存和索引构建时间
#
# ef_construction (构建时搜索宽度):
# - 控制索引构建时的搜索范围，影响索引质量
# - 默认 100，取 200 显著提升索引质量，构建略慢但值得
# - 建议为 M 的 5~10 倍
#
# ef_search (查询时搜索宽度):
# - 控制查询时的搜索范围，直接影响召回率
# - 默认 10（偏低），取 100 显著提升召回率
# - 增大 ef_search 提升召回但增加查询延迟
#
# num_threads (构建并行线程数):
# - 索引构建并行度，4 适合多数服务器 CPU
DEFAULT_HNSW_M = int(os.environ.get("RAG_HNSW_M", "32"))
DEFAULT_HNSW_EF_CONSTRUCTION = int(os.environ.get("RAG_HNSW_EF_CONSTRUCTION", "200"))
DEFAULT_HNSW_EF_SEARCH = int(os.environ.get("RAG_HNSW_EF_SEARCH", "100"))
DEFAULT_HNSW_NUM_THREADS = int(os.environ.get("RAG_HNSW_NUM_THREADS", "4"))
DEFAULT_HNSW_SPACE = os.environ.get("RAG_HNSW_SPACE", "cosine")


def _build_hnsw_metadata() -> dict[str, Any]:
    """构建 ChromaDB collection 的 HNSW metadata 配置。

    ChromaDB 通过 collection metadata 中以 ``hnsw:`` 为前缀的键来传递
    HNSW 参数（见 ChromaDB ``get_or_create_collection`` 文档）。

    Returns:
        可直接传给 ``get_or_create_collection(metadata=...)`` 的字典。
    """
    return {
        "hnsw:space": DEFAULT_HNSW_SPACE,  # 距离度量：cosine / l2 / ip
        "hnsw:M": DEFAULT_HNSW_M,  # 每层最大连接数
        "hnsw:construction_ef": DEFAULT_HNSW_EF_CONSTRUCTION,  # 构建时搜索宽度（chromadb 1.0 改名自 ef_construction）
        "hnsw:search_ef": DEFAULT_HNSW_EF_SEARCH,  # 查询时搜索宽度（chromadb 1.0 改名自 ef_search）
        "hnsw:num_threads": DEFAULT_HNSW_NUM_THREADS,  # 构建并行线程数
    }


class VectorStore:
    """ChromaDB-backed vector store with collection management and search."""

    def __init__(
        self,
        persist_directory: str | None = None,
        collection_name: str = DEFAULT_COLLECTION,
    ):
        self._persist_directory = persist_directory or DEFAULT_PERSIST_DIR
        self._collection_name = collection_name
        self._client: Any = None
        self._collection: Any = None
        self._closed = False  # P3 幂等性标志位

    @property
    def persist_directory(self) -> str:
        return self._persist_directory

    def _open_client(self):
        import chromadb

        os.makedirs(self._persist_directory, exist_ok=True)
        return chromadb.PersistentClient(path=self._persist_directory)

    def _archive_persist_dir(self) -> str | None:
        """把 persist 目录整体改名归档（legacy schema / 损坏库自愈用）。

        Returns:
            归档后的新路径；目录不存在或改名失败返回 None。
        """
        persist = self._persist_directory
        if not os.path.isdir(persist):
            return None
        base = f"{persist}.legacy-{time.strftime('%Y%m%d_%H%M%S')}"
        target = base
        counter = 2
        while os.path.exists(target):
            target = f"{base}_{counter}"
            counter += 1
        try:
            os.rename(persist, target)
        except OSError:
            logger.warning("ChromaDB 旧库归档失败: %s -> %s", persist, target, exc_info=True)
            return None
        return target

    def _ensure_client(self):
        if self._client is not None:
            return
        try:
            self._client = self._open_client()
        except ImportError:
            logger.warning("ChromaDB 未安装，RAG 向量存储不可用。请安装 chromadb 以启用持久化向量检索。")
            self._client = None
            raise RuntimeError(
                "ChromaDB 未安装，RAG 功能无法启动。请安装 chromadb（pip install chromadb）后重试。"
            ) from None
        except BaseException as e:  # noqa: BLE001 - pyo3 PanicException 不继承 Exception
            if type(e).__name__ in ("KeyboardInterrupt", "SystemExit"):
                raise
            # 引擎验证修复（2026-09）：legacy 库（旧版 chromadb schema）触发
            # Rust 迁移 panic。归档机制此前为一次性手工操作——客户机从旧版本
            # 升级仍会复现。此处自动归档旧库并重建空库一次，升级路径自愈；
            # 归档失败或重建仍失败才显式报错。
            archived = self._archive_persist_dir()
            if archived is None:
                logger.error("ChromaDB 初始化失败: %s", e, exc_info=True)
                self._client = None
                raise RuntimeError(f"向量存储初始化失败: {e}") from e
            logger.warning("ChromaDB 初始化失败（%s），旧库已归档至 %s，正在重建空库", e, archived)
            try:
                self._client = self._open_client()
            except BaseException as retry_err:  # noqa: BLE001
                if type(retry_err).__name__ in ("KeyboardInterrupt", "SystemExit"):
                    raise
                logger.error("ChromaDB 归档重建后仍初始化失败: %s", retry_err, exc_info=True)
                self._client = None
                raise RuntimeError(f"向量存储初始化失败（归档重建后）: {retry_err}") from retry_err
        logger.info("ChromaDB client initialized: %s", self._persist_directory)

    def _ensure_collection(self):
        self._ensure_client()
        if self._collection is not None:
            return
        # HNSW 调优参数通过 collection metadata 传入（键以 "hnsw:" 为前缀）
        # 参数选取依据见模块顶部注释；支持通过环境变量覆盖
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata=_build_hnsw_metadata(),
        )
        logger.info(
            "ChromaDB collection ready: %s (count=%d, M=%d, ef_construction=%d, ef_search=%d)",
            self._collection_name,
            self._collection.count(),
            DEFAULT_HNSW_M,
            DEFAULT_HNSW_EF_CONSTRUCTION,
            DEFAULT_HNSW_EF_SEARCH,
        )

    def add(
        self,
        ids: str | list[str],
        documents: str | list[str],
        embeddings: list[float] | list[list[float]] | None = None,
        metadatas: dict | list[dict] | None = None,
    ) -> list[str]:
        self._ensure_collection()

        if isinstance(ids, str):
            ids = [ids]
        if isinstance(documents, str):
            documents = [documents]
        if metadatas is None:
            metadatas = [{} for _ in documents]
        elif isinstance(metadatas, dict):
            metadatas = [metadatas for _ in documents]

        self._collection.add(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        return ids

    def query(
        self,
        query_embedding: list[float],
        n_results: int = 5,
        where: dict | None = None,
    ) -> dict[str, list]:
        self._ensure_collection()
        result = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        return result

    def get(
        self,
        ids: list[str] | None = None,
        where: dict | None = None,
        limit: int = 100,
    ) -> dict[str, list]:
        self._ensure_collection()
        kwargs: dict[str, Any] = {
            "include": ["documents", "metadatas"],
            "limit": limit,
        }
        if ids:
            kwargs["ids"] = ids
        if where:
            kwargs["where"] = where
        return self._collection.get(**kwargs)

    def delete(self, ids: list[str] | None = None, where: dict | None = None) -> int:
        self._ensure_collection()
        before = self._collection.count()
        if ids:
            self._collection.delete(ids=ids)
        elif where:
            self._collection.delete(where=where)
        after = self._collection.count()
        deleted = before - after
        logger.info("Deleted %d documents from collection", deleted)
        return deleted

    def count(self) -> int:
        self._ensure_collection()
        return self._collection.count()

    def list_documents(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        self._ensure_collection()
        result = self._collection.get(
            limit=limit,
            offset=offset,
            include=["documents", "metadatas"],
        )
        docs: list[dict[str, Any]] = []
        ids = result.get("ids", [])
        documents = result.get("documents", [])
        metadatas = result.get("metadatas", [])
        for i in range(len(ids)):
            docs.append(
                {
                    "id": ids[i] if i < len(ids) else "",
                    "document": documents[i] if i < len(documents) else "",
                    "metadata": metadatas[i] if i < len(metadatas) else {},
                }
            )
        return docs

    def optimize_index(self) -> bool:
        """Trigger index compaction/optimization for performance."""
        self._ensure_client()
        try:
            logger.info("Starting ChromaDB index optimization...")
            start = time.time()
            self._collection = None
            self._client = None
            self._ensure_collection()
            elapsed = (time.time() - start) * 1000
            logger.info("Index optimization completed in %.0fms", elapsed)
            return True
        except (OSError, RuntimeError, ValueError) as e:
            # ChromaDB 索引优化失败（IO/运行时/类型错误），不影响主流程
            logger.warning("Index optimization failed: %s", e, exc_info=True)
            return False

    def export_backup(self, backup_dir: str) -> str:
        self._ensure_collection()
        os.makedirs(backup_dir, exist_ok=True)
        import shutil

        src = self._persist_directory
        backup_path = os.path.join(
            backup_dir,
            f"chroma_backup_{time.strftime('%Y%m%d_%H%M%S')}",
        )
        shutil.copytree(src, backup_path)
        logger.info("Backup exported to %s", backup_path)
        return backup_path

    def import_backup(self, backup_dir: str) -> bool:
        import shutil

        if not os.path.exists(backup_dir):
            logger.error("Backup directory not found: %s", backup_dir)
            return False
        self._collection = None
        self._client = None
        if os.path.exists(self._persist_directory):
            shutil.rmtree(self._persist_directory)
        shutil.copytree(backup_dir, self._persist_directory)
        self._ensure_collection()
        logger.info("Backup restored from %s", backup_dir)
        return True

    def get_stats(self) -> dict[str, Any]:
        self._ensure_collection()
        total = self._collection.count()
        persist_size = 0
        persist_dir = Path(self._persist_directory)
        if persist_dir.exists():
            persist_size = sum(f.stat().st_size for f in persist_dir.rglob("*") if f.is_file())
        return {
            "total_documents": total,
            "collection_name": self._collection_name,
            "persist_directory": self._persist_directory,
            "persist_size_bytes": persist_size,
        }

    def close(self) -> None:
        """显式关闭 ChromaDB 客户端，释放底层 SQLite/DuckDB 资源。

        必要性：
            PersistentClient 内部持有 SQLite 句柄和 DuckDB 引擎，未显式关闭
            可能导致 WAL 未 checkpoint、内存索引未 flush、Windows 文件句柄
            锁定（下次启动 PersistentClient 重建失败）。

        幂等性：
            多次调用安全；已关闭后再次调用为 no-op。

        异常处理：
            关闭过程中的任何异常被捕获并记录 warning，不向上抛出，避免
            阻断 FastAPI shutdown 流程。
        """
        if self._closed:
            return
        try:
            if self._collection is not None:
                self._collection = None
            if self._client is not None:
                # ChromaDB 不同版本提供 close()/reset()/stop() 之一，按优先级尝试
                close_fn = getattr(self._client, "close", None) or getattr(self._client, "stop", None)
                if close_fn is not None:
                    try:
                        close_fn()
                    except Exception as e:
                        logger.warning("ChromaDB close() raised: %s", e, exc_info=True)
                self._client = None
                logger.info("ChromaDB PersistentClient closed")
        except Exception as e:
            logger.warning("Failed to close ChromaDB client: %s", e, exc_info=True)
        finally:
            self._closed = True


# Thread-safe lazy singleton (替代 ``global _`` 模式)
# 原实现使用 ``global _vector_store``；现改为将状态封装在内部类中，并使用
# 线程锁保证并发环境下的安全。调用方仍然通过 :func:`get_vector_store` 访问，
# 行为与重构前完全一致：首次访问时懒初始化、之后返回同一实例。


class _VectorStoreHolder:
    """Thread-safe lazy holder for the :class:`VectorStore` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: VectorStore | None = None

    def get(self) -> VectorStore:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            # 双重检查：可能在获取锁的过程中其他线程已创建实例
            if self._instance is not None:
                return self._instance
            self._instance = VectorStore()
            logger.info("Initialized vector store")
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _VectorStoreHolder()


def get_vector_store() -> VectorStore:
    """获取共享的 :class:`VectorStore` 单例；首次访问时懒初始化。

    Returns:
        :class:`VectorStore` 实例（应用生命周期内同一实例）。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_vector_store)``。
        实现是线程安全的，行为与重构前完全一致。
    """
    return _holder.get()


__all__ = [
    "VectorStore",
    "get_vector_store",
    "DEFAULT_COLLECTION",
    "DEFAULT_PERSIST_DIR",
]
