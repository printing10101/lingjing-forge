"""本地化 Memory Store。

Memory Store 核心概念：
    - 工作区级文本文档集合
    - 支持读写和只读两种模式
    - 每次修改生成不可变 Memory Version

本地化实现：
    - **结构化存储**：复用项目已有 GraphStore（networkx + DB 持久化）
    - **不可变版本**：使用 Git commit 哈希作为 version_id
    - **审计追溯**：所有写入带 source=dream_cycle + validation_count 标记
    - **安全隔离**：只读 Store 用于参考材料，读写 Store 用于运行时记忆

与通用云端方案的差异：
    - 不依赖云端 /mnt/memory/ 目录，改用本地 GraphStore
    - Memory Version 不是 30 天审计记录，而是 Git 永久快照
    - 单 Memory 100KB 限制 → 本地无限制（磁盘存储）
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.knowledge_graph.graph_store import GraphStore

logger = logging.getLogger(__name__)


# Dreaming 专用的节点类型和关系类型
DREAMING_NODE_TYPE = "dreaming_memory"
DREAMING_RELATION_TYPE = "CONSOLIDATED_FROM"


@dataclass
class MemoryEntry:
    """单条 Memory 条目。"""

    entity: str  # 关联的实体 ID（如 material-HRC52）
    content: str  # 记忆内容文本
    source: str = "dream_cycle"  # 来源标记
    validation_count: int = 0  # 验证通过次数
    confidence: float = 0.5  # 初始置信度
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_updated: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "content": self.content,
            "source": self.source,
            "validation_count": self.validation_count,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "last_updated": self.last_updated,
            "metadata": self.metadata,
        }


@dataclass
class MemoryVersion:
    """不可变 Memory 版本快照。"""

    version_id: str  # Git commit hash
    timestamp: str
    entry_count: int
    parent_version: str | None = None  # 上一个版本（用于 diff）


class LocalMemoryStore:
    """本地化 Memory Store。

    实现：
        - GraphStore 存结构化知识（networkx + DB）
        - Git 做不可变版本管理（commit hash 作为 version_id）
        - 所有写入标记 source=dream_cycle 以便追溯

    用法：
        store = LocalMemoryStore(graph_store=..., repo_root="...")
        store.add_observation("material-HRC52", "HRC52 进给速率安全系数 0.85")
        version = store.commit_version()  # 生成不可变快照
        diff = store.diff_versions(old_version, version)
    """

    def __init__(
        self,
        graph_store: GraphStore,
        repo_root: str,
        watch_paths: list[str] | None = None,
    ) -> None:
        """初始化 Memory Store。

        Args:
            graph_store: 项目 GraphStore 实例（已有 DB 持久化）
            repo_root: Git 仓库根目录绝对路径
            watch_paths: Git 跟踪的子路径列表（默认 knowledge_graph 数据目录）
        """
        self.graph = graph_store
        self.repo_root = Path(repo_root).resolve()
        # 默认只跟踪知识图谱数据目录，避免把无关文件纳入版本
        self.watch_paths = [str(p) for p in (watch_paths or ["python/app/knowledge_graph/"])]

    # 读取

    def read_all(self) -> list[dict[str, Any]]:
        """读取全部 Dreaming memory 条目。"""
        nodes = self.graph.list_nodes_by_type(DREAMING_NODE_TYPE)
        return [
            {
                "node_id": n["node_id"],
                "properties": n["properties"],
            }
            for n in nodes
        ]

    def read_by_entity(self, entity_id: str) -> list[dict[str, Any]]:
        """读取与指定实体关联的所有 memory 条目。"""
        all_entries = self.read_all()
        return [e for e in all_entries if e["properties"].get("entity") == entity_id]

    # 写入

    def add_observation(
        self,
        entity: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        confidence: float = 0.5,
    ) -> str:
        """新增 memory 条目。

        Args:
            entity: 关联实体 ID（如 material-HRC52）
            content: 记忆内容文本
            metadata: 附加元数据
            confidence: 初始置信度 [0, 1]

        Returns:
            新建的 node_id
        """
        entry = MemoryEntry(
            entity=entity,
            content=content,
            confidence=confidence,
            metadata=metadata or {},
        )

        # 构造符合 GraphStore 节点 ID 格式的 ID
        # GraphStore 要求 ^[a-zA-Z_][a-zA-Z0-9_.\-]{0,127}$
        safe_entity = entity.replace("-", "_").replace(":", "_")
        node_id = f"dream_{safe_entity}_{int(datetime.now(timezone.utc).timestamp() * 1000)}"

        # 确保实体节点存在（作为关联锚点）
        if not self.graph.has_node(entity):
            try:
                self.graph.add_node(
                    node_type="entity",
                    node_id=entity,
                    properties={"original_id": entity},
                )
            except (ValueError, TypeError):
                # 节点 ID 不符合格式要求时降级
                pass

        # 添加 memory 节点
        self.graph.add_node(
            node_type=DREAMING_NODE_TYPE,
            node_id=node_id,
            properties=entry.to_dict(),
        )

        # 建立 memory -> entity 的关联边
        try:
            if self.graph.has_node(entity):
                self.graph.add_edge(
                    source_id=node_id,
                    target_id=entity,
                    edge_type=DREAMING_RELATION_TYPE,
                    properties={"source": "dream_cycle"},
                )
        except (ValueError, TypeError):
            # 端点不存在时静默跳过（memory 节点已创建）
            pass

        logger.debug(
            "Dreaming memory added: entity=%s, confidence=%.2f",
            entity,
            confidence,
        )
        return node_id

    def update_observation(
        self,
        node_id: str,
        content: str | None = None,
        confidence: float | None = None,
        increment_validation: bool = False,
    ) -> bool:
        """更新已有 memory 条目。"""
        node = self.graph.get_node(node_id)
        if node is None:
            return False

        props = dict(node["properties"])
        if content is not None:
            props["content"] = content
        if confidence is not None:
            props["confidence"] = float(confidence)
        if increment_validation:
            props["validation_count"] = props.get("validation_count", 0) + 1
        props["last_updated"] = datetime.now(timezone.utc).isoformat()

        return self.graph.update_node_properties(node_id, props)

    # 版本管理（Git 不可变快照）

    def commit_version(
        self,
        message: str | None = None,
    ) -> MemoryVersion:
        """生成不可变版本快照。

        版本快照要点：
            - Git commit hash 作为 version_id
            - 永久保留（非 30 天）
            - 可通过 diff_versions 对比

        Args:
            message: commit 消息

        Returns:
            MemoryVersion 对象
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        commit_msg = message or f"dream: memory consolidation {timestamp}"

        # 获取上一个版本（用于 parent_version）
        parent_version = self._get_current_head()

        # git add 指定路径
        add_cmd = ["git", "add"] + self.watch_paths
        # [H20] 为所有 subprocess.run 添加 timeout，防止 git 异常挂起导致 dreaming 流程死锁
        try:
            add_result = subprocess.run(
                add_cmd,
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            logger.warning("git add 超时（30s），跳过本次 commit")
            add_result = None
        if add_result is not None and add_result.returncode != 0:
            logger.warning("git add 失败: %s", add_result.stderr)

        # 检查是否有变更可提交
        try:
            status_result = subprocess.run(
                ["git", "status", "--porcelain"] + self.watch_paths,
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=10,
            )
            has_changes = bool(status_result.stdout.strip())
        except subprocess.TimeoutExpired:
            logger.warning("git status 超时（10s），跳过本次 commit")
            has_changes = False

        if has_changes:
            try:
                commit_result = subprocess.run(
                    ["git", "commit", "-m", commit_msg],
                    cwd=str(self.repo_root),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if commit_result.returncode != 0:
                    logger.warning("git commit 失败: %s", commit_result.stderr)
            except subprocess.TimeoutExpired:
                logger.warning("git commit 超时（30s）")

        # 获取 commit hash
        try:
            hash_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=10,
            )
            version_id = hash_result.stdout.strip()[:12]  # 取前 12 位
        except subprocess.TimeoutExpired:
            logger.warning("git rev-parse HEAD 超时（10s），使用时间戳作为 version_id")
            version_id = f"fallback_{timestamp}"

        entry_count = self.graph.node_count(DREAMING_NODE_TYPE)

        version = MemoryVersion(
            version_id=version_id,
            timestamp=timestamp,
            entry_count=entry_count,
            parent_version=parent_version,
        )

        logger.info(
            "Memory version committed: %s, entries=%d",
            version.version_id,
            version.entry_count,
        )
        return version

    def diff_versions(self, v1: str, v2: str) -> str:
        """对比两个版本的差异。

        Args:
            v1: 旧版本 hash
            v2: 新版本 hash

        Returns:
            Git diff 文本
        """
        # [H20] 添加 timeout 防止 git diff 在大仓库上挂起
        try:
            result = subprocess.run(
                ["git", "diff", v1, v2] + self.watch_paths,
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.stdout
        except subprocess.TimeoutExpired:
            logger.warning("git diff 超时（30s），返回空字符串")
            return ""

    def _get_current_head(self) -> str | None:
        """获取当前 HEAD commit hash。"""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()[:12]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None

    # 清理（直接丢弃，不保留版本历史）

    def discard_version(self, version: str) -> bool:
        """丢弃指定版本（git revert，不删除历史）。

        安全策略：使用 revert 而非 reset --hard，保留审计记录。
        """
        # [H20] git revert 可能触发编辑器，添加 timeout 防止挂起
        try:
            result = subprocess.run(
                ["git", "revert", "--no-edit", version],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=60,
            )
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            logger.warning("git revert 超时（60s），版本 %s 未丢弃", version)
            return False
