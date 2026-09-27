"""NetworkX 内存图模型封装与高层 API（M1.2）。

设计要点：
    - **NetworkX 抽象**：使用 ``networkx.DiGraph`` 存储节点和关系，
      节点和边均以 ``dict`` 形式携带属性。
    - **节点 ID 规范**：必须为字符串，遵循 ``<type>-<slug>`` 格式。
      ``add_node`` 会强制校验，未通过则抛 ``ValueError``。
    - **关系方向**：使用有向图（``DiGraph``）表达 ``(source) -[edge_type]-> (target)``。
    - **不依赖 NetworkX 自带存储**：所有持久化逻辑由 :mod:`persistence`
      自定义实现，确保事务一致性。
    - **同步 API**：与 M0.4 ``MachiningRecordRepository`` 风格一致。

注意：
    - 内存图状态默认是**进程级**的。重启进程后需通过
      :meth:`GraphStore.load_from_repository` 从数据库重新加载。
    - 本类**不**在每次写操作时自动落库（写穿 write-through），
      避免半成品事务；调用方按需调用
      :meth:`GraphStore.flush_to_repository` 触发持久化。
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

import networkx as nx

logger = logging.getLogger(__name__)


# 常量与校验


# 节点 ID 格式：<type>-<slug>，slug 可包含字母数字、下划线、点号、横线
_NODE_ID_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.\-]{0,127}$")
_MAX_PROPS_SIZE_BYTES = 1 * 1024 * 1024  # 1 MB 序列化大小上限（防御性）


def _validate_node_id(node_id: str) -> None:
    """校验节点 ID 格式。"""
    if not isinstance(node_id, str):
        raise TypeError(f"node_id must be str, got {type(node_id).__name__}")
    if not _NODE_ID_PATTERN.match(node_id):
        raise ValueError(
            f"Invalid node_id {node_id!r}: must match pattern '<type>-<slug>' (letters/digits/_/./-, max 128 chars)"
        )


def _ensure_props(props: dict[str, Any] | None) -> dict[str, Any]:
    """归一化属性字典（拷贝 + 默认空 dict）。"""
    if props is None:
        return {}
    if not isinstance(props, dict):
        raise TypeError(f"properties must be dict or None, got {type(props).__name__}")
    # 仅做浅拷贝：深拷贝由调用方按需决定
    return dict(props)


# GraphStore


class GraphStore:
    """知识图谱内存存储门面。

    示例::

        g = GraphStore()
        g.add_node("material", "M-45steel", {"name": "45 steel"})
        g.add_node("tool", "T-endmill-10", {"name": "Endmill D10"})
        g.add_edge("T-endmill-10", "M-45steel", "SUITABLE_FOR",
                   {"confidence": 0.9, "source": "rule"})

        # 按 ID 查询
        node = g.get_node("M-45steel")

        # 按类型查询
        materials = g.list_nodes_by_type("material")

        # 按关系类型查询
        edges = g.list_edges_by_type("SUITABLE_FOR")

        # 按可信度查询
        edges = g.list_edges_by_confidence(min_confidence=0.7)
    """

    def __init__(self, auto_load: bool = True) -> None:
        # 使用 MultiDiGraph：允许 (source, target) 之间存在多条不同
        # ``edge_type`` 的关系（普通 DiGraph 仅允许一条边）。
        self._graph: nx.MultiDiGraph = nx.MultiDiGraph()
        # 并发保护：NetworkX MultiDiGraph 非线程安全，单例在多请求间共享时
        # 必须用锁保护所有读写操作，避免并发损坏图数据结构。使用 RLock 以
        # 允许同线程内嵌套加锁（例如持久化方法内部调用查询方法）。
        self._lock = threading.RLock()
        # M1.3 增强：支持 init 时自动从数据库加载。
        # 行为：
        # - 仅在 ``DB_URL`` 已配置且当前图为空时触发，避免在测试或
        # 手动构造场景下意外覆盖用户已添加的节点。
        # - 任何加载异常均降级为 debug 日志，不影响 ``GraphStore()``
        # 本身的可用性。
        if auto_load:
            self._maybe_auto_load()

    def _maybe_auto_load(self) -> None:
        """当 DB 已配置时，自动从数据库加载已有节点和边。"""
        if self._graph.number_of_nodes() > 0:
            return
        try:
            from app.knowledge_graph.repository import (
                get_sync_sessionmaker,
            )
        except (ImportError, OSError) as exc:  # pragma: no cover - 防御性兜底
            logger.debug("auto_load import failed: %s", exc)
            return
        if get_sync_sessionmaker() is None:
            return
        try:
            from app.knowledge_graph.persistence import GraphPersistence

            persistence = GraphPersistence()
            persistence.load_from_repository(self, replace=False)
        except (OSError, RuntimeError, ImportError) as exc:  # pragma: no cover - 防御性兜底
            logger.debug("auto_load from repository skipped: %s", exc)

    # ============================================================== 节点操作

    def add_node(
        self,
        node_type: str,
        node_id: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """添加或更新一个节点。

        Args:
            node_type: 节点类型（如 material / tool / feature / process）。
            node_id: 节点唯一 ID，格式 ``<type>-<slug>``。
            properties: 节点属性字典。

        Raises:
            TypeError: 参数类型错误。
            ValueError: 节点 ID 格式非法 / node_type 非字符串。
        """
        if not isinstance(node_type, str) or not node_type:
            raise ValueError(f"node_type must be a non-empty str, got {node_type!r}")
        _validate_node_id(node_id)
        props = _ensure_props(properties)
        # ``type`` 是 NetworkX 内置概念，重命名避免冲突；
        # 用户提供的 ``type`` 键若与 node_type 冲突，则 node_type 优先。
        props.pop("type", None)
        with self._lock:
            self._graph.add_node(
                node_id,
                type=node_type,
                properties=props,
            )
        logger.debug("add_node: %s (%s) props=%s", node_id, node_type, props)

    def has_node(self, node_id: str) -> bool:
        """判断节点是否存在。"""
        with self._lock:
            return self._graph.has_node(node_id)

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """按 ID 查询节点；返回包含 ``node_id`` / ``node_type`` / ``properties`` 的字典。"""
        with self._lock:
            if not self._graph.has_node(node_id):
                return None
            data = self._graph.nodes[node_id]
            return {
                "node_id": node_id,
                "node_type": data.get("type", ""),
                "properties": dict(data.get("properties", {})),
            }

    def update_node_properties(
        self,
        node_id: str,
        properties: dict[str, Any],
    ) -> bool:
        """更新节点的属性（合并语义：新值覆盖旧值）。

        Returns:
            是否真的更新了节点属性（节点不存在时返回 ``False``）。
        """
        with self._lock:
            if not self._graph.has_node(node_id):
                return False
            existing = self._graph.nodes[node_id]
            merged = dict(existing.get("properties", {}))
            merged.update(_ensure_props(properties))
            existing["properties"] = merged
            return True

    def remove_node(self, node_id: str) -> bool:
        """删除节点及其所有关联边。"""
        with self._lock:
            if not self._graph.has_node(node_id):
                return False
            self._graph.remove_node(node_id)
            return True

    def list_nodes_by_type(self, node_type: str) -> list[dict[str, Any]]:
        """按节点类型查询节点列表。"""
        with self._lock:
            results: list[dict[str, Any]] = []
            for nid, data in self._graph.nodes(data=True):
                if data.get("type") == node_type:
                    results.append(
                        {
                            "node_id": nid,
                            "node_type": data.get("type", ""),
                            "properties": dict(data.get("properties", {})),
                        }
                    )
            results.sort(key=lambda x: x["node_id"])
            return results

    def node_count(self, node_type: str | None = None) -> int:
        """返回节点数量；可选按类型过滤。"""
        with self._lock:
            if node_type is None:
                return self._graph.number_of_nodes()
            return sum(1 for _, data in self._graph.nodes(data=True) if data.get("type") == node_type)

    # ============================================================== 关系操作

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """添加或更新一条有向关系。

        Args:
            source_id: 起始节点 ID（必须已存在或与添加顺序无关，本方法
                不自动创建缺失端点，由调用方按需 :meth:`add_node`）。
            target_id: 目标节点 ID。
            edge_type: 关系类型字符串（建议使用大写 + 下划线，如
                ``SUITABLE_FOR`` / ``APPLIED_TO`` / ``USED``）。
            properties: 关系属性（如 ``confidence`` / ``source`` / ``evidence``）。
                若包含 ``confidence``，必须是 ``[0, 1]`` 范围内的数值。

        Raises:
            ValueError: 参数非法 / 端点节点不存在 / confidence 越界。
            TypeError: 参数类型错误。
        """
        if not isinstance(edge_type, str) or not edge_type:
            raise ValueError(f"edge_type must be a non-empty str, got {edge_type!r}")
        _validate_node_id(source_id)
        _validate_node_id(target_id)
        # 在加锁前进行参数校验，避免持锁等待期间抛错导致锁泄漏。
        # 端点存在性检查与写操作必须在同一把锁内完成，保证原子性。
        with self._lock:
            if not self._graph.has_node(source_id):
                raise ValueError(f"source node not found: {source_id!r}")
            if not self._graph.has_node(target_id):
                raise ValueError(f"target node not found: {target_id!r}")
            if source_id == target_id:
                # 允许自环，但记录日志以便排查异常数据
                logger.debug(
                    "add_edge: self-loop detected on %s with type %s",
                    source_id,
                    edge_type,
                )

            props = _ensure_props(properties)
            confidence = props.get("confidence")
            if confidence is not None:
                if not isinstance(confidence, (int, float)):
                    raise TypeError(f"confidence must be a number, got {type(confidence).__name__}")
                if not (0.0 <= float(confidence) <= 1.0):
                    raise ValueError(f"confidence must be in [0, 1], got {confidence!r}")
                props["confidence"] = float(confidence)
            else:
                # 未显式给出 confidence 时使用默认值 0.5，保证下游
                # 消费者按 ``properties["confidence"]`` 取值一致。
                props["confidence"] = 0.5

            self._graph.add_edge(
                source_id,
                target_id,
                key=edge_type,
                edge_type=edge_type,
                properties=props,
            )
        logger.debug(
            "add_edge: %s -[%s]-> %s props=%s",
            source_id,
            edge_type,
            target_id,
            props,
        )

    def has_edge(self, source_id: str, target_id: str, edge_type: str) -> bool:
        """判断关系是否存在。"""
        with self._lock:
            return self._graph.has_edge(source_id, target_id, key=edge_type)

    def get_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
    ) -> dict[str, Any] | None:
        """按 ``(source, target, type)`` 三元组查询关系。"""
        with self._lock:
            if not self._graph.has_edge(source_id, target_id, key=edge_type):
                return None
            data = self._graph.edges[source_id, target_id, edge_type]
            return {
                "source_id": source_id,
                "target_id": target_id,
                "edge_type": edge_type,
                "properties": dict(data.get("properties", {})),
            }

    def update_edge_properties(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
        properties: dict[str, Any],
    ) -> bool:
        """更新关系属性（合并语义）。"""
        with self._lock:
            if not self._graph.has_edge(source_id, target_id, key=edge_type):
                return False
            existing = self._graph.edges[source_id, target_id, edge_type]
            merged = dict(existing.get("properties", {}))
            update = _ensure_props(properties)
            if "confidence" in update:
                conf = update["confidence"]
                if not isinstance(conf, (int, float)):
                    raise TypeError(f"confidence must be a number, got {type(conf).__name__}")
                if not (0.0 <= float(conf) <= 1.0):
                    raise ValueError(f"confidence must be in [0, 1], got {conf!r}")
                update["confidence"] = float(conf)
            merged.update(update)
            existing["properties"] = merged
            return True

    def remove_edge(self, source_id: str, target_id: str, edge_type: str) -> bool:
        """按三元组删除关系。"""
        with self._lock:
            if not self._graph.has_edge(source_id, target_id, key=edge_type):
                return False
            self._graph.remove_edge(source_id, target_id, key=edge_type)
            return True

    def list_edges_by_type(self, edge_type: str) -> list[dict[str, Any]]:
        """按关系类型查询所有边。"""
        with self._lock:
            results: list[dict[str, Any]] = []
            for u, v, k, data in self._graph.edges(keys=True, data=True):
                if k == edge_type:
                    results.append(
                        {
                            "source_id": u,
                            "target_id": v,
                            "edge_type": k,
                            "properties": dict(data.get("properties", {})),
                        }
                    )
            results.sort(key=lambda x: (x["source_id"], x["target_id"]))
            return results

    def list_edges_by_source(
        self,
        source_id: str,
        edge_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """按起始节点查询出边。"""
        with self._lock:
            if not self._graph.has_node(source_id):
                return []
            results: list[dict[str, Any]] = []
            for _, v, k, data in self._graph.out_edges(source_id, keys=True, data=True):
                if edge_type is None or k == edge_type:
                    results.append(
                        {
                            "source_id": source_id,
                            "target_id": v,
                            "edge_type": k,
                            "properties": dict(data.get("properties", {})),
                        }
                    )
            results.sort(key=lambda x: (x["edge_type"], x["target_id"]))
            return results

    def list_edges_by_target(
        self,
        target_id: str,
        edge_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """按目标节点查询入边。"""
        with self._lock:
            if not self._graph.has_node(target_id):
                return []
            results: list[dict[str, Any]] = []
            for u, _, k, data in self._graph.in_edges(target_id, keys=True, data=True):
                if edge_type is None or k == edge_type:
                    results.append(
                        {
                            "source_id": u,
                            "target_id": target_id,
                            "edge_type": k,
                            "properties": dict(data.get("properties", {})),
                        }
                    )
            results.sort(key=lambda x: (x["edge_type"], x["source_id"]))
            return results

    def list_edges_by_confidence(
        self,
        min_confidence: float = 0.0,
        max_confidence: float = 1.0,
        edge_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """按可信度区间查询关系；可选 ``edge_type`` 过滤。

        Args:
            min_confidence: 下界（含），默认 0。
            max_confidence: 上界（含），默认 1。
            edge_type: 可选关系类型过滤。

        Returns:
            关系列表，按 ``confidence`` 降序排列。
        """
        if min_confidence > max_confidence:
            raise ValueError(f"min_confidence ({min_confidence}) must be <= max_confidence ({max_confidence})")
        if not (0.0 <= min_confidence <= 1.0):
            raise ValueError(f"min_confidence must be in [0, 1], got {min_confidence!r}")
        if not (0.0 <= max_confidence <= 1.0):
            raise ValueError(f"max_confidence must be in [0, 1], got {max_confidence!r}")
        with self._lock:
            results: list[dict[str, Any]] = []
            for u, v, k, data in self._graph.edges(keys=True, data=True):
                if edge_type is not None and k != edge_type:
                    continue
                conf = (data.get("properties") or {}).get("confidence")
                if conf is None:
                    continue
                try:
                    conf_f = float(conf)
                except (TypeError, ValueError):
                    continue
                if min_confidence <= conf_f <= max_confidence:
                    results.append(
                        {
                            "source_id": u,
                            "target_id": v,
                            "edge_type": k,
                            "confidence": conf_f,
                            "properties": dict(data.get("properties", {})),
                        }
                    )
            results.sort(key=lambda x: x.get("confidence", 0.0), reverse=True)
            return results

    def edge_count(self, edge_type: str | None = None) -> int:
        """返回关系数量；可选按类型过滤。"""
        with self._lock:
            if edge_type is None:
                return self._graph.number_of_edges()
            return sum(1 for _, _, k in self._graph.edges(keys=True) if k == edge_type)

    # ============================================================== 辅助操作

    def clear(self) -> None:
        """清空内存图（不影响数据库）。"""
        with self._lock:
            self._graph.clear()

    def graph(self) -> nx.DiGraph:
        """返回底层 NetworkX ``DiGraph``（只读视图语义，调用方不应原地修改）。

        注意：返回的是内部图对象的引用，调用方应在持锁期间使用，或自行
        拷贝后再使用，以避免在迭代过程中被其他线程修改。
        """
        with self._lock:
            return self._graph

    # ============================================================== 持久化便捷方法

    def flush_to_repository(
        self,
        *,
        clear_first: bool = False,
        session_factory: Any | None = None,
    ) -> dict[str, int]:
        """将当前内存图落库到 PostgreSQL（便捷方法）。

        实现说明：
            - 该方法是对 :class:`GraphPersistence` 的薄包装，调用方无需
              显式构造 Persistence 对象。
            - 默认沿用全局懒加载同步 sessionmaker；若调用方希望注入
              外部 sessionmaker，可通过 ``session_factory`` 传入。
            - 当数据库未配置（``DB_URL`` 为空）时，方法以 no-op 返回
              零计数字典，避免在测试环境无 DB 时崩溃。

        Args:
            clear_first: 是否先清空数据库中的 kg_nodes / kg_edges。
            session_factory: 可选外部 sessionmaker 工厂。

        Returns:
            包含 ``nodes_written`` / ``edges_written`` 字段的统计字典。
        """
        # 延迟导入以避免循环依赖（persistence 反向 import graph_store）
        from app.knowledge_graph.persistence import GraphPersistence

        if session_factory is None:
            # 复用 Repository 的 get_sync_sessionmaker 逻辑，避免在
            # 未配置 DB 时直接构造引擎抛错。
            from app.knowledge_graph.repository import (
                get_sync_sessionmaker,
            )

            if get_sync_sessionmaker() is None:
                logger.debug("flush_to_repository: DB not configured, skipping")
                return {"nodes_written": 0, "edges_written": 0}

        # 持锁以防止在落库过程中其他线程修改内存图，确保快照一致性。
        # 使用 RLock 允许 GraphPersistence 内部回调本类查询方法时复用锁。
        with self._lock:
            persistence = GraphPersistence(session_factory=session_factory)
            return persistence.flush_to_repository(self, clear_first=clear_first)

    def load_from_repository(
        self,
        *,
        node_limit: int = 100000,
        edge_limit: int = 1000000,
        replace: bool = True,
        session_factory: Any | None = None,
    ) -> dict[str, int]:
        """从数据库加载节点和关系到当前内存图（便捷方法）。

        实现说明：
            - 当数据库未配置（``DB_URL`` 为空）时，方法以 no-op 返回
              零计数字典，``replace`` 参数被忽略。
            - ``replace=True``（默认）会先 :meth:`clear` 内存图再加载，
              确保当前图与数据库一致；``replace=False`` 则保留已有
              节点 / 边，仅在缺失处补充。

        Returns:
            包含 ``nodes_loaded`` / ``edges_loaded`` 字段的统计字典。
        """
        from app.knowledge_graph.persistence import GraphPersistence
        from app.knowledge_graph.repository import (
            get_sync_sessionmaker,
        )

        if get_sync_sessionmaker() is None:
            logger.debug("load_from_repository: DB not configured, skipping")
            return {"nodes_loaded": 0, "edges_loaded": 0}

        # 持锁以防止在加载过程中其他线程读取到半成品图数据。
        # 使用 RLock 允许 GraphPersistence 内部回调本类写方法时复用锁。
        with self._lock:
            persistence = GraphPersistence(session_factory=session_factory)
            return persistence.load_from_repository(
                self,
                node_limit=node_limit,
                edge_limit=edge_limit,
                replace=replace,
            )


__all__ = ["GraphStore"]
