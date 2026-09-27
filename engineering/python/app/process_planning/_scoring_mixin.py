"""刀具适用度评分 mixin（从 tool_param_matcher 拆出）。"""

from __future__ import annotations


from app.data.process_data_manager import CuttingParameterEntry, ToolEntry


class _ScoringMixin:
    def _select_best_diameter(
        self,
        tools: list[ToolEntry],
        target_diameter: float,
    ) -> ToolEntry:
        """从刀具列表中选择直径最接近目标直径的。

        优先级：
        1. 直径 ≥ 目标直径（确保能加工出所需孔）
        2. 在≥目标直径的刀具中选最小的（减少余量）

        Args:
            tools: 候选刀具列表
            target_diameter: 目标直径 (mm)

        Returns:
            最优匹配的刀具
        """
        # 筛选直径≥目标直径的刀具
        oversize = [t for t in tools if t.diameter_mm >= target_diameter]
        if oversize:
            # 选择最接近目标直径的（即最小的≥目标直径的刀具）
            return min(oversize, key=lambda t: t.diameter_mm - target_diameter)

        # 若无≥目标直径的刀具，选择直径最大的作为备选
        return max(tools, key=lambda t: t.diameter_mm)

    def _calculate_suitability(
        self,
        tool: ToolEntry,
        target_diameter: float,
        params: CuttingParameterEntry | None,
    ) -> float:
        """计算刀具适用度评分 (0-100)。

        评分维度：
        - 直径匹配度（50分）：直径越接近目标 → 分数越高
        - 切削参数可用性（30分）：有切削参数 → +30
        - 刀具材质适配（20分）：硬质合金优于HSS

        Args:
            tool: 匹配的刀具
            target_diameter: 目标直径
            params: 切削参数（可为None）

        Returns:
            适用度评分
        """
        score = 50.0

        # 直径匹配评分
        dia_diff = abs(tool.diameter_mm - target_diameter)
        if dia_diff == 0:
            score += 20  # 完美匹配
        elif dia_diff <= 1.0:
            score += 15  # 偏差 ≤ 1mm
        elif dia_diff <= 3.0:
            score += 10  # 偏差 ≤ 3mm
        else:
            score += 5  # 偏差较大

        # 切削参数评分
        if params:
            score += 20

        # 材质评分
        if tool.material == "carbide":
            score += 10  # 硬质合金适用于高速高效加工

        return min(score, 100.0)

    def _build_match_reason(
        self,
        tool: ToolEntry,
        target_diameter: float,
        params: CuttingParameterEntry | None,
    ) -> str:
        """生成刀具匹配原因说明。

        详细解释为何推荐该刀具，包括：
        - 直径选择依据
        - 刀具材质理由
        - 切削参数来源
        """
        dia_diff = abs(tool.diameter_mm - target_diameter)

        if dia_diff == 0:
            dia_reason = f"刀具直径φ{tool.diameter_mm}mm与孔直径φ{target_diameter}mm精确匹配"
        else:
            dia_reason = f"刀具直径φ{tool.diameter_mm}mm覆盖目标直径φ{target_diameter}mm(偏差{dia_diff:.1f}mm)"

        mat_reason = {
            "carbide": "硬质合金材质，适用于高效高速钻孔",
            "HSS": "高速钢材质，适用于一般材料钻孔",
            "ceramic": "陶瓷材质，适用于硬材料高速加工",
        }.get(tool.material, f"{tool.material}材质")

        if params:
            param_reason = f"，切削参数来自知识库: {params.cutting_speed_min_mpm}-{params.cutting_speed_max_mpm} m/min"
        else:
            param_reason = "，建议根据经验设定切削参数"

        return f"{dia_reason}。{mat_reason}{param_reason}。"

    def _generate_warnings(
        self,
        tool: ToolEntry,
        target_diameter: float,
        params: CuttingParameterEntry | None,
    ) -> list[str]:
        """生成刀具使用注意事项。

        检测项：
        - 直径偏差过大 (≥2mm): 需确认余量是否可接受
        - 缺少切削参数: 需人工设定
        - HSS加工硬材料: 需降低切削速度
        """
        warnings: list[str] = []
        dia_diff = abs(tool.diameter_mm - target_diameter)

        if dia_diff >= 2.0:
            warnings.append(f"刀具直径偏差{dia_diff:.1f}mm较大，建议检查孔加工余量是否可接受")

        if params is None:
            warnings.append("该组合暂无切削参数数据，请根据实际工况设定切削速度和进给量")

        return warnings

    def _estimate_drilling_time(
        self,
        hole_diameter: float,
        tool: ToolEntry,
        params: CuttingParameterEntry | None,
        is_center_drill: bool = False,
        depth: float = 20.0,
    ) -> float:
        """估算单个孔的加工时间 (分钟)。

        计算公式:
        - 主轴转速 rpm = 1000 * vc / (π * d)
        - 进给速度 mm/min = rpm * fz
        - 加工时间 = (钻进深度 + 安全距离) / 进给速度 + 辅助时间

        Args:
            hole_diameter: 孔直径 (mm)
            tool: 刀具信息
            params: 切削参数
            is_center_drill: 是否为中心钻工序
            depth: 钻孔深度 (mm)

        Returns:
            估算加工时间 (分钟)
        """
        import math

        # 切削速度 (m/min) - 取推荐范围的中值
        if params:
            vc = (params.cutting_speed_min_mpm + params.cutting_speed_max_mpm) / 2
            feed = (params.feed_min_mmpr + params.feed_max_mmpr) / 2
            if "齿" in params.feed_unit:
                # 立铣刀进给按每齿计算
                rpm = 1000 * vc / (math.pi * tool.diameter_mm) if tool.diameter_mm > 0 else 2000
                feed_rate = rpm * feed * 2  # 2刃钻头
            else:
                # 钻头进给按每转计算
                rpm = 1000 * vc / (math.pi * tool.diameter_mm) if tool.diameter_mm > 0 else 2000
                feed_rate = rpm * feed
        else:
            # 无切削参数时使用经验值
            vc = 20.0  # HSS钻45#钢 ~20m/min
            rpm = 1000 * vc / (math.pi * tool.diameter_mm) if tool.diameter_mm > 0 else 2000
            feed_rate = rpm * 0.15  # 默认进给0.15mm/r

        if is_center_drill:
            # 中心钻工序时间：短距离钻进
            drilling_depth = 3.0  # 中心孔深度通常3-5mm
            safe_distance = 2.0  # 安全接近距离
        else:
            drilling_depth = depth
            safe_distance = 5.0  # 钻孔安全接近距离 + 啄钻回退

        # 加工时间 = 切削行程 / 进给速度
        cutting_length = drilling_depth + safe_distance
        cutting_time = cutting_length / max(feed_rate, 1.0)  # 避免除零

        # 辅助时间：换刀/进退刀
        auxiliary_time = 0.3 if is_center_drill else 0.5

        return round(cutting_time + auxiliary_time, 2)
