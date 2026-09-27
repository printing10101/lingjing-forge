"""知识图谱健康检查系统（M1.5）

提供自动化健康检查功能，评估图谱数据质量，识别并报告潜在问题。

模块划分：
    - :mod:`app.knowledge_graph.health.checker`
        核心检查逻辑，包含孤立节点、矛盾关系、老旧数据检测。
    - :mod:`app.knowledge_graph.health.report`
        Markdown 格式报告生成器。

设计目标：
    - 模块化设计，各检测功能独立可扩展。
    - 严格只读访问，确保检查过程不会修改原始图谱数据。
    - 性能优化，大规模图谱检查控制在30秒以内。
    - 与 M1.2 图存储系统无缝集成。
"""

from app.knowledge_graph.health.checker import (
    HealthChecker,
    HealthCheckResult,
    IsolatedNodeResult,
    ContradictoryEdgeResult,
    StaleNodeResult,
)
from app.knowledge_graph.health.report import HealthReportGenerator

__all__ = [
    "HealthChecker",
    "HealthCheckResult",
    "IsolatedNodeResult",
    "ContradictoryEdgeResult",
    "StaleNodeResult",
    "HealthReportGenerator",
]
