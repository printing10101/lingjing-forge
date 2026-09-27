"""刀具与切削参数匹配模块。

基于知识库(ProcessPlanningDataManager)实现刀具推荐和切削参数匹配。
根据工件材料和加工特征(孔特征)，查询知识库返回最优刀具和切削参数。

本模块为门面：实现已拆分至 _tool_models / _matching_mixin / _scoring_mixin。
"""

from __future__ import annotations

from typing import Any

from app.data.process_data_manager import ProcessPlanningDataManager
from app.process_planning._matching_mixin import _MatchingMixin
from app.process_planning._scoring_mixin import _ScoringMixin
from app.process_planning._tool_models import (  # noqa: F401
    HoleProcessPlan,
    MatchedTool,
)


class ToolParamMatcher(_MatchingMixin, _ScoringMixin):
    """刀具与参数匹配器。

    根据工件材料和孔特征信息，查询知识库返回优化的刀具选择和加工参数。

    匹配策略（三级优先级）：
    1. 精确匹配：材料ID + 刀具系列完全匹配 → 直接返回知识库中的切削参数
    2. 近似匹配：材料类别匹配 + 刀具系列匹配 → 使用同类材料的参数
    3. 通用匹配：仅刀具系列匹配 → 返回该系列刀具列表，使用默认参数

    使用示例:
        matcher = ToolParamMatcher()
        # 为45#钢的φ8mm通孔匹配刀具和参数
        plan = matcher.plan_for_hole(
            material_id="material_45steel",
            material_category="carbon_steel",
            hole_diameter=8.0,
            hole_type="through_hole",
            tolerance_grade="H8"
        )
    """

    # 钻孔工序序列模板
    # 格式: {孔类型: [工序名称, 对应的process查询名]}
    HOLE_PROCESS_TEMPLATES: dict[str, list[tuple[str, str]]] = {
        "through_hole": [
            ("打中心孔定位", "打中心孔定位"),
            ("钻孔", "钻孔"),
        ],
        "blind_hole": [
            ("打中心孔定位", "打中心孔定位"),
            ("钻孔(盲孔)", "钻孔"),
        ],
        "counterbore": [
            ("打中心孔定位", "打中心孔定位"),
            ("钻通孔", "钻孔"),
            ("锪沉头孔", "钻孔"),
        ],
        "center_hole": [
            ("打中心孔定位", "打中心孔定位"),
        ],
        "threaded_hole": [
            ("打中心孔定位", "打中心孔定位"),
            ("钻底孔", "钻孔"),
            ("攻螺纹", "钻孔"),
        ],
    }

    def match(self, data: dict[str, Any]) -> dict[str, Any]:
        """根据特征与材料推荐切削参数（与旧接口兼容的入口）。"""
        material = str(data.get("material") or "unknown")
        params: dict[str, Any] = {}
        for feat in data.get("features") or []:
            if not isinstance(feat, dict):
                continue
            try:
                dia = float(feat.get("diameter") or 0)
            except (TypeError, ValueError):
                continue
            if dia <= 0:
                continue
            try:
                plan = self.plan_for_hole(
                    material_id=material,
                    material_category="steel",
                    hole_diameter=dia,
                    hole_type=str(feat.get("hole_type") or "through_hole"),
                )
                params[str(feat.get("id") or dia)] = plan.to_dict()
            except (ValueError, KeyError, TypeError):
                continue
        return params

    def __init__(self, data_manager: ProcessPlanningDataManager | None = None) -> None:
        """初始化匹配器。

        Args:
            data_manager: 工艺数据管理器实例。若为None则自动创建
        """
        self._data = data_manager or ProcessPlanningDataManager()
