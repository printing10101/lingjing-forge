#!/usr/bin/env python3
"""
Sovereignty Ratio Calculator (P5-2 自主占比统计脚本)

统计项目中「自主实现」代码的占比，验证自主化路线图进度（目标 ≥50%）。

自主定义（与 docs/development/自主化与护城河路线图.md 一致）：
- 自主代码 = 本仓库自研的业务逻辑（含白盒模块、编排逻辑、契约层）
- 框架调用 = 对 CadQuery / PyTorch / FastAPI / Element Plus 等第三方框架的调用

统计方法（按代码行数）：
1. 扫描 engineering/python/app/ 下所有 .py 文件
2. 计算每文件「自主行数」：
   - 剔除空行/注释/docstring
   - 对每个非空代码行：若该行仅调用框架（import 框架模块 /
     明显的框架 API 调用如 cq.Workplane / torch.tensor / FastAPI 装饰器），
     计为框架行；否则计为自主行
3. 自主占比 = 自主行数 / (自主行数 + 框架行数)

Usage:
    python scripts/sovereignty_ratio.py            # 统计 app/ 整体
    python scripts/sovereignty_ratio.py --verbose  # 打印每模块占比
    python scripts/sovereignty_ratio.py --json     # JSON 输出（CI 门禁用）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "engineering" / "python" / "app"

# 第三方框架（import 或 from ... import 命中即视为框架依赖）
FRAMEWORK_MODULES = (
    "cadquery",
    "cq_",
    "torch",
    "numpy",
    "scipy",
    "sklearn",
    "pydantic",
    "fastapi",
    "starlette",
    "sqlalchemy",
    "pandas",
    "matplotlib",
    "jinja2",
    "yaml",
    "pytest",
    "httpx",
    "requests",
    "ezdxf",
    "trimesh",
    "pythonocc",
    "OCC",
)

# 框架 API 调用模式（行内出现即视为框架行）
FRAMEWORK_API_PATTERNS = (
    r"\bcq\.Workplane\b",
    r"\btorch\.\w+\(",
    r"\bnp\.\w+\(",
    r"\bsklearn\.",
    r"@router\.(get|post|put|delete|patch)\b",
    r"@app\.(get|post|put|delete)\b",
    r"Depends\(",
    r"select\(.+\)",
    r"sessionmaker\(",
    r"create_async_engine\(",
    r"\bElMessage\b",
    r"\bdefineStore\b",
    r"\bcreateRouter\b",
)

# 完全自主的白盒模块（命中即 100% 自主，不参与框架判定）
WHITEBOX_MODULE_MARKERS = (
    "_feature_classifier",
    "_review_state_machine",
    "_pipeline_stages",
    "baseline",
    "recommender",
    "evaluator",
)


def is_comment_or_blank(line: str) -> bool:
    """是否空行/纯注释/docstring 行。"""
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("#"):
        return True
    if stripped.startswith('"""') or stripped.startswith("'''"):
        return True
    return False


def is_framework_import(line: str) -> bool:
    """是否导入第三方框架。"""
    stripped = line.strip()
    if not (stripped.startswith("import ") or stripped.startswith("from ")):
        return False
    return any(fw in stripped for fw in FRAMEWORK_MODULES)


def is_framework_api_call(line: str) -> bool:
    """是否调用框架 API。"""
    return any(re.search(p, line) for p in FRAMEWORK_API_PATTERNS)


def analyze_file(path: Path) -> dict[str, Any]:
    """分析单个 .py 文件的自主/框架行数。

    Returns:
        {"path", "total", "sovereign", "framework", "ratio"}
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        return {"path": str(path), "total": 0, "sovereign": 0, "framework": 0, "ratio": 0.0}

    sovereign = 0
    framework = 0

    # 白盒模块 全部代码行视为自主（import 除外）
    is_whitebox = any(m in path.name for m in WHITEBOX_MODULE_MARKERS)

    for line in lines:
        if is_comment_or_blank(line):
            continue
        if is_whitebox and not is_framework_import(line):
            sovereign += 1
            continue
        if is_framework_import(line) or is_framework_api_call(line):
            framework += 1
        else:
            sovereign += 1

    total = sovereign + framework
    ratio = round(sovereign / total, 4) if total else 0.0
    return {
        "path": str(path),  # 使用绝对路径，避免测试时 relative_to 失败
        "total": total,
        "sovereign": sovereign,
        "framework": framework,
        "ratio": ratio,
    }


def analyze_tree(root: Path = APP_ROOT) -> list[dict[str, Any]]:
    """递归分析 app/ 下所有 .py 文件。"""
    results: list[dict[str, Any]] = []
    if not root.exists():
        return results
    for path in sorted(root.rglob("*.py")):
        # 跳过测试目录（app/**/tests/ 非生产代码）
        if "tests" in path.parts:
            continue
        results.append(analyze_file(path))
    return results


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总统计。"""
    total = sum(r["total"] for r in results)
    sovereign = sum(r["sovereign"] for r in results)
    framework = sum(r["framework"] for r in results)
    ratio = round(sovereign / total, 4) if total else 0.0
    return {
        "files": len(results),
        "total_lines": total,
        "sovereign_lines": sovereign,
        "framework_lines": framework,
        "sovereignty_ratio": ratio,
        "target_met": ratio >= 0.50,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="自主占比统计")
    parser.add_argument("--verbose", action="store_true", help="打印每模块占比")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--target", type=float, default=0.50, help="目标占比（默认 0.50）")
    args = parser.parse_args()

    results = analyze_tree()
    summary = aggregate(results)

    if args.json:
        print(json.dumps({"summary": summary, "modules": results}, ensure_ascii=False, indent=2))
    else:
        print(f"自主占比统计（目标 ≥{args.target:.0%}）")
        print(f"  文件数: {summary['files']}")
        print(f"  总代码行: {summary['total_lines']}")
        print(f"  自主行: {summary['sovereign_lines']}")
        print(f"  框架行: {summary['framework_lines']}")
        print(f"  自主占比: {summary['sovereignty_ratio']:.2%}")
        print(f"  目标达成: {'✅' if summary['ratio'] >= args.target else '❌'}")

        if args.verbose:
            print("\n按模块：")
            for r in sorted(results, key=lambda x: -x["ratio"]):
                print(f"  {r['path']:<60} {r['sovereign']:>6}/{r['total']:<6} ({r['ratio']:.1%})")

    # CI 门禁：未达目标返回非零
    return 0 if summary["sovereignty_ratio"] >= args.target else 1


if __name__ == "__main__":
    sys.exit(main())
