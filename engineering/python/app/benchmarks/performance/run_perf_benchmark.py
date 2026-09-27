"""自动化性能基准测试与回归检测框架。

提供：
- 性能基准测试主运行脚本
- 历史数据对比与回归检测
- Markdown/JSON格式报告生成
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

if __package__ in (None, ""):
    import _bootstrap  # noqa: F401  # 脚本直跑时引导 engineering/python 入 sys.path

from app.benchmarks.performance.thresholds import (
    REGRESSION_THRESHOLDS,
    check_violations,
)
from app.benchmarks.performance.lnn_inference_bench import LNNPerfBenchmark
from app.benchmarks.performance.nc_generation_bench import NCGenerationBenchmark
from app.benchmarks.performance.drawing_parse_bench import DrawingParseBenchmark
from app.benchmarks.performance.api_bench import APIPerfBenchmark
from app.benchmarks.performance.database_bench import DatabasePerfBenchmark

# 阶段2 解耦改造：business_logic_bench 模块已迁移到 research/，
# 工程侧运行时若需调用完整业务逻辑基准测试，请在 research/ 环境中执行。
# 这里通过 try/except 提供降级保护，避免 import 失败导致整个基准测试框架不可用。
try:
    from app.benchmarks.performance.business_logic_bench import (
        BusinessLogicPerfBenchmark,
    )

    _HAS_BUSINESS_LOGIC_BENCH = True
except ImportError:
    BusinessLogicPerfBenchmark = None
    _HAS_BUSINESS_LOGIC_BENCH = False
from app.benchmarks.performance.concurrency_bench import ConcurrencyPerfBenchmark
from app.benchmarks.performance.world_model_bench import WorldModelPerfBenchmark
from app.benchmarks.performance.rl_agent_bench import RLAgentPerfBenchmark
from app.benchmarks.performance.closed_loop_bench import ClosedLoopPerfBenchmark


@dataclass
class RegressionEntry:
    metric: str
    current: float
    previous: float
    change_pct: float
    status: str


@dataclass
class RegressionReport:
    timestamp: str = ""
    summary: str = ""
    entries: list[RegressionEntry] = field(default_factory=list)
    violations: list[dict[str, str]] = field(default_factory=list)
    has_regression: bool = False
    has_critical: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "summary": self.summary,
            "entries": [
                {
                    "metric": e.metric,
                    "current": e.current,
                    "previous": e.previous,
                    "change_pct": e.change_pct,
                    "status": e.status,
                }
                for e in self.entries
            ],
            "violations": self.violations,
            "has_regression": self.has_regression,
            "has_critical": self.has_critical,
        }

    def to_markdown(self) -> str:
        lines = [
            "# 性能基准测试报告",
            "",
            f"**生成时间**: {self.timestamp}",
            f"**状态**: {self.summary}",
            "",
            "## 回归检测结果",
            "",
            "| 指标 | 当前值 | 上次值 | 变化率 | 状态 |",
            "|------|--------|--------|--------|------|",
        ]
        for e in self.entries:
            status_icon = {
                "PASS": "[OK]",
                "WARNING": "[WARN]",
                "CRITICAL": "[CRIT]",
                "NEW": "[NEW]",
            }.get(e.status, "[?]")
            lines.append(
                f"| {e.metric} | {e.current:.3f} | {e.previous:.3f} | {e.change_pct:+.1f}% | {status_icon} {e.status} |"
            )

        if self.violations:
            lines.append("")
            lines.append("## 阈值违规")
            lines.append("")
            for v in self.violations:
                lines.append(f"- [{v['status']}] **{v['metric']}**: {v['message']}")

        return "\n".join(lines)


class PerformanceBenchmarkRunner:
    """性能基准测试主运行器。"""

    def __init__(
        self,
        history_dir: str | None = None,
        output_dir: str | None = None,
    ) -> None:
        if history_dir is None:
            history_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "history",
            )
        if output_dir is None:
            output_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "history",
            )

        self.history_dir = Path(history_dir)
        self.output_dir = Path(output_dir)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run_all(self) -> RegressionReport:
        timestamp = time.strftime("%Y%m%d_%H%M%S")

        current_results: dict[str, float] = {}
        suite_errors: list[str] = []

        def run_suite(name: str, body: Callable[[], dict[str, Any]]) -> None:
            """按套件容错执行：单个套件失败仅记录，不拖垮整个基准运行（W9.4）。"""
            logger.info("\n%s", name)
            try:
                for k, v in (body() or {}).items():
                    if isinstance(v, (int, float)):
                        current_results[k] = v
                        logger.info("  %s: %s", k, v)
            except Exception as exc:  # noqa: BLE001 - 编排层必须容错单套件失败
                suite_errors.append(f"{name}: {exc}")
                logger.warning("套件失败，跳过: %s — %s", name, exc)

        run_suite("[1/10] LNN推理性能测试", lambda: self._suite_lnn(timestamp))
        run_suite("[2/10] NC代码生成全流程测试", lambda: self._suite_nc(timestamp))
        run_suite("[3/10] 三视图解析性能测试", lambda: self._suite_drawing(timestamp))
        run_suite("[4/10] API接口性能测试（需本地运行中的后端服务）", lambda: self._suite_api(timestamp))
        run_suite("[5/10] 数据库性能测试", lambda: self._suite_database(timestamp))
        if _HAS_BUSINESS_LOGIC_BENCH:
            run_suite("[6/10] 业务逻辑性能测试", lambda: self._suite_business(timestamp))
        else:
            logger.info("\n[6/10] 业务逻辑性能测试... 跳过（business_logic_bench 已迁移到 research/）")
        run_suite("[7/10] 并发与压力测试", lambda: self._suite_concurrency(timestamp))
        run_suite("[8/10] 世界模型轨迹预测性能测试", lambda: self._suite_world_model(timestamp))
        run_suite("[9/10] RL agent 决策 + SafetyShield 性能测试", lambda: self._suite_rl_agent(timestamp))
        run_suite("[10/10] 闭环加工优化工作流端到端性能测试", lambda: self._suite_closed_loop(timestamp))

        if suite_errors:
            logger.warning("本次运行共 %d 个套件失败: %s", len(suite_errors), " | ".join(suite_errors))

        # Save current results（含失败套件清单，供白皮书如实记录覆盖面）
        current_path = self.output_dir / f"current_results_{timestamp}.json"
        with open(current_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "results": current_results,
                    "suite_errors": suite_errors,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )

        # Run regression check
        logger.info("\n" + "=" * 60)
        logger.info("回归检测")
        logger.info("=" * 60)

        report = check_regression(current_results, str(self.history_dir))

        report.timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

        # Save report
        report_path = self.output_dir / f"regression_report_{timestamp}.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report.to_markdown())
        logger.info("\n报告已保存: %s", report_path)

        report_json_path = self.output_dir / f"regression_report_{timestamp}.json"
        with open(report_json_path, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)

        return report

    # ---- 各基准套件（原 run_all 内联块，W9.4 重构为独立方法以支持按套件容错）----

    def _suite_lnn(self, timestamp: str) -> dict[str, Any]:
        lnn = LNNPerfBenchmark()
        lnn.setup()
        results: dict[str, Any] = {}
        results.update(lnn.run_single_inference())
        results.update(lnn.run_batch_10_inference())
        results.update(lnn.run_batch_50_inference())
        results.update(lnn.run_batch_100_inference())
        gpu = lnn.run_gpu_single_inference()
        if gpu:
            results.update(gpu)
        else:
            logger.info("  GPU: 不可用（跳过）")
        lnn_path = str(self.output_dir / f"lnn_inference_{timestamp}.json")
        lnn.save_results(lnn_path)
        logger.info("  -> %s", lnn_path)
        return results

    def _suite_nc(self, timestamp: str) -> dict[str, Any]:
        nc = NCGenerationBenchmark()
        nc.setup()
        results = nc.run_full_pipeline(n_parts=3)
        if results.get("bottlenecks"):
            logger.info("  瓶颈分析: %s", results["bottlenecks"])
        if results.get("threshold_violations"):
            logger.info("  违规: %s", results["threshold_violations"])
        nc_path = str(self.output_dir / f"nc_generation_{timestamp}.json")
        nc.save_results(nc_path)
        logger.info("  -> %s", nc_path)
        return results

    def _suite_drawing(self, timestamp: str) -> dict[str, Any]:
        dp = DrawingParseBenchmark()
        dp.setup()
        results = dp.run_parse(n_iterations=5)
        dp_path = str(self.output_dir / f"drawing_parse_{timestamp}.json")
        dp.save_results(dp_path)
        logger.info("  -> %s", dp_path)
        return results

    def _suite_api(self, timestamp: str) -> dict[str, Any]:
        api = APIPerfBenchmark()
        results = api.run_all()
        api_path = str(self.output_dir / f"api_performance_{timestamp}.json")
        api.save_results(api_path)
        logger.info("  -> %s", api_path)
        return results

    def _suite_database(self, timestamp: str) -> dict[str, Any]:
        db = DatabasePerfBenchmark()
        results = db.run_all()
        db_path = str(self.output_dir / f"database_performance_{timestamp}.json")
        db.save_results(db_path)
        logger.info("  -> %s", db_path)
        return results

    def _suite_business(self, timestamp: str) -> dict[str, Any]:
        biz = BusinessLogicPerfBenchmark()
        results = biz.run_all()
        biz_path = str(self.output_dir / f"business_logic_performance_{timestamp}.json")
        biz.save_results(biz_path)
        logger.info("  -> %s", biz_path)
        return results

    def _suite_concurrency(self, timestamp: str) -> dict[str, Any]:
        conc = ConcurrencyPerfBenchmark()
        results = conc.run_all()
        conc_path = str(self.output_dir / f"concurrency_performance_{timestamp}.json")
        conc.save_results(conc_path)
        logger.info("  -> %s", conc_path)
        return results

    def _suite_world_model(self, timestamp: str) -> dict[str, Any]:
        wm = WorldModelPerfBenchmark()
        wm.setup()
        results: dict[str, Any] = {}
        results.update(wm.run_single_prediction())
        results.update(wm.run_horizon_scaling())
        results.update(wm.run_batch_prediction())
        results.update(wm.run_plugin_execute())
        results.update(wm.run_model_cache_hit())
        wm_path = str(self.output_dir / f"world_model_{timestamp}.json")
        wm.save_results(wm_path)
        logger.info("  -> %s", wm_path)
        return results

    def _suite_rl_agent(self, timestamp: str) -> dict[str, Any]:
        rl = RLAgentPerfBenchmark()
        rl.setup()
        results: dict[str, Any] = {}
        results.update(rl.run_single_decision())
        results.update(rl.run_safety_shield_filter())
        results.update(rl.run_batch_decisions())
        results.update(rl.run_policy_cache_hit())
        results.update(rl.run_safety_violation_rate())
        rl_path = str(self.output_dir / f"rl_agent_{timestamp}.json")
        rl.save_results(rl_path)
        logger.info("  -> %s", rl_path)
        return results

    def _suite_closed_loop(self, timestamp: str) -> dict[str, Any]:
        cl = ClosedLoopPerfBenchmark()
        cl.setup()
        results: dict[str, Any] = {}
        pipeline = cl.run_full_pipeline()
        results.update(pipeline)
        if pipeline.get("cl_bottlenecks"):
            logger.info("  闭环瓶颈: %s", pipeline["cl_bottlenecks"])
        if pipeline.get("cl_threshold_violations"):
            logger.info("  闭环违规: %s", pipeline["cl_threshold_violations"])
        results.update(cl.run_node_breakdown())
        results.update(cl.run_throughput())
        cl_path = str(self.output_dir / f"closed_loop_{timestamp}.json")
        cl.save_results(cl_path)
        logger.info("  -> %s", cl_path)
        return results


def check_regression(
    current_results: dict[str, float],
    history_dir: str,
) -> RegressionReport:
    entries: list[RegressionEntry] = []
    violations = check_violations(current_results)

    history_path = _find_latest_history(str(history_dir))
    previous_results: dict[str, float] = {}

    if history_path and os.path.exists(history_path):
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            previous_results = data.get("results", {})
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as e:
            logger.warning("读取历史性能数据失败: %s，使用空数据", e)
            previous_results = {}

    warning_pct = REGRESSION_THRESHOLDS["warning_pct"]
    critical_pct = REGRESSION_THRESHOLDS["critical_pct"]
    has_regression = False
    has_critical = False

    for metric, current in sorted(current_results.items()):
        previous = previous_results.get(metric)

        if previous is None or previous == 0:
            entries.append(
                RegressionEntry(
                    metric=metric,
                    current=current,
                    previous=0,
                    change_pct=0,
                    status="NEW",
                )
            )
            continue

        change_pct = (current - previous) / previous * 100

        # For metrics where lower is better, regression means increase
        if change_pct > critical_pct:
            status = "CRITICAL"
            has_regression = True
            has_critical = True
        elif change_pct > warning_pct:
            status = "WARNING"
            has_regression = True
        elif change_pct < -critical_pct:
            status = "IMPROVED"
        else:
            status = "PASS"

        entries.append(
            RegressionEntry(
                metric=metric,
                current=round(current, 3),
                previous=round(previous, 3),
                change_pct=round(change_pct, 1),
                status=status,
            )
        )

    # Determine summary
    if has_critical:
        summary = f"[CRIT] 检测到 {sum(1 for e in entries if e.status == 'CRITICAL')} 项严重性能回退"
    elif has_regression:
        summary = f"[WARN] 检测到 {sum(1 for e in entries if e.status == 'WARNING')} 项性能回退"
    else:
        summary = "[PASS] 未检测到性能回退"

    return RegressionReport(
        entries=entries,
        violations=violations,
        summary=summary,
        has_regression=has_regression,
        has_critical=has_critical,
    )


def _find_latest_history(history_dir: str) -> str | None:
    candidates = list(Path(history_dir).glob("current_results_*.json"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(candidates[0])


def main() -> None:
    runner = PerformanceBenchmarkRunner()
    report = runner.run_all()
    logger.info("\n" + report.to_markdown())


if __name__ == "__main__":
    main()
