"""工艺决策四元组建模（CAMWorks TechDB 思路落地）。

竞品分析识别的核心补强点：CAMWorks 的 TechDB 通过 Feature → Process → Tool → Parameter
四元组建模实现自动工艺决策。本模块在现有 RAG 实体索引之上，新增工艺决策专用层。

数据模型：
    Feature（特征）  : pocket, slot, hole, profile, face, thread, chamfer...
    Process（工艺）  : rough_mill, finish_mill, drill, tap, ream, contour...
    Tool（刀具）     : endmill_d10, drill_d8, ballmill_r3...
    Parameter（参数）: spindle_rpm, feed_rate, depth_of_cut, width_of_cut...

四元组关系：Quadruple(feature, process, tool, parameters, confidence, source)

查询接口：
    - recommend_process(feature, material) → 推荐 (process, tool, params) 列表
    - find_similar_quadruples(feature) → 历史相似工艺记录
    - cross_source_lookup(feature) → 关联 RAG chunk_ids（复用 EntityIndex）

持久化：
    {persist_dir}/process_quadruple.json
    与 entity_index.json 同目录，互不干扰。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ProcessQuadruple:
    """工艺决策四元组。

    Attributes:
        feature: 加工特征 (pocket/slot/hole/profile/face/thread/chamfer...)
        process: 工艺方法 (rough_mill/finish_mill/drill/tap/ream/contour...)
        tool: 刀具标识 (endmill_d10/drill_d8/ballmill_r3...)
        parameters: 切削参数 {spindle_rpm, feed_rate, depth_of_cut, width_of_cut, ...}
        material: 工件材料 (aluminum/steel/titanium/cast_iron...)
        confidence: 置信度 [0, 1]，来源可靠性（手册=1.0, 经验=0.7, 推断=0.5）
        source: 数据来源 ("manual" / "experiment" / "knowledge_base" / "inferred")
        chunk_ids: 关联 RAG chunk（用于溯源到原始文档）
        tags: 自定义标签
    """

    feature: str
    process: str
    tool: str
    parameters: dict
    material: str = "general"
    confidence: float = 0.8
    source: str = "manual"
    chunk_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.feature = self.feature.strip().lower()
        self.process = self.process.strip().lower()
        self.tool = self.tool.strip().lower()
        self.material = self.material.strip().lower()
        if not 0 <= self.confidence <= 1:
            raise ValueError(f"confidence 必须在 [0, 1]，当前 {self.confidence}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProcessQuadruple":
        return cls(
            feature=data["feature"],
            process=data["process"],
            tool=data["tool"],
            parameters=data.get("parameters", {}),
            material=data.get("material", "general"),
            confidence=data.get("confidence", 0.8),
            source=data.get("source", "manual"),
            chunk_ids=data.get("chunk_ids", []),
            tags=data.get("tags", []),
        )


class ProcessQuadrupleIndex:
    """工艺决策四元组索引（线程安全）。

    数据结构：
        _by_feature:  dict[feature, list[Quadruple]]
        _by_feature_process: dict[(feature, process), list[Quadruple]]
        _by_material: dict[material, list[Quadruple]]
        _all: list[Quadruple]  # 全量列表（用于相似度检索）

    设计说明：
        作为独立的工艺决策层，同时通过可选的 ``entity_index`` 与 EntityIndex
        自动桥接：
        - ``add()`` 时自动将四元组的 feature/process/tool/material 提取为实体，
          通过 chunk_ids 关联写入 EntityIndex（若已注入）；
        - ``get_related_documents()`` 通过 chunk_ids + EntityIndex 反向查询
          原始文档（若 knowledge_base 已注入）。

        链路：
            quadruple → chunk_ids → EntityIndex.get_chunks(entities) → 原始文档

    集成点 4（软依赖设计）：
        ``entity_index`` 与 ``knowledge_base`` 均为可选注入。未注入时降级为
        仅维护内部索引，不阻断主链路。
    """

    def __init__(
        self,
        persist_dir: str | None = None,
        entity_index: Any = None,
        knowledge_base: Any = None,
    ):
        """初始化工艺四元组索引。

        Args:
            persist_dir: 持久化目录路径。None 表示仅内存模式。
            entity_index: 可选的 EntityIndex 实例（集成点 4 自动桥接）。
                提供后，``add()`` 会自动将四元组实体写入 EntityIndex，
                ``get_related_documents()`` 可借助 EntityIndex 扩展查找。
                未提供时降级为仅维护内部索引。
            knowledge_base: 可选的 KnowledgeBase 实例（用于反向查询原始文档）。
                提供后，``get_related_documents()`` 可返回完整文档内容；
                未提供时仅返回 chunk_ids。
        """
        self._persist_dir = persist_dir
        self._persist_path: str | None = None
        self._lock = threading.RLock()

        self._entity_index = entity_index
        self._knowledge_base = knowledge_base

        self._by_feature: dict[str, list[ProcessQuadruple]] = defaultdict(list)
        self._by_feature_process: dict[tuple[str, str], list[ProcessQuadruple]] = defaultdict(list)
        self._by_material: dict[str, list[ProcessQuadruple]] = defaultdict(list)
        self._all: list[ProcessQuadruple] = []

        self._dirty = False
        self._last_flush_time = 0.0
        # 5s flush 间隔：平衡 IO 压力与崩溃恢复数据丢失风险
        # （30s 间隔在进程崩溃时最多丢失 30s 写入，对工艺决策四元组这种
        # 低频但高价值数据不可接受；5s 已足够规避高频 IO）
        self._flush_interval = 5.0

        if persist_dir:
            self._persist_path = os.path.join(persist_dir, "process_quadruple.json")
            self._load_from_disk()

    def set_entity_index(self, entity_index: Any) -> None:
        """运行时注入 EntityIndex（软依赖）。

        用于无法在构造时提供 EntityIndex 的场景（如循环依赖规避）。
        注入后不会自动回填已有四元组；如需回填请显式调用
        ``rebuild_entity_index_links()``。
        """
        with self._lock:
            self._entity_index = entity_index

    def set_knowledge_base(self, knowledge_base: Any) -> None:
        """运行时注入 KnowledgeBase（软依赖，用于反向查询原始文档）。"""
        with self._lock:
            self._knowledge_base = knowledge_base

    # 写入

    @staticmethod
    def _extract_entities(quad: ProcessQuadruple) -> list[str]:
        """从四元组提取实体列表（用于 EntityIndex 桥接）。

        提取范围：feature / process / tool / material（去空、去重）。
        Tool 与 Material 仅在非空时纳入，避免污染索引。
        """
        entities = [quad.feature, quad.process, quad.tool]
        if quad.material:
            entities.append(quad.material)
        # 去重保持顺序
        seen: set[str] = set()
        result: list[str] = []
        for e in entities:
            if e and e not in seen:
                seen.add(e)
                result.append(e)
        return result

    def _sync_to_entity_index(self, quad: ProcessQuadruple) -> None:
        """集成点 4：将四元组的实体通过 chunk_ids 关联写入 EntityIndex。

        软依赖设计：
        - ``self._entity_index`` 未注入时直接返回，不阻断主链路；
        - ``quad.chunk_ids`` 为空时跳过（无关联文档可桥接）；
        - 单个 chunk 同步异常时记录 warning，继续处理后续 chunk；
        - 整体异常不抛出，仅记录 warning（避免影响 ``add()`` 主流程）。

        Args:
            quad: 已写入内部索引的工艺四元组
        """
        if self._entity_index is None:
            return
        if not quad.chunk_ids:
            return

        entities = self._extract_entities(quad)
        if not entities:
            return

        try:
            for chunk_id in quad.chunk_ids:
                try:
                    self._entity_index.add(chunk_id, entities)
                except (RuntimeError, OSError, ValueError, TypeError) as e:
                    logger.warning(
                        "EntityIndex.add 失败 (chunk_id=%s, entities=%s): %s",
                        chunk_id,
                        entities,
                        e,
                        exc_info=True,
                    )
        except (RuntimeError, OSError, ValueError, TypeError) as e:
            # 兜底保护：任何意外异常都不应阻断 add 主链路
            logger.warning(
                "_sync_to_entity_index 整体失败 (quad=%s/%s): %s",
                quad.feature,
                quad.process,
                e,
                exc_info=True,
            )

    def add(self, quad: ProcessQuadruple) -> None:
        """添加工艺四元组。

        集成点 4：若已注入 ``entity_index``，会自动将四元组的
        feature/process/tool/material 通过 chunk_ids 关联写入 EntityIndex。
        """
        with self._lock:
            self._by_feature[quad.feature].append(quad)
            self._by_feature_process[(quad.feature, quad.process)].append(quad)
            self._by_material[quad.material].append(quad)
            self._all.append(quad)
            self._dirty = True

        # 同步 EntityIndex（在锁外执行，避免与 EntityIndex 内部锁形成嵌套）
        self._sync_to_entity_index(quad)

    def add_batch(self, quads: list[ProcessQuadruple]) -> int:
        """批量添加。

        集成点 4：批量同步到 EntityIndex（使用 ``add_batch`` 提升效率）。
        """
        if not quads:
            return 0
        with self._lock:
            for q in quads:
                self._by_feature[q.feature].append(q)
                self._by_feature_process[(q.feature, q.process)].append(q)
                self._by_material[q.material].append(q)
                self._all.append(q)
            self._dirty = True

        # 批量同步 EntityIndex（构造 (chunk_id, entities) 列表）
        if self._entity_index is not None:
            items: list[tuple[str, list[str]]] = []
            for q in quads:
                if not q.chunk_ids:
                    continue
                entities = self._extract_entities(q)
                if not entities:
                    continue
                for chunk_id in q.chunk_ids:
                    items.append((chunk_id, entities))
            if items:
                try:
                    self._entity_index.add_batch(items)
                except (RuntimeError, OSError, ValueError, TypeError) as e:
                    logger.warning(
                        "EntityIndex.add_batch 失败 (%d items): %s",
                        len(items),
                        e,
                        exc_info=True,
                    )

        # 批量写入是低频高价值操作（如 seed_default_quadruples、知识库重建），
        # 立即强制 flush 防止进程崩溃时整批数据丢失
        if self._persist_path:
            self.flush(force=True)

        return len(quads)

    def rebuild_entity_index_links(self) -> int:
        """回填所有已加载四元组到 EntityIndex。

        适用场景：
        - 运行时通过 ``set_entity_index`` 注入 EntityIndex 后需要回填；
        - 磁盘加载完成后需要重建索引完整性；
        - EntityIndex 持久化丢失后重建。

        Returns:
            成功同步到 EntityIndex 的 chunk 关联数
        """
        if self._entity_index is None:
            return 0

        items: list[tuple[str, list[str]]] = []
        with self._lock:
            for q in self._all:
                if not q.chunk_ids:
                    continue
                entities = self._extract_entities(q)
                if not entities:
                    continue
                for chunk_id in q.chunk_ids:
                    items.append((chunk_id, entities))

        if not items:
            return 0

        try:
            return self._entity_index.add_batch(items)
        except (RuntimeError, OSError, ValueError, TypeError) as e:
            logger.warning(
                "rebuild_entity_index_links 失败 (%d items): %s",
                len(items),
                e,
                exc_info=True,
            )
            return 0

    # 查询：核心决策接口

    def recommend_process(
        self,
        feature: str,
        material: str = "general",
        top_k: int = 5,
    ) -> list[dict]:
        """推荐工艺方案（CAMWorks TechDB 式自动决策）。

        Args:
            feature: 加工特征 (pocket/slot/hole...)
            material: 工件材料
            top_k: 返回前 K 条推荐

        Returns:
            推荐方案列表，按 confidence 降序，每项含完整四元组 + 评分
        """
        feature = feature.strip().lower()
        material = material.strip().lower()

        with self._lock:
            candidates = list(self._by_feature.get(feature, []))

        # 材料匹配加权
        scored: list[tuple[float, ProcessQuadruple]] = []
        for q in candidates:
            score = q.confidence
            if q.material == material:
                score += 0.2  # 材料匹配加分
            elif q.material == "general":
                score += 0.05  # 通用参数小幅加分
            scored.append((score, q))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                **q.to_dict(),
                "score": round(score, 3),
            }
            for score, q in scored[:top_k]
        ]

    def find_by_feature_process(
        self,
        feature: str,
        process: str,
    ) -> list[ProcessQuadruple]:
        """按 (feature, process) 精确查找。"""
        with self._lock:
            return list(
                self._by_feature_process.get(
                    (feature.strip().lower(), process.strip().lower()),
                    [],
                )
            )

    def find_similar(
        self,
        feature: str,
        material: str = "general",
        top_k: int = 10,
    ) -> list[dict]:
        """查找相似工艺记录（模糊匹配）。

        匹配策略：
        1. 完全匹配 (feature + material)
        2. 特征匹配 (feature only)
        3. 同材料其他特征（材料迁移参考）
        """
        feature = feature.strip().lower()
        material = material.strip().lower()

        with self._lock:
            # 层级 1：精确匹配
            exact = [q for q in self._by_feature.get(feature, []) if q.material == material]
            # 层级 2：同特征不同材料
            same_feature = [q for q in self._by_feature.get(feature, []) if q.material != material]
            # 层级 3：同材料不同特征（材料迁移参考）
            same_material = [q for q in self._by_material.get(material, []) if q.feature != feature]

        results = []
        for q in exact:
            results.append({**q.to_dict(), "match_level": "exact"})
        for q in same_feature:
            results.append({**q.to_dict(), "match_level": "feature_only"})
        for q in same_material:
            results.append({**q.to_dict(), "match_level": "material_transfer"})

        return results[:top_k]

    def get_features(self) -> list[str]:
        """获取所有已建模的特征类型。"""
        with self._lock:
            return sorted(self._by_feature.keys())

    def get_processes_for_feature(self, feature: str) -> list[str]:
        """获取某特征对应的所有工艺方法。"""
        feature = feature.strip().lower()
        with self._lock:
            seen: set[str] = set()
            for (f, p), _ in self._by_feature_process.items():
                if f == feature:
                    seen.add(p)
            return sorted(seen)

    def get_stats(self) -> dict:
        """获取索引统计信息。"""
        with self._lock:
            return {
                "total_quadruples": len(self._all),
                "feature_count": len(self._by_feature),
                "process_count": len({p for _, p in self._by_feature_process.keys()}),
                "material_count": len(self._by_material),
                "tool_count": len({q.tool for q in self._all}),
            }

    # 持久化

    def flush(self, force: bool = False) -> bool:
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
                serializable = {
                    "quadruples": [q.to_dict() for q in self._all],
                    "stats": self.get_stats(),
                    "flushed_at": now,
                }
                tmp_path = self._persist_path + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(serializable, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self._persist_path)
                self._dirty = False
                self._last_flush_time = now
                return True
            except (OSError, TypeError, ValueError) as e:
                logger.warning("Failed to flush process quadruple index: %s", e)
                return False

    def _load_from_disk(self) -> None:
        if not self._persist_path or not os.path.exists(self._persist_path):
            return
        try:
            with open(self._persist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for qd in data.get("quadruples", []):
                try:
                    q = ProcessQuadruple.from_dict(qd)
                    self._by_feature[q.feature].append(q)
                    self._by_feature_process[(q.feature, q.process)].append(q)
                    self._by_material[q.material].append(q)
                    self._all.append(q)
                except (KeyError, ValueError) as e:
                    logger.warning("跳过无效四元组: %s", e)
            logger.info(
                "已加载 %d 条工艺四元组",
                len(self._all),
            )
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("加载工艺四元组索引失败: %s", e)

        # 集成点 4：磁盘恢复后回填 EntityIndex，保证索引完整性
        # （磁盘文件只保存四元组本身，EntityIndex 有自己的持久化，
        # 但若 EntityIndex 持久化丢失或被清理，需从四元组重建关联）
        if self._entity_index is not None:
            try:
                synced = self.rebuild_entity_index_links()
                if synced > 0:
                    logger.info(
                        "ProcessQuadrupleIndex: 已回填 %d 条 chunk-entity 关联到 EntityIndex",
                        synced,
                    )
            except (RuntimeError, OSError, ValueError, TypeError) as e:
                logger.warning(
                    "磁盘加载后回填 EntityIndex 失败: %s",
                    e,
                    exc_info=True,
                )

    # 集成点 4：反向查询 helper

    def get_related_documents(
        self,
        feature: str,
        material: str | None = None,
        top_k: int = 10,
        include_documents: bool = True,
    ) -> dict[str, Any]:
        """集成点 4：通过 chunk_ids + EntityIndex 反向查询原始文档。

        查询链路：
            1. 从 ``_by_feature`` 取该 feature 的所有四元组，收集直接关联的
               ``chunk_ids``（直接命中）；
            2. 若已注入 ``entity_index``，通过 ``EntityIndex.get_chunks``
               扩展查找与 [feature, material] 关联的 chunk_ids（扩展命中）；
               检索模式：**intersection 优先**（高精度），无结果时降级到
               **union**（高召回），响应中通过 ``extended_mode_used`` 字段标注；
            3. 合并去重后，若已注入 ``knowledge_base`` 且 ``include_documents=True``，
               通过 ``knowledge_base.get_by_id()`` 拉取完整文档内容，
               长度受 ``top_k`` 显式限制。

        Args:
            feature: 加工特征 (pocket/slot/hole...)
            material: 可选工件材料过滤（同时作为实体扩展查找）
            top_k: 返回前 K 条文档（默认 10）
            include_documents: True 时拉取完整文档内容（需注入 knowledge_base）；
                False 时仅返回 chunk_ids

        Returns:
            dict，含 feature/material/chunk_ids_direct/chunk_ids_extended/
            chunk_ids_all/documents/total_found/documents_returned/
            documents_truncated/extended_mode_used/entity_index_injected/
            knowledge_base_injected 等字段
        """
        feature = feature.strip().lower()
        material_norm = material.strip().lower() if material else None

        direct_chunk_ids: list[str] = []
        with self._lock:
            for q in self._by_feature.get(feature, []):
                if material_norm and q.material != material_norm:
                    continue
                direct_chunk_ids.extend(q.chunk_ids)

        # 去重保持顺序
        seen: set[str] = set()
        direct_unique: list[str] = []
        for cid in direct_chunk_ids:
            if cid and cid not in seen:
                seen.add(cid)
                direct_unique.append(cid)

        # 扩展查找：通过 EntityIndex 找到与 [feature, material] 关联的 chunk
        # 检索模式选择：
        # - intersection（默认）：所有实体都匹配的 chunk（高精度，低召回）
        # 避免 union 模式返回任一实体匹配的 chunk 引入无关文档噪音
        # - union 降级：当 intersection 返回空且实体数 > 1 时降级到 union
        # 覆盖罕见实体组合场景（如 "titanium" + "pocket" 在索引中无共现）
        extended_chunk_ids: list[str] = []
        extended_mode_used: str = "none"
        if self._entity_index is not None:
            query_entities = [feature]
            if material_norm:
                query_entities.append(material_norm)
            try:
                extended_chunk_ids = self._entity_index.get_chunks(
                    query_entities,
                    mode="intersection",
                )
                extended_mode_used = "intersection"

                # intersection 无结果且实体数 > 1：降级到 union
                if not extended_chunk_ids and len(query_entities) > 1:
                    union_ids = self._entity_index.get_chunks(
                        query_entities,
                        mode="union",
                    )
                    if union_ids:
                        logger.debug(
                            "intersection 模式无结果 (entities=%s)，降级到 union 模式（命中 %d 条）",
                            query_entities,
                            len(union_ids),
                        )
                        extended_chunk_ids = union_ids
                        extended_mode_used = "union_fallback"
            except (RuntimeError, OSError, ValueError, TypeError) as e:
                logger.warning(
                    "EntityIndex.get_chunks 失败 (entities=%s): %s",
                    query_entities,
                    e,
                    exc_info=True,
                )

        # 合并 direct + extended，去重
        all_chunk_ids: list[str] = list(direct_unique)
        for cid in extended_chunk_ids:
            if cid and cid not in seen:
                seen.add(cid)
                all_chunk_ids.append(cid)

        all_chunk_ids = all_chunk_ids[:top_k] if top_k > 0 else all_chunk_ids

        # 拉取完整文档内容（软依赖：未注入 knowledge_base 时仅返回 chunk_ids）
        # documents 与 all_chunk_ids 1:1 对应，长度已受 all_chunk_ids[:top_k] 限制；
        # 此处再显式截断一次，防止 knowledge_base.get_by_id 实现返回多文档
        documents: list[dict[str, Any]] = []
        documents_truncated = False
        if include_documents and self._knowledge_base is not None and all_chunk_ids:
            for cid in all_chunk_ids:
                try:
                    doc = self._knowledge_base.get_by_id(cid)
                    if doc is not None:
                        documents.append(doc)
                        # 显式长度守卫：防止 KB 实现异常返回过多文档
                        if top_k > 0 and len(documents) >= top_k:
                            documents_truncated = True
                            break
                except (RuntimeError, OSError, ValueError, TypeError) as e:
                    logger.debug(
                        "knowledge_base.get_by_id 失败 (chunk_id=%s): %s",
                        cid,
                        e,
                    )

        return {
            "feature": feature,
            "material": material_norm,
            "chunk_ids_direct": direct_unique,
            "chunk_ids_extended": [cid for cid in all_chunk_ids if cid not in direct_unique],
            "chunk_ids_all": all_chunk_ids,
            "documents": documents,
            "total_found": len(all_chunk_ids),
            "documents_returned": len(documents),
            "documents_truncated": documents_truncated,
            "extended_mode_used": extended_mode_used,
            "entity_index_injected": self._entity_index is not None,
            "knowledge_base_injected": self._knowledge_base is not None,
        }


# 全局单例

_singleton: ProcessQuadrupleIndex | None = None
_singleton_lock = threading.Lock()


def get_process_quadruple_index(
    persist_dir: str | None = None,
    entity_index: Any = None,
    knowledge_base: Any = None,
) -> ProcessQuadrupleIndex:
    """获取全局工艺四元组索引单例。

    集成点 4：默认注入 ``get_entity_index()`` 单例，使 ``add()`` 自动维护
    EntityIndex 倒排索引。``knowledge_base`` 默认不注入（避免循环依赖），
    可在运行时通过 ``set_knowledge_base()`` 注入以启用 ``get_related_documents()``
    的完整文档反查能力。

    Args:
        persist_dir: 持久化目录（仅首次调用生效）
        entity_index: 可选，覆盖默认的 EntityIndex 单例（用于测试）
        knowledge_base: 可选，注入 KnowledgeBase 以启用完整文档反查
    """
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                # 集成点 4：默认注入 EntityIndex 单例
                if entity_index is None:
                    try:
                        from app.rag.entity_index import get_entity_index

                        entity_index = get_entity_index()
                    except (ImportError, RuntimeError) as e:
                        logger.warning(
                            "无法注入 EntityIndex 单例（集成点 4 降级）: %s",
                            e,
                            exc_info=True,
                        )
                        entity_index = None

                _singleton = ProcessQuadrupleIndex(
                    persist_dir=persist_dir,
                    entity_index=entity_index,
                    knowledge_base=knowledge_base,
                )
                logger.info(
                    "ProcessQuadrupleIndex singleton initialized (entity_index=%s, knowledge_base=%s)",
                    "injected" if entity_index is not None else "none",
                    "injected" if knowledge_base is not None else "none",
                )
    return _singleton


# 默认工艺知识库（覆盖常见特征的典型工艺方案）

DEFAULT_QUADRUPLES: list[dict] = [
    # ── 型腔加工 ──────────────────────────────────────────
    {
        "feature": "pocket",
        "process": "rough_mill",
        "tool": "endmill_d10",
        "parameters": {
            "spindle_rpm": 6000,
            "feed_rate_mm_per_min": 800,
            "depth_of_cut_mm": 2.0,
            "width_of_cut_mm": 5.0,
            "stepover_pct": 50,
        },
        "material": "aluminum",
        "confidence": 0.95,
        "source": "manual",
        "tags": ["high_efficiency", "hsm"],
    },
    {
        "feature": "pocket",
        "process": "finish_mill",
        "tool": "endmill_d10",
        "parameters": {
            "spindle_rpm": 8000,
            "feed_rate_mm_per_min": 600,
            "depth_of_cut_mm": 0.5,
            "width_of_cut_mm": 0.5,
            "stepover_pct": 5,
        },
        "material": "aluminum",
        "confidence": 0.9,
        "source": "manual",
        "tags": ["high_precision"],
    },
    # ── 槽加工 ──────────────────────────────────────────
    {
        "feature": "slot",
        "process": "rough_mill",
        "tool": "endmill_d8",
        "parameters": {
            "spindle_rpm": 7000,
            "feed_rate_mm_per_min": 600,
            "depth_of_cut_mm": 1.5,
            "width_of_cut_mm": 4.0,
        },
        "material": "aluminum",
        "confidence": 0.85,
        "source": "manual",
    },
    # ── 孔加工 ──────────────────────────────────────────
    {
        "feature": "hole",
        "process": "drill",
        "tool": "drill_d8",
        "parameters": {
            "spindle_rpm": 3000,
            "feed_rate_mm_per_min": 150,
            "depth_of_cut_mm": 25.0,
            "peck_enable": True,
        },
        "material": "aluminum",
        "confidence": 0.95,
        "source": "manual",
    },
    {
        "feature": "hole",
        "process": "ream",
        "tool": "reamer_d8",
        "parameters": {
            "spindle_rpm": 1500,
            "feed_rate_mm_per_min": 100,
            "depth_of_cut_mm": 0.2,
        },
        "material": "aluminum",
        "confidence": 0.9,
        "source": "manual",
    },
    # ── 螺纹 ──────────────────────────────────────────
    {
        "feature": "thread",
        "process": "tap",
        "tool": "tap_m8",
        "parameters": {
            "spindle_rpm": 400,
            "feed_rate_mm_per_min": 0,
            "rigid_tapping": True,
        },
        "material": "aluminum",
        "confidence": 0.9,
        "source": "manual",
    },
    # ── 轮廓 ──────────────────────────────────────────
    {
        "feature": "profile",
        "process": "contour",
        "tool": "endmill_d10",
        "parameters": {
            "spindle_rpm": 7000,
            "feed_rate_mm_per_min": 700,
            "depth_of_cut_mm": 1.0,
            "width_of_cut_mm": 1.0,
        },
        "material": "aluminum",
        "confidence": 0.85,
        "source": "manual",
    },
    # ── 平面 ──────────────────────────────────────────
    {
        "feature": "face",
        "process": "face_mill",
        "tool": "facemill_d50",
        "parameters": {
            "spindle_rpm": 4000,
            "feed_rate_mm_per_min": 1000,
            "depth_of_cut_mm": 1.0,
            "width_of_cut_mm": 40.0,
        },
        "material": "aluminum",
        "confidence": 0.95,
        "source": "manual",
    },
    # ── 倒角 ──────────────────────────────────────────
    {
        "feature": "chamfer",
        "process": "chamfer_mill",
        "tool": "chamfer_d10",
        "parameters": {
            "spindle_rpm": 8000,
            "feed_rate_mm_per_min": 500,
            "depth_of_cut_mm": 1.0,
        },
        "material": "aluminum",
        "confidence": 0.85,
        "source": "manual",
    },
    # ── 钢件型腔（材料迁移参考） ──────────────────────────
    {
        "feature": "pocket",
        "process": "rough_mill",
        "tool": "endmill_d10",
        "parameters": {
            "spindle_rpm": 2500,
            "feed_rate_mm_per_min": 400,
            "depth_of_cut_mm": 1.5,
            "width_of_cut_mm": 4.0,
        },
        "material": "steel",
        "confidence": 0.85,
        "source": "manual",
    },
    {
        "feature": "pocket",
        "process": "rough_mill",
        "tool": "endmill_d10",
        "parameters": {
            "spindle_rpm": 1500,
            "feed_rate_mm_per_min": 200,
            "depth_of_cut_mm": 1.0,
            "width_of_cut_mm": 3.0,
        },
        "material": "titanium",
        "confidence": 0.8,
        "source": "manual",
    },
]


def seed_default_quadruples(index: ProcessQuadrupleIndex) -> int:
    """将默认工艺知识库注入索引。"""
    quads = [ProcessQuadruple.from_dict(d) for d in DEFAULT_QUADRUPLES]
    return index.add_batch(quads)
