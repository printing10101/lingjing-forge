"""刀具匹配数据类（从 tool_param_matcher 拆出）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.data.process_data_manager import CuttingParameterEntry, ToolEntry


@dataclass
class MatchedTool:
    """匹配的刀具推荐结果。

    Attributes:
        tool: 匹配到的刀具条目
        cutting_params: 匹配到的切削参数
        suitability_score: 适用度评分(0-100) - 基于直径匹配、材质匹配等
        match_reason: 匹配原因说明
        warnings: 使用注意事项
    """

    tool: ToolEntry
    cutting_params: CuttingParameterEntry | None = None
    suitability_score: float = 80.0
    match_reason: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert the matched tool result to a dictionary representation.

        Returns:
            A dictionary containing tool details, suitability score, match
            reason, warnings, and cutting parameters (if available).
        """
        result: dict[str, Any] = {
            "tool_id": self.tool.id,
            "tool_name": self.tool.name,
            "tool_series": self.tool.series,
            "tool_material": self.tool.material,
            "diameter_mm": self.tool.diameter_mm,
            "application": self.tool.application,
            "suitability_score": round(self.suitability_score, 1),
            "match_reason": self.match_reason,
            "warnings": self.warnings,
        }
        if self.cutting_params:
            result["cutting_parameters"] = {
                "cutting_speed_m_per_min": (
                    f"{self.cutting_params.cutting_speed_min_mpm}-{self.cutting_params.cutting_speed_max_mpm}"
                ),
                "feed_rate": (
                    f"{self.cutting_params.feed_min_mmpr}-"
                    f"{self.cutting_params.feed_max_mmpr} "
                    f"{self.cutting_params.feed_unit}"
                ),
                "description": self.cutting_params.description,
            }
        return result


@dataclass
class HoleProcessPlan:
    """单个孔的加工工艺方案。

    Attributes:
        hole_id: 孔标识符
        hole_type: 孔类型(through_hole/blind_hole/counterbore/center_hole)
        operations: 该孔的加工工序列表
            例如对于通孔：["打中心孔", "钻孔"]
            对于精密通孔：["打中心孔", "钻孔", "铰孔"]
    """

    hole_id: str
    hole_type: str
    operations: list[str] = field(default_factory=list)
    tools: list[MatchedTool] = field(default_factory=list)
    estimated_time_min: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert the process plan to a dictionary representation.

        Returns:
            A dictionary containing hole ID, type, operations list,
            matched tools, and estimated time.
        """
        return {
            "hole_id": self.hole_id,
            "hole_type": self.hole_type,
            "operations": self.operations,
            "tools": [t.to_dict() for t in self.tools],
            "estimated_time_min": round(self.estimated_time_min, 2),
        }
