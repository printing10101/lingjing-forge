# LNN 权重训练与分发接线方案（①「把真智能装进包里」）

> 定位：把「合成/实测数据 → research 训练 → 权重导出 → 随包分发 → 工程侧加载验证 → 精度基准」全链打通，产出**首个随包分发的真实 LNN 权重**。
> 上位文档：《产品叙事与战略对标-2026-09》W4.1（合成数据先行）/ W4 训练侧消费验证（）/ W9 信任证据工程；《自主化与护城河路线图》P2/P5。
> 盘点日期：2026-09-08。所有路径均已在仓库实测核实。

---

## 1. 现状盘点：三段链路与断点

### 1.1 数据侧（管道已通，缺的是进训练）

| 数据源 | 位置 | 现状 |
|---|---|---|
| 合成数据生成器 | `app/pipelines/synthetic_data_gen.py` | 已落地（W4.1）：参数扫描（rpm×feed×depth，默认 27 组合，上限 200）→ 每样本含参数 + 合成 G 代码 + 体素校验结果 + 切削力（PINN→Kienzle 降级链），经 `DatasetStore.commit_version()` 提交为不可变数据集版本（带 `LineageRecord` 血缘），数据集名 `synthetic_machining_params_v1` |
| 训练数据湖 | `app/training/data_lake.py` → `engineering/python/data/training_data/training_data_YYYYMMDD.jsonl` | 管道可用；现有内容为 feedback_loop E2E 测试数据（`REC-E2E-001` 等），schema：`features{machine_id, tool_id, workpiece_material, spindle_speed, feed_rate, depth_of_cut}` + `labels{first_pass_acceptance, actual_dimensions, surface_roughness}` |
| 科研侧数据集 | `research/datasets/`（force_vibration_567 / measured_stability / piecuch_2025 / uniwear / synthetic_chatter.py）+ Bosch 装载器（两侧各有） | 在库；**从未被工程侧注册表模型消费过** |

### 1.2 训练侧（research/ 齐备，断在导出）

- `research/training/trainer.py`：`LNNTrainer`（train_epoch / validate / fit、早停、MLflow 追踪、设备管理、可复现性种子）。
- `research/training/dataset.py`：`LNNDataset`（`(n_samples, features)` 矩阵 + 标签）、`TrainingDataPreprocessor`、`FeatureExtractor`、`BoschCNCDataset`、`DataAugmentation`。
- `research/models/`：`torch_ltc_model` / `torch_cfc_model` / `torch_hybrid_lnn` / `torch_mamba_lnn`（LNNConfig 统一配置）。
- **已有成功先例（SSM 链，升级④）**：`research/scripts/export_ssm_onnx.py` 把 `TorchMambaLNN` 单步接口 `forward(x, dt烘焙, h)` 导出 ONNX 并用 onnxruntime 做数值验证 → 工程侧 `app/ai/lnn/ssm_inference.py` `SsmOnnxPredictor`（onnxruntime，无 torch）→ `register_ssm_predictor` 挂到 `HybridInferenceEngine`。测试锚点：`research/tests/test_ssm_onnx_export.py`。依赖版本两侧对齐（onnx 1.17.0 + onnxruntime 1.20.1）。
- **缺口**：ONNX 导出脚本只有 SSM 有；LTC/CFC/HybridLNN 无导出路径。

### 1.3 推理侧（app/ai/lnn/ 消费端，三处硬伤）

注册表预定义模型（`inference/_lnn_registry.py`）：

| 模型 | 类型 | 声明路径 | 输入特征 | 输出 |
|---|---|---|---|---|
| cutting_force | CFC | `models/cutting_force.pt` | force_x/y/z, spindle_speed, feed_rate | predicted_cutting_force |
| wear_prediction | LTC | `models/wear_prediction.pt` | vb, time, spindle_speed, feed_rate, depth_of_cut | predicted_wear |
| surface_roughness | HybridLNN | `models/surface_roughness.pt` | roughness_ra, cutting_speed, feed_rate, tool_wear | predicted_surface_roughness |
| temperature | CFC | `models/temperature.pt` | temp_zone1/2, coolant_flow, cutting_time | predicted_temperature |

消费方：`app/services/model_registry_service.py`、`app/plugins/rl_agent/plugin.py`、`app/plugins/world_model/plugin.py`、`app/services/_agent_helpers.py`（均 `LNNModelRegistry()` 不传 model_dir → `models/*.pt` 相对 CWD 解析）。

**硬伤清单（比 PROJECT_OVERVIEW 记载的「权重未分发」更深）**：

| # | 断点 | 位置 | 后果 |
|---|---|---|---|
| H1 | **权重根本无法持久化**：`models/base_lnn.py` 的 `save()/load()` 只存元数据（model_name/input_dim/output_dim/is_trained），不存任何参数；三个子类无 override | `app/ai/lnn/models/base_lnn.py:500-517` | 即使把权重文件放进 models/，加载后权重仍是随机初始化——分发链路本身是断的 |
| H2 | **随机权重静默回退**：`_load_model` 在 `os.path.exists(model_path)` 为假时跳过加载，无任何标记 | `app/ai/lnn/inference/_runtime_registry.py:146-147` | 上层无法区分「训练好的模型」与「随机初始化演示」，违反学术诚信红线（对照 `_lnn_registry.py` S6 注释） |
| H3 | **格式口径分裂**：注册表声明 `.pt`，`load()` 却用 `np.load`（npz） | `_lnn_registry.py` ↔ `base_lnn.py:512` | 即使 torch .pt 存在也加载失败；路径与解析器互相矛盾 |

旁证：`engineering/python/models/lnn/` 只有 2026-05-08 两个 `*_training_summary.json`（当时 cuda 训练跑通过，但权重未落盘；且 cutting_force 分类 F1=0.095——分类口径本身也有问题）；`app/simulation/chatter/predictor.py` 的 `checkpoints/chatter_model.pt` 不存在（该模块回退时有显式 warning，治理是对的）；`app/benchmarks/performance/lnn_inference_bench.py` 用合成随机模型，测性能不测精度；应用内训练 API（`app/api/v1/lnn/_training_executor.py`）需 torch，生产桌面运行时（无 torch）返回 503，且只 log 到 MLflow 不落本地权重。

---

## 2. 目标架构

```
┌ 数据面（工程侧，无 torch）────────────────────────────────┐
│ 合成生成器 ──▶ DatasetStore(不可变版本+血缘) ─┐            │
│ TrainingDataLake(jsonl) ────────────────────┤            │
│ research/datasets 公开数据集 ────────────────┘            │
│        │  WP2: 导出适配器 app/training/exporters.py       │
│        ▼  训练集 (X 按 input_features 顺序, y) + 特征清单   │
├ 训练面（research/，独立 torch 环境）───────────────────────┤
│   WP3: LNNTrainer 训练 → checkpoint.pt                    │
│        → export_lnn_onnx.py 导出 ONNX + onnxruntime 验证   │
│        → npz 权重（NumPy 推理路径）                         │
│        ▼  产物: models/lnn/<model>.npz|.onnx + manifest    │
├ 消费面（工程侧运行时，onnxruntime/NumPy）──────────────────┤
│   WP1: BaseLNNModel save/load 补权重持久化（修 H1）         │
│        注册表路径/格式统一（修 H3）                          │
│        weights_source 显式标记（修 H2）                     │
│   WP4: 启动装载验证 + 精度基准(vs 解析解) + CI 锚点          │
└──────────────────────────────────────────────────────────┘
```

设计原则：
1. **不新开功能线**——全部是对既有模块的修复、导出与接线（呼应「不堆功能、做深信任」）。
2. **复用 SSM 先例**（导出→onnxruntime 验证→注册），不发明第二套机制。
3. **叙事红线**：随机初始化权重必须显式可辨（`weights_source`），任何对外精度宣称必须可溯源到 manifest 中的数据集版本与指标。
4. 工程侧运行时**保持零 torch**；训练只发生在 research/ 独立环境。

---

## 3. 工作包拆解

### WP1 推理侧权重加载修复（前置硬伤，纯工程侧）

**动作**：
1. `app/ai/lnn/models/base_lnn.py`：`save()/load()` 补齐真实参数持久化——npz 中新增各层参数数组（键名规范 `param.<layer_idx>.<name>`）+ `config_json`；子类如持有额外参数，通过 `state_arrays() -> dict[str, np.ndarray]` / `load_state_arrays(dict)` 两个新钩子参与序列化（子类无需 override save/load 本体）。
2. `app/ai/lnn/inference/_lnn_registry.py`：`PREDEFINED_MODELS` 路径统一改为 `models/lnn/<name>.npz`（见 §4 决策 D1），与 `np.load` 解析器一致；`validate_model()` 增加加载后「参数是否为默认初始化」检测（对照新初始化实例的参数逐元素比对）。
3. `app/ai/lnn/inference/_runtime_registry.py`：`_load_model` 加载后设置 `entry.metadata["weights_source"] = "trained" | "random_init"`；`get_model_info`/`list_models`/预测响应透出该字段。**不改变降级行为**（缺文件仍可用），但必须可辨。
4. 消费方冒烟：`model_registry_service`、rl_agent / world_model 插件的模型信息接口带出 `weights_source`（改动面控制在这 4 处调用点）。

**验收**：
- 新增测试：save→load 往返后预测输出逐元素一致（LTC/CFC/HybridLNN 三类）；权重文件缺失时 `weights_source="random_init"`；文件存在但内容为默认初始化时 validate 能识别。
- 既有测试全绿（`& $PY314 -m pytest app/ai/lnn 相关路径`），ruff 0 违规。
- 文档：本文件 §1.3 断点表在 PR 描述引用；`docs/api-reference.md` 若模型信息 API 响应新增字段则同步。

**提交**：`fix(ai): LNN NumPy 模型权重持久化与随机初始化显式标记`

### WP2 训练集导出适配器（工程侧 → research 数据契约）

**动作**：
1. 新模块 `app/training/exporters.py`：
   - `export_registry_training_set(model_name, sources, out_dir)`：从 `DatasetStore`（按数据集名+版本）与 `TrainingDataLake` 拉样本，按注册表 `input_features` 顺序组 X、目标列组 y，落 `out_dir/<model_name>/{X.npy,y.npy,feature_manifest.json}`。
   - `feature_manifest.json`：特征顺序、来源数据集 id/版本哈希、行数、生成参数网格、schema 版本、缺失值策略——训练侧与推理侧共用此清单作为唯一契约。
2. 逐模型「任务↔数据」映射（首版诚实边界）：
   - `cutting_force`：**重定义输入契约为 (spindle_rpm, feed_rate, depth_of_cut, material_encoded)** ← 合成数据集直接可训，目标 = 合力或 Fx/Fy/Fz（原输入含 force_x/y/z 属「用力预测力」，无预测价值，借机修正注册表声明并同步消费方）。材料编码表入 manifest。
   - `wear_prediction`：PHM2010 / `research/datasets/uniwear`（vb 标签现成）——由 research 侧训练脚本直接读，不强制过工程侧导出器（跨环境路径通过 manifest 描述）。
   - `surface_roughness`：合成数据无粗糙度标签，TrainingDataLake 现有样本为测试数据——**首版标 `data_insufficient`，不出权重**（叙事红线：不硬训）。
   - `temperature`：无数据源——同上标 `data_insufficient`。
3. CLI：`engineering/python/scripts/export_training_set.py --dataset synthetic_machining_params_v1 --model cutting_force --out <dir>`；触发一次合成生成器产出 ≥500 样本（放大网格，遵守 `MAX_COMBINATIONS` 上限调参）作为首训数据量。

**验收**：
- 导出器单测 ≥15 用例：特征顺序稳定、缺列报错、空数据集报错、manifest 与数据一致性、血缘字段完整、幂等（同版本重复导出逐字节一致）。
- 端到端冒烟：真实跑一次生成器 → 导出，产物规模与分布写进 manifest。
- `docs/development/` 增补本文件 §3 WP2 执行记录（数据规模/分布直方图数据）。

**提交**：`feat(training): 注册表模型训练集导出适配器`

### WP3 research 训练 + ONNX/npz 双格式导出

**动作**：
1. `research/scripts/export_lnn_onnx.py`（仿 `export_ssm_onnx.py`）：torch LTC/CFC/Hybrid → ONNX，导出接口与工程侧消费方式对齐（表格型预测 `forward(x) -> y`；时序任务走窗口或单步接口由 D2 决策定），onnxruntime 逐元素验证（阈值与 SSM 导出测试一致）。
2. `research/scripts/train_registry_models.py`：读 WP2 产物（或 research 本地数据集）→ `LNNTrainer` 训练（早停 + MLflow）→ 产出四件套：`checkpoint.pt`（复现用，LFS）、`<name>.npz`（NumPy 推理路径，参数键与 WP1 钩子对齐）、`<name>.onnx`（新链路）、`manifest.json`（模型版本/数据集血缘哈希/特征顺序/train-val 指标/训练环境）。
3. torch→npz 参数导出：torch state_dict 按层名映射到 NumPy 参数数组（LTC/CFC 的 torch 实现与 NumPy 实现参数命名对照表写入导出脚本，带数值一致性测试——torch 前向 vs NumPy 前向逐元素误差 < 1e-6，参照既有「LNN NumPy 双副本 parity 锁」测试模式）。
4. 产物落仓库根 `models/lnn/`（Git LFS，PROJECT_OVERVIEW 已预留该口径），manifest 同目录非 LFS。

**验收**：
- `cd research && pytest tests/` 全绿，新增：导出数值一致性（torch vs onnxruntime vs numpy 三方 parity）、manifest 完整性、小数据训练收敛冒烟（loss 下降阈值）。
- cutting_force 首训完成：验证集 R²/MAE 报告落 `docs/` 汇总表，与 Kienzle 解析解在同测试集对照（WP4 消费）。

**提交**：`feat(research): LTC/CFC 训练导出链与注册表模型首训`

### WP4 工程侧消费验证 + 精度基准 + CI 锚点

**动作**：
1. 启动装载验证：服务启动时对注册表模型跑 `validate_model`，结果（含 weights_source）进启动报告与 `/api/health` 扩展字段；缺失权重仅告警不阻断（与既有条件路由同风格）。
2. 精度基准新模块 `app/benchmarks/accuracy/lnn_accuracy_bench.py`：固定测试集上「LNN 预测 vs Kienzle/T-lusty 解析解 vs 真值」三方对照表（MAE/MAPE/最大误差），产物 JSON + 成文段落并入《AI引擎能力验证报告》与性能白皮书口径（对齐 W9.3 证据链格式）。
3. CI 锚点：
   - 工程侧：有分发权重时加载+冒烟预测测试；无权重时 `random_init` 标记测试（两种场景都覆盖，CI 环境按权重是否随仓决定走哪条）。
   - research 侧（独立 job，装 research/requirements.txt）：三方 parity + 导出脚本 smoke（微型网络），防导出链腐化。
4. 文档同步：PROJECT_OVERVIEW §2「LNN 模型权重未随仓库分发」口径更新；`models/README.md` 写明分发清单、版本、复现命令。

**验收**：
- 精度基准可复跑且数字与 manifest 一致；`check_api_docs_sync` 通过；性能白皮书增补精度章节。
- 全量回归：`& $PY314 -m pytest -m unit` 无新增失败；`cd research && pytest tests/` 全绿。

**提交**：`feat(benchmarks): LNN 精度基准与权重分发验证锚点`

---

## 4. 关键决策点（实施前拍板）

| # | 决策 | 建议 | 理由 |
|---|---|---|---|
| D1 | 权重分发格式：npz / ONNX / 双轨 | **双轨过渡，ONNX 收敛**：注册表 4 模型首版补 npz 真权重（改动最小、NumPy 路径零新依赖）；新链路（颤振时序等）一律走 ONNX（SSM 模式）；待 NumPy 前向 parity 锁稳定后评估全面 ONNX 化 | npz 修的是既有断链（H1/H3）；ONNX 是既定技术方向（工程侧已 onnxruntime-only），但注册表模型全面切 ONNX 需动 HybridInferenceEngine 注册路径，不宜与修断链混在一个 PR |
| D2 | 注册表模型接口口径：表格单步 vs 时序窗口 | 首版按注册表现状（表格单步）；时序化留给颤振链 | 合成数据是静态参数→结果对；改窗口口径会同时推翻注册表 input_features 与消费方，超出本方案边界 |
| D3 | cutting_force 输入契约修正 | 采纳 WP2.2 的重定义（参数→力） | 原契约「用力分量预测力」无信息增量；修正需同步 `_lnn_registry.py` 声明与消费方（world_model/rl_agent 的调用参数），作为 breaking change 在 CHANGELOG 标注 |
| D4 | 数据不足模型的处置（surface_roughness / temperature） | manifest 标 `data_insufficient`，注册表保留但响应明确「未训练」 | 叙事红线 3：叙事不跑在能力前面 |
| D5 | 权重分发通道：Git LFS 随仓 / 发布包附带 / 首启下载 | **Git LFS 随仓**（PROJECT_OVERVIEW 既有口径） | 首启下载违背「数据不出厂」叙事；发布包附带增加打包链复杂度，LFS 已在用 |
| D6 | 随机权重治理强度 | 只加标记 + 健康检查透出，不做硬门禁 | 保住「模型缺失可降级」的既有可用性契约；等精度基准出来后再评估是否升级为门禁 |

---

## 5. 风险与纪律

| 风险 | 对策 |
|---|---|
| torch↔NumPy 参数映射出错（静默换了一套权重） | 三方 parity 测试（torch / onnxruntime / numpy 同输入逐元素 <1e-6）进 research CI；映射对照表成文 |
| 合成数据分布偏差被误当模型能力 | manifest 强制记录数据来源与网格；精度报告分「合成测试集」与「公开数据集」两栏，不混报（sim2real gap 诚实表述，对齐 W4 风险条目） |
| cutting_force 契约修正破坏消费方 | 全仓 grep 调用点（已知 world_model/rl_agent/_agent_helpers）；golden/契约测试先行；CHANGELOG breaking 标注 |
| LFS 权重进仓后 CI 拉取负担 | 权重单体控制在 MB 级（LTC/CFC 小网络）；CI 按需 `git lfs pull` 指定路径 |
| 训练结果不可复现 | LNNTrainer 既有 reproducibility.set_global_seed + MLflow 参数记录；manifest 记录种子与环境 |
| 环境坑（AGENTS.md）：科研侧独立环境、PYTHONPATH、Python 3.14 口径 | 工程侧测试 `& $PY314 -m pytest`；research 侧 `cd research && pytest tests/`；训练在 research 独立 torch 环境执行 |

---

## 6. 度量（对接叙事 KPI）

| 叙事主张 | 指标 | 现状 → 本方案完成后 |
|---|---|---|
| 越用越懂你（W4） | 随包分发的训练模型数 / 首版可证迭代基线 | 0 → ≥2（cutting_force + wear_prediction），且后续飞轮版本有对照曲线的起点 |
| 敢上机（W1/W9） | LNN vs 解析解精度对照表 | 不存在 → 成文可复现 |
| 学术诚信（S6 红线） | 随机权重可辨识率 | 静默 → 100%（weights_source 全链路透出） |
| 证据链（W9） | manifest 覆盖（数据血缘/特征顺序/指标） | 无 → 每个分发权重一份 |

**建议实施顺序**：WP1（修断链，1 个会话）→ WP2（导出适配器 + 首批数据）→ WP3（训练 + 导出，research 环境）→ WP4（消费验证 + 基准 + CI）。WP1/WP2 纯工程侧、不依赖 torch 环境，可立即开始。

---

## 7. 执行记录（2026-09-08/09，四包全部完成）

### 7.1 交付物

| 工作包 | 交付 | 验证 |
|---|---|---|
| WP1 | `base_lnn.py` v2 npz 持久化（`state_arrays`/`load_state_arrays` 钩子 + 三子类实现）；注册表路径统一 `models/lnn/*.npz`；`weights_source` 三态记账（ModelRegistry + LNNModelRegistry `load_model()`）；`OnnxLNNModel` 适配器（按 `.onnx` 扩展名分发）；`model_registry_service` 锚定随包权重目录（CWD 无关）；`/api/v1/health/system` 新增 `lnn_weights` 组件 | 新增 47 个单测全绿；既有 LNN 套件 148 用例无回归 |
| WP2 | `app/training/exporters.py`（DatasetStore→X/y + feature_manifest 契约，`DATA_INSUFFICIENT_MODELS` 诚实边界）+ `scripts/export_training_set.py` CLI；`app/training/weight_bridge.py`（标准化折叠/镜像前向/回归指标，纯 NumPy） | 9+9 单测全绿 |
| WP3 | `scripts/train_registry_model.py`（镜像 MLP 训练 → 标准化折叠 → parity 门禁 → v2 npz + manifest → 注册表终验） | **实跑产物**：`models/lnn/cutting_force.npz`（合成数据 100 样本，test R²=0.998，parity 7.8e-08）+ `models/lnn/wear_prediction.npz`（uniwear 实测 39,904 行，test R²=0.823，parity 2.0e-06）+ 两份 manifest |
| WP4 | `app/benchmarks/accuracy/lnn_accuracy_bench.py`（LNN vs Kienzle 对照，插值网格 R²=0.986）；健康接口透出；`PROJECT_OVERVIEW.md`/`models/README.md` 口径同步 | 基准 5 单测全绿；全量 unit 回归通过 |

### 7.2 与原方案的偏差（及理由）

1. **训练脚本落位 `engineering/python/scripts/` 而非 `research/scripts/`**：实测 PY314（工程侧测试环境）自带 torch 2.13 CPU；训练镜像的是工程侧 NumPy 服务架构（见 3），放工程侧使 parity 校验零跨仓依赖，research/ 侧零改动。
2. **ONNX 导出暂缓**：环境无 `onnx` 包且 pip 安装遇网络 SSL 故障。按 D1 既定顺序回退——注册表模型首发 v2 npz（生产 NumPy 路径本来就是主消费路径）；`OnnxLNNModel` 适配器与分发逻辑已就位，onnx 包可用后仅需在训练脚本补导出分支。
3. **wear_prediction 训练落地**：原方案 WP2.2 假定 wear 走 research 侧数据集、本轮不可训；实测 `research/datasets/uniwear/uniwear/uniwear.csv`（39,904 行，含 tool_wear 标签）在库，遂纳入。契约随之修正（见 7.3.2）。

### 7.3 实施中额外发现并修复的真实缺陷

1. **`DatasetStore.commit_version` 血缘外键必炸**：接受 `LineageRecord` 却从不持久化本体，`dataset_versions.lineage_record_id` 的 FK 约束使带血缘的提交 100% 失败（W4.1 合成数据管线此前从未真正跑通过入库）。已修：血缘先经 `LineageStore.record()` 落库再挂版本。
2. **`ModelRegistry._load_model` 重复 kwargs**：hyperparams 含 `input_dim`/`output_dim` 时 `TypeError`（任何带维度配置的注册模型都无法加载）。已修：解包前剔除显式实参键。
3. **`LNNModelRegistry._register_predefined_models` 类级路径污染**：直接改写共享 `PREDEFINED_MODELS` 的 `model_path`，第二个带 `model_dir` 的实例会把路径永久写偏。已修：`ModelInfo` 写时拷贝。

### 7.4 已知边界与遗留

- **合成数据 voxel 100% failed**：`build_synth_gcode` 模板每层以 `G00` 快速下切到切削深度，体素校验必判扎刀。对切削力回归无影响（标签来自 Kienzle），但若未来要训「voxel 通过分类器」，模板需改为 G01 下切或分级注入缺陷。
- **cutting_force/ wear 输入契约为 breaking 变更**（v2.0.0）：消费方需按新 `input_features` 组输入向量；本轮已核查现有消费方（rl_agent/world_model/_agent_helpers）仅读 `model_path` 不组向量，无破坏面。
- **surface_roughness / temperature**：维持 `random_init` 标记，等真实数据源。
- **ONNX 工件**：待 `onnx` 包可装后补导出分支（适配器/分发/验证框架已备好）。
- **精度口径**：当前 LNN 为解析解响应面（链路保真度 R²=0.986），**不构成**对 Kienzle 的精度优势宣称——优势宣称待真实切削数据回灌（W4.3）与颤振校准（W9.3）。
