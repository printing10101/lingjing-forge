# 短程标签可预测性探针：简单条件基线结果

日期：2026-11-12。承接[状态尺度结果](world_model_state_scale_results_20261112.md)写明的下一步（核查短程标签可预测信息与简单条件基线）。结论先行：**切削力与磨损通道存在线性条件信号**——18 特征闭式岭回归在验证组上对持久性的 MSE 改进（R²）切削力 +0.35～+0.55、磨损 +0.13～+0.25，而 187k 参数 LSTM 世界模型在 12 项匹配试跑中连持久性都未超过。短程标签并非无可学信息；模型侧未兑现这部分信号是模型/训练问题，不能继续归咎"标签无信息"。

## 口径

- 探针入口 `engineering/python/scripts/wm_label_predictability_probe.py`，纯 numpy，闭式岭回归，无随机性。
- 特征 18 维：窗口末条状态（8）+ 末条动力学（6）+ 未来动作均值（4）。不使用未来状态。
- 预测目标为未来增量（target − 末条状态），逐通道逐步，与相对增量头同口径。拟合仅用训练折；λ 网格 7 档由训练折内部末 20% 窗口确定性选出后全量重拟合。
- 验证折即三个试跑的留组验证（c1/c4/c6 各 113 窗口），只评估一次。

## 结果（MSE 口径 R² = 1 − MSE_ridge / MSE_persistence；MAE 为参考）

| 组 | 通道 | ridge R² | MAE ridge | MAE drift | MAE persistence |
|---|---|---:|---:|---:|---:|
| c1 | cutting_force_norm | **+0.547** | 0.099972 | 0.143563 | 0.142195 |
| c1 | tool_wear | +0.191 | 0.000170 | 0.000192 | 0.000123 |
| c1 | vibration_rms | +0.275 | 0.082618 | 0.090823 | 0.090415 |
| c4 | cutting_force_norm | +0.338 | 0.143926 | 0.136036 | 0.134891 |
| c4 | tool_wear | +0.131 | 0.000217 | 0.000240 | 0.000184 |
| c4 | vibration_rms | **−0.578** | 0.115446 | 0.084728 | 0.084437 |
| c6 | cutting_force_norm | +0.352 | 0.111173 | 0.118281 | 0.116028 |
| c6 | tool_wear | +0.246 | 0.000228 | 0.000226 | 0.000179 |
| c6 | vibration_rms | +0.376 | 0.141331 | 0.183824 | 0.183789 |

chatter_probability、temperature_over_100c、mrr_norm、confidence 四通道在本数据集中为常量，零方差，无从谈起。

## 解读与负结果保留

1. **切削力信号最实在**：三组 R² 全部为正（+0.34～+0.55）。c1 上岭回归 MAE（0.0999）也优于训练均值常数（0.1131）与持久性（0.1422）；c4/c6 的 MAE 未全面胜出（岭回归最小化 MSE，MAE 口径吃亏），但 MSE 口径一致改进。**对照组**：12 项神经网络试跑切削力 MAE 全部高于训练均值常数。一个 18 特征线性模型能拿到的信号，187k 参数 LSTM 没拿到——问题在模型/训练侧，不是标签侧。
2. **磨损有信号但弱**：R² +0.13～+0.25，说明磨损增量里确实有超出持久性的条件信息，但相对噪声偏弱；MAE 口径三组仍未越过持久性（0.000170～0.000228 vs 0.000123～0.000184），与神经网络试跑的失败一致。磨损要在 MAE 口径越过持久性，靠现有 18 特征线性模型不够。
3. **振动不稳定**：c1/c6 为正，c4 为负（−0.578），跨组不一致，不能作为可靠信号宣称。
4. 对照["能感知历史不代表有效利用"](world_model_normalization_results_20261112.md)的旧结论：现在证明**存在可利用的信号**（至少切削力），下一步应让模型先兑现这个线性可得的信号，再谈更复杂的时序建模。

## 已知局限（不据此升级结论）

- 窗口 stride=25 小于窗口长度，训练折内部存在窗口重叠；绝对分数偏乐观，但三档基线在同一口径下比较，相对结论受影响较小。
- 验证组即 pilots 的选模验证组：这是诊断探针，不是独立终测。
- 岭回归只是线性条件基线；非线性信号是否存在不能由本探针判定。
- 只有一个数据集划分（留组方向由 meta 固定），无跨划分重复。

## 验收

- 新增单测 6 项通过（`test_label_predictability.py`）：线性信号恢复 R²>0.99、纯噪声不误报、常量目标往返、λ 选择确定性、非有限值拒绝、增量形状。
- 结果与数据指纹落库 `research/experiments/results/world_model_label_predictability_20261112.json`。
- 本轮不训练任何神经网络、不改动生产权重与默认版本。

## 复现（仓库根，全新输出路径）

```powershell
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
& 'C:/Users/<user>/AppData/Local/Programs/Python/Python314/python.exe' `
    engineering/python/scripts/wm_label_predictability_probe.py `
    --output research/experiments/results/world_model_label_predictability_new_run.json
```
