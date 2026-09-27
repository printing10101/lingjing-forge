"""Dreaming 命令行入口。

用法：
    python -m app.dreaming.cli reflect [--lookback-days 30] [--instructions "..."]
    python -m app.dreaming.cli extract [--lookback-days 30] [--output sessions.json]
    python -m app.dreaming.cli report --reflection <path>
    python -m app.dreaming.cli version

子命令：
    reflect   执行完整反思流程（提取 + 反思 + 合成 + 报告）
    extract   仅提取 Session（不执行反思）
    report    从已保存的反思结果生成报告
    version   显示版本信息

设计原则：
    - 所有子命令均可在无 LLM 环境下降级运行（规则统计模式）
    - 失败不崩溃，返回非零退出码并打印错误信息
    - 输出路径可配置，默认使用项目标准目录
"""

from __future__ import annotations

import sys

# WinSock 兼容（与项目 standalone_verify_cam_validation.py 一致）：
# Windows WinSock 损坏导致 import asyncio 失败，临时改 sys.platform
# 让 import asyncio 成功。
_original_platform = sys.platform
sys.platform = "linux"
try:
    import asyncio
finally:
    sys.platform = _original_platform

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path


logger = logging.getLogger(__name__)


class DreamingCLI:
    """Dreaming 命令行封装类。

    提供 programmatic 调用入口，便于 HeartbeatScheduler 等调度器集成。
    用法：
        cli = DreamingCLI()
        exit_code = cli.run(["reflect", "--lookback-days", "7"])
    """

    def __init__(self) -> None:
        self.parser = build_parser()

    def run(self, argv: list | None = None) -> int:
        """执行命令行。

        Args:
            argv: 参数列表，None 表示使用 sys.argv[1:]

        Returns:
            退出码
        """
        if argv is None:
            argv = sys.argv[1:]

        args = self.parser.parse_args(argv)

        if hasattr(args, "verbose") and args.verbose:
            _setup_logging(verbose=True)
        else:
            _setup_logging(verbose=False)

        if args.command == "reflect":
            try:
                return asyncio.run(args.func(args))
            except KeyboardInterrupt:
                print("\n反思被用户中断")
                return 130
            except Exception as e:
                logger.error("反思执行失败：%s", e, exc_info=True)
                print(f"反思执行失败：{e}")
                return 1
        else:
            try:
                return args.func(args)
            except KeyboardInterrupt:
                return 130
            except Exception as e:
                logger.error("命令执行失败：%s", e, exc_info=True)
                print(f"命令执行失败：{e}")
                return 1


def _setup_logging(verbose: bool = False) -> None:
    """配置日志。"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _get_repo_root() -> str:
    """获取项目根目录。"""
    # cli.py 位于 python/app/dreaming/cli.py
    # 项目根目录是 python/ 的上两级
    current = Path(__file__).resolve()
    # app/dreaming/cli.py 回退 3 级到 python/
    return str(current.parent.parent.parent.parent)


def _init_memory_store(repo_root: str):
    """初始化 LocalMemoryStore。"""
    from app.dreaming.memory_store import LocalMemoryStore
    from app.knowledge_graph.graph_store import GraphStore

    graph = GraphStore(auto_load=True)
    return LocalMemoryStore(
        graph_store=graph,
        repo_root=repo_root,
        watch_paths=["python/app/knowledge_graph/"],
    )


def _init_session_extractor():
    """初始化 SessionExtractor。"""
    from app.dreaming.session_extractor import SessionExtractor

    return SessionExtractor(
        mlflow_tracking_uri=os.environ.get(
            "MLFLOW_TRACKING_URI",
            f"file://{os.path.abspath('data/mlruns')}",
        ),
        cam_reports_dir="python/outputs/cam_validation",
        audit_log_dir="python/outputs/audit",
    )


# 子命令实现


async def cmd_reflect(args: argparse.Namespace) -> int:
    """执行完整反思流程（W7.2 起委托 service.run_reflection，与 API 共用管线）。"""
    repo_root = _get_repo_root()
    os.chdir(repo_root)

    logger.info("启动 Dreaming 反思流程")

    from app.dreaming.service import run_reflection

    summary = await run_reflection(
        lookback_days=args.lookback_days,
        max_sessions=args.max_sessions,
        instructions=args.instructions,
        enable_llm=not args.no_llm,
        include_ar_02=args.include_ar_02,
        repo_root=repo_root,
    )

    if not summary.ok:
        logger.warning("%s", summary.error)
        print(summary.error or "反思失败")
        return 1

    print("\n" + "=" * 60)
    print("Dreaming 反思完成")
    print("=" * 60)
    print(f"输入 Session 数：{summary.session_count}")
    print(f"去重合并：{summary.merged_count} 条")
    print(f"过时更新：失效 {summary.invalidated_count} 条，标记 {summary.updated_count} 条")
    print(f"洞察浮现：{summary.insight_count} 条")
    print(f"规则候选：{summary.draft_rule_count} 条（状态 draft）")
    print(f"Memory Version：{summary.memory_version or '(未提交)'}")
    print(f"LLM 模型：{summary.llm_model or '规则统计降级'}")
    print(f"反思报告：{summary.report_path}")
    print("=" * 60)
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    """仅提取 Session，不执行反思。"""
    repo_root = _get_repo_root()
    os.chdir(repo_root)

    extractor = _init_session_extractor()
    sessions = extractor.extract_sessions(
        lookback_days=args.lookback_days,
        max_sessions=args.max_sessions,
        include_ar_02_pre_fix=args.include_ar_02,
    )

    output_path = Path(args.output or "python/outputs/dreaming/sessions.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "extracted_at": datetime.now(timezone.utc).isoformat(),
                "lookback_days": args.lookback_days,
                "session_count": len(sessions),
                "sessions": [s.to_dict() for s in sessions],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"提取 {len(sessions)} 个 Session → {output_path}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """从已保存的反思结果生成报告。"""
    from app.dreaming.reflector import ReflectionResult, DeduplicationResult, UpdateResult, InsightItem
    from app.dreaming.report_generator import ReportGenerator

    reflection_path = Path(args.reflection)
    # M18 修复：移除 TOCTOU 检查，try/except 同时处理不存在和格式错误
    try:
        with open(reflection_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"反思结果文件不存在：{reflection_path}")
        return 1
    except (json.JSONDecodeError, OSError) as e:
        print(f"反思结果文件损坏或格式错误：{reflection_path}: {e}")
        return 1

    # 重建 ReflectionResult
    reflection = ReflectionResult(
        deduplicated=DeduplicationResult(
            merged_count=data.get("deduplicated", {}).get("merged_count", 0),
            removed_node_ids=data.get("deduplicated", {}).get("removed_node_ids", []),
            kept_node_ids=data.get("deduplicated", {}).get("kept_node_ids", []),
        ),
        updated=UpdateResult(
            updated_node_ids=data.get("updated", {}).get("updated_node_ids", []),
            invalidated_node_ids=data.get("updated", {}).get("invalidated_node_ids", []),
            details=data.get("updated", {}).get("details", []),
        ),
        insights=[
            InsightItem(
                category=i.get("category", "pattern"),
                content=i.get("content", ""),
                confidence=float(i.get("confidence", 0.5)),
                supporting_sessions=i.get("supporting_sessions", []),
            )
            for i in data.get("insights", [])
        ],
        new_memory_version=data.get("new_memory_version"),
        summary=data.get("summary", ""),
        llm_used=data.get("llm_used", False),
        llm_model=data.get("llm_model"),
        reflected_at=data.get("reflected_at", datetime.now(timezone.utc).isoformat()),
    )

    report_gen = ReportGenerator(
        output_dir="python/outputs/dreaming/reports",
    )
    report_path = report_gen.generate(
        sessions=[],  # 从文件加载时不带 sessions
        reflection=reflection,
        instructions=None,
    )

    print(f"报告已生成：{report_path}")
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    """显示版本信息。"""
    from app.dreaming import __version__

    print(f"灵境制造 Dreaming 模块 v{__version__}")
    print("对应 ADR-021：Dreaming 离线反思机制")
    print("离线反思（Dreaming）：记忆巩固与规则反馈闭环")
    return 0


# 参数解析


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="python -m app.dreaming.cli",
        description="灵境制造 Dreaming 离线反思模块（ADR-021）",
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令", required=True)

    # reflect 子命令
    reflect_parser = subparsers.add_parser("reflect", help="执行完整反思流程")
    reflect_parser.add_argument(
        "--lookback-days",
        type=int,
        default=30,
        help="回溯天数（默认 30）",
    )
    reflect_parser.add_argument(
        "--max-sessions",
        type=int,
        default=100,
        help="最大 Session 数（上限 100）",
    )
    reflect_parser.add_argument(
        "--instructions",
        type=str,
        default=None,
        help="反思指令（如 '重点关注 HRC52 进给速率异常'）",
    )
    reflect_parser.add_argument(
        "--include-ar-02",
        action="store_true",
        help="包含 AR-02 修复前数据（默认排除，论文数据集应排除）",
    )
    reflect_parser.add_argument(
        "--no-llm",
        action="store_true",
        help="禁用 LLM，强制使用规则统计降级模式",
    )
    reflect_parser.add_argument("--verbose", action="store_true", help="详细日志")
    reflect_parser.set_defaults(func=cmd_reflect)

    # extract 子命令
    extract_parser = subparsers.add_parser("extract", help="仅提取 Session")
    extract_parser.add_argument(
        "--lookback-days",
        type=int,
        default=30,
        help="回溯天数（默认 30）",
    )
    extract_parser.add_argument(
        "--max-sessions",
        type=int,
        default=100,
        help="最大 Session 数",
    )
    extract_parser.add_argument(
        "--include-ar-02",
        action="store_true",
        help="包含 AR-02 修复前数据",
    )
    extract_parser.add_argument(
        "--output",
        type=str,
        default="python/outputs/dreaming/sessions.json",
        help="输出文件路径",
    )
    extract_parser.set_defaults(func=cmd_extract)

    # report 子命令
    report_parser = subparsers.add_parser("report", help="从已保存的反思结果生成报告")
    report_parser.add_argument(
        "--reflection",
        type=str,
        required=True,
        help="反思结果 JSON 文件路径",
    )
    report_parser.set_defaults(func=cmd_report)

    # version 子命令
    version_parser = subparsers.add_parser("version", help="显示版本信息")
    version_parser.set_defaults(func=cmd_version)

    return parser


def main() -> int:
    """主入口。"""
    return DreamingCLI().run()


if __name__ == "__main__":
    sys.exit(main())
