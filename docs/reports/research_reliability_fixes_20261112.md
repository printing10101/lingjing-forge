# 科研端可靠性修复与复算记录

日期：2026-11-12。范围：世界模型直接预测权重接入、稳定标签方向、交叉验证隔离。未新增实切实验，不据此升级论文贡献或模型精度承诺。

## 1. 世界模型：直接服务加载训练权重

原问题：工作流插件已有权重解析，`WorldModelService._resolve_weights_path` 却固定返回 `None`。界面直接预测与预演卡因此可能使用随机初始化网络。

本次行为：

- 服务复用 `resolve_world_model_weights_path` 的版本路径约定，缺失、损坏、结构不匹配的权重报错；只有成功加载的预测器进入缓存。
- 读取 checkpoint 的 `model_config` 后再构造网络，恢复隐藏维度、融合模式和增量预测等训练配置，避免用环境默认值误配权重。
- 融合模型要求实际 `unified_state`。原始八字段与几何融合输入不能互相替代；缺几何时返回明确错误，不构造占位几何。
- 历史 `FusionWorldModelTrainer` 的八列输出依照 `machining_v1` 解释：颤振、磨损、Ra、振动、温度/100、力归一化、MRR归一化、置信度。旧服务曾按 `StateField` 将这些列错读成转速等量，现已区分。
- 响应增加 `model_info.weights_source`。直接预测成功时为 `checkpoint`；前端新请求失败后清除旧预测结果。
- 带 `state_feedback=true` 的权重必须取得上一时刻完整训练状态，否则拒绝推理。

权重根目录优先使用 `WORLD_MODEL_MODELS_DIR`（解析器初始化时读取），其次检查启动目录 `data/models` 和仓库根 `data/models`。版本映射为 `world_model/<version>.pt`。

首轮修复时，八字段界面尚未新增几何采集能力，只能提示输入不足。同日续作已接入已审核特征任务选择、尺度确认、几何摘要及服务端重新组装，见[界面输入验收](world_model_ui_inputs_20261112.md)。当前仍是单状态快照，未自动取得完整加工历史，也未覆盖任意零件。

### 融合请求格式

`POST /api/v1/world-model/predict` 的请求仍使用既有字段：

```json
{
  "model_uri": "model://world_model/1.1.0-real",
  "horizon": 3,
  "current_state": {},
  "candidate_action": {
    "spindle_speed_delta": 0,
    "feed_rate_delta": 0,
    "depth_of_cut_delta": 0,
    "width_of_cut_delta": 0
  },
  "unified_state": {
    "geometry": {
      "bbox_dimensions": [60, 60, 10],
      "feature_vector": [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
      "symmetry_score": 0.5,
      "complexity_score": 0.2
    },
    "dynamics": {
      "spindle_speed": 10400,
      "feed_rate": 1555,
      "depth_of_cut": 0.3,
      "tool_wear": 0.1,
      "vibration_rms": 0.4,
      "temperature": 25
    }
  }
}
```

上述数值只用于解释格式，实际请求必须提供对应来源数据，不能将示例作为实际零件。状态反馈版本还需在 `current_state` 提供以下八个训练列名及相应值：`chatter_probability`、`tool_wear`、`surface_roughness`、`vibration_rms`、`temperature_over_100c`、`cutting_force_norm`、`mrr_norm`、`confidence`。归一化通道保持原单位约定；尤其真实PHM微调的力通道不能直接解释为牛顿。

实际本机 checkpoint `1.1.0-real` 和 `2.2.1-delta-real-sf` 已通过服务加载及三步预测检查，均返回 `checkpoint`。检查只输入验证数据末帧（T=1），不等同于T=20历史序列的精度复现。记录：本地 `reports/research_world_model_smoke_20261112.json`。

## 2. 实测稳定性：统一判定并保留历史

`stability_rule.classify_stability` 被共享评估器及独立7点入口共同使用：`ap < a_lim` 为稳定，等号保守记为非稳定；拒绝非有限数与无效负值。独立入口保存完整浮点边界后计算分类，不再先舍入；`--output` 必须指定全新路径，拒绝覆盖历史结果。

复算产物：`research/experiments/results/real_points_validation_corrected_20261112.json`。旧JSON不改动，旁置 `real_points_validation_results.SUPERSEDED.md`。

| 模型 | Accuracy | Balanced Accuracy | MCC |
|---|---:|---:|---:|
| 默认 Tlusty | 0.428571 | 0.600000 | 0.258199 |
| 实测模态 Tlusty | 0.571429 | 0.400000 | -0.258199 |
| DL-LNN（合成训练） | 0.428571 | 0.600000 | 0.258199 |

数据仅7点（2稳、5失稳）。全判失稳的准确率为5/7，当前结果未建立预测优势。C模型在本次环境重训，不能将其新旧差异全归因于标签修复；未报告进给等假设仍存在。撤回旧报告“低分重现了文献机制”的解释。

复现（PowerShell，工作目录 `research/`）：

```powershell
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
$env:PYTHONIOENCODING='utf-8'
$env:OMP_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
& '.\.venv\Scripts\python.exe' -u experiments/real_validation/run_real_points_validation.py --output experiments/results/real_points_validation_new_run.json
```

## 3. 交叉验证：测试折不再参与早停

原函数把外层测试折前200行当作早停验证集，随后在整个测试折评分。现仅从外层2400行训练折中，以固定种子划出200行内层验证，其余2200行训练；外层600行测试只在训练完成后评分一次。隔离测试以可辨认的行ID检查训练器实际收到的数据，防止仅修分割函数、调用处仍泄漏。

当前五折复算（原3000行合成回归任务，保持原轮数和断言门槛）：

| 模型 | 平均 R² | 标准差 |
|---|---:|---:|
| CFC | 0.2801 | 0.0505 |
| LTC | 0.0251 | 0.0010 |
| HybridLNN | 0.2081 | 0.0096 |

这些分数仅用于合成任务的训练与隔离检查，不能宣称真实加工预测能力。未降低原测试断言。运行日志和XML：本地 `reports/research_cv_isolation_20261112.log`、`.xml`。

## 4. 验收范围

- 工程侧环境检查通过（系统Python3.14.4，OCP及关键模块可加载）。
- 世界模型插件、服务、契约及预演API回归：316通过、1跳过；新增服务测试先在修复前复现4项失败，修复后通过。
- 前端旧结果清理回归：1通过；类型检查通过。
- 新增稳定判定模块在独立覆盖率探针中行覆盖率100%（9条可执行语句）；这是单模块覆盖率，不是全仓覆盖率。科研环境没有pytest-cov，未为测覆盖率安装新依赖。
- API文档同步门禁通过，无代码新增路由漏文档；存量文档多余路由属于工具报告的非阻断项。
- 科研默认全量回归：479通过、14跳过、7项慢速benchmark按配置未运行，耗时403.68秒；本地记录 `reports/research_regression_20261112.xml`。

本轮没有验证真实车间控制效果，也没有建立新的论文贡献。`reports/` 是仓库约定的本地运行产物目录，受 `.gitignore` 排除；上述小型修正结果JSON及本说明可随源码保留。
