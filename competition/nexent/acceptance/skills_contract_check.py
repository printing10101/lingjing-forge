"""Skill 编排契约校验（模型自主编排验收的前置静态检查）。

为什么先做这个
--------------
"Nexent 里模型自己选 Skill" 这件事，失败通常不是模型笨，而是契约先错了：

- Skill 的 `allowed-tools` 写了一个服务端根本没有的工具名 → 模型选了 Skill 也调不到；
- 工具描述里没写读写权限 → 模型不该调的时候调了；
- Skill 没写边界声明（"不做 X"）→ 模型越界时没有可援引的依据；
- 某个 Skill 绑了审批类工具 → 治理闸门被绕开。

这些都能在跑之前用静态检查发现，不需要 Docker，也不需要模型。本脚本做四件事：

1. 从 `mcp_server/*.py` 收集实际注册的工具名（源码解析，不导入，避开 httpx 依赖）；
2. 解析 4 个 `SKILL.md` 的 frontmatter 与正文；
3. 交叉校验：工具存在性、边界声明、审批类工具禁令、命名一致性；
4. 输出 JSON 报告，退出码 0 = 可进入模型验收。

注意：本脚本**不能**替代"模型自主编排验收"。它只证明契约自洽，不证明模型会按契约走。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS_DIR = REPO_ROOT / "competition" / "nexent" / "skills"
MCP_DIR = REPO_ROOT / "mcp_server"

#: 审批类工具名（出现即视为绕过人工闸门）
FORBIDDEN_TOOL_PATTERNS = (r"approve", r"reject", r"publish", r"dnc_send", r"deploy")

#: 边界声明的最低要求：Skill 正文必须显式否定至少一件事
BOUNDARY_MARKERS = ("不执行", "不构建", "不得", "不能", "不写", "不声称", "不做", "不批准", "不下发")

#: 有意不绑定 Skill 的工具：必须写明理由，否则视为覆盖遗漏（error）。
#: 白名单是显式的——"忘了绑"和"故意不绑"必须区分开。
INTENTIONAL_UNBOUND: dict[str, str] = {
    "lnn_list_models": "科研侧模型管理，不在工艺决策链路",
    "lnn_get_model_info": "科研侧模型管理，不在工艺决策链路",
    "lnn_predict": "科研侧推理接口，比赛链路不直接调用",
    "lnn_train": "训练为写操作且耗时长，禁止模型自行触发",
    "lnn_get_train_status": "训练状态查询，随训练一同排除",
    "lnn_wait_for_training": "长等待阻塞，禁止模型进入",
    "gcode_create_job": "写入类且触发长任务，交人工在桌面端发起",
    "gcode_get_job_status": "仅服务于 create_job 之后的状态轮询",
    "gcode_list_jobs": "同上，非决策必需",
}

_TOOL_DEF = re.compile(r'name="([a-z][a-z0-9_]+)"')
_DESCRIPTION = re.compile(r'description="([^"]{10,})"')


def collect_server_tools(mcp_dir: Path) -> dict[str, dict[str, str]]:
    """从源码里抓取工具名与描述（避免导入模块触发 httpx 依赖）。"""
    tools: dict[str, dict[str, str]] = {}
    for path in sorted(mcp_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for block in re.finditer(r"@server\.tool\((.*?)\)\s*(?:async )?def", text, re.S):
            chunk = block.group(1)
            name = _TOOL_DEF.search(chunk)
            if not name:
                continue
            desc = _DESCRIPTION.search(chunk)
            tools[name.group(1)] = {
                "file": path.name,
                "description": desc.group(1) if desc else "",
            }
    return tools


def parse_skill(path: Path) -> dict[str, object]:
    """解析 SKILL.md 的 frontmatter 与工具白名单。"""
    text = path.read_text(encoding="utf-8")
    front, body = text, ""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            front, body = parts[1], parts[2]

    name_match = re.search(r"^name:\s*(\S+)", front, re.M)
    desc_match = re.search(r"^description:\s*(.+)$", front, re.M)

    allowed: list[str] = []
    in_block = False
    for line in front.splitlines():
        if re.match(r"^allowed-tools:", line):
            in_block = True
            continue
        if in_block:
            stripped = line.strip()
            if stripped.startswith("- "):
                allowed.append(stripped[2:].strip())
            elif stripped and not stripped.startswith("#"):
                in_block = False

    return {
        "skill": path.parent.name,
        "path": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
        "name": name_match.group(1) if name_match else None,
        "description_len": len(desc_match.group(1)) if desc_match else 0,
        "allowed_tools": allowed,
        "body": body,
    }


def check(skills: list[dict], server_tools: dict[str, dict[str, str]]) -> dict:
    findings: list[dict] = []
    inventory = set(server_tools)

    for skill in skills:
        label = skill["skill"]

        if not skill["allowed_tools"]:
            findings.append({"level": "error", "skill": label, "issue": "allowed-tools 为空"})

        for tool in skill["allowed_tools"]:
            if tool not in inventory:
                findings.append(
                    {
                        "level": "error",
                        "skill": label,
                        "issue": f"绑定了服务端不存在的工具：{tool}",
                    }
                )
            for pattern in FORBIDDEN_TOOL_PATTERNS:
                if re.search(pattern, tool):
                    findings.append(
                        {
                            "level": "error",
                            "skill": label,
                            "issue": f"绑定了越权类工具：{tool}（审批/下发必须留在人工侧）",
                        }
                    )

        body = str(skill["body"])
        if not any(marker in body for marker in BOUNDARY_MARKERS):
            findings.append(
                {"level": "error", "skill": label, "issue": "正文缺少边界声明（未显式否定任何能力）"}
            )

        if not skill["name"] or skill["name"] != label:
            findings.append(
                {
                    "level": "warn",
                    "skill": label,
                    "issue": f"frontmatter name 与目录名不一致：{skill['name']}",
                }
            )

        if skill["description_len"] < 20:
            findings.append(
                {"level": "warn", "skill": label, "issue": "description 过短，模型难以据此选技能"}
            )

    covered = {t for s in skills for t in s["allowed_tools"]}
    unbound = sorted(inventory - covered)
    undocumented = [t for t in unbound if t not in INTENTIONAL_UNBOUND]
    for tool in undocumented:
        findings.append(
            {
                "level": "error",
                "skill": "(全局)",
                "issue": f"工具未绑定任何 Skill 且未登记为有意排除：{tool}",
            }
        )
    governance_tools = sorted(
        t for t in inventory if any(re.search(p, t) for p in FORBIDDEN_TOOL_PATTERNS)
    )

    return {
        "skills": [
            {
                "skill": s["skill"],
                "allowed_tools": s["allowed_tools"],
                "has_boundary": any(m in str(s["body"]) for m in BOUNDARY_MARKERS),
            }
            for s in skills
        ],
        "server_tool_count": len(inventory),
        "covered_tool_count": len(covered),
        "unbound_tools": unbound,
        "unbound_intentional": {t: INTENTIONAL_UNBOUND[t] for t in unbound if t in INTENTIONAL_UNBOUND},
        "unbound_undocumented": undocumented,
        "governance_tools_on_server": governance_tools,
        "findings": findings,
        "errors": sum(1 for f in findings if f["level"] == "error"),
        "warnings": sum(1 for f in findings if f["level"] == "warn"),
    }


def self_test() -> int:
    """反向自检：故意造一个坏 Skill，确认校验器真的会报错。

    没有这一步，绿灯可能只是"什么都没检查"。三个注入缺陷分别对应三类真实事故：
    工具名拼错、绑了审批类工具、没写边界声明。
    """
    broken = {
        "skill": "broken-skill",
        "path": "(synthetic)",
        "name": "broken-skill",
        "description_len": 5,
        "allowed_tools": ["cam_approve_knowledge", "cam_not_exist_tool"],
        "body": "这个技能什么都能干。",
    }
    server_tools = {
        "cam_recommend_process": {"file": "synthetic.py", "description": "x"},
    }
    report = check([broken], server_tools)
    emitted = [f["issue"] for f in report["findings"]]
    expected = ("服务端不存在的工具", "越权类工具", "边界声明", "description 过短")
    missing = [e for e in expected if not any(e in issue for issue in emitted)]
    for issue in emitted:
        print(f"  [{report['findings'][0]['level']}] {issue}")
    if missing:
        print(f"自检失败：校验器漏检 {missing}")
        return 1
    print(f"自检通过：{report['errors']} 项错误、{report['warnings']} 项警告（预期全部命中）")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    if not SKILLS_DIR.is_dir():
        print(f"找不到技能目录：{SKILLS_DIR}")
        return 1

    server_tools = collect_server_tools(MCP_DIR)
    skills = [parse_skill(p) for p in sorted(SKILLS_DIR.glob("*/SKILL.md"))]
    report = check(skills, server_tools)

    out = REPO_ROOT / "competition" / "nexent" / "acceptance" / "skills-contract-report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"服务端工具 {report['server_tool_count']} 个，被 Skill 覆盖 {report['covered_tool_count']} 个")
    for item in report["skills"]:
        mark = "边界OK" if item["has_boundary"] else "边界缺失"
        print(f"  - {item['skill']}: {len(item['allowed_tools'])} 个工具，{mark}")
    if report["unbound_intentional"]:
        print(f"  有意不绑定 {len(report['unbound_intentional'])} 个（已登记理由，不计为缺陷）")
    if report["governance_tools_on_server"]:
        print(f"  服务端存在越权类工具（应为空）：{report['governance_tools_on_server']}")

    for finding in report["findings"]:
        print(f"  [{finding['level'].upper()}] {finding['skill']}: {finding['issue']}")

    print(f"\n错误 {report['errors']} 项，警告 {report['warnings']} 项；报告写入 {out.relative_to(REPO_ROOT)}")
    return 0 if report["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
