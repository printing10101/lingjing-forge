"""研究模块的评估指标：真实业务指标，而不是学术指标。

业务指标：
- 真实识别成功率（来自 data/bridge/usage_logs/recognition.jsonl）
- 真实耗时分布
- 真实返工率（用户报告后重做率）
- 影子模式 diff 率
"""
from __future__ import annotations

import json
import logging
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class BusinessMetrics:
    """业务指标。"""

    feature: str
    sample_count: int
    success_rate: float  # 0.0 - 1.0
    avg_latency_ms: float
    p95_latency_ms: float
    rework_rate: float  # 0.0 - 1.0（用户报告后重做率）
    shadow_diff_rate: float  # 0.0 - 1.0（与 baseline 的不一致率）
    open_problem_count: int


class BusinessMetricsCalculator:
    """业务指标计算器。"""

    def __init__(
        self,
        usage_logs_dir: str = "data/bridge/usage_logs",
        error_samples_dir: str = "data/bridge/error_samples",
        registry_path: str = "research/shared/problem_registry/registry.jsonl",
    ):
        self._usage_logs = Path(usage_logs_dir)
        self._errors = Path(error_samples_dir)
        self._registry = Path(registry_path)

    def calc(self, feature: str) -> BusinessMetrics:
        """计算某 feature 的业务指标。"""
        records = self._read_jsonl(self._usage_logs / "recognition.jsonl")
        diff_records = self._read_jsonl(self._usage_logs / "shadow_diff.jsonl")
        err_records = self._read_jsonl(self._errors / "errors.jsonl")
        feedback_records = self._read_jsonl(self._usage_logs / "feedback.jsonl")

        # 过滤出该 feature 的记录
        rec_for_feature = [r for r in records if r.get("payload", {}).get("feature") == feature]
        diff_for_feature = [r for r in diff_records if r.get("payload", {}).get("feature") == feature]
        err_for_feature = [r for r in err_records if r.get("payload", {}).get("feature") == feature]
        feedback_for_feature = [r for r in feedback_records if r.get("payload", {}).get("feature") == feature]

        n = len(rec_for_feature)
        if n == 0:
            return BusinessMetrics(
                feature=feature,
                sample_count=0,
                success_rate=0.0,
                avg_latency_ms=0.0,
                p95_latency_ms=0.0,
                rework_rate=0.0,
                shadow_diff_rate=0.0,
                open_problem_count=self._count_open_problems(feature),
            )

        # 成功率
        success = sum(1 for r in rec_for_feature if r.get("payload", {}).get("success"))
        success_rate = success / n

        # 耗时
        latencies = [int(r.get("payload", {}).get("latency_ms", 0)) for r in rec_for_feature]
        avg_latency = statistics.mean(latencies) if latencies else 0.0
        p95_latency = self._percentile(latencies, 95) if latencies else 0.0

        # 返工率 = 错误记录数 / 记录总数
        rework_rate = len(err_for_feature) / n if n > 0 else 0.0

        # 影子模式 diff 率
        if diff_for_feature:
            mismatches = sum(
                1 for r in diff_for_feature if not r.get("payload", {}).get("match", True)
            )
            shadow_diff_rate = mismatches / len(diff_for_feature)
        else:
            shadow_diff_rate = 0.0

        return BusinessMetrics(
            feature=feature,
            sample_count=n,
            success_rate=success_rate,
            avg_latency_ms=avg_latency,
            p95_latency_ms=p95_latency,
            rework_rate=rework_rate,
            shadow_diff_rate=shadow_diff_rate,
            open_problem_count=self._count_open_problems(feature),
        )

    def report(self, features: list[str]) -> str:
        """生成多 feature 的指标报告。"""
        lines = ["# 业务指标报告\n"]
        for f in features:
            m = self.calc(f)
            lines.append(f"## {m.feature}\n")
            lines.append(f"- 样本数: {m.sample_count}")
            lines.append(f"- 成功率: {m.success_rate:.2%}")
            lines.append(f"- 平均耗时: {m.avg_latency_ms:.1f} ms")
            lines.append(f"- P95 耗时: {m.p95_latency_ms:.1f} ms")
            lines.append(f"- 返工率: {m.rework_rate:.2%}")
            lines.append(f"- 影子 diff 率: {m.shadow_diff_rate:.2%}")
            lines.append(f"- 未解决问题数: {m.open_problem_count}\n")
        return "\n".join(lines)

    def _read_jsonl(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        result = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    result.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    continue
        return result

    def _percentile(self, values: list[float], p: float) -> float:
        if not values:
            return 0.0
        sorted_v = sorted(values)
        k = (len(sorted_v) - 1) * (p / 100.0)
        f_idx = int(k)
        c_idx = min(f_idx + 1, len(sorted_v) - 1)
        if f_idx == c_idx:
            return sorted_v[f_idx]
        return sorted_v[f_idx] + (sorted_v[c_idx] - sorted_v[f_idx]) * (k - f_idx)

    def _count_open_problems(self, feature: str) -> int:
        if not self._registry.exists():
            return 0
        cnt = 0
        with open(self._registry, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if (
                        rec.get("status") == "open"
                        and (feature is None or rec.get("feature") == feature)
                    ):
                        cnt += 1
                except Exception:  # noqa: BLE001
                    continue
        return cnt


__all__ = ["BusinessMetrics", "BusinessMetricsCalculator"]
