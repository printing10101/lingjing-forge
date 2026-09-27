"""[E-P0-1] 静态扫描 API 路由是否声明 response_model。

防复发机制：CI 非阻塞检查，统计未声明 ``response_model`` 的路由数量，
并在新增路由遗漏时输出警告。脚本使用 ast 静态分析，无需启动 FastAPI 应用，
适合在 CI 流水线中执行。

用法
----
::

    # 默认扫描 python/app/api 目录，输出警告列表（非零退出码仅当 --strict 时）
    python scripts/check_response_model.py

    # 严格模式：发现未声明路由即退出码 1（用于阻断 PR 合并）
    python scripts/check_response_model.py --strict

    # 基准对比模式：与上次基准文件对比，仅新增遗漏路由才告警
    python scripts/check_response_model.py --baseline .ci_baseline_response_model.txt

退出码
------
- 0: 无新增遗漏路由（或默认模式下有遗漏但不严格）
- 1: 严格模式下发现遗漏，或基准对比模式下发现新增遗漏

设计依据
--------
- 前序会话已建立 ``app/core/response_models.py`` 提供 ``SuccessResponse[T]`` /
  ``ErrorResponse`` / ``PaginatedData[T]`` 基类
- 历史路由约 390 个未声明 response_model，批量改造回归风险高
- 采用渐进式迁移：新增路由必须声明，历史路由按优先级逐步迁移
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from collections.abc import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _normalize_path(file_path: str | Path) -> str:
    """把文件路径归一化为相对项目根的 POSIX 路径，保证基准文件跨机器可移植。

    历史基准文件曾写入原开发机的绝对路径（例如
    ``C:/Users/<user>/Desktop/灵境制造（上线版）/python/app/api/v1/xxx.py``），
    换机或换盘后与当前扫描出的路径永不相等，基准条目会整体失配。
    本函数把比对双方统一拍平到同一坐标系。
    """
    raw = str(file_path).replace("\\", "/")
    try:
        return Path(raw).resolve().relative_to(PROJECT_ROOT).as_posix()
    except (ValueError, OSError):
        # 不在当前项目根下（旧机器路径、外部扫描目录）→ 退化为 app/api 之后的尾部
        if "/app/api/" in raw:
            return "engineering/python/app/api/" + raw.split("/app/api/", 1)[1]
        return raw.rsplit("/", 1)[-1]


def _baseline_key(finding: dict) -> str:
    """基准比对键：归一化文件路径 + 方法 + 路由路径 + 函数名。

    刻意 **不含行号**：行号会随上方代码增删而漂移，一旦纳入键，任何无关改动都会
    让整份基准失效（2026-09-10 实测：含行号的旧键在本仓库命中 0/504，
    全部路由被误判为"新增"，该 CI 门禁因此长期为红）。
    """
    return "|".join(
        (
            _normalize_path(finding.get("file", "")),
            str(finding.get("method", "")),
            str(finding.get("path", "")),
            str(finding.get("function_name", "")),
        )
    )


@dataclass
class RouteFinding:
    """单条路由扫描结果。"""

    file: str
    line: int
    method: str  # GET / POST / PUT / DELETE / PATCH
    path: str
    has_response_model: bool
    function_name: str


@dataclass
class ScanReport:
    """扫描报告。"""

    total_routes: int = 0
    with_response_model: int = 0
    without_response_model: int = 0
    findings_without: list[RouteFinding] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "total_routes": self.total_routes,
                "with_response_model": self.with_response_model,
                "without_response_model": self.without_response_model,
                "findings_without": [
                    {
                        # 归一化为项目相对路径，避免把本机绝对路径写进基准文件
                        "file": _normalize_path(f.file),
                        "line": f.line,
                        "method": f.method,
                        "path": f.path,
                        "function_name": f.function_name,
                    }
                    for f in self.findings_without
                ],
            },
            indent=2,
            ensure_ascii=False,
        )


# FastAPI 路由装饰器方法名
_ROUTE_METHODS = {"get", "post", "put", "delete", "patch", "head", "options", "trace"}


def _is_route_decorator(call: ast.Call) -> bool:
    """判断 Call 节点是否为路由装饰器（如 ``@router.get(...)``）。"""
    if not isinstance(call.func, ast.Attribute):
        return False
    return call.func.attr.lower() in _ROUTE_METHODS


def _extract_decorator_args(call: ast.Call) -> tuple[str | None, bool]:
    """从装饰器 Call 中提取路径和是否声明 response_model。

    Returns
    -------
    (path, has_response_model)
        path 为 None 表示无法解析；has_response_model 表示是否声明 response_model 参数。
    """
    path: str | None = None
    has_rm = False

    # 位置参数：第一个通常是路径字符串
    if call.args:
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            path = first.value

    # 关键字参数：查找 response_model
    for kw in call.keywords:
        if kw.arg == "response_model":
            has_rm = True
        elif kw.arg == "responses":
            # responses 字典也算作声明了响应模型（部分迁移）
            # 2026-09-13 修复：此前此分支为 pass，注释承诺"算作声明"但实现
            # 未生效，导致仅声明 responses 错误模型的新端点被误判为遗漏
            has_rm = True

    return path, has_rm


def scan_file(file_path: Path) -> list[RouteFinding]:
    """扫描单个 Python 文件中的所有路由装饰器。"""
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (SyntaxError, UnicodeDecodeError):
        return []

    findings: list[RouteFinding] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        for dec in node.decorator_list:
            # 处理 @router.get("/path") 形式
            call_node: ast.Call | None = None
            if isinstance(dec, ast.Call):
                call_node = dec
            elif isinstance(dec, ast.Attribute) and isinstance(dec.value, ast.Call):
                # 处理 @router.get("/path").depends(...) 这种链式调用（罕见）
                call_node = dec.value

            if call_node is None or not _is_route_decorator(call_node):
                continue

            method = call_node.func.attr.upper()  # type: ignore[union-attr]
            path, has_rm = _extract_decorator_args(call_node)

            findings.append(
                RouteFinding(
                    file=str(file_path),
                    line=node.lineno,
                    method=method,
                    path=path or "<dynamic>",
                    has_response_model=has_rm,
                    function_name=node.name,
                )
            )

    return findings


def scan_directory(root: Path, exclude_dirs: Iterable[str] = ()) -> ScanReport:
    """递归扫描目录下所有 Python 文件中的路由。"""
    exclude_set = set(exclude_dirs)
    report = ScanReport()

    for py_file in root.rglob("*.py"):
        # 排除指定目录
        if any(part in exclude_set for part in py_file.parts):
            continue
        # 跳过测试文件
        if py_file.name.startswith("test_") or py_file.name == "conftest.py":
            continue

        for finding in scan_file(py_file):
            report.total_routes += 1
            if finding.has_response_model:
                report.with_response_model += 1
            else:
                report.without_response_model += 1
                report.findings_without.append(finding)

    return report


def compare_with_baseline(report: ScanReport, baseline_path: Path) -> list[RouteFinding]:
    """与基准文件对比，返回新增的未声明路由。

    基准文件格式：JSON，包含 findings_without 列表。
    如果基准文件不存在，返回当前所有未声明路由（视为全部新增）。

    比对键为 ``归一化路径|方法|路由路径|函数名``（见 :func:`_baseline_key`），
    不含行号，也不含本机绝对路径前缀，因此基准可跨机器、跨代码漂移复用。
    """
    if not baseline_path.exists():
        return report.findings_without

    try:
        baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return report.findings_without

    baseline_keys = {
        _baseline_key(f) for f in baseline_data.get("findings_without", [])
    }
    baseline_total = len(baseline_keys)

    new_findings = [
        f for f in report.findings_without if _baseline_key(asdict(f)) not in baseline_keys
    ]

    if new_findings:
        growth = len(report.findings_without) - baseline_total
        print(
            f"  基准债务: {baseline_total} 条 → 当前 {len(report.findings_without)} 条"
            f"（净增 {growth:+d}）",
            file=sys.stderr,
        )
    return new_findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="扫描 API 路由是否声明 response_model（E-P0-1 防复发机制）"
    )
    parser.add_argument(
        "--scan-dir",
        default="python/app/api",
        help="扫描目录（默认：python/app/api）",
    )
    parser.add_argument(
        "--exclude",
        nargs="*",
        default=["__pycache__", "tests"],
        help="排除的目录名（默认：__pycache__ tests）",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="严格模式：发现未声明路由即返回非零退出码",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="基准文件路径，仅与基准对比时报告新增遗漏",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="更新基准文件（写入当前扫描结果）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 格式（便于 CI 解析）",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="将 JSON 报告写入指定文件（不影响 stdout，便于 CI 同时获取人类可读日志和 JSON 工件）",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    scan_root = project_root / args.scan_dir

    if not scan_root.exists():
        print(f"错误：扫描目录不存在：{scan_root}", file=sys.stderr)
        return 2

    report = scan_directory(scan_root, exclude_dirs=args.exclude)

    # 将 JSON 报告写入文件（独立于 stdout，避免与人类可读输出混杂）
    if args.json_output:
        try:
            args.json_output.write_text(report.to_json(), encoding="utf-8")
            print(f"[E-P0-1] JSON 报告已写入：{args.json_output}")
        except (OSError, UnicodeEncodeError) as write_err:
            print(f"警告：无法写入 JSON 报告文件：{write_err}", file=sys.stderr)

    if args.update_baseline and args.baseline:
        args.baseline.write_text(report.to_json(), encoding="utf-8")
        print(f"基准文件已更新：{args.baseline}")
        return 0

    if args.json:
        print(report.to_json())
    else:
        coverage = (
            (report.with_response_model / report.total_routes * 100)
            if report.total_routes > 0
            else 0
        )
        print("[E-P0-1] 路由 response_model 覆盖率报告")
        print(f"  扫描目录: {scan_root}")
        print(f"  总路由数: {report.total_routes}")
        print(f"  已声明 response_model: {report.with_response_model}")
        print(f"  未声明 response_model: {report.without_response_model}")
        print(f"  覆盖率: {coverage:.1f}%")
        print()

        if report.findings_without:
            print("未声明 response_model 的路由（前 20 条）:")
            for f in report.findings_without[:20]:
                print(f"  {_normalize_path(f.file)}:{f.line}  {f.method} {f.path}  ({f.function_name})")
            if len(report.findings_without) > 20:
                print(f"  ... 还有 {len(report.findings_without) - 20} 条")

    if args.baseline:
        new_findings = compare_with_baseline(report, args.baseline)
        if new_findings:
            print()
            print(f"发现 {len(new_findings)} 条新增未声明路由（相对基准）：")
            for f in new_findings:
                print(f"  {_normalize_path(f.file)}:{f.line}  {f.method} {f.path}  ({f.function_name})")
            print()
            print("修复建议：在路由装饰器添加 response_model=SuccessResponse[YourDataModel]")
            print("         并在 responses 字典声明错误模型 {404: {\"model\": ErrorResponse}}")
            return 1
        else:
            print()
            print("未发现新增未声明路由（相对基准）")
            return 0

    if args.strict and report.without_response_model > 0:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
