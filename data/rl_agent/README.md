# RL 训练轨迹数据集（trajectories.jsonl）

供 `POST /api/v1/rl-agent/training/start` 使用。训练循环基于
`ReplayOfflineEnvironment`（`app/plugins/rl_agent/training/environment.py`）
回放**真实历史轨迹**，缺失数据集时接口会诚实拒绝（学术诚信要求，禁止合成数据）。

## 放置位置与启用方式

- 默认路径：`data/rl_agent/trajectories.jsonl`（本目录）
- 或通过环境变量指定任意路径：`RL_AGENT_TRAINING_DATA=/path/to/trajectories.jsonl`

## 文件格式（JSONL，每行一条 JSON）

```json
{"state": [...], "action": [...], "next_state": [...], "episode": 0}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `state` | | 状态向量，8 维，字段顺序与 `StateField.all()` 对齐：spindle_speed, feed_rate, depth_of_cut, width_of_cut, tool_wear, vibration_rms, temperature, chatter_probability |
| `action` | | 动作向量，4 维（相对调整量，[-1,1]），顺序与 `ActionField.all()` 对齐：spindle_speed_delta, feed_rate_delta, depth_of_cut_delta, width_of_cut_delta |
| `next_state` | | 该转移后的状态向量（同 `state` 布局） |
| `episode` | 可选 | 轨迹分组键；同一 `episode` 值的连续行视为同一条轨迹 |

未提供 `episode` 时按连续性分组：相邻两行的 `next_state[i] ≈ state[i+1]`
（atol=1e-6）视为同一条轨迹，否则切分。

## 奖励计算

奖励由 `RewardFunction` 从状态转移中提取：

- `chatter_probability`：取 `next_state` 中的颤振概率
- `tool_wear_increment`：`next_state.tool_wear - state.tool_wear`（非负）
- `surface_roughness`：数据集不提供时按 +inf 处理（质量奖励记 0，不做补造）

## 终止条件（单条轨迹内）

- 轨迹耗尽
- `tool_wear ≥ 0.3 mm`
- `chatter_probability ≥ 0.95`
- 单 episode 步数超过 50（`max_episode_steps`）

## 数据来源建议

从 DNC/MTConnect 采集的真实加工记录中导出（状态字段与世界模型输入对齐）。
数据质量要求：每条转移必须是真实观测，禁止插值造数、禁止用随机过程合成。
