# ADR-017: 世界模型与 RL 模块

**日期**: 2026-07-14
**状态**: 已接受
**决策者**: 灵境制造团队
**关联**: ADR-005（核心架构契约）、ADR-013（颤振预测接入）、ADR-014（G 代码生成）、ADR-016（可解释性可视化）

---

## 背景

阶段 8 是用户最初愿景的核心："让软件真的能认识零件加工的全过程，同时还能自己进步"。
这要求系统从"被动预测"演进为"主动决策"——不仅能预测颤振，还能**主动调整切削参数
以避免颤振**，并从每次加工结果中**学习改进策略**。

### 当前现状

1. **预测能力已具备**：LTC 颤振预测（ADR-013）+ 切削参数推荐（ADR-009）已能给出
   "给定输入 → 预测输出"的前向映射，但缺少"给定目标 → 反推最优输入"的逆向决策能力。
2. **工作流编排已成熟**：ADR-005 阶段 1 的 `WorkflowRunner` + DAG 任务图 + 阶段 2
   的 `SnapshotStore` 已支持复杂任务编排与可复现实验，为 RL 训练管线提供基础。
3. **插件契约已就绪**：`app/contracts/plugin.py` 的 `TaskHandler` 协议 + `Capability`
   声明 + `app/plugins/contract_adapter.py` 的 legacy 适配器已支持插件以任务类型
   注册到工作流。`wm_predict_state` / `rl_act` 任务类型已在契约层预留。
4. **缺乏过程状态建模**：当前系统对"加工过程"的理解是片段化的（单帧预测），没有
   一个统一的"世界模型"来预测"如果执行动作 A，未来 N 步状态会如何演化"。
5. **缺乏闭环反馈**：飞轮（阶段 4）已能收集反馈数据，但反馈只用于重新训练预测模型，
   未用于优化决策策略。RL 闭环缺失。

### p8 目标

- **世界模型插件**：`plugins/world_model/` 实现"过程状态预测器"，输入当前状态 +
  候选动作，输出未来 N 步的状态轨迹（颤振概率 / 刀具磨损 / 表面粗糙度）
- **RL agent 插件**：`plugins/rl_agent/` 实现"决策策略"，输入世界模型预测的状态
  轨迹，输出最优动作（切削参数调整建议）
- **完整闭环工作流**：感知（传感器数据）→ 预测（世界模型）→ 决策（RL）→ 执行
  （CAM 参数生成）→ 反馈（实际加工结果回写飞轮）
- **RL 训练管线**：基于阶段 1 的 `Workflow` + 阶段 2 的 `Snapshot`，支持离线 RL
  （基于历史数据）+ 在线 RL（基于仿真环境）
- **插件化接入**：世界模型与 RL agent 均作为插件实现 `TaskHandler` 协议，不侵入
  核心引擎

## 决策

采用"**双插件 + 闭环工作流模板 + 离线 RL 优先**"方案，世界模型与 RL agent 各自
作为独立插件实现 `TaskHandler` 协议，通过工作流模板组合成完整闭环：

### 1. 世界模型插件（`plugins/world_model/`）

**任务类型**：`wm_predict_state`（已在 `core-contracts-design.md` 预留）

**输入**（TaskContext.input）：
```python
{
    "current_state": {
        "spindle_speed": 8000,           # rpm
        "feed_rate": 1200,               # mm/min
        "depth_of_cut": 0.5,             # mm
        "tool_wear": 0.12,               # mm
        "vibration_rms": 0.8,            # g
        "temperature": 45.0              # °C
    },
    "candidate_action": {
        "spindle_speed_delta": 500,      # rpm 调整量
        "feed_rate_delta": -100          # mm/min 调整量
    },
    "horizon": 10,                       # 预测步数
    "model_uri": "model://world_model/v1.0.0"
}
```

**输出**（TaskResult.output）：
```python
{
    "predicted_trajectory": [
        {
            "step": 0,
            "predicted_state": {...},     # 同 current_state 结构
            "chatter_probability": 0.12,
            "tool_wear_increment": 0.003,
            "surface_roughness": 0.8,     # Ra μm
            "confidence": 0.85
        },
        # ... horizon 个步骤
    ],
    "trajectory_metrics": {
        "mean_chatter_probability": 0.15,
        "max_chatter_probability": 0.28,
        "cumulative_tool_wear": 0.030,
        "final_surface_roughness": 1.2
    },
    "model_info": {
        "world_model_version": "1.0.0",
        "training_data_size": 50000,
        "prediction_horizon": 10,
        "uncertainty_estimate": 0.08
    }
}
```

**实现架构**：
- `WorldModelPlugin`：实现 `TaskHandler` 协议，注册任务类型 `wm_predict_state`
- `WorldModelNet`：基于 LSTM + LTC 混合架构，输入 (state, action) 序列，输出
  未来 state 序列。借鉴 ADR-001 的 LTC 引擎，但增加 action 条件输入
- `TrajectoryPredictor`：调用 `WorldModelNet` 做自回归预测（每步用上一步输出作为
  下一步输入）
- `ModelRegistry`：复用 `app/ai/lnn/inference/registry.py`，世界模型按
  `model://world_model/<version>` 注册

**插件 manifest**：
```yaml
name: world_model
version: 1.0.0
type: plugin
capabilities:
  - name: wm_predict_state
    category: predict
    input_schema: {...}   # JSON Schema
    output_schema: {...}
extension_points:
  - core.task.handler
dependencies:
  - python: ">=3.10"
  - torch: ">=2.0"
  - app_contracts: ">=1.0.0"
```

### 2. RL agent 插件（`plugins/rl_agent/`）

**任务类型**：`rl_act`（已在 `core-contracts-design.md` 预留）

**输入**：
```python
{
    "current_state": {...},              # 同世界模型输入
    "candidate_actions": [               # 候选动作集（离散动作空间）
        {"spindle_speed_delta": 500, "feed_rate_delta": 0},
        {"spindle_speed_delta": -500, "feed_rate_delta": 0},
        {"spindle_speed_delta": 0, "feed_rate_delta": 100},
        # ...
    ],
    "optimization_target": "minimize_chatter",  # 或 maximize_material_removal / balance
    "safety_constraints": {
        "max_chatter_probability": 0.3,
        "max_tool_wear_increment": 0.01,
        "min_surface_quality": 0.8
    },
    "model_uri": "model://rl_agent/v1.0.0"
}
```

**输出**：
```python
{
    "recommended_action": {
        "spindle_speed_delta": -500,
        "feed_rate_delta": 100,
        "reasoning": "降低主轴转速 + 提高进给量可在保持材料去除率的同时降低颤振概率"
    },
    "action_evaluation": [
        {
            "action": {...},
            "expected_return": -0.42,     # RL 价值函数
            "predicted_chatter_prob": 0.18,
            "predicted_tool_wear": 0.008,
            "safety_violation": false,
            "q_value": -0.42
        },
        # ... 每个候选动作的评估
    ],
    "policy_info": {
        "algorithm": "ppo",              # 或 dqn / sac
        "policy_version": "1.0.0",
        "training_episodes": 10000,
        "exploration_rate": 0.1
    }
}
```

**实现架构**：
- `RLAgentPlugin`：实现 `TaskHandler` 协议，注册任务类型 `rl_act`
- `PolicyNetwork`：PPO 策略网络（默认），输入 state，输出 action 概率分布
- `ValueNetwork`：价值网络，评估 state-action 对的回报
- `SafetyShield`：安全约束过滤层，剔除违反 `safety_constraints` 的动作
- `ActionEvaluator`：调用 `WorldModelPlugin` 预测每个候选动作的后果，结合
  `ValueNetwork` 计算 Q 值

### 3. 闭环工作流模板

工作流模板 `closed_loop_machining_optimization`（YAML 投影）：

```yaml
template_id: closed_loop_machining_optimization
version: 1.0.0
name: 闭环加工参数优化
category: optimization
description: |
  感知当前加工状态 → 世界模型预测候选动作后果 → RL agent 选择最优动作 →
  生成新切削参数 → CAM 验证 → 执行 → 反馈回写飞轮
nodes:
  - id: perceive
    task_type: data_ingest
    handler: plugins.sensors.stream_reader
    inputs:
      sensor_source: "${workflow.input.sensor_source}"
  - id: predict
    task_type: wm_predict_state
    handler: plugins.world_model.WorldModelPlugin
    depends_on: [perceive]
    inputs:
      current_state: "${perceive.output.state}"
      candidate_actions: "${workflow.input.candidate_actions}"
      horizon: 10
  - id: decide
    task_type: rl_act
    handler: plugins.rl_agent.RLAgentPlugin
    depends_on: [predict]
    inputs:
      current_state: "${perceive.output.state}"
      candidate_actions: "${workflow.input.candidate_actions}"
      predicted_trajectory: "${predict.output.predicted_trajectory}"
  - id: generate_params
    task_type: cam_generate
    handler: plugins.process_planning.cam_generate
    depends_on: [decide]
    inputs:
      recommended_action: "${decide.output.recommended_action}"
  - id: validate_cam
    task_type: cam_validate
    handler: plugins.process_planning.cam_validate
    depends_on: [generate_params]
  - id: execute
    task_type: job_dispatch
    handler: core.job_dispatcher
    depends_on: [validate_cam]
    inputs:
      gcode: "${generate_params.output.gcode}"
  - id: collect_feedback
    task_type: flywheel_collect
    handler: plugins.data_flywheel.flywheel_collect
    depends_on: [execute]
    inputs:
      actual_result: "${execute.output.result}"
      predicted_result: "${predict.output.predicted_trajectory}"
edges:
  - {from: perceive, to: predict}
  - {from: predict, to: decide}
  - {from: decide, to: generate_params}
  - {from: generate_params, to: validate_cam}
  - {from: validate_cam, to: execute}
  - {from: execute, to: collect_feedback}
```

### 4. RL 训练管线

基于阶段 1 `Workflow` + 阶段 2 `Snapshot`：

```
训练 Workflow:
  collect_episodes → build_replay_buffer → train_policy → evaluate → snapshot

每个 episode:
  1. 从历史数据集采样初始状态 s_0
  2. RL agent 选择动作 a_t（ε-greedy 探索）
  3. 世界模型预测下一状态 s_{t+1}（替代真实环境，离线 RL）
  4. 计算奖励 r_t = -chatter_prob - α·tool_wear + β·material_removal
  5. 存储 (s_t, a_t, r_t, s_{t+1}) 到 replay buffer
  6. 重复直到 episode 结束

训练循环:
  - 从 replay buffer 采样 batch
  - PPO clipped objective 更新策略网络
  - TD 误差更新价值网络
  - 每 1000 步评估一次，满足指标则 snapshot
  - snapshot 记录: policy_weights / value_weights / training_metrics / replay_buffer_stats
```

**奖励函数**（`RewardFunction`）：
```python
def compute_reward(
    predicted_state: dict,
    action: dict,
    safety_violation: bool,
) -> float:
    if safety_violation:
        return -10.0  # 严重惩罚
    chatter_penalty = -predicted_state["chatter_probability"] * 5.0
    wear_penalty = -predicted_state["tool_wear_increment"] * 100.0
    quality_bonus = predicted_state["surface_roughness"] < 1.0 and 0.5 or 0.0
    material_removal = action.get("feed_rate_delta", 0) * 0.001
    return chatter_penalty + wear_penalty + quality_bonus + material_removal
```

### 5. 安全约束

**硬约束**（`SafetyShield` 强制执行，不可被 RL 策略覆盖）：
- 主轴转速不超过机床最大转速（来自 `CuttingConstraintValidator`）
- 进给量不低于最小切屑厚度
- 切深不超过刀具承载力
- 颤振概率 > 0.5 的动作直接剔除
- 刀具磨损增量 > 0.05 mm/步的动作直接剔除

**软约束**（通过奖励函数引导）：
- 优先选择低颤振动作
- 平衡材料去除率与刀具寿命
- 鼓励表面质量优于目标值

### 6. 插件注册与依赖

世界模型与 RL agent 通过 `PluginManifest` 声明依赖：

```yaml
# plugins/world_model/manifest.yaml
dependencies:
  - plugin: lnn_engine         # 依赖 ADR-001 LTC 引擎
    min_version: "1.0.0"
  - contract: task             # 依赖 ADR-005 任务契约
    min_version: "1.0.0"
  - contract: dataset
    min_version: "1.0.0"

# plugins/rl_agent/manifest.yaml
dependencies:
  - plugin: world_model        # RL agent 依赖世界模型做环境模拟
    min_version: "1.0.0"
  - contract: task
    min_version: "1.0.0"
```

`PluginLoader` 在加载 `rl_agent` 前会先校验 `world_model` 已加载且版本兼容。

### 7. 前端契约与 Store

`src/contracts/world_model.ts` + `src/contracts/rl_agent.ts`：
- 任务类型常量：`WM_PREDICT_STATE_TASK_TYPE` / `RL_ACT_TASK_TYPE`
- 输入/输出接口（对应上述 JSON Schema）
- `IWorldModelService` / `IRLAgentService` 接口占位

`src/stores/worldModel.ts` + `src/stores/rlAgent.ts`：
- State：`lastPrediction` / `lastAction` / `trainingStatus` / `policyVersions`
- Actions：`predictState` / `selectAction` / `fetchTrainingStatus` /
  `fetchPolicyVersions` / `triggerTraining`

### 8. REST API

世界模型与 RL agent 复用插件任务执行端点（`POST /api/v1/workflows/runs`），
不单独开新 prefix。同时提供管理端点：

```
GET    /api/v1/world-model/versions              列出世界模型版本
GET    /api/v1/world-model/versions/{version}    查询版本详情
POST   /api/v1/world-model/predict               直接预测（不走工作流）
GET    /api/v1/rl-agent/versions                 列出 RL 策略版本
GET    /api/v1/rl-agent/versions/{version}       查询版本详情
POST   /api/v1/rl-agent/act                      直接决策（不走工作流）
GET    /api/v1/rl-agent/training/status          查询训练状态
POST   /api/v1/rl-agent/training/start           启动训练 Workflow
POST   /api/v1/rl-agent/training/stop            停止训练
```

权限模型：
- `world_model:read` / `world_model:write`
- `rl_agent:read` / `rl_agent:write`

## 实施计划

| # | 交付物 | 文件 | 状态 |
|---|--------|------|------|
| 1 | ADR-017 决策文档 | `docs/adr/ADR-017-世界模型与RL模块.md` | 本文件 |
| 2 | 世界模型插件骨架 | `python/app/plugins/world_model/`（manifest + plugin + net + predictor） | 待办 |
| 3 | RL agent 插件骨架 | `python/app/plugins/rl_agent/`（manifest + plugin + policy + value + safety_shield） | 待办 |
| 4 | 闭环工作流模板 | `python/app/plugins/workflow_templates/closed_loop_machining_optimization.yaml` | 待办 |
| 5 | RL 训练管线 | `python/app/plugins/rl_agent/training/`（reward + replay_buffer + trainer） | 待办 |
| 6 | 后端契约层 | `python/app/contracts/world_model.py` + `python/app/contracts/rl_agent.py` | 待办 |
| 7 | 后端 REST 路由 | `python/app/api/v1/world_model.py` + `python/app/api/v1/rl_agent.py` | 待办 |
| 8 | 后端导出与注册 | `contracts/__init__.py` / `models/__init__.py` / `training_task.py` 权限码 / `main.py` 路由 | 待办 |
| 9 | 前端契约 | `src/contracts/world_model.ts` + `src/contracts/rl_agent.ts` + `index.ts` 导出 + `api.ts` 路径 | 待办 |
| 10 | 前端 Store | `src/stores/worldModel.ts` + `src/stores/rlAgent.ts` | 待办 |

## 风险与缓解

| 风险 | 严重度 | 缓解 |
|------|--------|------|
| 世界模型预测误差累积（自回归多步预测漂移） | 高 | 训练时强制 teacher forcing + scheduled sampling；推理时用 anchor context 修正（借鉴 streaming.py） |
| RL 策略安全风险（推荐危险动作） | 高 | `SafetyShield` 硬约束强制过滤；所有 RL 推荐动作必须经 `CuttingConstraintValidator` 二次校验；CAM 验证层兜底 |
| 训练数据不足（无足够历史加工数据训练世界模型） | 高 | v1 优先用仿真数据预训练（CAD 仿真生成），真实数据微调；列入"远期完善"而非"立即可用" |
| RL 训练耗时长（单次训练 >24h） | 中 | 支持 checkpoint 续训；训练 Workflow 走 BackgroundTasks 异步；snapshot 每 1000 步自动保存 |
| 插件循环依赖（rl_agent 依赖 world_model，world_model 不应反向依赖） | 低 | 契约层无依赖；运行时 `PluginLoader` 强制拓扑排序加载 |
| 工程落地限制（大一独立项目无法独立完成物理加工验证） | 高 | v1 仅做到 CAM 验证层，物理执行需"持证操作员 + 导师签字 + 保险"硬门控（project_memory 已记录） |
| LTC + LSTM 混合架构训练不稳定 | 中 | 学习率 warmup + gradient clipping；batch normalization；训练监控 loss 曲线，发散自动 early stop |
| 奖励函数设计偏差导致策略退化 | 中 | 奖励函数参数化（α/β 系数可调）；训练后人工评估策略质量；提供"策略回滚"能力 |
| 在线 RL 探索阶段损坏工件 | 高 | v1 仅支持离线 RL（基于历史数据 + 仿真环境）；在线 RL 列入 v2 且必须有人工监督 |

## 设计原则

1. **插件化**：世界模型与 RL agent 均为独立插件，不侵入核心引擎
2. **契约优先**：任务类型 `wm_predict_state` / `rl_act` 在契约层预留，插件实现协议
3. **安全第一**：`SafetyShield` 硬约束 + `CuttingConstraintValidator` + CAM 验证三层兜底
4. **离线优先**：v1 仅支持离线 RL（基于历史 + 仿真），在线 RL 列入 v2
5. **可复现**：训练 Workflow + Snapshot 保证策略版本可追溯
6. **工程现实**：物理加工验证需持证操作员 + 导师签字 + 保险，v1 停在 CAM 验证层
7. **学术诚信**：训练随机种子固定 + MLflow 跟踪 + snapshot 持久化

## 与现有模块的关系

```
┌──────────────────────────────────────────────────────────────────┐
│  前端 WorldModelPanel.vue / RLAgentPanel.vue（后续 UI 任务）       │
│       ▲                                                           │
│  ┌────┴────────────────────────────────────────────────────────┐ │
│  │ src/stores/worldModel.ts + src/stores/rlAgent.ts             │ │
│  └────┬────────────────────────────────────────────────────────┘ │
│       │ HTTP                                                      │
│  ┌────▼────────────────────────────────────────────────────────┐ │
│  │ app/api/v1/world_model.py + app/api/v1/rl_agent.py           │ │
│  └────┬────────────────────────────────────────────────────────┘ │
│       │                                                           │
│  ┌────▼────────────────────────────────────────────────────────┐ │
│  │ 插件层（plugins/）                                            │ │
│  │  ┌─────────────────────┐  ┌──────────────────────────────┐  │ │
│  │  │ plugins/world_model/│  │ plugins/rl_agent/             │  │ │
│  │  │  WorldModelPlugin   │◄─┤  RLAgentPlugin                │  │ │
│  │  │  WorldModelNet      │  │  PolicyNetwork + ValueNetwork │  │ │
│  │  │  TrajectoryPredictor│  │  SafetyShield                 │  │ │
│  │  └──────────┬──────────┘  │  training/ (PPO + Replay)     │  │ │
│  │             │              └───────────┬──────────────────┘  │ │
│  └─────────────┼──────────────────────────┼────────────────────┘ │
│                │                          │                       │
│  ┌─────────────▼──────────────────────────▼────────────────────┐ │
│  │ 契约层 app/contracts/                                        │ │
│  │  task.py（TaskHandler 协议）+ world_model.py + rl_agent.py   │ │
│  └─────────────┬──────────────────────────┬────────────────────┘ │
│                │                          │                       │
│  ┌─────────────▼──────────────────────────▼────────────────────┐ │
│  │ 核心引擎                                                     │ │
│  │  WorkflowRunner（阶段 1）+ SnapshotStore（阶段 2）            │ │
│  │  + LNNPredictor（ADR-001）+ CuttingConstraintValidator       │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

## 工程现实约束（来自 project_memory）

依据用户明确指示（2026-07-13）："工程优先于学术价值，按'能否在真实车间跑出零件'评判"：

1. **物理加工硬门控**：v1 仅做到 CAM 验证层，物理机床执行需"持证操作员 + 导师签字 +
   保险"，大一独立项目无法独立完成此环节
2. **G-code 不直接接 CNC 控制器**：生成的 G-code 必须经现有 CAM 软件
   （NX/PowerMill/PyCAM）二次验证后方可执行
3. **mesh → 参数化 CAD 自动转换工业界未解**：系统定位为"工程师助手"而非"全自动
   生产线"
4. **训练数据不足**：v1 优先用 CAD 仿真生成训练数据，真实数据微调；不夸大"立即可用"
5. **RL 安全**：v1 仅离线 RL，在线 RL 列入 v2 且必须有人工监督

## checklist

- [x] ADR-017 决策文档
- [ ] 世界模型插件 `python/app/plugins/world_model/`
- [ ] RL agent 插件 `python/app/plugins/rl_agent/`
- [ ] 闭环工作流模板
- [ ] RL 训练管线（reward + replay_buffer + trainer）
- [ ] 后端契约层 `python/app/contracts/world_model.py` + `rl_agent.py`
- [ ] 后端 REST 路由 `world_model.py` + `rl_agent.py`
- [ ] 后端导出与注册
- [ ] 前端契约 `src/contracts/world_model.ts` + `rl_agent.ts`
- [ ] 前端 Store `src/stores/worldModel.ts` + `rlAgent.ts`
