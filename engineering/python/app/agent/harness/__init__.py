"""Harness 自进化层（M1）。

在既有 M0 管道之上补两块抽象：

- ``snapshot``：把多条 prompt 版本绑成一个可命名、可指纹化的整体；
- ``ab_runner``：同一批任务分别跑两套快照，用配对通过率 + McNemar 检验
  判断改动是否真的有效；
- ``ledger``：带签字人的采纳台账，规则固定、只增不改。

不引入 Harness² 框架本体：它要求 Linux + OpenCode/Codex 执行体，与本项目
的 Nexent + MCP 工具链不同构。这里只取它的机制，落在既有存储之上。

用法（不接真模型即可自测）::

    python -m app.agent.harness.selfcheck
    python -m app.agent.harness.cli list
"""

from __future__ import annotations

from app.agent.harness.ab_runner import (
    ABResult,
    ABRunner,
    CaseOutcome,
    ReplayExecutor,
    TaskCase,
    mcnemar_exact,
)
from app.agent.harness.attribution import (
    Attribution,
    coverage,
    load_attribution,
    sample_cases,
)
from app.agent.harness.ledger import (
    AdoptionLedger,
    AdoptionRecord,
    decide,
    get_adoption_ledger,
)
from app.agent.harness.snapshot import (
    HarnessRegistry,
    HarnessSnapshot,
    capture_snapshot,
    get_harness_registry,
)

__all__ = [
    "ABResult",
    "ABRunner",
    "AdoptionLedger",
    "AdoptionRecord",
    "Attribution",
    "CaseOutcome",
    "HarnessRegistry",
    "HarnessSnapshot",
    "ReplayExecutor",
    "TaskCase",
    "capture_snapshot",
    "coverage",
    "decide",
    "get_adoption_ledger",
    "get_harness_registry",
    "load_attribution",
    "mcnemar_exact",
    "sample_cases",
]
