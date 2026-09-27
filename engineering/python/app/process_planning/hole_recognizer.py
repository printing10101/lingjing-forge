"""孔特征识别模块。

从三维模型描述数据中提取所有孔特征信息，包括孔的位置、尺寸、类型和数量。
支持通孔、盲孔、螺纹孔、沉头孔、中心孔等常见孔类型。

输入数据结构：
- 接受来自STEP导入/CAD生成的结构化几何描述
- 格式：包含 faces/solids/features 的字典或列表
- 基于几何语义（圆柱面+平面边界）进行孔特征识别

识别流程：
1. 遍历所有几何面(face)，筛选圆柱面(cylindrical surface)
2. 对于每个圆柱面，判定其边界条件以区分通孔/盲孔/沉头孔
3. 根据直径和深度参数对孔进行分类
4. 合并共轴孔（如沉头孔的大孔和小孔）
5. 输出统一格式的HoleFeature列表

特征类型定义：
- through_hole: 通孔，圆柱面两端均与开放空间相邻
- blind_hole: 盲孔，圆柱面一端封闭（锥底/平底）
- counterbore: 沉头孔，多段共轴圆柱面，上大下小
- center_hole: 中心孔，小直径锥形孔，用于定位
- threaded_hole: 螺纹孔，具有螺纹标识的孔

质量标准：提取准确率需达到99%以上。

本模块为门面：实现已拆分至 _hole_models / _recognize_mixin。
"""

from __future__ import annotations

from typing import Any

from app.process_planning._hole_models import (  # noqa: F401
    HoleFeature,
    HoleRecognitionResult,
)
from app.process_planning._recognize_mixin import _RecognizeMixin


class HoleFeatureRecognizer(_RecognizeMixin):
    """孔特征识别器。

    从三维模型几何描述中提取所有孔特征。
    支持通孔、盲孔、沉头孔、中心孔、螺纹孔的自动识别。

    识别原理：
    1. 几何遍历法 - 遍历所有圆柱面，分析其边界拓扑关系
       - 两端开放 → 通孔
       - 一端开放 + 一端闭合(锥面/平面) → 盲孔
    2. 共轴合并法 - 检测共轴圆柱面的直径变化
       - 上大下小 + 共享轴线 → 沉头孔
    3. 直径-深度分类法 - 按经典孔型几何比分类
       - 深径比 < 0.5 → 浅孔/倒角孔
       - 锥形截面 + 小直径 → 中心孔

    使用方法:
        recognizer = HoleFeatureRecognizer()
        result = recognizer.recognize(geometry_data)
        for hole in result.holes:
            logger.info("%s: %s φ%smm", hole.hole_id, hole.type, hole.diameter)
    """

    # 标准孔底角：麻花钻118°，用于盲孔底部建模
    STANDARD_DRILL_POINT_ANGLE = 118.0

    # 最小可识别的孔直径 (mm) - 低于此值的圆柱面视为销孔或中心孔
    MIN_RECOGNIZABLE_DIAMETER = 0.5

    # 共轴判定阈值 (mm) - 两圆柱面轴线距离小于此值视为共轴
    COAXIAL_THRESHOLD = 0.05

    def recognize_holes(
        self,
        geometry_data: dict[str, Any],
    ) -> HoleRecognitionResult:
        """通用孔特征识别入口。

        根据输入数据的结构自动选择解析路径：
        - 直接定义的 holes 列表 → 快速解析
        - features 特征列表 → 特征过滤+解析
        - solids 实体列表 → 拓扑遍历+解析

        Args:
            geometry_data: 三维模型的几何描述数据

        Returns:
            HoleRecognitionResult: 识别结果
        """
        return self.recognize_from_part_description(geometry_data)

    def validate_result(
        self,
        result: HoleRecognitionResult,
        expected_count: int | None = None,
    ) -> dict[str, Any]:
        """对识别结果进行验证。

        验证项：
        1. 孔总数与预期对比
        2. 各孔直径均为正值
        3. 通孔深度正确（通孔需 > 0）
        4. 位置坐标有效（非NaN/Infinity）
        5. 无未处理错误

        Args:
            result: 要验证的识别结果
            expected_count: 期望的孔总数（可选）

        Returns:
            验证报告字典，包含：
            - is_valid: bool, 验证是否通过
            - issues: list[str], 发现的问题
            - passed_checks: list[str], 通过的检查项
        """
        issues: list[str] = []
        passed: list[str] = []

        # 检查1: 数量验证
        if expected_count is not None:
            if result.total_count == expected_count:
                passed.append(f"孔数量匹配: {result.total_count} == {expected_count}")
            else:
                issues.append(f"孔数量不匹配: 识别到{result.total_count}个，期望{expected_count}个")
        else:
            passed.append(f"孔总数: {result.total_count}")

        # 检查2: 直径验证
        invalid_diameter = [h for h in result.holes if h.diameter <= 0]
        if invalid_diameter:
            issues.append(f"{len(invalid_diameter)}个孔的直径无效: {', '.join(h.hole_id for h in invalid_diameter)}")
        else:
            passed.append("所有孔直径均为正值")

        # 检查3: 通孔深度验证
        through_holes = [h for h in result.holes if h.is_through()]
        invalid_depth = [h for h in through_holes if h.depth <= 0]
        if invalid_depth:
            issues.append(f"{len(invalid_depth)}个通孔的深度无效")
        else:
            passed.append(f"{len(through_holes)}个通孔深度有效")

        # 检查4: 位置验证
        import math

        invalid_pos = [
            h
            for h in result.holes
            if any(math.isnan(v) or math.isinf(v) for v in [h.position_x, h.position_y, h.position_z])
        ]
        if invalid_pos:
            issues.append(f"{len(invalid_pos)}个孔的位置坐标无效")
        else:
            passed.append("所有孔位置坐标有效")

        # 检查5: 错误检查
        if result.errors:
            issues.append(f"识别过程中有{len(result.errors)}个错误")
        else:
            passed.append("识别过程无错误")

        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "passed_checks": passed,
        }
