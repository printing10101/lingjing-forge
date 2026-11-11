"""Harness 层命令行入口。

    PYTHONPATH=D:/灵境制造/engineering/python python -m app.agent.harness.cli stats
    ... cli list
    ... cli current h-20261023-a

只读为主：``stats`` 读失败案例库，``list``/``current`` 读快照注册表，
``ledger`` 读采纳台账。唯一写操作是 ``current``（切换生效快照）。
"""

from __future__ import annotations

import argparse
import json
import sys

from app.agent.harness.ledger import get_adoption_ledger
from app.agent.harness.snapshot import get_harness_registry


def _cmd_stats(_args: argparse.Namespace) -> int:
    """读失败案例库的既有统计（只读，不写）。"""
    try:
        from app.gcode_generation.failure_case_store import get_failure_case_store
    except Exception as exc:  # noqa: BLE001
        print(f"无法加载失败案例库: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    stats = get_failure_case_store().stats()
    if not stats.get("total"):
        print("案例库为空（尚未产生失败/成功记录）")
        return 0
    print(f"总案例      {stats['total']}")
    print(f"一次通过    {stats['successes']}")
    print(f"失败        {stats['failures']}")
    rate = stats.get("one_pass_rate")
    print(f"一次通过率  {rate:.2%}" if rate is not None else "一次通过率  n/a")
    if stats.get("by_source"):
        print("\n按来源：")
        for k, v in stats["by_source"].items():
            print(f"  {k:<20} {v}")
    if stats.get("by_code"):
        print("\n按错误码：")
        for k, v in list(stats["by_code"].items())[:10]:
            print(f"  {k:<28} {v}")
    return 0


def _cmd_list(_args: argparse.Namespace) -> int:
    reg = get_harness_registry()
    snaps = reg.list_all()
    if not snaps:
        print(f"尚无快照。注册表路径：{reg.path}")
        return 0
    cur = reg.current()
    print(f"{'':2} {'id':<16} {'指纹':<14} {'mechanism'}")
    for s in snaps:
        mark = "*" if cur and cur.harness_id == s.harness_id else " "
        print(f"{mark:2} {s.harness_id:<16} {s.fingerprint():<14} {s.mechanism[:48]}")
    print(f"\n注册表：{reg.path}")
    return 0


def _cmd_current(args: argparse.Namespace) -> int:
    reg = get_harness_registry()
    if not args.harness_id:
        cur = reg.current()
        print(f"当前生效：{cur.harness_id}（{cur.fingerprint()}）" if cur else "尚未设置 current")
        return 0
    snap = reg.set_current(args.harness_id)
    print(f"已切换 current → {snap.harness_id}（{snap.fingerprint()}）")
    return 0


def _cmd_ledger(_args: argparse.Namespace) -> int:
    ledger = get_adoption_ledger()
    rows = ledger.list_all()
    if not rows:
        print(f"台账为空。路径：{ledger.path}")
        return 0
    for r in rows[-20:]:
        print(
            f"{r.get('created_at', '')[:19]}  {r.get('decision', ''):<6} "
            f"{r.get('baseline_id', '')} → {r.get('harness_id', '')}  "
            f"Δ={r.get('delta', 0):+.3f}  p={r.get('p_value', 1):.4f}  by {r.get('reviewer', '')}"
        )
    print(f"\n台账：{ledger.path}（共 {len(rows)} 条）")
    return 0


def _resolve_trace_dir(explicit: str) -> "object":
    """解析 trace 目录。

    ``AgentOrchestrator`` 的默认落点是 ``os.getcwd()/data/traces``，实测历史
    上既出现在仓库根（``D:/灵境制造/data/traces``），也可能出现在
    ``engineering/python/data/traces``。这里按存在性探测，避免 CLI 只能识别
    一种布局。
    """
    from pathlib import Path

    if explicit:
        return Path(explicit)
    here = Path(__file__).resolve()
    candidates = [
        here.parents[5] / "data" / "traces",  # 仓库根（实测落点）
        here.parents[3] / "data" / "traces",  # engineering/python
        Path.cwd() / "data" / "traces",  # 运行时 cwd
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    return candidates[0]


def _cmd_coverage(args: argparse.Namespace) -> int:
    """看 trace 归属覆盖率：回答「现在能不能做 A/B」。"""
    from app.agent.harness.attribution import coverage

    trace_dir = _resolve_trace_dir(args.trace_dir)
    if not trace_dir.exists():
        print(f"trace 目录不存在：{trace_dir}")
        print("提示：管线尚未在本机产生 trace，或 trace_log_dir 配在别处（默认 cwd/data/traces）")
        return 0
    cov = coverage(trace_dir, days=args.days)
    print(f"trace 目录  {trace_dir}")
    print(f"文件        {cov['files']}")
    print(f"总行数      {cov['lines']}（坏行 {cov['bad_lines']}）")
    print(f"有效行      {cov['valid_lines']}")
    print(f"已归属      {cov['attributed']}")
    print(f"未归属      {cov['unattributed']}")
    print(f"覆盖率      {cov['coverage']:.2%}")
    if cov["by_harness"]:
        print("\n按 harness：")
        for k, v in cov["by_harness"].items():
            print(f"  {(k or '(未登记)'):<20} {v}")
    print(f"\n可做 A/B：{'是' if cov['ready_for_ab'] else '否（已归属样本 < 20）'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.agent.harness.cli", description="Harness 自进化层 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stats", help="读失败案例库统计").set_defaults(func=_cmd_stats)
    sub.add_parser("list", help="列出快照").set_defaults(func=_cmd_list)
    p_cur = sub.add_parser("current", help="查看或切换当前快照")
    p_cur.add_argument("harness_id", nargs="?", default="")
    p_cur.set_defaults(func=_cmd_current)
    sub.add_parser("ledger", help="列出采纳台账").set_defaults(func=_cmd_ledger)
    p_cov = sub.add_parser("coverage", help="trace 归属覆盖率（A/B 前置检查）")
    p_cov.add_argument("--days", type=int, default=7, help="只扫最近 N 天（默认 7）")
    p_cov.add_argument("--trace-dir", default="", help="trace 目录，缺省 data/traces")
    p_cov.set_defaults(func=_cmd_coverage)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())


def _json(obj: object) -> str:  # noqa: D401 - 调试辅助
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
