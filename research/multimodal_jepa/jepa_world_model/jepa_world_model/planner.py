"""CEM规划算法模块。

实现基于Cross-Entropy Method (CEM)的高效多步工艺规划算法：
- 采样N个候选动作序列
- 利用World Model预测每个序列的完整结果轨迹
- 选择Top-K%性能最优序列
- 更新动作序列的高斯分布参数
- 迭代优化直到收敛
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.ai.jepa_world_model.config import JEPAWorldModelConfig
from app.ai.jepa_world_model.state import ManufacturingState
from app.ai.jepa_world_model.action import ManufacturingAction, OPERATION_TYPE_MAP
from app.ai.jepa_world_model.predictor import JEPAPredictor


@dataclass
class PlanningResult:
    """单步规划结果。

    Attributes:
        action: 推荐的动作
        expected_next_state: 预期下一状态嵌入
        expected_rewards: 预期奖励 [质量, 效率, 风险, 综合]
        confidence: 置信度
        risk_assessment: 风险评估报告
    """

    action: ManufacturingAction
    expected_next_state_embedding: np.ndarray
    expected_rewards: np.ndarray
    confidence: float
    risk_assessment: Dict = field(default_factory=dict)


@dataclass
class MultiStepPlanningResult:
    """多步规划完整结果。

    Attributes:
        action_sequence: 推荐的工艺动作序列
        state_trajectory: 状态嵌入轨迹
        reward_trajectory: 奖励轨迹
        confidence_trajectory: 置信度轨迹
        total_reward: 总奖励
        planning_time_ms: 规划耗时（毫秒）
        risk_report: 全面风险评估报告
        convergence_history: 收敛历史
    """

    action_sequence: List[ManufacturingAction]
    state_trajectory: np.ndarray
    reward_trajectory: np.ndarray
    confidence_trajectory: np.ndarray
    total_reward: float
    planning_time_ms: float
    risk_report: Dict = field(default_factory=dict)
    convergence_history: List[float] = field(default_factory=list)


class CEMPlanner:
    """Cross-Entropy Method (CEM) 规划器。

    使用CEM算法进行多步工艺规划，通过迭代采样和优化
    找到最优的动作序列。

    算法流程：
    1. 初始化动作序列的高斯分布（均值、方差）
    2. 采样N个候选动作序列
    3. 利用World Model预测每个序列的完整轨迹
    4. 评估序列奖励并选择Top-K%
    5. 更新高斯分布参数（均值和方差）
    6. 重复2-5直到收敛
    """

    def __init__(self, config: JEPAWorldModelConfig, predictor: JEPAPredictor):
        """初始化CEM规划器。

        Args:
            config: JEPA World Model配置
            predictor: JEPA预测器
        """
        self.config = config
        self.predictor = predictor
        self.planning_horizon = config.cem_planning_horizon
        self.population_size = config.cem_population_size
        self.num_elite = config.cem_num_elite
        self.max_iterations = config.cem_max_iterations

        # 动作空间参数
        self.num_operation_types = len(OPERATION_TYPE_MAP)
        self.action_dim = 4  # [op_type_idx, spindle_speed, feed_rate, depth_of_cut]

    def _compute_reward(
        self,
        reward_estimates: np.ndarray,
        action_risks: np.ndarray,
    ) -> np.ndarray:
        """计算综合奖励。

        奖励函数设计：
        - 质量达标率（权重40%）：reward_estimates[:, 0]
        - 生产效率（权重35%）：reward_estimates[:, 1]
        - 工艺风险（权重25%）：(1 - reward_estimates[:, 2]) * (1 - action_risks)

        Args:
            reward_estimates: 模型预测奖励 (T, 4)
            action_risks: 动作风险 (T,)

        Returns:
            综合奖励 (T,)
        """
        w_q = self.config.reward_quality_weight
        w_e = self.config.reward_efficiency_weight
        w_r = self.config.reward_risk_weight

        quality = reward_estimates[:, 0]
        efficiency = reward_estimates[:, 1]
        risk = (1.0 - reward_estimates[:, 2]) * (1.0 - action_risks)

        return w_q * quality + w_e * efficiency + w_r * risk

    def _estimate_action_risk(self, action: ManufacturingAction) -> float:
        """估计单个动作的风险。

        基于操作参数评估工艺风险：
        - 高转速 + 大进给 + 深切削 = 高风险
        - 冷却液使用降低风险

        Args:
            action: 制造动作

        Returns:
            风险值 (0.0~1.0)
        """
        params = action.parameters
        spindle_speed = params.get("spindle_speed", 8000)
        feed_rate = params.get("feed_rate", 500.0)
        depth_of_cut = params.get("depth_of_cut", 2.0)
        coolant = params.get("coolant", True)

        # 风险因子计算
        speed_risk = np.clip(spindle_speed / 30000.0, 0.0, 1.0)
        feed_risk = np.clip(feed_rate / 2000.0, 0.0, 1.0)
        depth_risk = np.clip(depth_of_cut / 20.0, 0.0, 1.0)
        coolant_factor = 0.7 if coolant else 1.3

        risk = coolant_factor * (0.3 * speed_risk + 0.3 * feed_risk + 0.4 * depth_risk)
        return np.clip(risk, 0.0, 1.0)

    def _sample_action_sequence(
        self,
        means: np.ndarray,
        stds: np.ndarray,
    ) -> np.ndarray:
        """从高斯分布采样动作序列。

        Args:
            means: 均值 (T, action_dim)
            stds: 标准差 (T, action_dim)

        Returns:
            采样动作序列 (T, action_dim)
        """
        samples = np.random.normal(means, stds)
        # 裁剪到合理范围
        samples[:, 0] = np.clip(samples[:, 0], 0, self.num_operation_types - 1)
        samples[:, 1] = np.clip(samples[:, 1], 500, 30000)  # spindle_speed
        samples[:, 2] = np.clip(samples[:, 2], 10, 2000)  # feed_rate
        samples[:, 3] = np.clip(samples[:, 3], 0.1, 20.0)  # depth_of_cut
        return samples

    def _params_to_actions(self, action_params: np.ndarray) -> List[ManufacturingAction]:
        """将参数数组转换为ManufacturingAction列表。

        Args:
            action_params: (T, action_dim) 动作参数

        Returns:
            动作列表
        """
        op_types = list(OPERATION_TYPE_MAP.keys())
        actions = []
        for i in range(action_params.shape[0]):
            op_idx = int(np.clip(action_params[i, 0], 0, len(op_types) - 1))
            action = ManufacturingAction(
                operation_type=op_types[op_idx],
                tool_id=f"tool_{op_types[op_idx][:4]}_{i}",
                parameters={
                    "spindle_speed": int(action_params[i, 1]),
                    "feed_rate": float(action_params[i, 2]),
                    "depth_of_cut": float(action_params[i, 3]),
                    "coolant": True,
                },
            )
            actions.append(action)
        return actions

    def _evaluate_sequence(
        self,
        initial_state_embedding: np.ndarray,
        actions: List[ManufacturingAction],
    ) -> Tuple[float, np.ndarray, np.ndarray, np.ndarray]:
        """评估单个动作序列。

        Args:
            initial_state_embedding: 初始状态嵌入
            actions: 动作序列

        Returns:
            (total_reward, state_trajectory, reward_trajectory, confidence_trajectory)
        """
        action_embeddings = np.stack([a.action_embedding for a in actions])
        trajectory = self.predictor.predict_trajectory(
            initial_state_embedding, action_embeddings,
        )

        # 计算每个动作的风险
        action_risks = np.array([self._estimate_action_risk(a) for a in actions])

        # 计算综合奖励
        rewards = self._compute_reward(trajectory["reward_trajectory"], action_risks)

        # 累积奖励（带折扣）
        discount = 0.95
        total_reward = sum(r * (discount ** i) for i, r in enumerate(rewards))

        return (
            total_reward,
            trajectory["state_trajectory"],
            trajectory["reward_trajectory"],
            trajectory["confidence_trajectory"],
        )

    def plan(
        self,
        initial_state: ManufacturingState,
        target_goal: Optional[Dict] = None,
    ) -> MultiStepPlanningResult:
        """执行CEM多步规划。

        Args:
            initial_state: 初始制造状态
            target_goal: 目标规格（可选），包含目标精度、质量等

        Returns:
            MultiStepPlanningResult: 完整规划结果
        """
        start_time = time.perf_counter()
        T = self.planning_horizon

        # 初始化高斯分布参数
        means = np.zeros((T, self.action_dim), dtype=np.float32)
        stds = np.ones((T, self.action_dim), dtype=np.float32) * 0.5

        # 初始化均值（使用合理的默认值）
        means[:, 0] = 0  # 默认操作类型索引
        means[:, 1] = 0.3  # 归一化主轴转速
        means[:, 2] = 0.2  # 归一化进给率
        means[:, 3] = 0.1  # 归一化切削深度

        initial_embedding = initial_state.state_embedding.copy()
        best_sequence = None
        best_reward = -float("inf")
        convergence_history = []

        for iteration in range(self.max_iterations):
            # 采样候选序列
            population_params = np.array([
                self._sample_action_sequence(means, stds)
                for _ in range(self.population_size)
            ])

            # 评估所有候选序列
            rewards = np.zeros(self.population_size)
            for i in range(self.population_size):
                actions = self._params_to_actions(population_params[i])
                total_reward, _, _, _ = self._evaluate_sequence(
                    initial_embedding, actions,
                )
                rewards[i] = total_reward

            # 选择精英
            elite_indices = np.argsort(rewards)[-self.num_elite:]
            elite_params = population_params[elite_indices]
            elite_rewards = rewards[elite_indices]

            # 更新最佳序列
            if elite_rewards[-1] > best_reward:
                best_reward = elite_rewards[-1]
                best_sequence = elite_params[-1].copy()

            convergence_history.append(float(np.mean(elite_rewards)))

            # 更新分布参数
            new_means = np.mean(elite_params, axis=0)
            new_stds = np.std(elite_params, axis=0) + 1e-6

            # 平滑更新
            alpha = 0.3
            means = alpha * new_means + (1 - alpha) * means
            stds = alpha * new_stds + (1 - alpha) * stds

            # 检查收敛
            if iteration >= 3:
                recent_improvement = (
                    convergence_history[-1] - convergence_history[-3]
                )
                if abs(recent_improvement) < 1e-4:
                    break

        # 构建最终结果
        final_actions = self._params_to_actions(best_sequence)
        total_reward, state_traj, reward_traj, conf_traj = self._evaluate_sequence(
            initial_embedding, final_actions,
        )

        planning_time = (time.perf_counter() - start_time) * 1000

        # 生成风险评估报告
        risk_report = self._generate_risk_report(final_actions, reward_traj, conf_traj)

        return MultiStepPlanningResult(
            action_sequence=final_actions,
            state_trajectory=state_traj,
            reward_trajectory=reward_traj,
            confidence_trajectory=conf_traj,
            total_reward=total_reward,
            planning_time_ms=planning_time,
            risk_report=risk_report,
            convergence_history=convergence_history,
        )

    def plan_single_step(
        self,
        initial_state: ManufacturingState,
        action: ManufacturingAction,
    ) -> PlanningResult:
        """单步预测（给定状态和动作，预测结果）。

        Args:
            initial_state: 初始状态
            action: 制造动作

        Returns:
            PlanningResult: 单步规划结果
        """
        result = self.predictor.predict_step(
            initial_state.state_embedding,
            action.action_embedding,
        )

        action_risk = self._estimate_action_risk(action)
        risk_assessment = {
            "action_risk": action_risk,
            "tool_risk": "high" if action_risk > 0.6 else "medium" if action_risk > 0.3 else "low",
            "quality_estimate": float(result["reward_estimates"][0]),
            "efficiency_estimate": float(result["reward_estimates"][1]),
            "risk_estimate": float(result["reward_estimates"][2]),
        }

        return PlanningResult(
            action=action,
            expected_next_state_embedding=result["next_state_embedding"],
            expected_rewards=result["reward_estimates"],
            confidence=float(np.squeeze(result["confidence"])),
            risk_assessment=risk_assessment,
        )

    def counterfactual_compare(
        self,
        initial_state: ManufacturingState,
        action_a: ManufacturingAction,
        action_b: ManufacturingAction,
    ) -> Dict:
        """反事实推理：比较两个工艺方案的效果差异。

        Args:
            initial_state: 初始状态
            action_a: 方案A
            action_b: 方案B

        Returns:
            比较结果字典
        """
        result_a = self.plan_single_step(initial_state, action_a)
        result_b = self.plan_single_step(initial_state, action_b)

        # 计算状态差异
        state_diff = np.linalg.norm(
            result_a.expected_next_state_embedding - result_b.expected_next_state_embedding
        )

        # 计算奖励差异
        reward_diff = result_a.expected_rewards - result_b.expected_rewards

        return {
            "action_a": {
                "type": action_a.operation_type,
                "rewards": result_a.expected_rewards.tolist(),
                "confidence": result_a.confidence,
                "risk": result_a.risk_assessment,
            },
            "action_b": {
                "type": action_b.operation_type,
                "rewards": result_b.expected_rewards.tolist(),
                "confidence": result_b.confidence,
                "risk": result_b.risk_assessment,
            },
            "differences": {
                "state_distance": float(state_diff),
                "reward_diff": reward_diff.tolist(),
                "quality_diff": float(reward_diff[0]),
                "efficiency_diff": float(reward_diff[1]),
                "risk_diff": float(reward_diff[2]),
            },
            "recommendation": (
                "方案A优于方案B" if result_a.confidence > result_b.confidence
                else "方案B优于方案A"
            ),
        }

    def _generate_risk_report(
        self,
        actions: List[ManufacturingAction],
        reward_trajectory: np.ndarray,
        confidence_trajectory: np.ndarray,
    ) -> Dict:
        """生成全面的风险评估报告。

        Args:
            actions: 动作序列
            reward_trajectory: 奖励轨迹
            confidence_trajectory: 置信度轨迹

        Returns:
            风险评估报告字典
        """
        action_risks = [self._estimate_action_risk(a) for a in actions]

        # 识别高风险步骤
        high_risk_steps = []
        for i, (action, risk) in enumerate(zip(actions, action_risks)):
            if risk > 0.5:
                high_risk_steps.append({
                    "step": i,
                    "operation": action.operation_type,
                    "risk_level": risk,
                    "risk_factors": {
                        "spindle_speed": action.parameters.get("spindle_speed", 0),
                        "feed_rate": action.parameters.get("feed_rate", 0),
                        "depth_of_cut": action.parameters.get("depth_of_cut", 0),
                    },
                })

        # 评估设备损耗趋势
        tool_wear_trend = np.cumsum([r * 0.01 for r in action_risks])

        # 低置信度步骤
        low_confidence_steps = [
            {"step": i, "confidence": float(c)}
            for i, c in enumerate(confidence_trajectory) if c < 0.7
        ]

        return {
            "overall_risk": float(np.mean(action_risks)),
            "max_risk": float(np.max(action_risks)),
            "high_risk_steps": high_risk_steps,
            "tool_wear_trend": tool_wear_trend.tolist(),
            "low_confidence_steps": low_confidence_steps,
            "quality_trend": reward_trajectory[:, 0].tolist(),
            "efficiency_trend": reward_trajectory[:, 1].tolist(),
            "risk_trend": reward_trajectory[:, 2].tolist(),
            "estimated_total_time": float(len(actions) * 5.0),  # 估算每步5分钟
        }
