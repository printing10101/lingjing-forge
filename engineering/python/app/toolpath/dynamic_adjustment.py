"""刀路动态调参闭环（刀具磨损 ↔ 工艺规划）。

落地竞品分析中识别的 MachineMetrics / 工业 CNC 监控系统补强点：
基于实时刀具磨损状态，动态调整切削参数（主轴转速 / 进给速度 / 切深），
并将调整后的参数应用到 NC 代码段，必要时反推回工艺规划流水线。

闭环链路：
    ToolWearPredictor（磨损预测 + 实时校正）
        → suggest_parameter_adjustment / get_compensation_recommendations（决策）
        → FeedRateOptimizer.optimize_feed_rate（进给微调，tool_wear_factor）
        → BasePostProcessor.get_spindle_rpm / get_feed_rate（机床能力限幅）
        → ToolpathParser.parse_gcode → 段级参数改写 → 重新格式化 NC 代码
        → Tool ORM 更新 wear_amount / usage_time / status
        → 必要时触发 ProcessPlanningPipeline 重新规划

设计原则：
- 各组件保持松耦合，通过本编排器协同；
- 决策必须经过机床能力限幅，避免输出物理不可执行参数；
- 所有调整需保留可追溯日志（reasoning / before / after）以支撑学术复现。
"""

from __future__ import annotations

import logging
import math
import re
import threading
from dataclasses import asdict, dataclass, field
from typing import Any

from app.postprocessor.registry import PostProcessorRegistry
from app.services.tool_wear_predictor import ToolWearPredictor
from app.simulation.toolpath_parser import ToolpathParser, ToolpathSegment
from app.toolpath.feed_rate_optimizer import CuttingConditions, FeedRateOptimizer

logger = logging.getLogger(__name__)


# 数据结构


@dataclass
class WearState:
    """刀具磨损快照。"""

    tool_id: int
    wear_amount: float  # mm (VB)
    usage_time: float  # 分钟
    wear_threshold: float  # mm，更换阈值
    material_type: str = "steel_45"
    tool_type: str = "carbide"
    tool_diameter: float = 10.0  # mm
    flute_count: int = 2

    @property
    def wear_ratio(self) -> float:
        """磨损比 = 当前磨损 / 更换阈值。"""
        if self.wear_threshold <= 0:
            return 0.0
        return self.wear_amount / self.wear_threshold

    @property
    def tool_wear_factor(self) -> float:
        """FeedRateOptimizer 所需的 tool_wear_factor。

        约定：1.0 = 新刀，>1.0 = 磨损刀具。
        本实现按 wear_ratio 线性映射到 [1.0, 2.0] 区间，
        即磨损达阈值时因子为 2.0。
        """
        return 1.0 + max(0.0, min(1.0, self.wear_ratio))


@dataclass
class CurrentParameters:
    """当前切削参数（来自工艺规划或 NC 代码）。"""

    cutting_speed: float  # m/min
    feed_rate: float  # mm/rev（每转进给，进给速度 mm/min = feed_rate * spindle_rpm）
    depth_of_cut: float  # mm（轴向切深 ap）
    width_of_cut: float = 0.0  # mm（径向切深 ae，默认 0 表示未指定）
    spindle_rpm: float | None = None  # RPM（None 时由 cutting_speed 反算）
    coolant_flow: float = 10.0  # L/min

    def to_input_parameters(self, wear: WearState) -> dict[str, Any]:
        """转换为 ToolWearPredictor 期望的 input_parameters 字典。"""
        return {
            "cutting_speed": self.cutting_speed,
            "feed_rate": self.feed_rate,
            "depth_of_cut": self.depth_of_cut,
            "material_type": wear.material_type,
            "tool_type": wear.tool_type,
            "tool_diameter": wear.tool_diameter,
            "current_wear": wear.wear_amount,
            "coolant_flow": self.coolant_flow,
        }


@dataclass
class AdjustmentDecision:
    """单次调整决策结果。"""

    strategy: str  # no_adjustment / slight_compensation / moderate_compensation /
    # aggressive_compensation / replace_tool
    urgency: str  # normal / warning / critical
    new_cutting_speed: float
    new_feed_rate: float  # mm/rev
    new_depth_of_cut: float
    new_spindle_rpm: float  # 经机床限幅后
    new_feed_rate_mm_min: float  # 经机床限幅后
    life_extension_pct: float  # 预期寿命延长百分比
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NCRewriteResult:
    """NC 代码段级改写结果。"""

    rewritten_gcode: str
    segments_total: int
    segments_adjusted: int
    per_segment_log: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# 核心编排器


class DynamicAdjustmentOrchestrator:
    """刀路动态调参闭环编排器。

    整合 ToolWearPredictor、FeedRateOptimizer、BasePostProcessor、ToolpathParser，
    提供「磨损状态 → 决策 → 限幅 → NC 改写」的端到端闭环能力。
    """

    def __init__(
        self,
        tool_wear_predictor: ToolWearPredictor | None = None,
        feed_rate_optimizer: FeedRateOptimizer | None = None,
        toolpath_parser: ToolpathParser | None = None,
    ) -> None:
        self.wear_predictor = tool_wear_predictor or ToolWearPredictor()
        self.feed_optimizer = feed_rate_optimizer or FeedRateOptimizer()
        # ToolpathParser 默认 fanuc 方言，可按调用方需求覆盖
        self.toolpath_parser = toolpath_parser

    # 公共入口

    def decide_adjustment(
        self,
        wear: WearState,
        current: CurrentParameters,
        machine_capabilities: dict[str, float] | None = None,
        optimization_goal: str = "tool_life",
        real_time_wear: float | None = None,
        sensor_features: dict[str, float] | None = None,
        elapsed_time: float | None = None,
    ) -> AdjustmentDecision:
        """根据磨损状态给出参数调整决策。

        Args:
            wear: 刀具磨损快照
            current: 当前切削参数
            machine_capabilities: 机床能力上限（None 使用 ToolWearPredictor 默认）
            optimization_goal: FeedRateOptimizer 优化目标
                （efficiency / tool_life / surface_finish）
            real_time_wear: 实测磨损量 (mm)，与 sensor_features/elapsed_time
                同时提供时启用「实时信号 → 磨损模型在线校正 → 决策」闭环
            sensor_features: 传感器特征字典（vibration_rms / cutting_force /
                temperature / acoustic_emission），与 ToolWearPredictor.
                calibrate_with_real_time_data 对齐
            elapsed_time: 自上次校正以来的加工时间 (min)

        Returns:
            调整决策（含限幅后参数）
        """
        input_params = current.to_input_parameters(wear)

        # 0) 可选：实时传感器数据 EWMA 校正磨损预测
        # 集成点 1：打通 calibrate_with_real_time_data decide_adjustment 闭环
        calibration_info: dict[str, Any] = {}
        effective_wear = wear
        if real_time_wear is not None and sensor_features is not None and elapsed_time is not None:
            try:
                calibration = self.wear_predictor.calibrate_with_real_time_data(
                    real_time_wear=real_time_wear,
                    sensor_features=sensor_features,
                    elapsed_time=elapsed_time,
                    input_parameters=input_params,
                )
                corrected = float(calibration.get("corrected_wear", wear.wear_amount))
                # 用校正后的磨损值构造新的 WearState 副本（不污染入参）
                effective_wear = WearState(
                    tool_id=wear.tool_id,
                    wear_amount=max(0.0, corrected),
                    usage_time=wear.usage_time,
                    wear_threshold=wear.wear_threshold,
                    material_type=wear.material_type,
                    tool_type=wear.tool_type,
                    tool_diameter=wear.tool_diameter,
                    flute_count=wear.flute_count,
                )
                calibration_info = {
                    "measured_wear": calibration.get("measured_wear"),
                    "predicted_wear_at_time": calibration.get("predicted_wear_at_time"),
                    "corrected_wear": corrected,
                    "deviation_ratio": calibration.get("deviation_ratio"),
                    "sensor_adjustment": calibration.get("sensor_adjustment"),
                    "adjustment_reasons": calibration.get("adjustment_reasons", []),
                    "confidence": calibration.get("confidence"),
                    "sensor_coverage": calibration.get("sensor_coverage"),
                }
                # 校正后的 input_params 也要更新 current_wear
                input_params["current_wear"] = corrected
                logger.info(
                    "磨损校正闭环启用：measured=%.4f → predicted=%.4f → corrected=%.4f "
                    "(deviation_ratio=%.3f, sensor_adjustment=%.3f)",
                    real_time_wear,
                    calibration.get("predicted_wear_at_time", 0.0),
                    corrected,
                    calibration.get("deviation_ratio", 0.0),
                    calibration.get("sensor_adjustment", 1.0),
                )
            except Exception as exc:
                logger.warning("实时磨损校正失败，降级到原始磨损值决策: %s", exc)
                calibration_info = {"error": f"calibration failed: {exc}"}

        # 1) 调用 ToolWearPredictor 获取补偿建议（含机床能力粗校验）
        compensation = self.wear_predictor.get_compensation_recommendations(
            current_wear=effective_wear.wear_amount,
            input_parameters=input_params,
            machine_capabilities=machine_capabilities,
        )

        # 2) 解析补偿建议中的新参数
        # get_compensation_recommendations 返回 suggestions 列表，
        # 每项含 param/current/recommended 字段
        new_cutting_speed = current.cutting_speed
        new_feed_rate = current.feed_rate
        new_depth_of_cut = current.depth_of_cut
        for sug in compensation.get("suggestions", []):
            param = sug.get("param")
            recommended = sug.get("recommended")
            if recommended is None:
                continue
            if param == "cutting_speed":
                new_cutting_speed = float(recommended)
            elif param == "feed_rate":
                new_feed_rate = float(recommended)
            elif param == "depth_of_cut":
                new_depth_of_cut = float(recommended)

        # 3) 调用 FeedRateOptimizer 进一步优化进给（结合 tool_wear_factor）
        # 注意：使用 effective_wear（校正后）以保证闭环一致性
        spindle_rpm = self._compute_spindle_rpm(new_cutting_speed, effective_wear.tool_diameter)
        try:
            conditions = CuttingConditions(
                material=self._normalize_material_name(effective_wear.material_type),
                tool_diameter=effective_wear.tool_diameter,
                tool_material=effective_wear.tool_type,
                depth_of_cut=new_depth_of_cut,
                width_of_cut=current.width_of_cut if current.width_of_cut > 0 else new_depth_of_cut,
                spindle_speed=spindle_rpm,
                feed_rate=new_feed_rate * spindle_rpm,  # mm/min
            )
            optimized_feed_mm_min = self.feed_optimizer.optimize_feed_rate(
                conditions=conditions,
                optimization_goal=optimization_goal,
                tool_wear_factor=effective_wear.tool_wear_factor,
            )
            # 反算 mm/rev
            if spindle_rpm > 0:
                new_feed_rate = optimized_feed_mm_min / spindle_rpm
        except Exception as exc:
            logger.warning("FeedRateOptimizer 进给优化失败，使用补偿建议值: %s", exc)

        # 4) 通过后处理器限幅（机床能力硬约束）
        post = self._get_postprocessor(machine_capabilities)
        clamped_rpm = post.get_spindle_rpm(spindle_rpm)
        feed_mm_min = new_feed_rate * clamped_rpm if clamped_rpm > 0 else new_feed_rate * spindle_rpm
        clamped_feed_mm_min = post.get_feed_rate(feed_mm_min)

        # 反算最终 mm/rev
        final_feed_per_rev = clamped_feed_mm_min / clamped_rpm if clamped_rpm > 0 else new_feed_rate

        # 5) 收集 warnings / reasoning
        warnings: list[str] = list(compensation.get("warnings", []))
        if clamped_rpm < spindle_rpm - 1e-3:
            warnings.append(f"主轴转速 {spindle_rpm:.0f} RPM 超出机床限幅，已降至 {clamped_rpm:.0f} RPM")
        if clamped_feed_mm_min < feed_mm_min - 1e-3:
            warnings.append(f"进给速度 {feed_mm_min:.0f} mm/min 超出机床限幅，已降至 {clamped_feed_mm_min:.0f} mm/min")

        reasoning: list[str] = [
            f"当前磨损比 {effective_wear.wear_ratio:.1%}（阈值 {effective_wear.wear_threshold:.3f} mm）",
            f"补偿策略: {compensation.get('strategy', 'unknown')}",
            f"优化目标: {optimization_goal}, tool_wear_factor={effective_wear.tool_wear_factor:.3f}",
            f"主轴转速: {spindle_rpm:.0f} → 限幅后 {clamped_rpm:.0f} RPM",
            f"进给速度: {feed_mm_min:.0f} → 限幅后 {clamped_feed_mm_min:.0f} mm/min",
        ]

        # 追加实时校正闭环信息（若启用）
        if calibration_info:
            if "error" in calibration_info:
                reasoning.append(f"实时校正失败: {calibration_info['error']}")
            else:
                reasoning.append(
                    f"实时校正闭环: measured={calibration_info.get('measured_wear')}mm, "
                    f"predicted={calibration_info.get('predicted_wear_at_time')}mm, "
                    f"corrected={calibration_info.get('corrected_wear')}mm "
                    f"(deviation_ratio={calibration_info.get('deviation_ratio')}, "
                    f"sensor_adjustment={calibration_info.get('sensor_adjustment')})"
                )
                for reason in calibration_info.get("adjustment_reasons", []):
                    reasoning.append(f"传感器修正: {reason}")

        return AdjustmentDecision(
            strategy=compensation.get("strategy", "no_adjustment"),
            urgency=compensation.get("urgency", "normal"),
            new_cutting_speed=new_cutting_speed,
            new_feed_rate=final_feed_per_rev,
            new_depth_of_cut=new_depth_of_cut,
            new_spindle_rpm=clamped_rpm,
            new_feed_rate_mm_min=clamped_feed_mm_min,
            life_extension_pct=float(compensation.get("life_extension_pct", 0.0) or 0.0),
            suggestions=compensation.get("suggestions", []),
            warnings=warnings,
            reasoning=reasoning,
        )

    def rewrite_nc_code(
        self,
        gcode_text: str,
        decision: AdjustmentDecision,
        controller_type: str = "fanuc",
        apply_to_motion_only: bool = True,
    ) -> NCRewriteResult:
        """按调整决策改写 NC 代码中的主轴转速与进给速度。

        Args:
            gcode_text: 原始 NC/G 代码文本
            decision: 调整决策
            controller_type: 控制器方言（fanuc/siemens/heidenhain）
            apply_to_motion_only: 是否仅改写切削进给段（G01/G02/G03），
                True 时跳过 G00 快速移动段

        Returns:
            改写结果（含新 NC 文本与段级日志）
        """
        parser = self.toolpath_parser or ToolpathParser(controller_type=controller_type)
        if parser.controller_type != controller_type:
            parser = ToolpathParser(controller_type=controller_type)

        try:
            segments = parser.parse_gcode(gcode_text)
        except Exception as exc:
            logger.exception("NC 代码解析失败: %s", exc)
            return NCRewriteResult(
                rewritten_gcode=gcode_text,
                segments_total=0,
                segments_adjusted=0,
                per_segment_log=[{"error": f"parse failed: {exc}"}],
            )

        if not segments:
            return NCRewriteResult(
                rewritten_gcode=gcode_text,
                segments_total=0,
                segments_adjusted=0,
            )

        per_segment_log: list[dict[str, Any]] = []
        adjusted_count = 0

        # 按段改写：直接重写文本中的 S/F 字段
        # 这里采用保守策略：保留原代码结构，仅替换 S 与 F 数值
        # （对 G00 段不改写 F，对 G01/G02/G03 段同时改写 S 与 F）
        new_lines: list[str] = []
        original_lines = gcode_text.splitlines()

        # 构建按 block_number 索引的段映射
        seg_by_block: dict[int, ToolpathSegment] = {seg.block_number: seg for seg in segments}

        for line in original_lines:
            stripped = line.strip()
            # 提取行号 Nxxxx
            block_num = self._extract_block_number(stripped)
            seg = seg_by_block.get(block_num) if block_num is not None else None

            if seg is None or not seg.g_code:
                new_lines.append(line)
                continue

            is_motion = seg.type in ("linear", "arc")
            is_rapid = seg.type == "rapid"

            if apply_to_motion_only and is_rapid:
                new_lines.append(line)
                continue

            if not (is_motion or is_rapid):
                new_lines.append(line)
                continue

            new_line = line
            old_rpm = seg.spindle_speed
            old_feed = seg.feed_rate

            # 主轴转速改写（仅当段含 S 字段时）
            if old_rpm is not None:
                new_line = self._replace_word(new_line, "S", decision.new_spindle_rpm)

            # 进给改写（仅切削段）
            if is_motion and old_feed is not None:
                new_line = self._replace_word(new_line, "F", decision.new_feed_rate_mm_min)

            if new_line != line:
                adjusted_count += 1
                per_segment_log.append(
                    {
                        "block_number": block_num,
                        "g_code": seg.g_code,
                        "spindle_speed_before": old_rpm,
                        "spindle_speed_after": decision.new_spindle_rpm,
                        "feed_before": old_feed,
                        "feed_after": decision.new_feed_rate_mm_min,
                    }
                )
            new_lines.append(new_line)

        rewritten = "\n".join(new_lines)
        if gcode_text.endswith("\n"):
            rewritten += "\n"

        return NCRewriteResult(
            rewritten_gcode=rewritten,
            segments_total=len(segments),
            segments_adjusted=adjusted_count,
            per_segment_log=per_segment_log,
        )

    # 内部辅助

    def _compute_spindle_rpm(self, cutting_speed_m_min: float, tool_diameter: float) -> float:
        """由切削速度反算主轴转速。

        公式: n = (v_c * 1000) / (π * D)
        """
        if tool_diameter <= 0:
            return 0.0
        return (cutting_speed_m_min * 1000.0) / (math.pi * tool_diameter)

    def _get_postprocessor(self, machine_capabilities: dict[str, float] | None) -> Any:
        """获取限幅器实例（鸭子类型：仅需 get_spindle_rpm / get_feed_rate）。

        优先使用 PostProcessorRegistry 中的 fanuc_0i 后处理器；
        若调用方提供 machine_capabilities，则构建独立的简单限幅器。
        """
        # 若调用方提供机床能力，构建独立限幅器（覆盖后处理器默认配置）
        if machine_capabilities is not None:
            max_rpm = machine_capabilities.get("max_spindle_speed")
            max_feed = machine_capabilities.get("max_feed_rate")
            if max_rpm is not None or max_feed is not None:
                return _SimpleLimiter(
                    max_rpm=max_rpm if max_rpm is not None else 1e9,
                    max_feed=max_feed if max_feed is not None else 1e9,
                )

        # 默认使用 fanuc_0i 后处理器作为通用限幅器
        registry = PostProcessorRegistry()
        try:
            post = registry.get_processor("fanuc_0i")
            return post
        except KeyError:
            logger.warning("fanuc_0i 后处理器未注册，限幅逻辑将退化为无操作")
            return _SimpleLimiter(max_rpm=1e9, max_feed=1e9)

    @staticmethod
    def _normalize_material_name(material_type: str) -> str:
        """将 ToolWearPredictor 的 material_type 映射为 FeedRateOptimizer 材料名。

        FeedRateOptimizer 仅识别 aluminum / steel / stainless / titanium 四类。
        """
        name = material_type.lower()
        if "aluminum" in name or "6061" in name or "7075" in name:
            return "aluminum"
        if "titanium" in name or "ti64" in name or "tc4" in name:
            return "titanium"
        if "stainless" in name or "304" in name or "316" in name or "hrc" in name:
            return "stainless"
        return "steel"

    @staticmethod
    def _extract_block_number(line: str) -> int | None:
        """从 NC 行中提取 N 字段后的行号。"""
        m = re.match(r"^\s*N(\d+)", line, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _replace_word(line: str, word: str, new_value: float) -> str:
        """替换 NC 行中指定字段的数值（保留原行格式与注释）。"""
        pattern = re.compile(rf"({word}\s*)(\-?\d+\.?\d*)", re.IGNORECASE)

        def _replacer(m: re.Match) -> str:
            return f"{m.group(1)}{new_value:.4f}".rstrip("0").rstrip(".")

        return pattern.sub(_replacer, line, count=1)


# 辅助类


class _SimpleLimiter:
    """简单限幅器（鸭子类型，与 BasePostProcessor.get_spindle_rpm / get_feed_rate 接口一致）。

    当 PostProcessorRegistry 不可用、或调用方显式提供 machine_capabilities 时使用。
    """

    def __init__(self, max_rpm: float = 1e9, max_feed: float = 1e9) -> None:
        self._max_rpm = float(max_rpm)
        self._max_feed = float(max_feed)

    def get_spindle_rpm(self, requested_rpm: float | None = None) -> float:
        if requested_rpm is None:
            return 0.0
        return max(0.0, min(float(requested_rpm), self._max_rpm))

    def get_feed_rate(self, requested_feed: float | None = None) -> float:
        if requested_feed is None:
            return 0.0
        return max(0.0, min(float(requested_feed), self._max_feed))


# 单例访问


_orchestrator: DynamicAdjustmentOrchestrator | None = None
_orchestrator_lock = threading.Lock()


def get_dynamic_adjustment_orchestrator() -> DynamicAdjustmentOrchestrator:
    """获取动态调参编排器单例（双重检查锁，线程安全）。"""
    global _orchestrator
    if _orchestrator is not None:
        return _orchestrator
    with _orchestrator_lock:
        if _orchestrator is None:
            _orchestrator = DynamicAdjustmentOrchestrator()
    return _orchestrator
