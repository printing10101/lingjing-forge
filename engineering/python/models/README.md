# models/lnn — LNN 随包权重与 manifest

> 本目录是 LNN 注册表模型（`app/ai/lnn/inference/_lnn_registry.py`）的**已训练权重分发目录**。
> 训练/导出/验证链路与门禁见 `docs/development/lnn-权重训练与分发接线方案.md`。

## 分发清单

| 文件 | 说明 |
|---|---|
| `cutting_force.npz` | 切削力回归（CFC 镜像 MLP 4→64→64→1）。输入 `[spindle_rpm, feed_rate, depth_of_cut, material_encoded]` → 合力 N。v2 npz，`is_trained=true` |
| `cutting_force.manifest.json` | 数据血缘（`synthetic_machining_params_v1@<hash>`）/ 标准化参数（已折叠进权重）/ torch↔NumPy parity / held-out 指标 |
| `wear_prediction.npz` | 刀具磨损回归（LTC 镜像 MLP 4→64→64→1）。输入 `[force_z, vibration_x, vibration_y, time_s]` → 磨损量。数据源：uniwear（NUAA，39,904 行实测） |
| `wear_prediction.manifest.json` | 同上，source=csv（uniwear.csv sha256 前 16 位） |
| `*_training_summary.json` | 历史遗留（2026-05-08 一次训练的摘要，权重当时未落盘）；新链路以 `*.manifest.json` 为准 |

**未分发权重的模型**：`surface_roughness` / `temperature` 数据不足（`DATA_INSUFFICIENT_MODELS` 声明），
运行时以 `weights_source=random_init` 显式标记，**不得对外宣称为 AI 能力**。

## 权重格式（v2 npz）

- 元数据：`format_version / model_name / input_dim / output_dim / is_trained / config_json`
- 参数：`param.<key>` 数组（weights.N / biases.N / [memory_weights.N / memory_state]）
- **标准化已折叠进首末层权重**：服务端零预处理，直接 `predict(raw_x)`。

## 复现（再训练）

```bash
cd engineering/python
PY314="C:\Users\<user>\AppData\Local\Programs\Python\Python314\python.exe"

# 1. 合成数据充能（切削力）
$PY314 -c "import asyncio; from app.pipelines.synthetic_data_gen import generate_synthetic_dataset; \
print(asyncio.run(generate_synthetic_dataset(rpm_values=[1500.0,3000.0,4500.0,6000.0,8000.0], \
feed_values=[150.0,300.0,500.0,800.0,1200.0], depth_values=[0.3,0.8,1.5,2.5])).to_dict())"

# 2. 导出训练集（X/y + 特征清单契约）
$PY314 scripts/export_training_set.py --model cutting_force --out data/training_sets/cutting_force

# 3. 训练 + 导出权重（需 torch 的环境；产物落本目录）
$PY314 scripts/train_registry_model.py cutting_force --dataset-dir data/training_sets/cutting_force
$PY314 scripts/train_registry_model.py wear_prediction \
    --csv ../../research/datasets/uniwear/uniwear/uniwear.csv \
    --features force_z,vibration_x,vibration_y --target tool_wear --time-col timestamp

# 4. 精度基准（LNN vs Kienzle 解析解，插值网格）
$PY314 -m app.benchmarks.accuracy.lnn_accuracy_bench
```

## 运行时语义（2026-09 起）

- 注册表加载：权重文件缺失 → `weights_source=random_init`（可降级运行但状态可辨）；
  文件存在且 `is_trained=true` → `trained`；仅元数据的历史 v1 文件 → `file_untrained`。
- `GET /api/v1/health/system` 响应新增 `lnn_weights` 组件（trained_count / 逐模型明细）。
- 契约变更（breaking，见 manifest/registry 注释）：cutting_force 输入改为参数→力；
  wear_prediction 输入改为传感器信号→磨损（原 vb 输入属循环预测）。
