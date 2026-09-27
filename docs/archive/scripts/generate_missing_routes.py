#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 api-sync-report.json 生成缺失路由的 Markdown 文档
按 source_file 分组，便于阅读和维护
"""
import json
import sys
from pathlib import Path
from collections import defaultdict


def main(report_path: str = "api-sync-report.json", output_path: str = "missing_routes.md"):
    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    missing = report.get("missing_from_docs", [])
    only_in_docs = report.get("only_in_docs", [])

    # 按 source_file 分组
    by_source = defaultdict(list)
    for r in missing:
        by_source[r["source_file"]].append(r)

    lines = []
    lines.append("# API 路由补全文档（自动生成）")
    lines.append("")
    lines.append(f"> 本文档由 `scripts/generate_missing_routes.py` 自动生成，源数据：`{report_path}`")
    lines.append(f">")
    lines.append(f"> **总览**")
    lines.append(f">")
    lines.append(f"> - 代码中路由总数: **{report['summary']['total_routes_in_code']}**")
    lines.append(f"> - 文档中路由总数: **{report['summary']['total_routes_in_docs']}**")
    lines.append(f"> - 文档覆盖率: **{report['summary']['documented_coverage_percent']}%**")
    lines.append(f"> - 本次补全: **{len(missing)}** 个缺失路由")
    lines.append(f"> - 仅在文档中存在: **{len(only_in_docs)}** 个（需复核）")
    lines.append("")

    # 目录
    lines.append("## 目录（按源文件分组）")
    lines.append("")
    for i, src in enumerate(sorted(by_source.keys()), 1):
        lines.append(f"{i}. [`{src}`](#{src.replace('.', '').replace('/', '')}) — {len(by_source[src])} 个路由")
    lines.append("")

    # 按 source_file 分组的路由
    lines.append("---")
    lines.append("")
    for src in sorted(by_source.keys()):
        routes = by_source[src]
        lines.append(f"## `{src}`")
        lines.append("")
        lines.append(f"> 共 **{len(routes)}** 个路由")
        lines.append("")

        # 路由表
        lines.append("| 方法 | 路径 | 处理函数 | 源码行号 |")
        lines.append("|------|------|----------|----------|")
        for r in sorted(routes, key=lambda x: (x["path"], x["method"])):
            func = r.get("function", "—") or "—"
            line_no = r.get("source_line", "—")
            lines.append(f"| `{r['method']}` | `{r['path']}` | `{func}` | {line_no} |")
        lines.append("")

        # 每个路由的详细说明
        lines.append("### 详细说明")
        lines.append("")
        for r in sorted(routes, key=lambda x: (x["path"], x["method"])):
            func = r.get("function", "—") or "—"
            lines.append(f"#### `{r['method']} {r['path']}`")
            lines.append("")
            if func and func != "—":
                lines.append(f"**Source function:** `{func}`  ")
            lines.append(f"**Source:** `{src}:{r.get('source_line', '?')}`")
            lines.append("")
            lines.append("**Response (200):**")
            lines.append("```json")
            lines.append("{")
            lines.append('  "code": 0,')
            lines.append('  "message": "操作成功",')
            lines.append('  "data": { ... }')
            lines.append("}")
            lines.append("```")
            lines.append("")

    # 仅在文档中存在的路由（需复核）
    lines.append("---")
    lines.append("")
    lines.append("## 仅在文档中存在（需复核）")
    lines.append("")
    lines.append("> 以下路由出现在 `docs/API.md` 中但未在代码中匹配到，可能为：")
    lines.append(">")
    lines.append("> - 旧版 API 已废弃/重构")
    lines.append("> - 通过装饰器别名或中间件注册")
    lines.append("> - 文档笔误")
    lines.append(">")
    lines.append(f"> 共 **{len(only_in_docs)}** 条")
    lines.append("")
    for path in only_in_docs:
        lines.append(f"- `{path}`")
    lines.append("")

    output = "\n".join(lines)
    Path(output_path).write_text(output, encoding="utf-8")
    print(f"已生成 {output_path}，共 {len(missing)} 个路由（{len(by_source)} 个源文件），{len(only_in_docs)} 条需复核。")


if __name__ == "__main__":
    report = sys.argv[1] if len(sys.argv) > 1 else "api-sync-report.json"
    output = sys.argv[2] if len(sys.argv) > 2 else "missing_routes.md"
    main(report, output)
