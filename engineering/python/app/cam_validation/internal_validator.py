"""内部预校验器（阶段 7 第一层校验）。

复用 app.simulation.collision_detector.CollisionDetector，将 G 代码刀路段
映射到特征，输出每个特征的碰撞事件归因。

核心设计：组合（has-a）而非继承（is-a）
    InternalValidator 内部组合 ToolpathParser + CollisionDetector，
    不继承 CollisionDetector，避免破坏现有测试用例（项目记忆硬约束：
    组合优于继承，InternalValidator 仅作为外部调用者）。

特征归因策略：
    CollisionEvent.block_number → 查询 feature_results 中 line_range
    包含该 block_number 的特征。
    若归因失败（block_number 不在任何特征的 line_range 内），
    归因到 "unknown" 并追加警告。

校验局限告知（项目记忆硬约束）：
    CollisionDetector 是 AABB 包围盒级别快速预筛，秒级反馈，
    **不可替代** CAM 软件二次校验：
    - 无法检测刀轨几何精度
    - 无法检测切削力 / 机床运动学
    - 无法检测后处理器语法兼容性
    仅捕获明显的刀柄-工件碰撞 / 工作空间超限 / 安全 Z 违规 / 快速移动碰撞
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, replace
from typing import Any

from app.cam_validation.cam_store import (
    FeatureValidationResult,
    InternalValidationError,
)
from app.config import CamValidationConfig
from app.simulation.collision_detector import (
    CollisionReport,
    CollisionDetector,
)
from app.simulation.stock_model import StockModel
from app.simulation.toolpath_parser import ToolpathParser

logger = logging.getLogger(__name__)


# 常量

# 5-axis 模式内部预校验不支持（需 tool_vectors，阶段 7 不实现）
# 5-axis 校验应通过 CamAdapter 调用 NX/PowerMill
_UNSUPPORTED_MODES: frozenset[str] = frozenset({"5axis", "5_axis"})

# stock_top_z 与 stock_height 一致性容差（mm）
# StockModel 底部 Z=0，顶面 z_max=height；阶段 6 传入的 stock_top_z 应与之一致
_STOCK_TOP_Z_TOLERANCE_MM: float = 0.01

# 归因失败时追加到 CollisionReport.warnings 的前缀
_UNATTRIBUTED_WARNING_PREFIX: str = (
    "InternalValidator 归因失败：以下碰撞事件 block_number 不在任何 feature_results.line_range 区间内："
)


# InternalValidationReport：聚合 CollisionReport + 特征归因结果


@dataclass
class InternalValidationReport:
    """InternalValidator 单次校验聚合报告。

    封装 CollisionDetector 原始输出 + 特征归因后的 FeatureValidationResult
    列表 + 未归因事件集合，供 pipeline.py 写入 internal_report.json（调试细节）。

    Attributes:
        collision_report: CollisionDetector 原始输出（含全部碰撞事件 + 警告）
        feature_results: 归因后的特征校验结果列表（已填充 internal_check_passed
            和 internal_events）
        unattributed_events: 归因失败的碰撞事件列表（block_number 不在任何
            feature_results.line_range 内），已归因到 "unknown" 并追加警告
        total_segments: G 代码解析出的运动段总数
        segments_checked: CollisionDetector 实际检测的运动段数
        mode: 校验模式（"3axis" / "5axis"）
        controller_type: 控制器类型（fanuc / siemens / heidenhain）
        safe_z: 安全 Z 高度（mm）
        stock_top_z: 毛坯顶面 Z（mm）
        stock_dimensions: 毛坯尺寸 (length, width, height)（mm）
        warnings: 警告列表（包含归因失败警告 + StockModel 一致性警告）
    """

    collision_report: CollisionReport
    feature_results: list[FeatureValidationResult] = field(default_factory=list)
    unattributed_events: list[dict[str, Any]] = field(default_factory=list)
    total_segments: int = 0
    segments_checked: int = 0
    mode: str = "3axis"
    controller_type: str = "fanuc"
    safe_z: float = 80.0
    stock_top_z: float = 50.0
    stock_dimensions: tuple[float, float, float] = (200.0, 150.0, 50.0)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，供 internal_report.json 调试导出。"""
        return {
            "collision_report": self.collision_report.to_dict(),
            "feature_results": [fr.to_dict() for fr in self.feature_results],
            "unattributed_events": self.unattributed_events,
            "total_segments": self.total_segments,
            "segments_checked": self.segments_checked,
            "mode": self.mode,
            "controller_type": self.controller_type,
            "safe_z": round(self.safe_z, 4),
            "stock_top_z": round(self.stock_top_z, 4),
            "stock_dimensions": list(self.stock_dimensions),
            "warnings": self.warnings,
        }

    @property
    def safe(self) -> bool:
        """综合安全判定：CollisionDetector 判定 safe 且无未归因事件。"""
        return self.collision_report.safe and not self.unattributed_events


# InternalValidator：组合 ToolpathParser + CollisionDetector


class InternalValidator:
    """阶段 7 第一层校验器：复用 CollisionDetector 执行 AABB 预校验。

    设计原则（项目记忆硬约束）：
        - 组合（has-a）：InternalValidator 持有 ToolpathParser + CollisionDetector
          实例，**不继承** CollisionDetector，避免破坏现有测试用例。
        - 单次使用：每次 validate() 调用根据传入的 controller_type 和 stock
          参数创建新的 parser/detector 实例（modal state 隔离）。
        - 不替代 CAM 软件：仅做 AABB 包围盒级快速预筛，秒级反馈；
          完整刀轨仿真由 CamAdapter 调用 NX/PowerMill/PyCAM 完成。

    校验流程：
        1. ToolpathParser.parse_gcode() 解析 G 代码 → list[ToolpathSegment]
        2. StockModel(length, width, height) 构建毛坯包围盒
        3. CollisionDetector(stock, safe_z_height, mode).check_segments(segments)
           → CollisionReport
        4. _attribute_collision_to_feature() 按 block_number 归因到
           feature_results.line_range 区间
        5. 返回 (CollisionReport, list[FeatureValidationResult])

    异常处理：
        - ToolpathParser 解析失败 → InternalValidationError
        - StockModel 构造失败 → InternalValidationError
        - CollisionDetector 调用失败 → InternalValidationError
        - 5-axis 模式 → InternalValidationError（暂不支持，需 CamAdapter）
        - G 代码无运动段 → 返回空 CollisionReport（safe=True），不抛异常

    线程安全：
        - InternalValidator 无状态（每次 validate 创建新实例），线程安全
        - 调用方 CamTaskStore._cam_call_lock 串行化 CAM 软件调用
    """

    def __init__(self, config: CamValidationConfig) -> None:
        """初始化内部预校验器。

        Args:
            config: CAM 校验配置（CamValidationConfig）。当前主要承载
                precision_tier 用于 disclaimer 显示；实际校验参数由
                validate() 调用方传入。
        """
        self._config = config
        # 不在 __init__ 创建 parser/detector：
        # - controller_type 每次 validate 可能不同（fanuc/siemens/heidenhain）
        # - stock 尺寸每次 validate 不同
        # - ToolpathParser 维护 modal state，复用会污染

    def validate(
        self,
        gcode_text: str,
        feature_results: list[FeatureValidationResult],
        controller_type: str = "fanuc",
        safe_z: float = 80.0,
        stock_top_z: float = 50.0,
        stock_length: float = 200.0,
        stock_width: float = 150.0,
        stock_height: float = 50.0,
        mode: str = "3axis",
    ) -> tuple[CollisionReport, list[FeatureValidationResult]]:
        """执行内部预校验。

        Args:
            gcode_text: G 代码文本（来自 GCodeLoader.load_from_report）
            feature_results: 阶段 6 特征结果列表（含 line_range 和阶段 6 上下文）
            controller_type: 控制器类型（fanuc / siemens / heidenhain）
            safe_z: 安全 Z 高度（mm），来自阶段 6 GCodeReport
            stock_top_z: 毛坯顶面 Z 高度（mm），来自阶段 6 GCodeReport
            stock_length: 毛坯长度（mm，X 轴方向）
            stock_width: 毛坯宽度（mm，Y 轴方向）
            stock_height: 毛坯高度（mm，Z 轴方向）
            mode: 校验模式（"3axis" / "5axis"）

        Returns:
            (collision_report, updated_feature_results)
            - collision_report: CollisionDetector 原始输出
            - updated_feature_results: feature_results 副本，已填充
              internal_check_passed 和 internal_events 字段

        Raises:
            InternalValidationError: ToolpathParser 解析失败 / StockModel
                构造失败 / CollisionDetector 调用失败 / 5-axis 模式不支持
        """
        # 1. 模式校验：5-axis 暂不支持（需 tool_vectors）
        if mode in _UNSUPPORTED_MODES:
            raise InternalValidationError(
                "5-axis 模式暂不支持内部预校验：CollisionDetector.check_segments_5axis "
                "需要 tool_vectors 参数，阶段 7 不实现。"
                "5-axis 校验应通过 CamAdapter 调用 NX/PowerMill 完成完整刀轨仿真。"
            )

        # 2. 解析 G 代码
        try:
            parser = ToolpathParser(controller_type=controller_type)
            segments = parser.parse_gcode(gcode_text)
        except Exception as e:
            raise InternalValidationError(
                f"ToolpathParser 解析 G 代码失败（controller_type={controller_type}）: {e}"
            ) from e

        # 3. G 代码无运动段：返回空报告 + 不修改 feature_results
        if not segments:
            logger.warning(
                "InternalValidator: G 代码无运动段（segments=0），可能是空文件或仅含注释/坐标系选择指令。返回空报告。"
            )
            empty_report = CollisionReport(
                total_segments=0,
                segments_checked=0,
                collisions=[],
                warnings=["G 代码无运动段，未执行碰撞检测"],
                safe=True,
            )
            # feature_results 副本：internal_check_passed=True（无碰撞）
            updated = [replace(fr) for fr in feature_results]
            for fr in updated:
                fr.internal_events = []
                fr.internal_check_passed = True
            return empty_report, updated

        # 4. stock_top_z 与 stock_height 一致性校验（仅警告，不抛异常）
        warnings_collected: list[str] = []
        if not math.isclose(stock_top_z, stock_height, abs_tol=_STOCK_TOP_Z_TOLERANCE_MM):
            msg = (
                f"stock_top_z={stock_top_z:.4f}mm 与 stock_height="
                f"{stock_height:.4f}mm 不一致（容差 {_STOCK_TOP_Z_TOLERANCE_MM}mm）。"
                f"StockModel 以 stock_height 为准（底部 Z=0，顶面 z_max=stock_height）。"
            )
            logger.warning(msg)
            warnings_collected.append(msg)

        # 5. 构建 StockModel + CollisionDetector
        # 语义转换：
        # - validate() 接收的 safe_z 是「安全 Z 平面的绝对坐标」（mm）
        # 来自阶段 6 GCodeReport.safe_z（如 80.0 表示 Z=80 是安全平面）
        # - CollisionDetector.safe_z_height 期望「stock 顶面以上的安全余量」（mm）
        # 内部计算：safe_z_plane = stock_z_top + safe_z_height
        # - 转换公式：safe_z_height_margin = safe_z - stock_top_z
        # 即把绝对坐标的安全平面换算为相对毛坯顶面的余量
        if safe_z <= stock_top_z:
            raise InternalValidationError(
                f"safe_z={safe_z}mm 必须大于 stock_top_z={stock_top_z}mm，"
                f"否则安全 Z 平面位于毛坯顶面以下，无法保证安全余量。"
                f"请检查阶段 6 GCodeReport 中 safe_z / stock_top_z 字段。"
            )
        safe_z_height_margin = safe_z - stock_top_z

        try:
            stock = StockModel(
                length=stock_length,
                width=stock_width,
                height=stock_height,
            )
            detector = CollisionDetector(
                stock=stock,
                safe_z_height=safe_z_height_margin,
                mode=mode,
            )
        except InternalValidationError:
            raise
        except Exception as e:
            raise InternalValidationError(f"StockModel/CollisionDetector 构造失败: {e}") from e

        # 6. 执行碰撞检测
        try:
            report = detector.check_segments(segments)
        except Exception as e:
            raise InternalValidationError(f"CollisionDetector.check_segments 失败: {e}") from e

        # 7. 追加 stock 一致性警告到 CollisionReport.warnings
        for w in warnings_collected:
            report.warnings.append(w)

        # 8. 归因碰撞事件到特征
        updated = self._attribute_collision_to_feature(report, feature_results)

        logger.info(
            "InternalValidator 完成：total_segments=%d, segments_checked=%d, collisions=%d, warnings=%d, features=%d",
            report.total_segments,
            report.segments_checked,
            len(report.collisions),
            len(report.warnings),
            len(updated),
        )

        return report, updated

    def _attribute_collision_to_feature(
        self,
        report: CollisionReport,
        feature_results: list[FeatureValidationResult],
    ) -> list[FeatureValidationResult]:
        """将 CollisionEvent.block_number 归因到 feature_results.line_range。

        归因策略：
            - 遍历 report.collisions
            - 对每个 CollisionEvent，查找 feature_results 中 line_range
              包含 block_number 的特征
            - 命中：追加到该特征的 internal_events，标记
              internal_check_passed=False
            - 未命中：归因到 "unknown"，追加到 CollisionReport.warnings

        副作用说明：
            - 不修改原 feature_results 列表中的对象
            - 使用 dataclasses.replace 创建副本，确保调用方原对象不受影响

        Args:
            report: CollisionDetector 输出（collisions 字段可能为空）
            feature_results: 阶段 6 传入的特征列表

        Returns:
            更新后的 feature_results 副本列表：
            - internal_check_passed: 无碰撞事件 → True；有碰撞 → False
            - internal_events: 该特征 line_range 内的碰撞事件字典列表
        """
        # 创建副本，避免修改原对象
        updated = [replace(fr) for fr in feature_results]

        # 初始化 internal 字段
        for fr in updated:
            fr.internal_events = []
            fr.internal_check_passed = True

        # 无碰撞事件：所有特征 internal_check_passed=True
        if not report.collisions:
            return updated

        # 归因每个碰撞事件
        unattributed_events: list[dict[str, Any]] = []
        unattributed_blocks: list[int] = []

        for event in report.collisions:
            block = event.block_number
            attributed = False

            for fr in updated:
                start, end = fr.line_range
                if start <= block <= end and start > 0 and end > 0:
                    # block_number 在该特征的 line_range 区间内
                    fr.internal_events.append(event.to_dict())
                    fr.internal_check_passed = False
                    attributed = True
                    break

            if not attributed:
                # 归因失败：block_number 不在任何 line_range 区间内
                # （或所有特征的 line_range 都是 (0, 0) 默认值）
                unattributed_events.append(event.to_dict())
                unattributed_blocks.append(block)

        # 追加归因失败警告到 CollisionReport.warnings
        if unattributed_events:
            blocks_str = ", ".join(str(b) for b in unattributed_blocks)
            warning_msg = (
                f"{_UNATTRIBUTED_WARNING_PREFIX}"
                f"[{blocks_str}]。"
                f"可能原因：阶段 6 feature_results.line_range 未覆盖这些 G 代码行号，"
                f"或 G 代码包含特征之外的辅助运动（如换刀 / 回参考点 / 安全高度过渡）。"
                f"已归因到 unknown，工程师需审核原始 internal_report.json 中"
                f"的 unattributed_events 字段。"
            )
            report.warnings.append(warning_msg)

            logger.warning(
                "InternalValidator 归因失败：%d 个碰撞事件 block_number "
                "不在任何 feature_results.line_range 内（blocks=[%s]）",
                len(unattributed_events),
                blocks_str,
            )

        return updated

    def build_report(
        self,
        collision_report: CollisionReport,
        feature_results: list[FeatureValidationResult],
        unattributed_events: list[dict[str, Any]] | None = None,
        total_segments: int = 0,
        segments_checked: int = 0,
        mode: str = "3axis",
        controller_type: str = "fanuc",
        safe_z: float = 80.0,
        stock_top_z: float = 50.0,
        stock_dimensions: tuple[float, float, float] = (200.0, 150.0, 50.0),
        warnings: list[str] | None = None,
    ) -> InternalValidationReport:
        """构建聚合报告（供 pipeline.py 写入 internal_report.json）。

        Args:
            collision_report: CollisionDetector 原始输出
            feature_results: 归因后的特征列表
            unattributed_events: 归因失败的碰撞事件列表（如有）
            total_segments: G 代码运动段总数
            segments_checked: 实际检测的段数
            mode: 校验模式
            controller_type: 控制器类型
            safe_z: 安全 Z 高度
            stock_top_z: 毛坯顶面 Z
            stock_dimensions: (length, width, height)
            warnings: 额外警告列表

        Returns:
            InternalValidationReport 实例
        """
        # 从 collision_report.warnings 中提取归因失败警告
        unattributed = list(unattributed_events or [])
        all_warnings = list(warnings or [])

        # 合并 CollisionReport.warnings 到 InternalValidationReport.warnings
        all_warnings.extend(collision_report.warnings)

        return InternalValidationReport(
            collision_report=collision_report,
            feature_results=feature_results,
            unattributed_events=unattributed,
            total_segments=total_segments,
            segments_checked=segments_checked,
            mode=mode,
            controller_type=controller_type,
            safe_z=safe_z,
            stock_top_z=stock_top_z,
            stock_dimensions=stock_dimensions,
            warnings=all_warnings,
        )
