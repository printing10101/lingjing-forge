"""知识图谱 JSON 导入共享基础设施（M1.3 重构 P1-4）

职责
----
- 集中存放 4 个 ``import_<file>`` 函数共享的辅助函数、去重器、数据类、
  常量与路径定义。
- 为 :mod:`importers.material_importer` / :mod:`importers.tool_importer`
  / :mod:`importers.machine_importer` / :mod:`importers.process_importer`
  以及 :mod:`coordinator` 提供公共依赖。

设计原则
--------
- 本模块**不**包含任何 ``import_<file>`` 业务逻辑，仅提供基础设施。
- 路径常量（``MATERIALS_JSON`` 等）定义在此处；测试可通过
  ``monkeypatch.setattr(_common, "MATERIALS_JSON", ...)`` 替换。
  为兼容历史用法（``monkeypatch.setattr(json_importer, ...)``），
  各导入器在 ``source_path is None`` 时会惰性从 ``json_importer``
  模块读取路径常量。
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Callable

logger = logging.getLogger(__name__)


# 路径常量

# 项目根目录：
# python/app/knowledge_graph/importer/importers/_common.py parents[5] = engineering/
_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_DATA_DIR = _PROJECT_ROOT / "python" / "app" / "data"
_DATABASE_DATA_DIR = _PROJECT_ROOT / "python" / "app" / "database" / "data"

MATERIALS_JSON = _DATA_DIR / "materials.json"
TOOLS_JSON = _DATA_DIR / "tools.json"
MACHINES_JSON = _DATABASE_DATA_DIR / "machines.json"
PROCESS_RULES_JSON = _DATA_DIR / "process_rules.json"


# 节点类型常量
NODE_TYPE_MATERIAL = "material"
NODE_TYPE_TOOL = "tool"
NODE_TYPE_MACHINE = "machine"
NODE_TYPE_FEATURE = "feature"
NODE_TYPE_PROCESS = "process"

# 关系类型常量
EDGE_SUITABLE_FOR = "SUITABLE_FOR"
EDGE_APPLIED_TO = "APPLIED_TO"
EDGE_USED = "USED"

# 工具 特征 的映射（基于 series 或 application 关键词）
# 值元组为 (feature_id, feature_name, feature_type) 三元组
_SERIES_TO_FEATURES: dict[str, list[tuple[str, str, str]]] = {
    # series -> [(feature_id, feature_name, feature_type)]
    "twist_drill": [("feature-hole", "孔", "hole")],
    "endmill": [
        ("feature-pocket", "型腔", "pocket"),
        ("feature-contour", "轮廓", "contour"),
    ],
    "face_mill": [("feature-face", "面", "face")],
    "center_drill": [("feature-hole", "孔", "hole")],
}

# 工具 适用材料（用于 Tool SUITABLE_FOR Material 关系）
# 全部 4 种材料，可根据材料 category 进一步细化
_ALL_MATERIAL_NAMES: tuple[str, ...] = (
    "45#钢",
    "铝合金6061",
    "不锈钢304",
    "40Cr",
)

# 规则中特征 推荐工具映射（用于 Process USED Tool 关系）
_FEATURE_TO_REPRESENTATIVE_TOOLS: dict[str, list[str]] = {
    "feature-face": ["tool-face_mill_50", "tool-face_mill_80"],
    "feature-hole": ["tool-twist_drill_5", "tool-twist_drill_10"],
    "feature-pocket": ["tool-endmill_6", "tool-endmill_10"],
    "feature-contour": ["tool-endmill_6", "tool-endmill_10"],
    "feature-slot": ["tool-endmill_4", "tool-endmill_6"],
    "feature-thread": ["tool-twist_drill_5"],
    "feature-datum": ["tool-face_mill_50", "tool-face_mill_63"],
}


# 数据类


@dataclass
class ImportStats:
    """单个 JSON 文件的导入统计。"""

    source_file: str = ""
    node_type: str = ""
    success: int = 0
    duplicate: int = 0
    failed: int = 0
    edges_added: int = 0
    edge_type_breakdown: dict[str, int] = field(default_factory=dict)
    node_type_breakdown: dict[str, int] = field(default_factory=dict)
    error_messages: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    retries: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "node_type": self.node_type,
            "success": self.success,
            "duplicate": self.duplicate,
            "failed": self.failed,
            "edges_added": self.edges_added,
            "edge_type_breakdown": dict(self.edge_type_breakdown),
            "node_type_breakdown": dict(self.node_type_breakdown),
            "elapsed_ms": self.elapsed_ms,
            "retries": self.retries,
            "error_count": len(self.error_messages),
            "first_errors": self.error_messages[:3],
        }


@dataclass
class ImportReport:
    """4 个 JSON 文件导入的整体报告。"""

    materials: ImportStats = field(default_factory=ImportStats)
    tools: ImportStats = field(default_factory=ImportStats)
    machines: ImportStats = field(default_factory=ImportStats)
    process_rules: ImportStats = field(default_factory=ImportStats)
    total_nodes: int = 0
    total_edges: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    overall_success: bool = False
    overall_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_success": self.overall_success,
            "overall_message": self.overall_message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_s": round(self.finished_at - self.started_at, 3),
            "total_nodes": self.total_nodes,
            "total_edges": self.total_edges,
            "files": {
                "materials": self.materials.to_dict(),
                "tools": self.tools.to_dict(),
                "machines": self.machines.to_dict(),
                "process_rules": self.process_rules.to_dict(),
            },
        }

    def render_markdown(self) -> str:
        """渲染为 Markdown 报告。"""
        lines: list[str] = []
        lines.append("# 知识图谱导入结果报告（M1.3）")
        lines.append("")
        lines.append(f"- 整体成功：{'是' if self.overall_success else '否'}")
        lines.append(f"- 总耗时：{round(self.finished_at - self.started_at, 3)} s")
        lines.append(f"- 节点总数：**{self.total_nodes}**")
        lines.append(f"- 关系总数：**{self.total_edges}**")
        lines.append("")

        for label, stats in (
            ("materials.json", self.materials),
            ("tools.json", self.tools),
            ("machines.json", self.machines),
            ("process_rules.json", self.process_rules),
        ):
            lines.append(f"## {label}")
            lines.append("")
            lines.append(f"- 来源：``{stats.source_file}``")
            lines.append(f"- 成功节点：{stats.success}")
            lines.append(f"- 重复跳过：{stats.duplicate}")
            lines.append(f"- 失败：{stats.failed}")
            lines.append(f"- 添加关系：{stats.edges_added}")
            lines.append(f"- 重试次数：{stats.retries}")
            lines.append(f"- 耗时：{round(stats.elapsed_ms, 2)} ms")
            if stats.node_type_breakdown:
                lines.append("- 节点类型分布：")
                for nt, n in sorted(stats.node_type_breakdown.items()):
                    lines.append(f"  - {nt}: {n}")
            if stats.edge_type_breakdown:
                lines.append("- 关系类型分布：")
                for et, n in sorted(stats.edge_type_breakdown.items()):
                    lines.append(f"  - {et}: {n}")
            if stats.error_messages:
                lines.append(f"- 错误数：{len(stats.error_messages)}")
            lines.append("")

        return "\n".join(lines)


# 工具函数


# 材料 ID 映射（按 name 归一化为稳定的 slug）
def _material_id_from_name(name: str) -> str:
    """基于材料名生成稳定 node_id。"""
    # 简单 slugify
    slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", name).strip("-").lower()
    if not slug:
        slug = "x"
    return f"material-{slug}"


def _slugify_id(text: str, *, prefix: str = "node") -> str:
    """基于给定文本生成稳定的 node_id 后缀。

    - 仅保留 ASCII 字母 / 数字 / 下划线 / 横线 / 点号；
    - 非 ASCII 字符用 ``u<ord_hex>`` 形式转写以保证唯一性；
    - 输出格式：``<prefix>-<slug>``。
    """
    if not text:
        return f"{prefix}-x"
    buf: list[str] = []
    for ch in text:
        if ch.isascii() and (ch.isalnum() or ch in "_-."):
            buf.append(ch)
        else:
            buf.append(f"u{ord(ch):x}")
    slug = "".join(buf).strip("-_.")
    if not slug:
        slug = "x"
    return f"{prefix}-{slug[:80]}"


def _load_json(path: Path) -> list[dict[str, Any]]:
    """从 JSON 文件加载对象列表。文件不存在或解析失败时抛 RuntimeError。"""
    if not path.exists():
        raise RuntimeError(f"JSON source file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise RuntimeError(f"Failed to read {path}: {exc}") from exc
    if not isinstance(data, list):
        raise RuntimeError(f"Expected JSON array at top level in {path}, got {type(data).__name__}")
    return data


def _retry_with_backoff(
    func: Callable[[], Any],
    *,
    retries: int = 3,
    base_delay_s: float = 0.1,
    label: str = "import",
) -> Any:
    """简单的指数退避重试包装。

    .. note::
        仅同步上下文使用：本函数使用 ``time.sleep`` 进行退避等待，
        不应在 async 上下文中直接调用。async 路径请使用
        ``asyncio.sleep`` 实现的异步重试包装。

    Args:
        func: 实际执行函数（无参 callable）。
        retries: 总重试次数（含首次执行）。``retries=3`` 表示最多尝试 3 次。
        base_delay_s: 首次退避秒数。
        label: 日志标签。

    Returns:
        ``func`` 的返回值。

    Raises:
        最后一次尝试的异常。
    """
    attempts = max(1, int(retries))
    last_exc: BaseException | None = None
    for i in range(attempts):
        try:
            return func()
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            last_exc = exc
            logger.warning(
                "%s attempt %d/%d failed: %s",
                label,
                i + 1,
                attempts,
                exc,
            )
            if i < attempts - 1:
                time.sleep(base_delay_s * (2**i))
    assert last_exc is not None
    raise last_exc


def _resolve_default_path(attr_name: str) -> Path:
    """解析默认 JSON 路径，保留对 ``json_importer`` 模块级 monkeypatch 的兼容。

    优先读取 :mod:`json_importer` 模块上的同名属性（便于测试通过
    ``monkeypatch.setattr(json_importer, "MATERIALS_JSON", ...)`` 替换）；
    若该模块尚未加载或属性缺失，回退到本模块（``_common``）中的定义。
    """
    import sys

    mod = sys.modules.get("app.knowledge_graph.importer.json_importer")
    if mod is not None and hasattr(mod, attr_name):
        return getattr(mod, attr_name)
    return globals()[attr_name]


# 公共工具：去重 & 节点 ID 归一化


class _MaterialDeduper:
    """Material 实体去重（按 name 完全匹配）。"""

    def __init__(self) -> None:
        self._name_to_id: dict[str, str] = {}

    def resolve(self, raw: dict[str, Any]) -> tuple[str | None, bool]:
        """返回 ``(node_id, is_duplicate)``。``is_duplicate=True`` 时不要新建。"""
        name = str(raw.get("name", "")).strip()
        if not name:
            return None, False
        if name in self._name_to_id:
            return self._name_to_id[name], True
        nid = _material_id_from_name(name)
        self._name_to_id[name] = nid
        return nid, False


class _ToolDeduper:
    """Tool 实体去重（按 series + diameter_mm 组合）。"""

    def __init__(self) -> None:
        self._seen: dict[tuple[str, float], str] = {}

    def resolve(self, raw: dict[str, Any]) -> tuple[str | None, bool]:
        """返回 ``(node_id, is_duplicate)``。"""
        series = str(raw.get("series", "")).strip()
        diameter = raw.get("diameter_mm")
        if not series or diameter is None:
            # 缺少关键字段，使用 id 作为兜底（不参与去重）
            tool_id = str(raw.get("id", "")).strip()
            return (
                _slugify_id(tool_id, prefix="tool") if tool_id else None,
                False,
            )
        try:
            diameter_f = float(diameter)
        except (TypeError, ValueError):
            return None, False
        key = (series, diameter_f)
        if key in self._seen:
            return self._seen[key], True
        nid = _slugify_id(f"{series}_{diameter_f}", prefix="tool")
        self._seen[key] = nid
        return nid, False


class _MachineDeduper:
    """Machine 实体去重（按 id 完全匹配）。"""

    def __init__(self) -> None:
        self._ids: dict[str, str] = {}

    def resolve(self, raw: dict[str, Any]) -> tuple[str | None, bool]:
        machine_id = str(raw.get("id", "")).strip()
        if not machine_id:
            return None, False
        if machine_id in self._ids:
            return self._ids[machine_id], True
        nid = _slugify_id(machine_id, prefix="machine")
        self._ids[machine_id] = nid
        return nid, False
