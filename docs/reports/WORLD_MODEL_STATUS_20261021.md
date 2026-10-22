# 世界模型通电状态台账（2026-10-21 收束）

**状态口径**：本文件是「灵境制造 · 世界模型线」的唯一事实源。
`docs/reports/PROJECT_STATUS.md`（2026-07-09）为旧版全局状态，**未覆盖本次工作**，暂不改动（GBK 编码 + 内容过期）。

**一句话状态**：L1 动力学世界模型已从「代码完成、随机权重」推进到「训练→落盘→加载→插件预测→真实数据校准」全链路可复现；生产默认 `1.1.0-real`，慢变量变体 `2.2.1-delta-real-sf` 留档待用。

---

## 1. 环境台账

| 项 | 值 | 备注 |
|---|---|---|
| 训练环境 | conda env `lingjing-wm` / Python 3.12 | `<用户主目录>\miniconda3\envs\lingjing-wm` |
| torch | **2.7.1+cu126**，`cuda=True` | RTX 3080 Laptop（16GB 可用），驱动 617.42 |
| 关键依赖 | numpy 2.2.6 / pydantic 2.14 / matplotlib / trimesh / rtree / scipy / pytest 8.3.4 | 未装完整 `requirements.txt`（依赖树含 opentelemetry/chromadb，世界模型不需要） |
| MLflow | **2.16.0**（对齐 `research/requirements.txt` pin）+ **文件后端** `data/mlruns` | 3.17 会弃用文件后端且缺 `mlflow.genai`；2.16 + 新 sqlalchemy 的 sqlite 后端也不可用 |
| 官方验收 | `scripts/verify_torch_ready.py` → 55 passed / 1 skipped / 0 failed | ADR-020 L4 阻塞解除 |
| torch wheel | `D:\灵境制造\data\torch-2.7.1+cu126-cp312-cp312-win_amd64.whl`（2.59GB） | 官方源约 2.0MB/s；阿里云镜像仅 0.1MB/s |

---

## 2. 代码变更台账（9 改 / 8 新脚本 / 4 新测试，均未提交）

### 2.1 修改（git 工作区）

| 文件 | 改动 | 验证 |
|---|---|---|
| `app/plugins/world_model/predictor.py` | **P0 修复**：`load_state_dict` 原在 `raise` 之后（死代码）→ 移入正常路径；兼容嵌套 checkpoint dict；新增 `weights_source` / `_weights_source` | `test_predictor_weight_loading.py` 4 条 + e2e |
| `app/plugins/world_model/plugin.py` | 输出 metadata 增加 `weights_source`（可判定 `checkpoint` / `random_init`） | `wm_plugin_e2e.py` 两轮通过 |
| `app/plugins/world_model/net.py` | 新增 `delta_head`（Δ 状态预测，`--delta-prediction`）与 `forward(initial_state=)` 状态反馈入口 | `test_delta_prediction.py` 4 条 |
| `app/plugins/world_model/training/fusion_dataset.py` | ADR-023：可选第 5 元 `initial_state`（全量/全缺一致性校验）；collate 兼容 4/5 元组 | `test_state_feedback_contract.py` 6 条 |
| `app/plugins/world_model/training/fusion_trainer.py` | 按维 `loss_weights`；4/5 元 batch 解包；`initial_state` 透传 forward；checkpoint 记 `state_feedback` | 上述测试 + 训练报告 |
| `app/plugins/world_model/training/weights_resolver.py` | 默认路径候选链（显式 → `WORLD_MODEL_MODELS_DIR` → cwd/data/models → 仓库根 data/models） | `test_weights_resolver_fallback.py` 6 条 |
| `app/simulation/chatter/stability.py` | **P1 修复**：Tlusty 只取正实部 → 改用负实部区间；扫描中心由 `natural_freq`(100Hz) 改为 `sqrt(k/m)` 实际共振(318Hz) | `test_stability.py` 24 passed；vmc_850 d10 30.59→10.5mm |
| `app/ai/lnn/_research_bridge.py` | MLflow 桥接函数名对齐（`mlflow_start_run` → research 侧 `start_run`），兼容两种命名 | run `548363e7…` FINISHED |
| `scripts/verify_torch_ready.py` | 测试清单存在性过滤（`test_lnn_trainer.py` 已因重构不存在），缺失时告警不失败 | 退出码 0 |

### 2.2 新增脚本（`engineering/python/scripts/`）

| 脚本 | 用途 |
|---|---|
| `build_wm_trajectory_dataset.py` | 物理合成轨迹数据集（Tlusty + Kienzle + 经验磨损/Ra），带 `initial_state` |
| `build_wm_real_dataset.py` | PHM2010 真实数据 → 窗口样本；支持任意 train/val 实验组组合（交叉验证基础） |
| `train_world_model.py` | 训练 CLI：npz → Dataset → `WorldModelNet` → trainer → checkpoint + MLflow |
| `wm_eval_real_wear.py` | 评估：模型 vs 持久性基线；支持 `--state-feedback` 与 Δ 模型自动识别 |
| `wm_predict_snapshot.py` | 固定输入预测快照（基线/训练对照） |
| `wm_compare_snapshots.py` | 两份快照差异判定（3 项 PASS 规则） |
| `wm_plugin_e2e.py` | 插件层端到端（不依赖 HTTP 后端） |

### 2.3 新增测试（`tests/plugins/world_model/`）

`test_predictor_weight_loading.py`(4) / `test_weights_resolver_fallback.py`(6) / `test_delta_prediction.py`(4) / `test_state_feedback_contract.py`(6)

---

## 3. 数据资产台账（`data/world_model/`）

| 文件 | 规模 | 来源 |
|---|---|---|
| `trajectories_v1.npz` | 1500 条，T=20 H=5，含 `initial_state` | 物理合成 `physics_synthetic_v0`（切深按稳定性极限 0.2–1.6× 采样，颤振 0.002–0.999，均值 0.42） |
| `phm2010_train.npz` | 226 窗口（c1+c4） | **真实** PHM2010（`research/datasets/uniwear/uniwear/uniwear.csv` 8,515 行），`real_phm2010_wear_v1` |
| `phm2010_val.npz` | 113 窗口（c6） | 同上 |
| `cv_c{1,4,6}_train/val.npz` | 226 / 113 | 留一交叉验证折 |

**数据边界**：PHM2010 只提供力/振动/真实磨损；几何为常量占位、动作为常量、温度/颤振/Ra 为物理派生 → 只能校准动力学通道。

---

## 4. Checkpoint 台账（`data/models/world_model/`）

| 版本 | 架构 | 训练数据 | 用途 | 状态 |
|---|---|---|---|---|
| `1.0.0.pt` | 绝对状态 + 融合 | 合成 1500 | 首次通电验证 | 基线 |
| **`1.1.0-real.pt`** | 绝对状态 + 融合 | 合成→PHM2010 微调 | **生产默认交付** | ✅ 交付 |
| `1.1.1-real.pt` / `1.1.2-real.pt` | 同上 | 逆方差权 / 带下限权 | 常量维漂移、综合退化 | 负结果 |
| `2.0.0-delta` / `2.1.0-delta-real` | Δ 预测 | 合成 / 真实 | Δ 验证（磨损无改善，暴露水平塌缩） | 实验 |
| `2.2.0-delta-sf.pt` | Δ + 状态反馈 | 合成 1500 | 慢变量变体的预训练母本（val 0.0052） | 母本 |
| **`2.2.1-delta-real-sf.pt`** | Δ + 状态反馈 | 2.2.0 → PHM2010 | 慢变量/长时程变体（val 0.0106） | ✅ 交付候选 |
| `2.2.2-delta-sf-w.pt` | Δ + 状态反馈 + 加权 | 同上 | 磨损 0.0076 但常量维漂移 | 负结果 |
| `cv-c{1,4,6}.pt` / `cvsf-c{1,4,6}.pt` | 绝对 / Δ+反馈 | 留一折 | 三折交叉验证 | 证据 |
| `data/models/_mlflow_test/1.2.x-mlflowtest.pt` | 绝对 | 合成 3 轮 | MLflow 落库验证 | 临时 |

---

## 5. 指标台账（全部本机实测）

| 指标 | 基线 | 1.1.0-real | 2.2.1-delta-sf | 判定 |
|---|---|---|---|---|
| 总体 MAE（真实 val） | 0.3795（合成模型直接迁移） | **0.1342（−64.6%）** | — | 微调有效 |
| 磨损 MAE（c6 折，H=5） | 0.000179（持久性） | 0.0418 | **0.0115** | 改善 3.6×，**未打平** |
| Ra MAE（c6 折） | 0.000537 | 0.5056 | **0.0218** | 改善 23×，**未打平** |
| 切削力增益（3 折） | — | +16.5%±8.2%（3/3 正） | +8.2%±2.7%（3/3 正） | **稳健（两架构）** |
| 振动增益（3 折） | — | −9.9%±26.6%（1/3 正） | −23.2%±45.4%（1/3 正，c4 −83%） | **不稳健** |
| 单折说法（已作废） | — | “振动 +15.75%” 仅 c6 折巧合 | — | ❌ 不得引用 |

**根因结论**：磨损/Ra 打不过持久性 = ①PHM2010 磨损 5 步内变化极小（持久性近最优）；②**dynamics 输入无切削力信号**（力只在标签里）→ 缺信息增量，非调参问题。

---

## 6. 文档台账

| 文件 | 内容 |
|---|---|
| `docs/adr/ADR-023-世界模型状态反馈契约.md` | initial_state 契约、三折验收表、风险与后续 |
| `reports/wm_power_on_report_20261021.md` | 通电全过程（环境/数据/训练/端到端/复现命令） |
| `reports/wm_real_finetune_20261021.md` | 真实数据微调 + §7 ADR-023 结果（含负结果） |
| `docs/reports/wm_state_schema_20261021.md` | state 8 维语义、合成数据物理公式、Tlusty 偏差记录 |
| `reports/wm_*.json`（30 份） | 快照/评估/交叉验证/训练报告原始数据 |

---

## 7. 回归快照（收束时实测）

| 范围 | 结果 |
|---|---|
| `tests/plugins/world_model/` + `tests/image_to_3d/` + `chatter/tests/test_stability.py` | **209 passed / 1 skipped / 0 failed** |
| `verify_torch_ready.py` | 55 passed / 1 skipped |

**既有问题（非本次引入）**：`app/simulation/chatter/tests/test_predictor.py` 5 个红测试断言旧契约（HEAD 的 predictor 已返回 `-1.0` 哨兵），需单独订正。

---

## 8. 遗留项台账

| 优先级 | 事项 | 验收标准 |
|---|---|---|
| **P0** | dynamics 输入加入测力信号（重训） | 磨损 MAE ≤ 持久性基线 2×，或明确证明不可达 |
| **P0** | 生产打包必须内含 `world_model/*.pt`（或设 `WORLD_MODEL_MODELS_DIR`） | 干净环境启动后 `weights_source=checkpoint` |
| **P1** | `test_predictor.py` 5 个红测试契约订正 | 该文件全绿 |
| **P1** | Tlusty 轴向选择（`stiffness_z` 10.5mm vs 最柔性轴 0.79mm）立项 | ADR 定稿 + 叶瓣图口径统一 |
| **P1** | HTTP 后端端到端（完整 requirements） | `/api/v1/world-model/predict` 真实响应 |
| **P2** | JEPA 三线盘点 → ADR-022（vjepa 晋升候选 / ijepa 几何 latent / jepa 修 import） | 三线状态矩阵 + 决策 |
| **P2** | 加工视频数据来源决策（仿真渲染 / 公开集 / 车间） | 1 页决策文档含许可结论 |
| **P2** | 异常检测接 DNC 硬闸前置 | 注入异常可拦截，误报率有数 |
| **P2** | GrabCAD → part_prior VAE 预训练 | ≥1000 STEP、Chamfer 优于基线 |

---

## 9. 复现命令（最小集）

```powershell
$py = "<用户主目录>\miniconda3\envs\lingjing-wm\python.exe"
Set-Location D:\灵境制造\engineering\python

# 1) 合成数据
& $py scripts\build_wm_trajectory_dataset.py --samples 1500 --history 20 --horizon 5 --out data/world_model/trajectories_v1.npz
# 2) 真实数据（PHM2010 窗口）
& $py scripts\build_wm_real_dataset.py --out-dir data/world_model
# 3) 合成预训练 + 真实微调（生产默认）
& $py scripts\train_world_model.py --dataset data/world_model/trajectories_v1.npz --version 1.0.0 --epochs 40 --models-dir D:\灵境制造\data\models
& $py scripts\train_world_model.py --dataset data/world_model/phm2010_train.npz --val-dataset data/world_model/phm2010_val.npz --version 1.1.0-real --epochs 30 --lr 0.0001 --patience 6 --init-checkpoint data/models/world_model/1.0.0.pt --freeze-geometry --models-dir D:\灵境制造\data\models
# 4) 慢变量变体（Δ + 状态反馈）
& $py scripts\train_world_model.py --dataset data/world_model/trajectories_v1.npz --version 2.2.0-delta-sf --epochs 40 --delta-prediction --models-dir D:\灵境制造\data\models
& $py scripts\train_world_model.py --dataset data/world_model/phm2010_train.npz --val-dataset data/world_model/phm2010_val.npz --version 2.2.1-delta-real-sf --epochs 40 --lr 0.0001 --init-checkpoint data/models/world_model/2.2.0-delta-sf.pt --freeze-geometry --delta-prediction --models-dir D:\灵境制造\data\models
# 5) 评估 + 端到端
& $py scripts\wm_eval_real_wear.py --dataset data/world_model/phm2010_val.npz --weights data/models/world_model/2.2.1-delta-real-sf.pt --state-feedback --out reports/wm_eval_real_delta_sf.json
& $py scripts\wm_plugin_e2e.py --out reports/wm_plugin_e2e_20261021.json
```

---

## 10. 变更状态与风险

- **全部改动未提交 git**（`main` 停在 `ca1cf1c`）。工作区同时存在其他线的未提交改动（`rl_agent/plugin.py`、`data_flywheel/*`、`src-tauri`、多个 docs），**本次未做任何 commit**，避免混提。
- `plugin.py` 中 `_load_artifact_data` → `app.plugins.artifact_data.load_artifact_data` 的收敛是**工作区既有改动**（非本次），依赖模块存在且 import 通过。
- 数据/权重（`data/world_model/`、`data/models/`）当前不受版本控制；如需跨机复现需另行归档或上传对象存储。
