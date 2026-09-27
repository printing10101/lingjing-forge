"""装夹方案与定位基准分析模块。

提供装夹方案设计、定位基准选择和工序顺序编排等关键工艺规划功能。
"""

from __future__ import annotations

from app.process_planning.feature_dependency import (
    FeatureDependencyGraph,
    FeatureEdge,
    MachiningFeature,
    Setup,
)
from app.process_planning.datum_selector import (
    DatumCandidate,
    DatumSelection,
    DatumSelector,
)
from app.process_planning.operation_sequencer import (
    Operation,
    OperationPlan,
    OperationSequencer,
)
from app.process_planning.fixture_analyzer import (
    FixtureAnalysis,
    FixtureAnalyzer,
    FixtureRecommendation,
)

__all__ = [
    "MachiningFeature",
    "Setup",
    "FeatureEdge",
    "FeatureDependencyGraph",
    "DatumCandidate",
    "DatumSelection",
    "DatumSelector",
    "Operation",
    "OperationPlan",
    "OperationSequencer",
    "FixtureRecommendation",
    "FixtureAnalysis",
    "FixtureAnalyzer",
]
