"""统一评分服务（自进化 M0 ·「一个分数」原则的唯一出口）。

聚合三个既有校验器为单一 ``EvalReport``，供回归门控、成功案例收割、
进化报告与未来的 RL reward 消费——全项目只有这一处定义「好不好」：

- **syntax 语法/安全**：``SafetyValidator.validate_gcode_text``（L5 语法合规
  + L6 结构完整性，结构化错误码；error 级 = 硬失败）。可选附带
  ``chatter_features`` 的参数级校验（L1-L4）。
- **geometry 几何**：``VoxelValidator`` 体素材料去除仿真（过切/快移碰撞）。
  需要毛坯尺寸与安全高度输入；任一缺失则该维度跳过（不参与评分）。
- **physics 物理**：``SimulationIntegration`` 连续 0-100 分（切削力 40% +
  颤振稳定性 60%）。需要切削参数输入；torch 不可用时仿真内部自动降级
  为仅颤振分析。

设计约束：
- ``evaluate()`` 永不抛异常：任一维度内部失败 → ``status="error"`` +
  ``skip_reason``，其余维度照常评分（判分器本身不允许击穿调用方）；
- 输入不足 → ``status="skipped"``（不算失败，复合分按可用维度权重归一）；
- 硬门禁 ``passed`` = 语法无 error 且（几何执行时）几何无碰撞——与生产
  现状对齐：文本安全校验是硬门禁，物理分仅决策辅助，体素碰撞由 DNC
  闸门单独把关，此处作为 reward/回归的强负信号；
- 复合分与 ``passed`` 的消费者（回归门控 / reward / 报表）必须使用本模块，
  不得各自另建评分口径。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from app.gcode_generation.safety_validator import SafetyValidator

logger = logging.getLogger(__name__)

__all__ = [
    "DimensionResult",
    "EvalReport",
    "GcodeEvaluationRequest",
    "EvaluationService",
    "DEFAULT_WEIGHTS",
    "get_evaluation_service",
    "reset_evaluation_service",
    "evaluate_gcode",
]

#: 维度默认权重（评估口径，非安全门禁）：语法/安全 0.5、几何 0.3、物理 0.2
DEFAULT_WEIGHTS: dict[str, float] = {"syntax": 0.5, "geometry": 0.3, "physics": 0.2}

# 评分口径常量
_SYNTAX_ERROR_PENALTY = 15.0
_SYNTAX_WARNING_PENALTY = 2.0
_GEOMETRY_PASS_SCORE = 100.0
_GEOMETRY_WARNING_SCORE = 60.0  # severity=warning（碰撞点 ≤3）
_GEOMETRY_CRITICAL_SCORE = 20.0  # severity=critical（碰撞点 >3）

#: 物理仿真必需的切削参数键（缺任一则跳过物理维度）
_PHYSICS_REQUIRED_KEYS = ("spindle_rpm", "feed_rate", "depth_of_cut")


@dataclass
class GcodeEvaluationRequest:
    """一次 G 代码评分请求。

    Attributes:
        gcode_text: 待评分的 G 代码文本（必填）。
        controller_type: 控制器类型（决定语法白名单与刀轨解析方言）。
        chatter_features: 参数级校验输入（可选）：具备 spindle_rpm /
            axial_depth_mm / limit_depth_mm 等属性的特征对象列表（L1-L4）。
        safe_z / stock_top_z / stock_length / stock_width / stock_height:
            几何维度输入（可选）：安全 Z 平面与毛坯尺寸（mm）。任一缺失
            则跳过体素仿真。
        cutting_params: 物理维度输入（可选）：需含 spindle_rpm /
            feed_rate / depth_of_cut（其余键走 SimulationIntegration 默认值）。
    """

    gcode_text: str
    controller_type: str = "fanuc_0i"
    chatter_features: list[Any] = field(default_factory=list)
    safe_z: float | None = None
    stock_top_z: float | None = None
    stock_length: float | None = None
    stock_width: float | None = None
    stock_height: float | None = None
    cutting_params: dict[str, Any] | None = None


@dataclass
class DimensionResult:
    """单维度评分结果。

    Attributes:
        name: 维度名（syntax / geometry / physics）。
        weight: 权重（复合分按 available 维度归一后生效）。
        status: "evaluated"（已评分）| "skipped"（输入不足未评）|
            "error"（已请求但执行失败）。
        passed: 该维度是否通过（仅 evaluated 时有意义）。
        score: 0-100 分（仅 evaluated 时有意义）。
        skip_reason: skipped / error 的原因。
        detail: 校验器原始报告摘要（可审计）。
    """

    name: str
    weight: float
    status: str = "skipped"
    passed: bool = False
    score: float = 0.0
    skip_reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.status == "evaluated"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "weight": self.weight,
            "status": self.status,
            "passed": self.passed,
            "score": round(self.score, 2),
            "skip_reason": self.skip_reason,
            "detail": self.detail,
        }


@dataclass
class EvalReport:
    """统一评分报告（回归门控 / reward / 报表的唯一分数出口）。

    Attributes:
        passed: 硬门禁判定：语法无 error 且（几何执行时）几何无碰撞。
            语法维度本身执行失败时 fail-closed 置 False。
        composite_score: 复合分 0-100（可用维度加权，权重归一）。
        dimensions: 各维度结果。
        failure_classes: 结构化失败类别（语法错误码 / GEOMETRY_COLLISION /
            PHYSICS_NOT_RECOMMENDED / EVAL_INCOMPLETE），供按类别统计迭代。
        controller_type: 控制器类型。
        duration_ms: 评分总耗时。
    """

    passed: bool
    composite_score: float
    dimensions: list[DimensionResult] = field(default_factory=list)
    failure_classes: list[str] = field(default_factory=list)
    controller_type: str = ""
    duration_ms: float = 0.0

    def dimension(self, name: str) -> DimensionResult | None:
        """按名取维度结果。"""
        for d in self.dimensions:
            if d.name == name:
                return d
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "composite_score": round(self.composite_score, 2),
            "dimensions": [d.to_dict() for d in self.dimensions],
            "failure_classes": list(self.failure_classes),
            "controller_type": self.controller_type,
            "duration_ms": round(self.duration_ms, 2),
        }


class EvaluationService:
    """统一评分服务（无状态，可并发调用；校验器/仿真器可注入便于测试）。"""

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        machine_config: dict[str, Any] | None = None,
        voxel_validator: Any = None,
        simulator: Any = None,
    ) -> None:
        """初始化。

        Args:
            weights: 维度权重覆盖（合并到 DEFAULT_WEIGHTS）。
            machine_config: SafetyValidator 机床能力配置（缺省用默认值）。
            voxel_validator: 注入 VoxelValidator（测试桩）；缺省时首次几何
                评分按 CamValidationConfig 默认参数构建并缓存。
            simulator: 注入仿真器（需有 ``run_simulation``，测试桩）；
                缺省时首次物理评分构建 SimulationIntegration。
        """
        self._weights = {**DEFAULT_WEIGHTS, **(weights or {})}
        self._safety_validator = SafetyValidator(machine_config=machine_config)
        self._voxel_validator = voxel_validator
        self._simulator = simulator

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def evaluate(self, request: GcodeEvaluationRequest) -> EvalReport:
        """评分入口。永不抛异常（见模块 docstring 设计约束）。"""
        started = time.perf_counter()
        syntax = self._evaluate_syntax(request)
        geometry = self._evaluate_geometry(request)
        physics = self._evaluate_physics(request)
        dims = [syntax, geometry, physics]

        # 复合分：只对 evaluated 维度做权重归一
        evaluated = [d for d in dims if d.available]
        total_weight = sum(d.weight for d in evaluated)
        composite = sum(d.score * d.weight for d in evaluated) / total_weight if total_weight > 0 else 0.0

        # 硬门禁：语法必过；几何已执行时必须无碰撞。
        # 物理 / skipped / error 的几何不阻断（与生产现状口径一致）。
        passed = bool(syntax.available and syntax.passed and (not geometry.available or geometry.passed))

        failure_classes: list[str] = []
        if syntax.available and not syntax.passed:
            failure_classes.extend(str(c) for c in syntax.detail.get("error_codes", []))
        if geometry.available and not geometry.passed:
            failure_classes.append("GEOMETRY_COLLISION")
        if physics.available and not physics.passed:
            failure_classes.append("PHYSICS_NOT_RECOMMENDED")
        if any(d.status == "error" for d in dims):
            failure_classes.append("EVAL_INCOMPLETE")

        return EvalReport(
            passed=passed,
            composite_score=composite,
            dimensions=dims,
            failure_classes=failure_classes,
            controller_type=request.controller_type,
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    # ------------------------------------------------------------------
    # 各维度
    # ------------------------------------------------------------------

    def _evaluate_syntax(self, request: GcodeEvaluationRequest) -> DimensionResult:
        """语法/安全维度：文本级 L5/L6（+ 可选参数级 L1-L4）。"""
        dim = DimensionResult(name="syntax", weight=self._weights.get("syntax", 0.0))
        try:
            report = self._safety_validator.validate_gcode_text(
                request.gcode_text, controller_type=request.controller_type
            )
            if request.chatter_features:
                feat_report = self._safety_validator.validate_features(request.chatter_features)
                report.issues.extend(feat_report.issues)
            dim.status = "evaluated"
            dim.passed = report.is_valid
            dim.score = max(
                0.0,
                100.0 - _SYNTAX_ERROR_PENALTY * len(report.errors) - _SYNTAX_WARNING_PENALTY * len(report.warnings),
            )
            dim.detail = report.to_dict()
        except Exception as e:  # noqa: BLE001 - 判分器自身不允许抛出
            dim.status = "error"
            dim.skip_reason = f"语法维度执行失败: {type(e).__name__}: {e}"
            logger.warning("评分语法维度失败: %s", e, exc_info=True)
        return dim

    def _evaluate_geometry(self, request: GcodeEvaluationRequest) -> DimensionResult:
        """几何维度：体素材料去除仿真（输入不足跳过，不参与评分）。"""
        dim = DimensionResult(name="geometry", weight=self._weights.get("geometry", 0.0))
        missing = [
            key
            for key, value in (
                ("safe_z", request.safe_z),
                ("stock_top_z", request.stock_top_z),
                ("stock_length", request.stock_length),
                ("stock_width", request.stock_width),
                ("stock_height", request.stock_height),
            )
            if value is None
        ]
        if missing:
            dim.skip_reason = f"缺少几何输入（{', '.join(missing)}），跳过体素仿真"
            return dim
        try:
            validator = self._get_voxel_validator()
            report = validator.validate(
                gcode_text=request.gcode_text,
                controller_type=request.controller_type,
                safe_z=float(request.safe_z),
                stock_top_z=float(request.stock_top_z),
                stock_length=float(request.stock_length),
                stock_width=float(request.stock_width),
                stock_height=float(request.stock_height),
            )
            dim.status = "evaluated"
            dim.passed = bool(report.passed)
            dim.score = (
                _GEOMETRY_PASS_SCORE
                if report.passed
                else (_GEOMETRY_WARNING_SCORE if report.severity == "warning" else _GEOMETRY_CRITICAL_SCORE)
            )
            dim.detail = report.to_dict()
        except Exception as e:  # noqa: BLE001 - 判分器自身不允许抛出
            dim.status = "error"
            dim.skip_reason = f"几何维度执行失败: {type(e).__name__}: {e}"
            logger.warning("评分几何维度失败: %s", e, exc_info=True)
        return dim

    def _evaluate_physics(self, request: GcodeEvaluationRequest) -> DimensionResult:
        """物理维度：切削力 + 颤振稳定性连续评分（缺切削参数跳过）。"""
        dim = DimensionResult(name="physics", weight=self._weights.get("physics", 0.0))
        cutting = request.cutting_params
        if not isinstance(cutting, dict) or any(cutting.get(k) is None for k in _PHYSICS_REQUIRED_KEYS):
            dim.skip_reason = "缺少切削参数（spindle_rpm / feed_rate / depth_of_cut），跳过物理仿真"
            return dim
        try:
            simulator = self._get_simulator()
            result = simulator.run_simulation(
                material=cutting.get("material", "45steel"),
                tool=cutting.get("tool", "endmill_d10"),
                spindle_rpm=cutting["spindle_rpm"],
                feed_rate=cutting["feed_rate"],
                depth_of_cut=cutting["depth_of_cut"],
                machine=cutting.get("machine", "vmc_850"),
            )
            if result.status != "success":
                dim.status = "error"
                dim.skip_reason = f"物理仿真未完成: {result.error_message or result.status}"
                return dim
            dim.status = "evaluated"
            dim.passed = bool(result.passed)
            dim.score = float(result.score)
            dim.detail = result.to_dict()
        except Exception as e:  # noqa: BLE001 - 判分器自身不允许抛出
            dim.status = "error"
            dim.skip_reason = f"物理维度执行失败: {type(e).__name__}: {e}"
            logger.warning("评分物理维度失败: %s", e, exc_info=True)
        return dim

    # ------------------------------------------------------------------
    # 懒加载依赖（保持模块 import 轻量：体素/仿真链路较重）
    # ------------------------------------------------------------------

    def _get_voxel_validator(self) -> Any:
        if self._voxel_validator is None:
            from app.cam_validation.voxel_validator import VoxelValidator
            from app.config.cam_validation import CamValidationConfig

            self._voxel_validator = VoxelValidator(CamValidationConfig())
        return self._voxel_validator

    def _get_simulator(self) -> Any:
        if self._simulator is None:
            from app.process_planning.sim_integration import SimulationIntegration

            self._simulator = SimulationIntegration()
        return self._simulator


# 全局单例（双重检查锁，与 failure_case_store 风格一致）

_service: EvaluationService | None = None
_service_lock = threading.Lock()


def get_evaluation_service() -> EvaluationService:
    """获取全局 EvaluationService（线程安全懒加载）。"""
    global _service
    if _service is not None:
        return _service
    with _service_lock:
        if _service is None:
            _service = EvaluationService()
        return _service


def reset_evaluation_service() -> None:
    """重置全局单例（测试用）。"""
    global _service
    with _service_lock:
        _service = None


def evaluate_gcode(request: GcodeEvaluationRequest) -> EvalReport:
    """便捷入口：使用全局服务评分。"""
    return get_evaluation_service().evaluate(request)
