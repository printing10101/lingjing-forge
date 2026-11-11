"""本体扩展提案的人工审核入口（命令行）。

批准本体类型是知识治理动作，必须由人执行——MCP 侧刻意没有审批工具。审核人用本脚本：

    python -m mcp_server.ontology_review list
    python -m mcp_server.ontology_review list --kind entity_type --all
    python -m mcp_server.ontology_review approve <proposal_id> --reviewer engineer-01
    python -m mcp_server.ontology_review reject  <proposal_id> --reviewer engineer-01 -r "与已有别名重复"

设计约束与知识候选审核一致：
- ``--reviewer`` 必填，空值拒收（不留匿名批准）；
- 批准只影响本体注册表，不写入任何工艺参数；
- 输出为纯 JSON，便于留档与复验。
"""

from __future__ import annotations

import argparse
import json
import sys

from mcp_server.ontology_tools import _registry


def _dump(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _brief(proposal) -> dict:
    return {
        "proposal_id": proposal.proposal_id,
        "kind": proposal.kind,
        "name": proposal.name,
        "status": proposal.status,
        "occurrences": proposal.occurrences,
        "reviewer": proposal.reviewer,
        "reviewed_at": proposal.reviewed_at,
        "evidence": [
            {"source_id": e.source_id, "field": e.field, "excerpt": e.excerpt}
            for e in proposal.evidence
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ontology_review", description="本体扩展提案人工审核")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列出提案")
    p_list.add_argument("--kind", default="", choices=["", "entity_type", "relation_type", "property_key"])
    p_list.add_argument("--all", action="store_true", help="包含已批准/已拒绝")

    p_appr = sub.add_parser("approve", help="批准提案并登记进本体")
    p_appr.add_argument("proposal_id")
    p_appr.add_argument("--reviewer", required=True)

    p_rej = sub.add_parser("reject", help="拒绝提案")
    p_rej.add_argument("proposal_id")
    p_rej.add_argument("--reviewer", required=True)
    p_rej.add_argument("-r", "--reason", default="")

    args = parser.parse_args(argv)
    registry = _registry()

    try:
        if args.cmd == "list":
            items = registry.all_proposals()
            if args.kind:
                items = [p for p in items if p.kind == args.kind]
            if not args.all:
                items = [p for p in items if p.status == "pending"]
            _dump({"count": len(items), "proposals": [_brief(p) for p in items]})
            return 0

        if args.cmd == "approve":
            changed = registry.approve(args.proposal_id, reviewer=args.reviewer)
            proposal = registry.get(args.proposal_id)
            _dump({"changed": changed, "proposal": _brief(proposal)})
            return 0

        changed = registry.reject(args.proposal_id, reviewer=args.reviewer, reason=args.reason)
        proposal = registry.get(args.proposal_id)
        _dump({"changed": changed, "proposal": _brief(proposal)})
        return 0
    except (KeyError, ValueError) as exc:
        _dump({"error": str(exc)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
