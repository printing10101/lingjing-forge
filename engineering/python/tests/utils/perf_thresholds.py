"""性能断言阈值的环境感知助手。

CI 共享跑机（2 vCPU、无独占保障）实测延迟可达本机独占的 1.5-2 倍以上，
纯绝对延迟断言在 CI 上必然偶发超标（2026-09-18 五连跑每次炸不同的性能用例）。
约定：base 一律填本机独占口径的原始阈值；CI 环境放宽 3 倍。
真正的性能回归拦截在 perf-benchmark 工作流（历史基线对比），不在单次绝对值。
"""

from __future__ import annotations

import os

_CI_FACTOR = 3.0


def perf_threshold(base: float) -> float:
    """返回当前环境应使用的性能断言阈值（秒或毫秒，单位跟随 base）。"""
    return base * (_CI_FACTOR if os.environ.get("CI") else 1.0)
