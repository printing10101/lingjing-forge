"""Dreaming 反思服务层（W7.2）。

将 CLI（``app.dreaming.cli``）中的完整反思管线提取为可编程服务：
提取 Session → 离线反思 → 规则合成 → 报告生成 → 结果持久化。

设计约束：
- **CLI 与 API 共用同一管线**（单一事实来源）：命令行能跑的 = 接口能调的；
- **不 os.chdir**：CLI 需要兼容历史行为（在 cmd_reflect 入口处自行 chdir），
  服务层一律使用 ``repo_root`` 拼接绝对路径，供 FastAPI 进程内安全调用；
- 反思结果（含每条规则的来源洞察与支撑 Session）随报告落盘，为前端
  「它这周学会了什么」看板与全流程血缘（W2）提供数据源。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _default_repo_root() -> str:
    """python/ 目录（app/dreaming/service.py 上溯三级）。"""
    return str(Path(__file__).resolve().parent.parent.parent)


@dataclass
class ReflectionRunSummary:
    """一次反思运行的摘要（API 响应体 / CLI 打印源）。"""

    ok: bool = False
    error: str | None = None
    session_count: int = 0
    merged_count: int = 0
    invalidated_count: int = 0
    updated_count: int = 0
    insight_count: int = 0
    draft_rule_count: int = 0
    memory_version: str | None = None
    llm_model: str | None = None
    report_path: str | None = None
    reflection_json_path: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def run_reflection(
    lookback_days: int = 30,
    max_sessions: int | None = None,
    instructions: str | None = None,
    enable_llm: bool = True,
    include_ar_02: bool = False,
    repo_root: str | None = None,
) -> ReflectionRunSummary:
    """执行完整反思流程，返回结构化摘要。

    Args:
        lookback_days: 回看天数（提取窗口）。
        max_sessions: 最多提取的 Session 数（None = 不限）。
        instructions: 反思指令（自然语言，传给 LLM）。
        enable_llm: 是否启用 LLM 反思（False = 规则统计降级模式）。
        include_ar_02: 是否包含 AR-02 修复前数据。
        repo_root: 项目根目录（python/ 的上级）；None 则按模块位置推断。

    Returns:
        :class:`ReflectionRunSummary`；无 Session 或管线失败时 ``ok=False``。
    """
    import time

    root = Path(repo_root or _default_repo_root()).resolve()
    started = time.perf_counter()
    summary = ReflectionRunSummary()

    try:
        # 1. 提取 Session（数据源均可选：MLflow / CAM 报告 / 审计日志缺失时为空）
        from app.dreaming.session_extractor import SessionExtractor

        mlflow_uri = os.environ.get(
            "MLFLOW_TRACKING_URI",
            f"file://{root / 'data' / 'mlruns'}",
        )
        extractor = SessionExtractor(
            mlflow_tracking_uri=mlflow_uri,
            cam_reports_dir=str(root / "python" / "outputs" / "cam_validation"),
            audit_log_dir=str(root / "python" / "outputs" / "audit"),
        )
        sessions = extractor.extract_sessions(
            lookback_days=lookback_days,
            max_sessions=max_sessions,
            include_ar_02_pre_fix=include_ar_02,
        )
        summary.session_count = len(sessions)
        if not sessions:
            summary.error = "未提取到任何 Session，请检查数据源配置（MLflow / CAM 报告 / 审计日志）"
            return _finish(summary, started)

        # 2. Memory Store
        from app.knowledge_graph.graph_store import GraphStore
        from app.dreaming.memory_store import LocalMemoryStore

        store = LocalMemoryStore(
            graph_store=GraphStore(auto_load=True),
            repo_root=str(root),
            watch_paths=["python/app/knowledge_graph/"],
        )

        # 3. 离线反思
        from app.dreaming.reflector import DreamReflector

        reflector = DreamReflector(
            memory_store=store,
            repo_root=str(root),
            enable_llm=enable_llm,
        )
        reflection = await reflector.reflect(sessions=sessions, instructions=instructions)

        # 4. 规则合成（状态 draft，不直接应用——应用必须经灰度发布管线）
        from app.dreaming.rule_synthesizer import RuleSynthesizer

        synthesizer = RuleSynthesizer(output_dir=str(root / "python" / "outputs" / "dreaming" / "rules"))
        rules = synthesizer.synthesize(reflection)

        # 5. 报告生成 + 反思结果持久化
        from app.dreaming.report_generator import ReportGenerator

        reports_dir = root / "python" / "outputs" / "dreaming" / "reports"
        report_gen = ReportGenerator(output_dir=str(reports_dir))
        report_path = report_gen.generate(
            sessions=sessions,
            reflection=reflection,
            rules=rules,
            instructions=instructions,
        )

        reflection_json_path = reports_dir / (f"reflection_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        try:
            reflection_json_path.write_text(
                json.dumps(reflection.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("反思结果持久化失败：%s", e)
            reflection_json_path = None

        summary.ok = True
        summary.merged_count = reflection.deduplicated.merged_count
        summary.invalidated_count = len(reflection.updated.invalidated_node_ids)
        summary.updated_count = len(reflection.updated.updated_node_ids)
        summary.insight_count = len(reflection.insights)
        summary.draft_rule_count = len(rules)
        summary.memory_version = reflection.new_memory_version
        summary.llm_model = reflection.llm_model
        summary.report_path = str(report_path)
        summary.reflection_json_path = str(reflection_json_path) if reflection_json_path else None
    except Exception as exc:  # noqa: BLE001 — 管线入口必须兜底为结构化失败
        logger.exception("Dreaming 反思管线失败")
        summary.ok = False
        summary.error = f"反思管线失败：{exc}"

    return _finish(summary, started)


def _finish(summary: ReflectionRunSummary, started: float) -> ReflectionRunSummary:
    import time

    summary.duration_ms = round((time.perf_counter() - started) * 1000, 2)
    return summary
