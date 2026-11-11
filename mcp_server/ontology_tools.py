"""本体扩展提案 MCP 工具组（赛题三任务要求②的对外接口）。

把 ``app/knowledge_graph/ontology_proposal.py`` 的提案机制暴露给 Nexent 侧智能体，
使"检出未登记的本体类型 → 形成待审提案"这一环可以在平台内被真实调用，而不是只留在
本地单测里。

三个工具：

- ``cam_propose_ontology_extension``（B，写待审队列）：扫描一份知识候选，检出固定
  本体之外的概念 / 关系 / 属性键，生成带溯源的待审提案。
- ``cam_list_ontology_proposals``（R）：列出现有待审或全部提案，供审核人处理。
- ``cam_get_ontology_registry``（R）：列出**已生效**的实体类型 / 关系类型 / 属性键。

刻意**不提供审批工具**：批准是知识治理动作，必须由人在审核界面或
``python -m mcp_server.ontology_review`` 完成。智能体能把提案摆到人面前，但不能替人
拍板——这与既有 ``cam_submit_knowledge_candidate`` 的设计一致。

数据落本机 SQLite（默认 ``data/ontology/ontology.sqlite``），路径可用环境变量
``LINGJING_ONTOLOGY_DB`` 覆盖。开关：``LINGJING_MCP_ONTOLOGY_TOOLS=0`` 关闭。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

try:  # 生产环境与网关工具共用同一套输出格式
    from mcp_server.tools import _format_error, _format_success
except ModuleNotFoundError:  # 精简环境（未装 httpx）下本体工具仍可独立运行
    def _format_success(data: Any) -> str:
        return json.dumps(data, indent=2, ensure_ascii=False)

    def _format_error(message: str, detail: Any = None) -> str:
        payload: dict[str, Any] = {"error": True, "message": message}
        if detail is not None:
            payload["detail"] = detail
        return json.dumps(payload, indent=2, ensure_ascii=False)

logger = logging.getLogger("lingjing-mcp")
_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_ROOT = _REPO_ROOT / "engineering" / "python"


def _ensure_app_importable() -> None:
    """把 ``engineering/python`` 加入 sys.path，使 ``app.*`` 可导入。

    MCP 服务以 ``python -m mcp_server.server`` 从仓库根启动，仓库根本身不含 ``app``
    包；这里做一次幂等补丁，避免依赖启动目录或外部 PYTHONPATH。
    """
    target = str(_APP_ROOT)
    if target not in sys.path:
        sys.path.insert(0, target)


def _db_path() -> Path:
    override = os.environ.get("LINGJING_ONTOLOGY_DB", "").strip()
    path = Path(override) if override else _REPO_ROOT / "data" / "ontology" / "ontology.sqlite"
    return path


def _registry() -> Any:
    _ensure_app_importable()
    from app.knowledge_graph.ontology_proposal import OntologyRegistry

    return OntologyRegistry.fixed_default(db_path=_db_path())


def _proposal_brief(proposal: Any) -> dict[str, Any]:
    """提案的对外视图：去掉内部对象，只留审核人需要判断的字段。"""
    return {
        "proposal_id": proposal.proposal_id,
        "kind": proposal.kind,
        "name": proposal.name,
        "status": proposal.status,
        "occurrences": proposal.occurrences,
        "rationale": proposal.rationale,
        "evidence": [
            {
                "source_id": e.source_id,
                "field": e.field,
                "excerpt": e.excerpt,
                "doc_sha256": e.doc_sha256,
            }
            for e in proposal.evidence
        ],
        "first_seen": proposal.first_seen,
        "last_seen": proposal.last_seen,
        "reviewer": proposal.reviewer,
    }


def handle_propose(candidate: dict, submission_id: str) -> str:
    """扫描候选并登记提案；返回本次产生/合并的提案。"""
    try:
        if not isinstance(candidate, dict) or not candidate:
            raise ValueError("candidate 必须是非空 JSON 对象")
        if not submission_id or not submission_id.strip():
            raise ValueError("submission_id 不能为空：提案必须可溯源到来源候选")
        if len(json.dumps(candidate, allow_nan=False).encode()) > 64 * 1024:
            raise ValueError("candidate 超过 64KB 上限")

        registry = _registry()
        produced = registry.scan_candidate(candidate, source_id=submission_id.strip())
        if not produced:
            return _format_success(
                {
                    "proposals": [],
                    "message": "未检出固定本体之外的类型或属性键；本次不产生提案",
                    "registered_entity_types": registry.registered("entity_type"),
                    "registered_relation_types": registry.registered("relation_type"),
                }
            )
        return _format_success(
            {
                "proposals": [_proposal_brief(p) for p in produced],
                "message": (
                    "已登记为待审提案，尚未生效；需人工在审核入口批准后该类型才被认可。"
                ),
                "requires_human_review": True,
            }
        )
    except (ValueError, TypeError) as exc:
        return _format_error(str(exc))
    except Exception:  # noqa: BLE001 - MCP 必须返回可用 observation
        logger.exception("Ontology proposal failed")
        return _format_error("本体提案未完成，请检查候选格式与本体存储路径；不要推定提案已登记")


def handle_list(kind: str = "", include_reviewed: bool = False) -> str:
    """列出提案；``kind`` 为空则返回全部种类。"""
    try:
        registry = _registry()
        if kind and kind not in {"entity_type", "relation_type", "property_key"}:
            raise ValueError("kind 只能是 entity_type / relation_type / property_key")
        proposals = registry.all_proposals()
        if kind:
            proposals = [p for p in proposals if p.kind == kind]
        if not include_reviewed:
            proposals = [p for p in proposals if p.status == "pending"]
        proposals.sort(key=lambda p: (-p.occurrences, p.kind, p.name))
        return _format_success(
            {
                "count": len(proposals),
                "proposals": [_proposal_brief(p) for p in proposals],
                "note": "pending 表示仅被检出、尚未生效；生效类型见 cam_get_ontology_registry",
            }
        )
    except ValueError as exc:
        return _format_error(str(exc))
    except Exception:  # noqa: BLE001
        logger.exception("Ontology proposal listing failed")
        return _format_error("无法读取提案队列，请检查本体存储路径后重试")


def handle_registry() -> str:
    """列出已生效的本体类型；未经批准的类型不会出现在这里。"""
    try:
        registry = _registry()
        return _format_success(
            {
                "entity_types": registry.registered("entity_type"),
                "relation_types": registry.registered("relation_type"),
                "property_keys": registry.registered("property_key"),
                "note": "此处仅含已人工批准的类型；pending 提案不在其中",
            }
        )
    except Exception:  # noqa: BLE001
        logger.exception("Ontology registry read failed")
        return _format_error("无法读取本体注册表，请检查本体存储路径后重试")


def register_ontology_tools(server) -> None:
    """注册本体提案相关工具（失败不影响既有工具可用性）。"""

    @server.tool(
        name="cam_propose_ontology_extension",
        description=(
            "扫描一份待审知识候选，检出固定本体之外的概念/关系/属性键并登记为待审提案（B·写待审队列）。"
            "只检出，不生效；批准必须由人工完成。submission_id 用于溯源，不得为空。"
        ),
    )
    async def propose(candidate: dict, submission_id: str) -> list[dict]:
        return [{"type": "text", "text": handle_propose(candidate, submission_id)}]

    @server.tool(
        name="cam_list_ontology_proposals",
        description=(
            "列出本体扩展提案（R）。默认只返回待审项；include_reviewed=true 时含已批准/已拒绝。"
            "kind 可限定 entity_type / relation_type / property_key。"
        ),
    )
    async def list_proposals(kind: str = "", include_reviewed: bool = False) -> list[dict]:
        return [{"type": "text", "text": handle_list(kind, include_reviewed)}]

    @server.tool(
        name="cam_get_ontology_registry",
        description="读取当前已生效的本体注册表（R）：实体类型、关系类型、属性键。仅含已人工批准项。",
    )
    async def registry_view() -> list[dict]:
        return [{"type": "text", "text": handle_registry()}]
