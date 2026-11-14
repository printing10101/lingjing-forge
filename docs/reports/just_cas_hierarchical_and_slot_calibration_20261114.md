# 分层参数响应模型 + 槽铣力系数标定（JUST_CAS）

日期：2026-11-14。承接[参数响应检验](just_cas_param_response_20261114.md)。三件事：分层模型落地、槽铣标定、以及对上轮一处结论的**更正**。

## 结论

1. **同工况内参数响应可建模**：对数幂律 `log F = a_condition + b_ap·log ap + b_ae·log ae`，逐工况留一组合（100 折，合并口径）力学 R² **+0.992**（MAE 1.66 N），噪声 +0.741。
2. **新工况不可由截距回退解决**：留一工况下力 R² +0.179、电流 −0.168、噪声 +0.049；分层（组内识别斜率 + 截距回退）与普通池化结果**完全一致**——瓶颈是未知工况的截距，不是斜率。
3. **更正上轮结论**：电流 R² 0.998 **不是** MRR 物理关系的复现。实测 G1 组内 ap/ae 从 (2,2) 降到 (1.2,1.2)，力从 77.6 N 降到 44.8 N（−42%），电流仅 10.63→10.62 A（±0.5%）。0.998 全部来自工况间差异（G2 转速 10000 rpm 时空载电流 7.78 A，其余工况 10.6 A）。**电流不能被当作参数响应的证据**。

## A. 分层模型

形式：`log y = a_condition + b_ap·log ap + b_ae·log ae`。斜率由含工况 dummy 的设计**在组内识别**（组间差异被 dummy 吸收）；截距逐工况计算，工况未知时回退总体截距 `a_pop`。

| 响应 | 组内插值 R²（100 折，合并） | 新工况·分层 | 新工况·池化 | 组内 MAE |
|---|---:|---:|---:|---:|
| 合力 RMS | **+0.992** | +0.179 | +0.179 | 1.66 N |
| 电流 RMS | **≈0**（无响应） | −0.168 | −0.168 | — |
| 噪声 RMS | +0.741 | +0.049 | +0.049 | 0.048 |

最终系数（全量拟合）：

| 响应 | b_ap | b_ae | 截距 G1 / G2 / G3 / G4 | 总体截距 |
|---|---:|---:|---|---:|
| 合力 | 0.652 | 0.498 | 3.617 / 3.249 / 4.002 / 4.010 | 3.719 |
| 噪声 | 0.761 | 0.136 | −1.606 / −2.070 / −1.221 / −1.172 | −1.517 |

解读：截距排序 = 同 ap/ae 下的力水平——CK45 钢 ≈ 高进给(G3) > 铝常规(G1) > 铝高转速(G2)。钢比铝高约 `exp(4.010−3.617) ≈ 1.48` 倍。

**分解"工况偏移 vs 参数响应"**（留组合口径，合并 R²）：

| 响应 | 仅 ap/ae（原始线性） | ap/ae + 进给/转速/材料 |
|---|---:|---:|
| 合力 | +0.198 | +0.957 |
| 电流 | −0.000 | +0.998 |
| 噪声 | +0.088 | +0.737 |

差值几乎全部是工况偏移：同一 (ap,ae) 组合在不同工况下力水平不同，跨工况比较必须带齐工况特征。

**可用性**：`predict(model, ap=…, ae=…, group="G1")` 单点预测已可用（`wm_param_predictor.py`），未知 `group` 时回退总体截距并按上述 R² 预期上限工作——**新工况必须先补 20+ 条该工况样本再预测**，否则误差量级与均值基线同级。

## B. 槽铣切削力系数（15 段）

模型 `F_mean = kc·ap·fz + ke·ap`（ap=1 mm、fz 七/八档、每档 1 段）：

| 材料 | kc (N/mm²) | ke (N/mm) | R² | RMSE |
|---|---:|---:|---:|---:|
| 7030 铝 | 1044.1 | 40.1 | +0.991 | 2.0 N |
| CK45 钢 | 1824.4 | 64.3 | +0.992 | 1.9 N |

钢/铝比 1.75，与"钢更难切"的常识方向一致。**注意**：此处 kc 是"合力均值/（ap·fz）"的等效系数，与手册 `kc1.1`（切向、特定切屑厚度基准）不同口径，不能直接对比文献数值，也不宣称吻合。

与侧铣指数的一致性：侧铣幂律 b_ap=0.653、b_ae=0.498，槽铣线性假设对应 b_ap≈1。差异来自：(a) 侧铣为半浸入、ae 变化跨越空切区；(b) 侧铣指标是整段合力 RMS（含空切段），槽铣是稳定段合合力均值。**两者不是同一物理量，不做等效宣称**，仅记录为形式差异。

## 限制

- 同一把刀具贯穿全部试验（论文自述），后期样本可能含磨损漂移；未按时间顺序校正。
- 工况仅 4 个、每因素 2 水平，任何"外推到新水平"的结论不可辨识。
- 槽铣每档仅 1 段，无重复，无法估计批内方差。
- 预测未做不确定性估计（未给置信区间）。

## 验收

- `wm_param_predictor.py` + `wm_slot_force_calibration.py`，单测 6 项通过（`test_param_predictor_slot.py`）：指数/截距恢复、预测回退与校验、组内 100 折确定性、槽铣编号映射与边界、Kienzle 系数恢复、侧铣指数恢复。
- 结果：`research/experiments/results/just_cas_param_predictor_20261114.json`、`just_cas_slot_calibration_20261114.json`。
- 修正过一处实现缺陷：`ridge_fit` 权重在标准化空间，斜率还原需除以 `x_scale`（初版漏做，导致指数被压缩到 0.16）。

## 复现

```powershell
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
& 'C:/Users/<user>/AppData/Local/Programs/Python/Python314/python.exe' `
    engineering/python/scripts/wm_param_predictor.py `
    --samples research/experiments/results/just_cas_param_response_20261114.json `
    --out research/experiments/results/just_cas_param_predictor_new_run.json
& 'C:/Users/<user>/AppData/Local/Programs/Python/Python314/python.exe' `
    engineering/python/scripts/wm_slot_force_calibration.py `
    --dir data/external/just_cas `
    --out research/experiments/results/just_cas_slot_calibration_new_run.json `
    --samples research/experiments/results/just_cas_param_response_20261114.json
```
