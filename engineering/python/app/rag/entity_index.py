"""Entity inverted index for cross-source retrieval.

维护 entity → chunk_ids 的倒排映射，支持：
- 通过实体名快速定位相关 chunk（无需向量检索）
- 跨数据源关联（同一实体在不同 source 中的 chunk）
- 持久化到磁盘（JSON 格式），避免每次重启重建
- 线程安全，支持并发读写

设计动机：
原架构中 chunk 与 entity 完全分离，跨源检索需要并行查询多个 source。
引入倒排索引后，可以先通过实体名定位相关 chunk_ids，再批量获取，
将跨源检索从 O(sources × n) 降为 O(1 + related_chunks)。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


class EntityIndex:
    """线程安全的 entity → chunk_ids 倒排索引。

    数据结构：
        _index: dict[str, set[str]]  # entity_name (lowercase) -> {chunk_id, ...}
        _chunk_entities: dict[str, set[str]]  # chunk_id -> {entity_name, ...}（反向索引，用于删除）

    持久化：
        - 保存到 ``{persist_dir}/entity_index.json``
        - 采用追加写 + 定期 flush 策略，避免每次 add 都写磁盘
    """

    def __init__(self, persist_dir: str | None = None):
        """初始化 entity 倒排索引。

        Args:
            persist_dir: 持久化目录路径。None 表示仅内存模式（不落盘）。
        """
        self._index: dict[str, set[str]] = defaultdict(set)
        self._chunk_entities: dict[str, set[str]] = defaultdict(set)
        self._lock = threading.RLock()
        self._persist_dir = persist_dir
        self._persist_path: str | None = None
        self._dirty = False  # 是否有未落盘的修改
        self._last_flush_time = 0.0
        self._flush_interval = 30.0  # 30 秒内最多 flush 一次，避免频繁 IO

        # 统计信息
        self._total_add_calls = 0
        self._total_query_calls = 0
        self._total_query_hits = 0

        if persist_dir:
            self._persist_path = os.path.join(persist_dir, "entity_index.json")
            self._load_from_disk()

    # 索引操作

    def add(self, chunk_id: str, entities: list[str]) -> None:
        """将 chunk_id 关联到一组实体。

        Args:
            chunk_id: 文档/chunk 的唯一 ID
            entities: 实体名列表（会自动转小写去重）
        """
        if not chunk_id or not entities:
            return

        # 统一小写、去重、去空
        normalized = {e.strip().lower() for e in entities if e and e.strip()}
        if not normalized:
            return

        with self._lock:
            # 先清理 chunk_id 的旧关联（支持重复 add 更新）
            self._remove_chunk_internal(chunk_id)

            for entity in normalized:
                self._index[entity].add(chunk_id)
                self._chunk_entities[chunk_id].add(entity)

            self._total_add_calls += 1
            self._dirty = True

    def add_batch(self, items: list[tuple[str, list[str]]]) -> int:
        """批量添加 chunk-entity 关联。

        Args:
            items: [(chunk_id, [entity1, entity2, ...]), ...]

        Returns:
            成功添加的 chunk 数量
        """
        if not items:
            return 0

        count = 0
        with self._lock:
            for chunk_id, entities in items:
                if not chunk_id or not entities:
                    continue
                normalized = {e.strip().lower() for e in entities if e and e.strip()}
                if not normalized:
                    continue

                self._remove_chunk_internal(chunk_id)
                for entity in normalized:
                    self._index[entity].add(chunk_id)
                    self._chunk_entities[chunk_id].add(entity)
                count += 1

            if count > 0:
                self._total_add_calls += count
                self._dirty = True

        return count

    def get_chunks(
        self,
        entities: list[str],
        mode: str = "union",
    ) -> list[str]:
        """查询与给定实体关联的 chunk_ids。

        Args:
            entities: 实体名列表（会自动转小写）
            mode: "union" 取并集（任一实体匹配）；"intersection" 取交集（所有实体都匹配）

        Returns:
            chunk_id 列表（无序）
        """
        if not entities:
            return []

        normalized = [e.strip().lower() for e in entities if e and e.strip()]
        if not normalized:
            return []

        with self._lock:
            self._total_query_calls += 1

            if mode == "intersection":
                # 交集：取所有实体都包含的 chunk
                result_set: set[str] | None = None
                for entity in normalized:
                    chunk_set = self._index.get(entity, set()).copy()
                    if result_set is None:
                        result_set = chunk_set
                    else:
                        result_set &= chunk_set
                    if not result_set:
                        return []
                result = list(result_set) if result_set else []
            else:
                # 并集（默认）：任一实体匹配的 chunk
                result_set = set()
                for entity in normalized:
                    result_set.update(self._index.get(entity, set()))
                result = list(result_set)

            if result:
                self._total_query_hits += 1
            return result

    def get_entities(self, chunk_id: str) -> list[str]:
        """查询某个 chunk 关联的所有实体。"""
        with self._lock:
            return list(self._chunk_entities.get(chunk_id, set()))

    def remove_chunk(self, chunk_id: str) -> int:
        """删除 chunk 的所有实体关联。

        Args:
            chunk_id: 要删除的 chunk ID

        Returns:
            删除的实体关联数
        """
        with self._lock:
            return self._remove_chunk_internal(chunk_id)

    def _remove_chunk_internal(self, chunk_id: str) -> int:
        """内部删除（调用方需持锁）。"""
        entities = self._chunk_entities.pop(chunk_id, set())
        if not entities:
            return 0

        for entity in entities:
            chunk_set = self._index.get(entity)
            if chunk_set:
                chunk_set.discard(chunk_id)
                if not chunk_set:
                    self._index.pop(entity, None)

        self._dirty = True
        return len(entities)

    # 持久化

    def flush(self, force: bool = False) -> bool:
        """将索引落盘。

        Args:
            force: True 强制落盘；False 受 _flush_interval 节流

        Returns:
            是否实际执行了落盘
        """
        if not self._persist_path:
            return False

        with self._lock:
            if not force and not self._dirty:
                return False

            now = time.time()
            if not force and (now - self._last_flush_time) < self._flush_interval:
                return False

            try:
                os.makedirs(str(self._persist_dir), exist_ok=True)
                # 转换 set 为 list 以便 JSON 序列化
                serializable = {
                    "index": {k: list(v) for k, v in self._index.items() if v},
                    "chunk_entities": {k: list(v) for k, v in self._chunk_entities.items() if v},
                    "stats": {
                        "total_add_calls": self._total_add_calls,
                        "total_query_calls": self._total_query_calls,
                        "total_query_hits": self._total_query_hits,
                    },
                    "flushed_at": now,
                }
                tmp_path = self._persist_path + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(serializable, f, ensure_ascii=False)
                os.replace(tmp_path, self._persist_path)

                self._dirty = False
                self._last_flush_time = now
                return True
            except (OSError, TypeError, ValueError) as e:
                logger.warning(
                    "Failed to flush entity index to %s: %s",
                    self._persist_path,
                    e,
                    exc_info=True,
                )
                return False

    def _load_from_disk(self) -> None:
        """从磁盘加载索引。"""
        if not self._persist_path or not os.path.exists(self._persist_path):
            return

        try:
            with open(self._persist_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 恢复 index
            for entity, chunk_ids in data.get("index", {}).items():
                self._index[entity] = set(chunk_ids)

            # 恢复 chunk_entities
            for chunk_id, entities in data.get("chunk_entities", {}).items():
                self._chunk_entities[chunk_id] = set(entities)

            # 恢复统计
            stats = data.get("stats", {})
            self._total_add_calls = stats.get("total_add_calls", 0)
            self._total_query_calls = stats.get("total_query_calls", 0)
            self._total_query_hits = stats.get("total_query_hits", 0)

            self._dirty = False
            logger.info(
                "EntityIndex loaded from disk: %d entities, %d chunks",
                len(self._index),
                len(self._chunk_entities),
            )
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as e:
            logger.warning(
                "Failed to load entity index from %s: %s",
                self._persist_path,
                e,
                exc_info=True,
            )

    # 统计与诊断

    def get_stats(self) -> dict[str, Any]:
        """获取索引统计信息。"""
        with self._lock:
            total_chunks = len(self._chunk_entities)
            total_relations = sum(len(v) for v in self._index.values())
            avg_entities_per_chunk = total_relations / total_chunks if total_chunks > 0 else 0.0
            return {
                "entity_count": len(self._index),
                "chunk_count": total_chunks,
                "relation_count": total_relations,
                "avg_entities_per_chunk": round(avg_entities_per_chunk, 2),
                "persist_path": self._persist_path,
                "dirty": self._dirty,
                "total_add_calls": self._total_add_calls,
                "total_query_calls": self._total_query_calls,
                "total_query_hits": self._total_query_hits,
                "query_hit_rate": (
                    round(self._total_query_hits / self._total_query_calls, 4) if self._total_query_calls > 0 else 0.0
                ),
            }

    def clear(self) -> None:
        """清空索引（不影响磁盘文件）。"""
        with self._lock:
            self._index.clear()
            self._chunk_entities.clear()
            self._dirty = True


# 单例 holder

_entity_index_instance: EntityIndex | None = None
_entity_index_lock = threading.Lock()


def get_entity_index(persist_dir: str | None = None) -> EntityIndex:
    """获取共享的 EntityIndex 单例。

    Args:
        persist_dir: 持久化目录。仅在首次调用时生效。

    Returns:
        EntityIndex 实例
    """
    global _entity_index_instance
    if _entity_index_instance is not None:
        return _entity_index_instance
    with _entity_index_lock:
        if _entity_index_instance is not None:
            return _entity_index_instance

        if persist_dir is None:
            # 默认持久化到 data/entity_index/
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            persist_dir = os.path.join(project_root, "data", "entity_index")

        _entity_index_instance = EntityIndex(persist_dir=persist_dir)
        logger.info("EntityIndex singleton initialized at %s", persist_dir)
    return _entity_index_instance


__all__ = ["EntityIndex", "get_entity_index"]
