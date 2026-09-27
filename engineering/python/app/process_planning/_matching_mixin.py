"""刀具匹配 mixin（从 tool_param_matcher 拆出）。"""

from __future__ import annotations

from typing import Any
from collections.abc import Callable

from app.data.process_data_manager import MaterialEntry, QueryError, ToolEntry
from app.process_planning._tool_models import HoleProcessPlan, MatchedTool


class _MatchingMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _build_match_reason: Callable[..., Any]
    _calculate_suitability: Callable[..., Any]
    _estimate_drilling_time: Callable[..., Any]
    _generate_warnings: Callable[..., Any]
    _select_best_diameter: Callable[..., Any]
    HOLE_PROCESS_TEMPLATES: Any
    _data: Any

    def match_tool_for_hole(
        self,
        material_id: str,
        material_category: str,
        hole_diameter: float,
        process_name: str,
    ) -> MatchedTool:
        """为指定孔特征匹配刀具和切削参数。

        Args:
            material_id: 材料ID（如 'material_45steel'）
            material_category: 材料类别（如 'carbon_steel'）
            hole_diameter: 孔的公称直径 (mm)
            process_name: 加工工序名称（如 '钻孔', '打中心孔定位'）

        Returns:
            MatchedTool: 匹配结果，包含刀具和切削参数

        Raises:
            QueryError: 当任一查询参数无效时
        """
        if not material_id or not material_id.strip():
            raise QueryError("材料ID不能为空")
        if not process_name or not process_name.strip():
            raise QueryError("加工工序名称不能为空")

        # 查询该工序适用的所有刀具
        all_tools = self._data.get_tools_by_material_and_process(
            material_category,
            process_name,
        )

        if not all_tools:
            return MatchedTool(
                tool=ToolEntry(
                    id="fallback",
                    series="twist_drill",
                    name=f"麻花钻 φ{hole_diameter}mm(通用)",
                    diameter_mm=hole_diameter,
                    material="HSS",
                    application=process_name,
                ),
                suitability_score=30.0,
                match_reason=(f"知识库中未找到 {material_category}+{process_name} 的刀具，返回通用刀具建议"),
                warnings=["建议向知识库添加该组合的专用刀具数据"],
            )

        # 按直径匹配最佳刀具
        # 选择直径最接近hole_diameter的刀具（优先选择大于等于孔直径的，其次选最大的）
        best_tool = self._select_best_diameter(all_tools, hole_diameter)

        # 查询该材料+刀具系列的切削参数
        cutting_params = None
        cp_list = self._data.get_cutting_parameters(material_id, best_tool.series)
        if cp_list:
            cutting_params = cp_list[0]  # 取第一个匹配的参数组

        # 计算适用度评分
        score = self._calculate_suitability(
            best_tool,
            hole_diameter,
            cutting_params,
        )

        # 生成匹配说明
        match_reason = self._build_match_reason(best_tool, hole_diameter, cutting_params)

        # 生成使用注意事项
        warnings = self._generate_warnings(best_tool, hole_diameter, cutting_params)

        return MatchedTool(
            tool=best_tool,
            cutting_params=cutting_params,
            suitability_score=score,
            match_reason=match_reason,
            warnings=warnings,
        )

    def plan_for_hole(
        self,
        material_id: str,
        material_category: str,
        hole_diameter: float,
        hole_type: str = "through_hole",
        tolerance_grade: str = "H8",
    ) -> HoleProcessPlan:
        """为单个孔生成完整的加工工艺方案。

        包括：所有工序的刀具选择、切削参数匹配。
        遵循工艺规则：基准先行(假定已完成) → 先面后孔(假定已完成) →
        中心孔定位 → 钻孔。

        Args:
            material_id: 材料ID
            material_category: 材料类别
            hole_diameter: 孔直径 (mm)
            hole_type: 孔类型(through_hole/blind_hole/counterbore/center_hole)
            tolerance_grade: 公差等级(H7/H8/H9等)

        Returns:
            HoleProcessPlan: 包含完整工序列表和刀具匹配结果
        """
        process_template = self.HOLE_PROCESS_TEMPLATES.get(
            hole_type,
            self.HOLE_PROCESS_TEMPLATES["through_hole"],
        )

        plan = HoleProcessPlan(
            hole_id="",
            hole_type=hole_type,
        )
        matched_tools: list[MatchedTool] = []
        operations: list[str] = []
        total_time = 0.0

        for op_name, process_query_name in process_template:
            operations.append(op_name)

            matched = self.match_tool_for_hole(
                material_id=material_id,
                material_category=material_category,
                hole_diameter=hole_diameter,
                process_name=process_query_name,
            )
            matched_tools.append(matched)

            # 估算加工时间
            op_time = self._estimate_drilling_time(
                hole_diameter=hole_diameter,
                tool=matched.tool,
                params=matched.cutting_params,
                is_center_drill=("中心" in op_name),
            )
            total_time += op_time

        plan.operations = operations
        plan.tools = matched_tools
        plan.estimated_time_min = total_time
        return plan

    def get_material_info(self, material_name: str) -> MaterialEntry | None:
        """查询材料基本信息。

        Args:
            material_name: 材料名称（支持模糊匹配）

        Returns:
            MaterialEntry: 材料信息，未找到返回None
        """
        return self._data.get_material_by_name(material_name)

    def get_all_process_rules(self) -> list[dict[str, Any]]:
        """获取所有工艺规则。

        Returns:
            规则字典列表
        """
        rules = self._data.get_all_process_rules()
        return [
            {
                "id": r.id,
                "name": r.name,
                "category": r.category,
                "description": r.description,
                "details": r.details,
            }
            for r in rules
        ]

    def get_available_tools(self, series: str) -> list[dict[str, Any]]:
        """获取指定系列的可用刀具列表。

        Args:
            series: 刀具系列(twist_drill/endmill/face_mill/center_drill)

        Returns:
            刀具信息列表
        """
        tools = self._data.get_tools_by_series(series)
        return [
            {
                "id": t.id,
                "name": t.name,
                "series": t.series,
                "diameter_mm": t.diameter_mm,
                "material": t.material,
                "application": t.application,
            }
            for t in sorted(tools, key=lambda t: t.diameter_mm)
        ]
