#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将缺失路由追加到 docs/API.md，保持风格一致
按模块分章节，每条路由使用与原文相同的格式
"""

import json
import sys
from pathlib import Path
from collections import defaultdict


# 源文件到模块章节名的映射
SOURCE_TO_MODULE = {
    "agent_state.py": "Agent State",
    "agent_gateway.py": "Agent Gateway",
    "agents.py": "Agent Gateway",
    "api.py": "Simulation",
    "auth.py": "Authentication",
    "cost_budget.py": "Cost & Budget",
    "goal_alignment.py": "Goal Alignment",
    "governance.py": "Governance",
    "health.py": "Base Routes",
    "heartbeat.py": "Heartbeat",
    "jobs.py": "Async Jobs",
    "lnn.py": "LNN Models",
    "main.py": "Base Routes",
    "ollama_routes.py": "Ollama",
    "plugins.py": "Plugins",
    "routes.py": "RAG",
    "skills.py": "Skills",
    "task_checkout.py": "Task Checkout",
    "user_sovereignty.py": "User Sovereignty",
    "users.py": "User Management",
    "wear_prediction.py": "Wear Prediction",
}


def humanize_func_name(name: str) -> str:
    if not name:
        return "API operation"
    return name.replace("_", " ")


def render_route_block(method: str, path: str, func: str, source: str, line: int) -> str:
    """渲染与 docs/API.md 风格一致的路由块"""
    lines = []
    lines.append(f"#### {method} {path}")
    lines.append("")
    lines.append("```")
    lines.append(f"{method} {path}")
    lines.append("```")
    lines.append("")
    lines.append(f"**Description:** {humanize_func_name(func)}.")
    lines.append("")
    if func:
        lines.append(f"**Handler:** `{func}` (`{source}:{line}`)")
        lines.append("")
    lines.append("**Response (200):**")
    lines.append("```json")
    lines.append("{")
    lines.append('  "code": 0,')
    lines.append('  "message": "操作成功",')
    lines.append('  "data": { }')
    lines.append("}")
    lines.append("```")
    lines.append("")
    lines.append("**Status Codes:** `200 OK`, `400 Bad Request`, `401 Unauthorized`")
    lines.append("")
    return "\n".join(lines)


def main(report_path: str = "api-sync-report.json", api_md_path: str = "docs/API.md"):
    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    missing = report.get("missing_from_docs", [])
    only_in_docs = report.get("only_in_docs", [])

    # 过滤掉 FastAPI 生命周期事件 (ON_EVENT)，它们不是真实的 HTTP 端点
    missing = [r for r in missing if r.get("method", "").upper() != "ON_EVENT"]

    # 按 module（源文件）分组
    by_module = defaultdict(list)
    for r in missing:
        module = SOURCE_TO_MODULE.get(r["source_file"], r["source_file"])
        by_module[module].append(r)

    # 构建追加内容
    parts = []
    parts.append("---")
    parts.append("")
    parts.append("## API 路由补全（自动同步）")
    parts.append("")
    parts.append("> 本节由 `scripts/sync_api_docs.py` 根据 `api-sync-report.json` 自动生成。")
    parts.append(">")
    parts.append(f"> 用于将 `docs/API.md` 与 `python/app/**` 中的实际 FastAPI 路由保持同步。")
    parts.append(">")
    parts.append(f"> **补全状态**: {len(missing)} 个缺失路由 / {len(only_in_docs)} 个需复核")
    parts.append("")

    # 目录
    parts.append("### 模块索引")
    parts.append("")
    for i, mod in enumerate(sorted(by_module.keys()), 1):
        parts.append(
            f"{i}. [{mod}](#{mod.lower().replace(' ', '-').replace('&', 'and')}) — {len(by_module[mod])} 个路由"
        )
    parts.append("")

    for mod in sorted(by_module.keys()):
        routes = sorted(by_module[mod], key=lambda x: (x["path"], x["method"]))
        parts.append(f"### {mod} (Routes补全)")
        parts.append("")
        parts.append(f"> 以下路由已存在于 `python/app/**` 中但未在本文档前面章节记录。共 {len(routes)} 条。")
        parts.append("")
        for r in routes:
            block = render_route_block(
                r["method"],
                r["path"],
                r.get("function", ""),
                r["source_file"],
                r.get("source_line", 0),
            )
            parts.append(block)

    # 需复核的路由
    parts.append("---")
    parts.append("")
    parts.append("### 仅在文档中存在（需复核）")
    parts.append("")
    parts.append("> 以下路径在 `docs/API.md` 中被记录但未在代码中匹配到，可能为：")
    parts.append(">")
    parts.append("> - 旧版 API（已废弃 / 重构）")
    parts.append("> - 通过中间件或别名注册")
    parts.append("> - 文档笔误")
    parts.append(">")
    parts.append(f"> 共 **{len(only_in_docs)}** 条，需要人工复核。")
    parts.append("")
    for path in only_in_docs:
        parts.append(f"- `{path}`")
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append(f"*本节由 scripts/sync_api_docs.py 自动生成于 2026-06-11。源数据：{report_path}*")
    parts.append("")

    appendix = "\n".join(parts)

    # 追加到 docs/API.md
    api_md = Path(api_md_path)
    if not api_md.exists():
        sys.exit(f"ERROR: {api_md_path} not found")

    existing = api_md.read_text(encoding="utf-8")
    # 移除之前自动生成的同一节（如果存在），避免重复
    marker_start = "\n---\n\n## API 路由补全（自动同步）\n"
    marker_end = "*本节由 scripts/sync_api_docs.py 自动生成"
    if marker_start in existing and marker_end in existing:
        # 移除旧块
        start = existing.index(marker_start) + 1  # +1 去掉开头的 \n
        end = existing.index(marker_end, start)
        # 找到下一个 --- 之后换行
        tail = existing[end:]
        # 找到 *本节...* 这一行结尾
        nl = tail.index("\n")
        end += nl + 1
        existing = existing[:start] + existing[end:]

    new_content = existing.rstrip() + "\n" + appendix
    api_md.write_text(new_content, encoding="utf-8")

    print(f"已追加 {len(missing)} 个路由到 {api_md_path}")
    print(f"原文件大小: {len(existing.encode('utf-8'))} bytes")
    print(f"新文件大小: {len(new_content.encode('utf-8'))} bytes")


if __name__ == "__main__":
    report = sys.argv[1] if len(sys.argv) > 1 else "api-sync-report.json"
    api_md = sys.argv[2] if len(sys.argv) > 2 else "docs/API.md"
    main(report, api_md)
