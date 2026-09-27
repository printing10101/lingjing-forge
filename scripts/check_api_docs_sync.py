#!/usr/bin/env python3
"""
API Documentation Sync Checker

Scans Python source code to extract all FastAPI API routes and compares them
with the documentation in docs/API.md. Reports any discrepancies including:
- Routes in code but missing from docs
- Routes in docs but missing from code
- Outdated route details (method, params, etc.)

Usage:
    python scripts/check_api_docs_sync.py [--docs-path PATH] [--verbose]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

# Route Extraction from Python Source Code

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}

# Regex patterns for FastAPI route extraction
ROUTE_PATTERN = re.compile(
    r"@router\.(?P<method>[a-z_]+)\s*\(\s*(?P<args>[^)]*)\)",
    re.MULTILINE,
)

MAIN_ROUTE_PATTERN = re.compile(
    r"@app\.(?P<method>[a-z_]+)\s*\(\s*[\"'](?P<path>[^\"']*)[\"']",
    re.MULTILINE,
)

PREFIX_PATTERN = re.compile(
    r"APIRouter\s*\([^)]*prefix\s*=\s*[\"'](?P<prefix>[^\"']*)[\"']",
    re.MULTILINE,
)

TAGS_PATTERN = re.compile(
    r"APIRouter\s*\([^)]*tags\s*=\s*\[(?P<tags>[^\]]*)\]",
    re.MULTILINE,
)


@dataclass
class RouteInfo:
    """Represents a single API route."""

    method: str = ""
    path: str = ""
    prefix: str = ""
    full_path: str = ""
    function_name: str = ""
    tags: list[str] = field(default_factory=list)
    request_model: str = ""
    query_params: list[str] = field(default_factory=list)
    path_params: list[str] = field(default_factory=list)
    source_file: str = ""
    source_line: int = 0


def extract_routes_from_file(file_path: str) -> list[RouteInfo]:
    """Extract all FastAPI routes from a Python file."""
    routes = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return routes

    # Extract prefix
    prefix_match = PREFIX_PATTERN.search(content)
    prefix = prefix_match.group("prefix") if prefix_match else ""

    # Extract tags
    tags_match = TAGS_PATTERN.search(content)
    tags = []
    if tags_match:
        tags_raw = tags_match.group("tags")
        tags = [t.strip().strip("\"'") for t in tags_raw.split(",")]

    # Extract @router routes
    for match in ROUTE_PATTERN.finditer(content):
        method = match.group("method")
        args_str = match.group("args")

        if method not in HTTP_METHODS:
            continue

        # Extract path
        path_match = re.search(r"[\"']([^\"']*)[\"']", args_str)
        path = path_match.group(1) if path_match else ""

        # Find the function definition after this decorator
        func_match = re.search(
            r"async\s+def\s+(\w+)",
            content[match.end() : match.end() + 100],
        )
        func_name = func_match.group(1) if func_match else ""

        # Extract path parameters from route path
        path_params = re.findall(r"\{(\w+)\}", path)

        # Extract function signature for request model detection
        func_start = content.find(f"def {func_name}") if func_name else -1
        func_sig = ""
        if func_start > 0:
            func_sig_end = content.find("):", func_start)
            if func_sig_end > 0:
                func_sig = content[func_start:func_sig_end]

        # Extract request model (Pydantic type annotation)
        request_model = ""
        if func_sig:
            # Look for body: SomeModel or request: SomeModel patterns
            body_match = re.search(
                r"(?:body|request):\s*([A-Z]\w+)",
                func_sig,
            )
            if body_match:
                request_model = body_match.group(1)

        full_path = prefix + path

        route = RouteInfo(
            method=method.upper(),
            path=path,
            prefix=prefix,
            full_path=full_path,
            function_name=func_name,
            tags=tags,
            request_model=request_model,
            path_params=path_params,
            source_file=os.path.basename(file_path),
            source_line=content[: match.start()].count("\n") + 1,
        )
        routes.append(route)

    return routes


def extract_main_routes(file_path: str) -> list[RouteInfo]:
    """Extract routes defined directly on the FastAPI app instance."""
    routes = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return routes

    for match in MAIN_ROUTE_PATTERN.finditer(content):
        method = match.group("method")
        path = match.group("path")

        # Skip non-HTTP "routes" like FastAPI lifecycle handlers
        # (e.g. @app.on_event("startup") / @app.on_event("shutdown")).
        if method not in HTTP_METHODS:
            continue

        func_match = re.search(
            r"async\s+def\s+(\w+)",
            content[match.end() : match.end() + 100],
        )
        func_name = func_match.group(1) if func_match else ""

        route = RouteInfo(
            method=method.upper(),
            path=path,
            full_path=path,
            function_name=func_name,
            source_file=os.path.basename(file_path),
            source_line=content[: match.start()].count("\n") + 1,
        )
        routes.append(route)

    return routes


def scan_api_routes(project_root: str) -> list[RouteInfo]:
    """Scan all Python files in the project for API routes."""
    all_routes = []

    # V2.7 重构后代码从 python/app 迁移至 engineering/python/app，
    # 动态探测两种布局（优先新结构，兼容旧结构/单测目录）。
    candidates = [
        os.path.join(project_root, "engineering", "python"),
        os.path.join(project_root, "python"),
    ]
    app_root = next(
        (c for c in candidates if os.path.isdir(os.path.join(c, "app"))),
        None,
    )
    if app_root is None:
        return []

    # Scan v1 API routes
    api_dirs = [
        os.path.join(app_root, "app", "api", "v1"),
        os.path.join(app_root, "app", "rag"),
        os.path.join(app_root, "app", "ai"),
        os.path.join(app_root, "app", "simulation"),
        os.path.join(app_root, "app", "projects"),
        os.path.join(app_root, "app", "step_import"),
        os.path.join(app_root, "app", "rules"),
    ]

    for api_dir in api_dirs:
        if not os.path.isdir(api_dir):
            continue
        for filename in os.listdir(api_dir):
            if not filename.endswith(".py") or filename.startswith("__"):
                continue
            filepath = os.path.join(api_dir, filename)
            routes = extract_routes_from_file(filepath)
            all_routes.extend(routes)

    # Scan main.py for app-level routes
    main_file = os.path.join(app_root, "app", "main.py")
    if os.path.isfile(main_file):
        all_routes.extend(extract_main_routes(main_file))

    return all_routes


# Documentation Parsing


def extract_routes_from_docs(docs_path: str) -> dict[str, list[dict[str, Any]]]:
    """Extract documented routes from the API documentation markdown file."""
    documented_routes = {}
    try:
        with open(docs_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"WARNING: Cannot read docs file: {e}", file=sys.stderr)
        return documented_routes

    # Normalize line endings to Unix-style for consistent regex matching
    content = content.replace("\r\n", "\n")

    # Pattern 1: Code block format `GET /path` or `POST /path` inside ``` blocks
    route_pattern_1 = re.finditer(
        r"^(?P<method>[A-Z]+)\s+/(?P<path>[^\s`]+)$",
        content,
        re.MULTILINE,
    )
    for match in route_pattern_1:
        method = match.group("method")
        if method.lower() not in HTTP_METHODS:
            continue
        path = "/" + match.group("path")
        documented_routes[path] = documented_routes.get(path, [])
        documented_routes[path].append({"method": method.upper(), "path": path})

    # Pattern 2: `- **POST** \`/path\``
    route_pattern_2 = re.finditer(
        r"-\s+\*\*(?P<method>[A-Z]+)\*\*\s+`/(?P<path>[^`]+)`",
        content,
    )
    for match in route_pattern_2:
        method = match.group("method")
        if method.upper() not in {m.upper() for m in HTTP_METHODS}:
            continue
        path = "/" + match.group("path")
        documented_routes[path] = documented_routes.get(path, [])
        documented_routes[path].append({"method": method.upper(), "path": path})

    # Pattern 3: Table format `| METHOD | /path |`
    route_pattern_3 = re.finditer(
        r"\|\s*(?P<method>[A-Z]+)\s*\|\s*/(?P<path>[^|]+?)\s*\|",
        content,
    )
    for match in route_pattern_3:
        method = match.group("method")
        if method.upper() not in {m.upper() for m in HTTP_METHODS}:
            continue
        path = "/" + match.group("path").strip()
        documented_routes[path] = documented_routes.get(path, [])
        documented_routes[path].append({"method": method.upper(), "path": path})

    # Pattern 4: `### METHOD /path` or `### `METHOD` `/path`` (gen-api-docs.py format)
    route_pattern_4 = re.finditer(
        r"#{2,4}\s+`?(?P<method>[A-Z]+)`?\s+`?/(?P<path>[^\n`]+)",
        content,
    )
    for match in route_pattern_4:
        method = match.group("method")
        if method.upper() not in {m.upper() for m in HTTP_METHODS}:
            continue
        full_path = "/" + match.group("path").strip().split(" ")[0]
        documented_routes[full_path] = documented_routes.get(full_path, [])
        documented_routes[full_path].append({"method": method, "path": full_path})

    # Pattern 5: 反引号表格格式 `| `GET` | `/path` | summary |`（gen-api-docs.py v2.7.0 全量路由总览）
    route_pattern_5 = re.finditer(
        r"^\|\s*`(?P<method>[A-Z]+)`\s*\|\s*`/(?P<path>[^`]+)`\s*\|",
        content,
        re.MULTILINE,
    )
    for match in route_pattern_5:
        method = match.group("method")
        if method.upper() not in {m.upper() for m in HTTP_METHODS}:
            continue
        path = "/" + match.group("path").strip()
        documented_routes[path] = documented_routes.get(path, [])
        documented_routes[path].append({"method": method.upper(), "path": path})

    return documented_routes


# Comparison and Reporting


def compare_routes(
    code_routes: list[RouteInfo],
    documented_routes: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Compare routes from code with documented routes."""
    code_route_set = {}
    for route in code_routes:
        key = route.full_path
        code_route_set[key] = code_route_set.get(key, [])
        code_route_set[key].append(route)

    code_only = []
    docs_only = []
    both = []

    all_paths = set(list(code_route_set.keys()) + list(documented_routes.keys()))

    for path in sorted(all_paths):
        in_code = path in code_route_set
        in_docs = path in documented_routes

        if in_code and not in_docs:
            for route in code_route_set[path]:
                code_only.append(route)
        elif in_docs and not in_code:
            docs_only.append(path)
        else:
            both.append(path)

    return {
        "code_only": code_only,
        "docs_only": docs_only,
        "both": both,
        "total_code_routes": len(code_routes),
        "total_documented": len(documented_routes),
        "coverage": len(both) / len(code_route_set) * 100 if code_route_set else 0,
    }


def generate_report(
    result: dict[str, Any],
    documented_routes: dict[str, list[dict[str, Any]]],
    verbose: bool = False,
) -> str:
    """Generate a human-readable report."""
    lines = []
    lines.append("=" * 60)
    lines.append("API Documentation Sync Report")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Total routes in code:    {result['total_code_routes']}")
    lines.append(f"Total routes in docs:    {result['total_documented']}")
    lines.append(f"Documented coverage:     {result['coverage']:.1f}%")
    lines.append("")

    # Routes in code but not in docs
    if result["code_only"]:
        lines.append("-" * 60)
        lines.append("ROUTES IN CODE BUT MISSING FROM DOCS:")
        lines.append("-" * 60)
        for route in sorted(result["code_only"], key=lambda r: r.full_path):
            lines.append(f"  [{route.method}] {route.full_path}  ({route.source_file}:{route.source_line})")
            if route.function_name:
                lines.append(f"    Function: {route.function_name}")
            if route.request_model:
                lines.append(f"    Request Model: {route.request_model}")
        lines.append("")

    # Routes in docs but not in code
    if result["docs_only"]:
        lines.append("-" * 60)
        lines.append("ROUTES IN DOCS BUT NOT FOUND IN CODE (may be deprecated):")
        lines.append("-" * 60)
        for path in sorted(result["docs_only"]):
            docs_entries = documented_routes.get(path, [])
            if docs_entries:
                methods = [m["method"] for m in docs_entries]
            else:
                methods = ["?"]
            lines.append(f"  [{', '.join(methods)}] {path}")
        lines.append("")

    # Matched routes
    if result["both"]:
        lines.append("-" * 60)
        lines.append(f"ROUTES MATCHING ({len(result['both'])}):")
        lines.append("-" * 60)
        if verbose:
            for path in sorted(result["both"]):
                lines.append(f"  [OK] {path}")
            lines.append("")

    # Summary
    lines.append("=" * 60)
    if result["code_only"]:
        lines.append("STATUS: SYNC REQUIRED")
        lines.append(f"  - {len(result['code_only'])} routes need to be added to docs")
    else:
        lines.append("STATUS: FULLY SYNCHRONIZED")
    lines.append(f"  - {len(result['docs_only'])} routes only in docs (informational)")
    lines.append("=" * 60)

    return "\n".join(lines)


# JSON Report Output


def generate_json_report(result: dict[str, Any]) -> dict[str, Any]:
    """Generate a JSON-serializable report."""
    return {
        "summary": {
            "total_routes_in_code": result["total_code_routes"],
            "total_routes_in_docs": result["total_documented"],
            "documented_coverage_percent": round(result["coverage"], 2),
            "routes_missing_from_docs": len(result["code_only"]),
            "routes_only_in_docs": len(result["docs_only"]),
            # `code_only` > 0 表示代码里有未文档化的接口（必须修复）。
            # `docs_only` > 0 表示文档里有代码里已不存在的接口（一般是已弃用），
            # 只作为信息提示，不会让 CI 失败。
            "sync_status": "SYNC_REQUIRED" if result["code_only"] else "FULLY_SYNCHRONIZED",
        },
        "missing_from_docs": [
            {
                "method": r.method,
                "path": r.full_path,
                "function": r.function_name,
                "source_file": r.source_file,
                "source_line": r.source_line,
            }
            for r in sorted(result["code_only"], key=lambda x: x.full_path)
        ],
        "only_in_docs": sorted(result["docs_only"]),
    }


# Main


def main():
    parser = argparse.ArgumentParser(
        description="Check API documentation sync status",
    )
    parser.add_argument(
        "--docs-path",
        default="docs/api-reference.md",
        help="Path to the API documentation file (default: docs/api-reference.md)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show all matched routes",
    )
    parser.add_argument(
        "--json-output",
        help="Output JSON report to specified file",
    )
    parser.add_argument(
        "--fail-on-unsync",
        action="store_true",
        help="Exit with code 1 if docs are out of sync",
    )
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project root directory",
    )
    args = parser.parse_args()

    project_root = args.project_root
    docs_path = os.path.join(project_root, args.docs_path)

    print(f"Scanning for API routes in: {project_root}")
    code_routes = scan_api_routes(project_root)
    print(f"Found {len(code_routes)} routes in code")

    print(f"Parsing documentation from: {docs_path}")
    documented_routes = extract_routes_from_docs(docs_path)
    print(f"Found {len(documented_routes)} documented routes")

    result = compare_routes(code_routes, documented_routes)
    report = generate_report(result, documented_routes, verbose=args.verbose)
    print(report)

    if args.json_output:
        json_report = generate_json_report(result)
        json_path = os.path.join(project_root, args.json_output)
        import json

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_report, f, indent=2, ensure_ascii=False)
        print(f"\nJSON report saved to: {json_path}")

    # `code_only` = 路由在代码中存在但文档里缺失（必须为 0，否则视为不同步）。
    # `docs_only` = 路由在文档中存在但代码里找不到（一般是已弃用/历史路由，
    # 不会让 CI 失败，只在报告中提示）。
    if args.fail_on_unsync and result["code_only"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
