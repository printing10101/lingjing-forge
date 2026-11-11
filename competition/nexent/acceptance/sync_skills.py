"""把第 5 个 Skill 同步进 Nexent，并绑定到 process_advisor。

只做三件事，全部经 Nexent 的公开接口，不直接改数据库：

1. 刷新 MCP 工具表（`/mcp/refresh-tools` + `/tool/scan_tool`），确保 3 个本体工具已入库；
2. 若 `manufacturing-ontology-maintain` 不存在则上传 zip 创建，存在则更新；
3. 校验 tool_ids 与 SKILL.md 的 allowed-tools 一致，再在智能体的技能实例上启用。

运行：
    py -3.14 competition/nexent/acceptance/sync_skills.py [--agent-id 1]

退出码 0 = 工具、技能、绑定三者一致。
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import closing
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
NEXENT_DEPLOY = Path("D:/nexent-deploy")
SKILL_NAME = "manufacturing-ontology-maintain"
ZIP_PATH = REPO_ROOT / "competition" / "nexent" / "dist" / f"{SKILL_NAME}-i18.zip"
SKILL_MD = REPO_ROOT / "competition" / "nexent" / "skills" / SKILL_NAME / "SKILL.md"

sys.path.insert(0, str(NEXENT_DEPLOY))
from nexent_connection import authenticated_client  # noqa: E402


def _allowed_tools() -> list[str]:
    front = SKILL_MD.read_text(encoding="utf-8").split("---", 2)[1]
    meta = yaml.safe_load(front)
    return meta.get("allowed-tools") or meta.get("allowed_tools") or []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-id", type=int, default=1)
    args = parser.parse_args(argv)

    report: dict = {"skill": SKILL_NAME, "agent_id": args.agent_id}
    with closing(authenticated_client(NEXENT_DEPLOY, timeout=60)) as client:
        # 1. 工具入库
        client.post("/mcp/refresh-tools", params={"mcp_id": 1}, timeout=120).raise_for_status()
        client.get("/tool/scan_tool", timeout=180).raise_for_status()
        tools = {t["name"]: t for t in client.get("/tool/list").json()}
        allowed = _allowed_tools()
        missing = [n for n in allowed if n not in tools]
        if missing:
            report["error"] = f"工具未入库：{missing}"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1
        report["tools_ready"] = allowed

        # 2. 技能创建或更新
        existing = {s["name"]: s for s in client.get("/skills").json()["skills"]}
        if SKILL_NAME in existing:
            with ZIP_PATH.open("rb") as fh:
                resp = client.put(
                    f"/skills/{SKILL_NAME}/upload",
                    files={"file": (ZIP_PATH.name, fh, "application/zip")},
                    timeout=60,
                )
            action = "updated"
        else:
            with ZIP_PATH.open("rb") as fh:
                resp = client.post(
                    "/skills/upload",
                    files={"file": (ZIP_PATH.name, fh, "application/zip")},
                    timeout=60,
                )
            action = "created"
        resp.raise_for_status()
        report["skill_action"] = action

        skills = {s["name"]: s for s in client.get("/skills").json()["skills"]}
        if SKILL_NAME not in skills:
            report["error"] = "上传后技能仍不存在"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1
        skill_id = skills[SKILL_NAME]["skill_id"]
        report["skill_id"] = skill_id

        # 3. 工具映射校验：把 SKILL.md 里的工具名映射成 Nexent 的 tool_id
        detail = client.get(f"/skills/{skill_id}").json()
        expected = {tools[n]["tool_id"] for n in allowed}
        actual = set(detail.get("tool_ids") or [])
        if actual != expected:
            # 上传不会自动带工具映射，需要显式更新
            client.put(f"/skills/{skill_id}", json={"tool_ids": sorted(expected)}).raise_for_status()
            detail = client.get(f"/skills/{skill_id}").json()
            actual = set(detail.get("tool_ids") or [])
        report["tool_mapping_ok"] = actual == expected
        if not report["tool_mapping_ok"]:
            report["error"] = f"工具映射不一致：expected={sorted(expected)} actual={sorted(actual)}"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1

        # 4. 绑定到智能体（若未绑定则启用）
        rows_resp = client.get("/skills/instance/list", params={"agent_id": args.agent_id, "version_no": 0})
        rows_resp.raise_for_status()
        payload = rows_resp.json()
        rows = payload if isinstance(payload, list) else payload.get("instances", payload.get("data", []))
        current = {r["skill_id"]: r.get("enabled") for r in rows}
        if skill_id in current:
            if not current[skill_id]:
                client.post(
                    "/skills/instance/update",
                    json={"agent_id": args.agent_id, "version_no": 0, "skill_id": skill_id, "enabled": True},
                ).raise_for_status()
            report["binding"] = "already_bound" if current[skill_id] else "enabled"
        else:
            client.post(
                "/skills/instance/update",
                json={"agent_id": args.agent_id, "version_no": 0, "skill_id": skill_id, "enabled": True},
            ).raise_for_status()
            report["binding"] = "bound"

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
