# 工艺参数神经路径补训练报告（颤振 LTC + 切削力 PINN）

**日期**：2026-10-22 ｜ **触发**：`FULLCHAIN_DIAGNOSIS_20261021.md` §5 缺陷 4——工艺参数环节两个权重缺失，全部走解析回退，"AI 驱动"实际成分为 0%
**性质**：实机训练 + 端到端复验，所有数字可用 §6 命令复现
**环境**：conda `lingjing-wm`（Python 3.12.15，torch 2.7.1+cu126，CPU 推理）

---

## 1. 结论摘要

| 项 | 结果 |
|---|---|
| 颤振 LTC 权重 | ✅ 已生成 `app/simulation/chatter/checkpoints/chatter_model.pt`（59.8 KB） |
| 颤振代理精度 | 切深 MAE **0.0946 mm**、相对误差中位 **2.91%**、R² **0.99899**、稳定性判定 **99.7%** |
| 颤振端到端 | 三组机床/刀具与解析法偏差 **0.14% / 0.71% / 0.93%**，`method=neural_network` |
| 切削力 PINN 权重 | ✅ 已生成 `app/simulation/cutting_force/checkpoints/best_model.pt`（345.3 KB） |
| 切削力代理精度 | 切深 0.2–5.0 mm 五个工况相对误差 **1.1%–3.3%**（改造前浅切 67.8%），`method=pinn` |
| 回归 | predictor 测试 19 passed（原 5 failed）；新增训练器测试 7 passed；unit 门禁 **3,209 passed / 0 failed** |

**定性**：两个权重都是**解析法的快速代理（蒸馏）**，不是独立物理模型。它们的价值是让"双路径"名副其实、推理 O(1)（省掉 2000 点 FRF 扫描），**不构成"AI 优于解析法"的证据**。

---

## 2. 颤振 LTC 代理模型

### 2.1 训练设计

| 项 | 设置 | 依据 |
|---|---|---|
| 标签 | `compute_stability_limit`（Tlusty 1-DOF 绝对包络） | 与解析回退路径同源，两条路径不会给出互相矛盾的稳定性结论；该函数 10-21 已修负实部搜索 |
| 参数采样 | 刚度 5e6–3e8 N/m（对数均匀）、阻尼 0.01–0.10、固有频率 80–1200 Hz、直径 6–25 mm、K_s 1500–3000 N/mm²、转速 1000–20000 rpm | 覆盖预设机床与常用刀具量级 |
| 模态质量 | 固定 50 kg | predictor 契约为 6 维输入，不含模态质量；解析包络依赖 sqrt(k/m)，故训练时固定 |
| 样本 | 训练 12,000 / 验证 2,000 | 数据生成 172.7 s（解析法 2000 点扫描 × 2 万样本） |
| 网络 | `LTC(6→64→32→2)`，2 层，time_constant=1.0 | 与 `predictor.py` 加载契约完全一致 |
| 损失 | BCEWithLogits(稳定性) + 0.01 × MSE(极限切深 mm) | 判断 + 回归双头 |
| 训练 | 150 轮，批 256，Adam 1e-3，梯度裁剪 1.0 | 实际 1,772 s（CPU） |

### 2.2 收敛过程（节选）

| 轮次 | 验证损失 | 切深 MAE | 相对误差中位 | 稳定性判定 |
|---|---|---|---|---|
| 1 | 0.5969 | 2.810 mm | 73.0% | 85.7% |
| 10 | 0.0942 | 0.753 mm | 25.3% | 98.0% |
| 20 | 0.0342 | 0.235 mm | 7.9% | 99.0% |
| 50 | 0.0226 | 0.179 mm | 5.5% | 98.8% |
| 80 | 0.0141 | 0.102 mm | 3.1% | 99.6% |
| **124（最佳）** | **0.0098** | **0.0946 mm** | **2.91%** | **99.7%** |
| 150 | 0.0100 | 0.092 mm | 2.75% | 99.7% |

### 2.3 端到端复验（`predict_stability`）

| 机床/刀具 | 神经网络 | 解析法 | 相对偏差 |
|---|---|---|---|
| vmc_850 / endmill_d10 | 10.515 mm | 10.500 mm | 0.14% |
| vmc_850 / endmill_d16 | 9.478 mm | 9.546 mm | 0.71% |
| small_vmc_640 / endmill_d10 | 9.451 mm | 9.540 mm | 0.93% |

**口径一致性**：三条都在 5% 内，`test_analytical_vs_neural_consistency` 的断言首次真正生效（此前该断言因模型缺失而空跑）。

### 2.4 诚实边界（必须随交付继承）

1. **代理而非发现**：标签来自解析包络本身，模型只学到"用 6 维输入复现解析结果"。不能表述为"神经网络独立预测颤振"。
2. **输入信息量**：当前 1-DOF 包络仅依赖 刚度 / 阻尼 / K_s（模态质量固定 50 kg），转速、固有频率、刀具直径在解析口径下不参与计算；模型会学到"忽略"这些输入。这是建模局限，不是训练不足。
3. **与实测数据的关系**：本次没有做实测验证；跨源验证属 P08（在审 IJMS）范围，不得引用本报告充当实测精度。
4. **重训触发条件**：叶瓣口径统一（Tlusty 轴向选择立项）或归一化尺度变更后，必须重新训练——归一化在 `predictor.normalize_features` 单一定义，训练脚本直接导入。

---

## 3. 切削力 PINN

### 3.1 两次训练的差异（本次关键发现）

| 方案 | 训练目标 | 最终损失 | 浅切 0.2 mm 相对误差 | 典型切深 1.5–3 mm |
|---|---|---|---|---|
| 原始线性空间（仓库既有实现） | 原始力值 MSE | ~8,586（RMSE≈93 N） | **67.8%** | 0.2–0.4% |
| 归一化线性（中途尝试） | F/1000 | 0.0081 | — | 1.5% / 0.3% |
| **log1p 空间（采用）** | log(1+F) | **0.0025** | **3.3%** | **1.1–1.3%** |

根因：Kienzle 是幂律（F ∝ 切深^1.25），力值跨两个数量级（20 N–5.9 kN）。线性空间 MSE 按绝对误差优化，浅切样本的相对精度被系统性牺牲。对数空间把幂律线性化，浅切与大载荷获得同等权重。

### 3.2 复验（`predict_cutting_force`，45steel）

| 切深 | PINN (Fx,Fy,Fz) | Kienzle | 最大相对误差 |
|---|---|---|---|
| 0.2 mm | (7, 9, 22) N | (6, 9, 21) N | 3.3% |
| 0.5 mm | (32, 42, 105) N | (32, 42, 106) N | 1.4% |
| 1.5 mm | (217, 289, 714) N | (217, 289, 723) N | 1.3% |
| 3.0 mm | (728, 967, 2404) N | (730, 973, 2432) N | 1.1% |
| 5.0 mm | (1802, 2414, 5927) N | (1784, 2378, 5946) N | 1.5% |

### 3.3 契约变更（向后兼容）

- 训练端：目标进 `log1p` 空间；检查点新增 `target_transform="log1p"` 与 `force_scale=1.0`。
- 推理端：`predictor._load_model` 读取 `target_transform` / `force_scale`（缺省 `linear`/`1.0`），log1p 时用 `expm1` 还原并截断负值。
- 旧权重（无这两个字段）行为不变。

---

## 4. 顺手修掉的三个真实缺陷

| # | 缺陷 | 证据 | 修复 |
|---|---|---|---|
| 1 | LTC 是有状态网络，`hidden_state` 缓存在模块上；同批次大小的连续调用会继承上一次隐状态，预测依赖调用顺序 | 修前每次 `predict()` 都携带上次隐状态 | `predict()` 前重置 `model.hidden_state = None` |
| 2 | 归一化尺度与预设机床不匹配：`vmc_850` 的 stiffness_z=2e8，原尺度 `/1e7` 把输入放大到 20 | 预设表实测 | 改为 `/1e8`；并把归一化提为模块级 `normalize_features()`，训练脚本直接导入（消除两套尺度） |
| 3 | `predict_stability` 每次调用都新建 `ChatterPredictor`（重复 `torch.load`） | 批量预测场景 | 进程级单例 + `reset_predictor_cache()`；异常白名单补 `TypeError`（torch 与解释器版本不兼容时必须回退解析法而非崩溃） |

---

## 5. 验证与回归

| 范围 | 结果 |
|---|---|
| `tests/unit/test_chatter_trainer.py`（新增 7 项：归一化契约/标签自洽/训练器烟测/加载契约/哨兵契约） | **7 passed** |
| `app/simulation/chatter/tests/test_predictor.py`（订正 5 个红测试） | **19 passed**（原 5 failed） |
| unit 门禁 `-m "unit and not slow and not skip_ci"`（Python 3.14） | **3,209 passed / 0 failed / 145 s** |
| 契约脚本（归一化单位映射/标签自洽/检查点结构/加载推理/哨兵） | 5/5 OK |
| `ruff check app/` + `ruff format --check app/`（CI 固定 0.15.12） | 全绿（37 文件已重排） |

---

## 6. 复现命令

```powershell
$env:PYTHONPATH = ""
Set-Location D:\灵境制造\engineering\python
$py = "C:\Users\Lenovo\miniconda3\envs\lingjing-wm\python.exe"

# 颤振 LTC（数据生成 173 s + 训练 ~1770 s，CPU）
& $py -m app.simulation.chatter.trainer --samples 12000 --val-samples 2000 --epochs 150

# 切削力 PINN（log1p 空间，~340 s）
& $py -m app.simulation.cutting_force.trainer --epochs 120 --samples 6000

# 端到端复验
& $py -c "from app.simulation.chatter.predictor import predict_stability; print(predict_stability(8000,'vmc_850','endmill_d10'))"
& $py -c "from app.simulation.cutting_force.predictor import predict_cutting_force; print(predict_cutting_force(params={'speed':3500,'feed':1200,'depth':1.5}))"
```

## 7. 遗留与未做

1. ~~权重不入 git~~ → **已补做（同日）**：两文件以普通 git 二进制入库随安装包分发（不走 LFS——`build_desktop_runtime` 只做文件拷贝、不做 LFS 检出），`.gitignore` 白名单 + `.gitattributes` 例外；回归测试断言 `copy_tree` 保留 checkpoints 且仓库实际存在权重。
2. **实测验证未做**：两个代理都没有与实测力/实测稳定性边界对标；§2.4 已写明边界。
3. ~~叶瓣口径不一致~~ → **已补做（同日）**：`compute_stability_lobe` 修正三处——只取 `Re[G] > 0`、频率窗口用 `natural_freq` 而非实际模态共振 `sqrt(k_z/m)/2π`、转速关系漏齿数。实测 **叶瓣最低点 30.59 mm → 10.50 mm，与绝对包络比值 1.00**（修正前 2.9 倍安全高估）；回归断言 `test_lobe_minimum_matches_absolute_envelope` 固化（rel ≤ 5%）。**注意：叶瓣图数值整体下移，这是安全修正，消费方（前端叶瓣图）需同步复核展示。** 该修正不要求重训 LTC——训练标签是包络，与叶瓣口径无关。
4. **训练脚本未接入 MLflow**：世界模型线有完整 track，这两个训练只落检查点与 JSON 指标。
