"""仿真服务集成模块。

实现仿真器与工艺规划流程的无缝集成，提供标准化的仿真服务调用接口。

主要功能：
- 切削力预测集成（M2.1）
- 颤振稳定性分析集成（M2.2）
- 仿真结果评分与方案推荐
- 超时处理与降级机制

使用方式：
    from app.process_planning.sim_integration import SimulationIntegration

    simulator = SimulationIntegration()
    result = simulator.run_simulation(
        material='45steel',
        tool='endmill_d10',
        spindle_rpm=8000,
        feed_rate=1200,
        depth_of_cut=2.0
    )

    if result['passed']:
        score = result['score']
        recommendation = result['recommendation']
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from typing import Any

# torch 相关模块软依赖：桌面 MVP 打包时排除 torch，此时
# app.simulation.cutting_force.predictor 的 predict_cutting_force 会被
# 置为 None（见 app/simulation/cutting_force/__init__.py），运行时需检查。
from app.simulation.cutting_force import _HAS_TORCH as _CUTTING_FORCE_HAS_TORCH
from app.simulation.cutting_force.predictor import predict_cutting_force
from app.simulation.chatter.predictor import predict_stability

logger = logging.getLogger(__name__)


@dataclass
class SimulationResult:
    """仿真结果数据结构。

    Attributes:
        status: 仿真状态 ('success', 'timeout', 'failed', 'not_run')
        passed: 仿真是否通过
        score: 仿真评分 (0-100)
        recommendation: 推荐级别 ('recommended', 'acceptable', 'not_recommended')
        cutting_force: 切削力预测结果
        chatter_stability: 颤振稳定性分析结果
        duration_ms: 仿真耗时(毫秒)
        error_message: 错误信息
    """

    status: str = "not_run"
    passed: bool = False
    score: float = 0.0
    recommendation: str = "not_recommended"
    cutting_force: dict[str, Any] | None = None
    chatter_stability: dict[str, Any] | None = None
    duration_ms: float = 0.0
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式。"""
        return {
            "status": self.status,
            "passed": self.passed,
            "score": round(self.score, 2),
            "recommendation": self.recommendation,
            "cutting_force": self.cutting_force,
            "chatter_stability": self.chatter_stability,
            "duration_ms": round(self.duration_ms, 2),
            "error_message": self.error_message,
        }


class SimulationIntegration:
    """仿真服务集成器。

    提供标准化的仿真服务调用接口，整合切削力预测和颤振稳定性分析，
    实现仿真结果评分与方案推荐。

    特性：
    - 同步非阻塞调用模式
    - 5秒超时保护
    - 仿真失败降级机制
    - 综合评分与推荐系统
    """

    # 仿真参数阈值（用于评分）
    FORCE_THRESHOLD_FX = 500.0  # 进给力阈值 (N)
    FORCE_THRESHOLD_FY = 400.0  # 径向力阈值 (N)
    FORCE_THRESHOLD_FZ = 600.0  # 主切削力阈值 (N)
    CHATTER_LIMIT_DEPTH = 2.0  # 颤振极限切深阈值 (mm)

    def __init__(self, timeout_seconds: float = 5.0):
        """初始化仿真集成器。

        Args:
            timeout_seconds: 仿真超时时间(秒)，默认5秒
        """
        self.timeout_seconds = timeout_seconds
        self._executor = ThreadPoolExecutor(max_workers=2)

    def run_simulation(
        self,
        material: str = "45steel",
        tool: str = "endmill_d10",
        spindle_rpm: float = 8000,
        feed_rate: float = 1200,
        depth_of_cut: float = 2.0,
        machine: str = "vmc_850",
        workpiece: str | None = None,
    ) -> SimulationResult:
        """执行完整仿真流程。

        同步非阻塞调用模式，包含切削力预测和颤振稳定性分析。

        Args:
            material: 材料名称
            tool: 刀具标识
            spindle_rpm: 主轴转速 (rpm)
            feed_rate: 进给速率 (mm/min)
            depth_of_cut: 切深 (mm)
            machine: 机床标识
            workpiece: 工件材料（可选，默认使用material）

        Returns:
            SimulationResult: 完整的仿真结果
        """
        start_time = time.time()
        result = SimulationResult()

        if workpiece is None:
            workpiece = material

        # 准备切削力预测参数
        force_params = {
            "speed": spindle_rpm,
            "feed": feed_rate,
            "depth": depth_of_cut,
        }

        try:
            # 使用线程池执行仿真，设置超时保护。
            # 当 torch 不可用时（桌面 MVP），predict_cutting_force 为 None，
            # 此处跳过切削力预测，仅执行颤振稳定性分析。
            future_force = None
            if _CUTTING_FORCE_HAS_TORCH and predict_cutting_force is not None:
                future_force = self._executor.submit(
                    predict_cutting_force,
                    material=material,
                    tool=tool,
                    params=force_params,
                    use_pinn=True,
                )
            else:
                logger.warning("torch 不可用，跳过切削力预测（PINN 模型未加载）")

            future_chatter = self._executor.submit(
                predict_stability,
                spindle_rpm=spindle_rpm,
                machine=machine,
                tool=tool,
                workpiece=workpiece,
            )

            # 等待结果，设置超时
            try:
                force_result = future_force.result(timeout=self.timeout_seconds) if future_force is not None else None
                chatter_result = future_chatter.result(timeout=self.timeout_seconds)

                result.cutting_force = force_result
                result.chatter_stability = chatter_result
                result.status = "success"

                # 切削力缺失时使用空字典兜底，保证评分函数可用
                force_for_score = force_result or {}
                result.score = self._calculate_score(force_for_score, chatter_result, depth_of_cut)
                result.passed = self._evaluate_pass(force_for_score, chatter_result, depth_of_cut)
                result.recommendation = self._generate_recommendation(result.passed, result.score)

            except FuturesTimeoutError:
                result.status = "timeout"
                result.error_message = f"仿真超时（>{self.timeout_seconds}秒）"
                result.recommendation = "not_recommended"
                logger.warning("仿真超时: material=%s, tool=%s", material, tool)

            except Exception as e:
                # future.result() 会把工作线程的任意异常原样重抛——
                # 仿真是决策辅助，内部崩溃必须降级为 failed 结果而非击穿工艺管线
                result.status = "failed"
                result.error_message = f"仿真执行失败: {type(e).__name__}"
                result.recommendation = "not_recommended"
                logger.error("仿真执行失败: %s", e, exc_info=True)

        except Exception as e:
            result.status = "failed"
            result.error_message = f"仿真服务调用失败: {type(e).__name__}"
            result.recommendation = "not_recommended"
            logger.error("仿真服务调用失败: %s", e, exc_info=True)

        result.duration_ms = (time.time() - start_time) * 1000
        return result

    def _calculate_score(
        self,
        force_result: dict[str, Any],
        chatter_result: dict[str, Any],
        depth_of_cut: float,
    ) -> float:
        """计算仿真综合评分。

        评分规则：
        - 切削力评分 (40%): 基于三个方向力的相对大小
        - 颤振稳定性评分 (60%): 基于稳定性和极限切深

        Args:
            force_result: 切削力预测结果
            chatter_result: 颤振稳定性结果
            depth_of_cut: 实际切深

        Returns:
            综合评分 (0-100)
        """
        # 切削力评分
        fx = force_result.get("Fx", 0)
        fy = force_result.get("Fy", 0)
        fz = force_result.get("Fz", 0)

        force_score = 100.0
        force_score -= max(0, (fx - self.FORCE_THRESHOLD_FX) / self.FORCE_THRESHOLD_FX * 30)
        force_score -= max(0, (fy - self.FORCE_THRESHOLD_FY) / self.FORCE_THRESHOLD_FY * 30)
        force_score -= max(0, (fz - self.FORCE_THRESHOLD_FZ) / self.FORCE_THRESHOLD_FZ * 40)
        force_score = max(0, min(100, force_score))

        # 颤振稳定性评分
        stable = chatter_result.get("stable", False)
        limit_depth = chatter_result.get("limit_depth", 0)

        chatter_score = 0.0
        if stable:
            chatter_score = 100.0
            # 根据极限切深与实际切深的比值调整
            if limit_depth > 0:
                ratio = limit_depth / max(depth_of_cut, 0.1)
                if ratio > 2.0:
                    chatter_score = 100.0
                elif ratio > 1.5:
                    chatter_score = 90.0
                elif ratio > 1.0:
                    chatter_score = 75.0
                else:
                    chatter_score = 50.0
        else:
            chatter_score = 20.0

        # 综合评分
        total_score = force_score * 0.4 + chatter_score * 0.6
        return max(0, min(100, total_score))

    def _evaluate_pass(
        self,
        force_result: dict[str, Any],
        chatter_result: dict[str, Any],
        depth_of_cut: float,
    ) -> bool:
        """评估仿真是否通过。

        通过条件：
        - 切削力在安全范围内
        - 颤振稳定或极限切深大于实际切深

        Args:
            force_result: 切削力预测结果
            chatter_result: 颤振稳定性结果
            depth_of_cut: 实际切深

        Returns:
            是否通过仿真
        """
        fx = force_result.get("Fx", 0)
        fy = force_result.get("Fy", 0)
        fz = force_result.get("Fz", 0)

        # 切削力检查（允许10%余量）
        force_pass = (
            fx <= self.FORCE_THRESHOLD_FX * 1.1
            and fy <= self.FORCE_THRESHOLD_FY * 1.1
            and fz <= self.FORCE_THRESHOLD_FZ * 1.1
        )

        # 颤振稳定性检查
        stable = chatter_result.get("stable", False)
        limit_depth = chatter_result.get("limit_depth", 0)
        chatter_pass = stable or limit_depth >= depth_of_cut

        return force_pass and chatter_pass

    def _generate_recommendation(self, passed: bool, score: float) -> str:
        """生成方案推荐级别。

        推荐规则：
        - passed=False → 'not_recommended'
        - score >= 80 → 'recommended'
        - score >= 60 → 'acceptable'
        - score < 60 → 'not_recommended'

        Args:
            passed: 仿真是否通过
            score: 仿真评分

        Returns:
            推荐级别 ('recommended', 'acceptable', 'not_recommended')
        """
        if not passed:
            return "not_recommended"

        if score >= 80:
            return "recommended"
        elif score >= 60:
            return "acceptable"
        else:
            return "not_recommended"

    def __del__(self):
        """清理资源。"""
        if hasattr(self, "_executor"):
            self._executor.shutdown(wait=False)


def run_simulation_for_operation(
    operation: dict[str, Any],
    material: str,
    timeout_seconds: float = 5.0,
) -> SimulationResult:
    """为单个工序运行仿真。

    便捷函数，从工序参数中提取仿真所需参数并执行仿真。

    Args:
        operation: 工序参数字典，包含：
            - tool: 刀具标识
            - spindle_rpm: 主轴转速
            - feed_rate: 进给速率
            - depth_of_cut: 切深
            - machine: 机床标识（可选）
        material: 材料名称
        timeout_seconds: 超时时间(秒)

    Returns:
        SimulationResult: 仿真结果
    """
    simulator = SimulationIntegration(timeout_seconds=timeout_seconds)

    return simulator.run_simulation(
        material=material,
        tool=operation.get("tool", "endmill_d10"),
        spindle_rpm=operation.get("spindle_rpm", 8000),
        feed_rate=operation.get("feed_rate", 1200),
        depth_of_cut=operation.get("depth_of_cut", 2.0),
        machine=operation.get("machine", "vmc_850"),
    )
