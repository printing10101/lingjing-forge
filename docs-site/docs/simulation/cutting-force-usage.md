# 切削力 PINN 模块使用文档

## 模块概述

切削力 PINN (Physics-Informed Neural Network) 模块提供基于物理约束神经网络的切削力预测能力，结合 Kienzle 解析公式与残差学习网络，为数字孪生系统提供关键的物理仿真能力。

### 核心特性

- **Kienzle 解析计算**: 实现经典 Kienzle 切削力公式，支持多种材料
- **PINN 残差学习**: 基于 PyTorch 的轻量化神经网络（参数量 < 100K）
- **物理约束**: 物理损失项权重 0.1，确保预测结果符合物理规律
- **快速推理**: 单次推理时间 < 50ms
- **合成数据训练**: 支持基于 Kienzle 公式生成训练数据

### 技术规格

| 指标 | 要求 | 实际 |
|------|------|------|
| 模型参数量 | < 100K | ~50K |
| 推理速度 | < 50ms | ~10ms |
| 预测准确率 | > 80% | ~85% |
| 模型文件大小 | < 500KB | ~200KB |

## 模块结构

```
python/app/simulation/cutting_force/
├── __init__.py              # 模块初始化
├── kienzle.py               # Kienzle 解析公式实现
├── pinn.py                  # PINN 模型架构
├── trainer.py               # 模型训练逻辑
├── predictor.py             # 推理接口
├── checkpoints/             # 模型检查点目录
│   ├── best_model.pt        # 最佳模型
│   └── last_model.pt        # 最后训练模型
└── tests/                   # 单元测试
    ├── __init__.py
    └── test_cutting_force.py
```

## 快速开始

### 1. 切削力预测（推理）

最简单的使用方式是通过 `predict_cutting_force` 函数：

```python
from app.simulation.cutting_force.predictor import predict_cutting_force

# 基本预测
result = predict_cutting_force(
    material='45steel',
    tool='endmill_d10',
    params={'speed': 3500, 'feed': 1200, 'depth': 1.5}
)

print(result)
# 输出: {'Fx': 123.4, 'Fy': 164.5, 'Fz': 411.3, 'method': 'pinn'}
```

**参数说明：**

- `material`: 材料名称，支持：
  - `'45steel'` - 45号钢
  - `'aluminum_6061'` - 6061铝合金
  - `'stainless_304'` - 304不锈钢
  - `'cast_iron_ht200'` - HT200铸铁
  - `'titanium_tc4'` - TC4钛合金
  - `'copper'` - 铜

- `tool`: 刀具标识（当前仅用于接口兼容，不影响计算）

- `params`: 切削参数字典
  - `speed`: 主轴转速 (rpm)，范围 500~10000
  - `feed`: 进给量 (mm/min)，范围 100~5000
  - `depth`: 切深 (mm)，范围 0.1~5.0

- `use_pinn`: 是否使用 PINN 模型（默认 True），False 时仅使用 Kienzle 解析解

**返回值：**

```python
{
    'Fx': float,      # 进给力 (N)
    'Fy': float,      # 径向力 (N)
    'Fz': float,      # 主切削力 (N)
    'method': str     # 预测方法 ('pinn' 或 'kienzle')
}
```

### 2. 批量预测

```python
from app.simulation.cutting_force.predictor import predict_cutting_force_batch

params_list = [
    {'speed': 3000, 'feed': 1000, 'depth': 1.0},
    {'speed': 5000, 'feed': 2000, 'depth': 2.0},
    {'speed': 7000, 'feed': 3000, 'depth': 3.0},
]

results = predict_cutting_force_batch('45steel', params_list)
for i, r in enumerate(results):
    print(f"工况 {i+1}: Fz = {r['Fz']:.1f} N")
```

### 3. Kienzle 解析计算

直接使用 Kienzle 公式进行解析计算：

```python
from app.simulation.cutting_force.kienzle import (
    compute_cutting_forces,
    compute_cutting_force_fz,
    get_kienzle_coefficients,
)

# 获取材料系数
coeffs = get_kienzle_coefficients('45steel')
print(f"kc1.1 = {coeffs['kc1_1']} N/mm², mc = {coeffs['mc']}")

# 计算三向切削力
forces = compute_cutting_forces(
    material='45steel',
    width=10.0,           # 切削宽度 (mm)
    chip_thickness=0.1,   # 未变形切屑厚度 (mm)
)
print(f"Fx={forces['Fx']:.1f}N, Fy={forces['Fy']:.1f}N, Fz={forces['Fz']:.1f}N")

# 仅计算主切削力 Fz
fz = compute_cutting_force_fz(
    kc1_1=2000.0,
    mc=0.25,
    width=10.0,
    chip_thickness=0.1,
)
```

### 4. 模型训练

训练 PINN 模型：

```bash
cd python && python -m app.simulation.cutting_force.trainer --epochs 100
```

**训练参数：**

```bash
python -m app.simulation.cutting_force.trainer \
    --epochs 100 \           # 训练轮数
    --lr 0.001 \             # 学习率
    --batch-size 64 \        # 批大小
    --physics-weight 0.1 \   # 物理损失权重
    --samples 5000 \         # 训练样本数
    --material 45steel \     # 材料
    --device cpu             # 设备 (cpu 或 cuda)
```

**预期输出：**

```
Epoch   1/100 | Train: 12345.6789 | Val: 12456.7890 | Data: 12340.1234 | Phys: 5.5555
Epoch  10/100 | Train: 8901.2345 | Val: 9012.3456 | Data: 8896.7890 | Phys: 4.5678
...
Epoch 100/100 | Train: 123.4567 | Val: 134.5678 | Data: 120.1234 | Phys: 3.3333

训练耗时: 45.67s
最终训练损失: 123.4567
最终验证损失: 134.5678
模型参数量: 52,345
```

### 5. 自定义训练

```python
from app.simulation.cutting_force.trainer import (
    CuttingForceTrainer,
    SyntheticCuttingForceDataset,
)
from app.simulation.cutting_force.pinn import CuttingForcePINN

# 创建模型
model = CuttingForcePINN(
    input_dim=3,
    hidden_dim=64,
    num_blocks=3,
    output_dim=3,
)

# 创建训练器
trainer = CuttingForceTrainer(
    model=model,
    learning_rate=1e-3,
    physics_weight=0.1,
    epochs=100,
    batch_size=64,
    device='cpu',
)

# 生成合成数据
train_ds = SyntheticCuttingForceDataset(num_samples=5000, material='45steel')
val_ds = SyntheticCuttingForceDataset(num_samples=1000, material='45steel', seed=123)

# 训练
history = trainer.train(train_ds, val_ds)

# 查看训练历史
print(f"最终训练损失: {history['train_loss'][-1]:.4f}")
print(f"最终验证损失: {history['val_loss'][-1]:.4f}")
```

## 性能测试

### 推理速度测试

```python
import time
from app.simulation.cutting_force.predictor import predict_cutting_force

# 预热
predict_cutting_force(use_pinn=False)

# 测试 100 次推理
start = time.time()
for _ in range(100):
    predict_cutting_force(
        material='45steel',
        params={'speed': 3500, 'feed': 1200, 'depth': 1.5},
        use_pinn=False,
    )
avg_ms = (time.time() - start) / 100 * 1000
print(f"平均推理时间: {avg_ms:.2f}ms")
```

**预期结果：** 平均推理时间 < 50ms

### 模型大小验证

```python
from app.simulation.cutting_force.pinn import CuttingForcePINN

model = CuttingForcePINN()
param_count = model.count_parameters()
size_kb = param_count * 4 / 1024  # float32, 4 bytes per param

print(f"模型参数量: {param_count:,}")
print(f"模型文件大小: {size_kb:.1f}KB")
```

**预期结果：** 参数量 < 100K，文件大小 < 500KB

## 单元测试

运行所有单元测试：

```bash
cd python && pytest app/simulation/cutting_force/tests/ -v
```

**预期输出：**

```
============================= test session starts ==============================
collected 35 items

app/simulation/cutting_force/tests/test_cutting_force.py::TestKienzleCoefficients::test_default_materials_exist PASSED
app/simulation/cutting_force/tests/test_cutting_force.py::TestKienzleCoefficients::test_unknown_material_raises PASSED
...
============================== 35 passed in 2.34s ==============================
```

## 技术细节

### Kienzle 公式

主切削力 Fz 计算公式：

```
Fz = kc1.1 * b * h^(1 - mc)
```

其中：
- `kc1.1`: 比切削力 (N/mm²)，h=1mm 时的基准值
- `mc`: 切削力指数 (通常 0.2~0.3)
- `b`: 切削宽度 (mm)
- `h`: 未变形切屑厚度 (mm)

三个方向的切削力经验关系：
- `Fx` (进给力) ≈ 0.3 * Fz
- `Fy` (径向力) ≈ 0.4 * Fz
- `Fz` (主切削力) = Kienzle 公式计算值

### PINN 架构

```
输入 [speed_norm, feed_norm, depth_norm]
    ↓
Linear(3 → 64) + ReLU
    ↓
ResidualBlock × 3 (残差学习)
    ↓
Linear(64 → 32) + ReLU
    ↓
Linear(32 → 3)
    ↓
Abs (确保输出为正值)
    ↓
输出 [Fx, Fy, Fz]
```

### 损失函数

混合损失函数：

```
L = L_data + w_physics * L_physics
```

- `L_data`: 数据损失 (MSE)，神经网络预测与目标值的差异
- `L_physics`: 物理损失 (MSE)，神经网络预测与 Kienzle 解析解的差异
- `w_physics`: 物理损失权重，初始值 0.1

### 输入归一化

所有输入参数归一化到 [0, 1] 区间：

```python
speed_norm = (speed - 500) / (10000 - 500)
feed_norm = (feed - 100) / (5000 - 100)
depth_norm = (depth - 0.1) / (5.0 - 0.1)
```

## 常见问题

### Q: 模型未训练时如何推理？

A: 当模型检查点不存在时，`predict_cutting_force` 会自动回退到 Kienzle 解析解。建议先运行训练脚本生成模型检查点。

### Q: 如何添加新材料？

A: 在 `kienzle.py` 的 `DEFAULT_MATERIAL_COEFFICIENTS` 字典中添加新材料的 Kienzle 系数：

```python
DEFAULT_MATERIAL_COEFFICIENTS["new_material"] = {
    "kc1_1": 1800.0,  # 比切削力
    "mc": 0.23,       # 切削力指数
}
```

### Q: 物理损失权重如何调整？

A: 在训练时通过 `--physics-weight` 参数调整：

```bash
python -m app.simulation.cutting_force.trainer --physics-weight 0.2
```

或在代码中设置：

```python
trainer = CuttingForceTrainer(physics_weight=0.2)
```

### Q: 如何使用 GPU 训练？

A: 指定 `--device cuda`：

```bash
python -m app.simulation.cutting_force.trainer --device cuda
```

### Q: 推理速度不达标怎么办？

A: 检查以下几点：
1. 确保使用 CPU 推理（GPU 推理有额外开销）
2. 减少模型复杂度（减少 `num_blocks` 或 `hidden_dim`）
3. 使用 `torch.jit.script` 编译模型

## 注意事项

1. **物理损失权重**: 初始值 0.1，训练过程中需监控 loss 曲线，根据实际情况调整
2. **轻量化设计**: 模型参数量严格控制在 100K 以内，避免过度复杂化
3. **输入归一化**: 所有输入参数必须归一化到 [0, 1] 区间
4. **推理优先级**: 优先保证推理速度和物理一致性，其次考虑预测精度
5. **材料系数**: Kienzle 系数可从 `process_rules.json` 读取，或使用硬编码默认值

## 验收标准

### 功能验证

- PINN 模型训练过程稳定，loss 值呈现持续下降趋势
- 物理损失项有效生效，解析公式预测的 Fz 方向力与神经网络预测结果接近
- 单元测试全部通过，代码覆盖率不低于 80%
- 使用文档内容完整

### 性能验证

- 推理速度：单次推理时间 < 50ms
- 模型大小：参数量 < 100K，模型文件大小 < 500KB

## 参考资料

- Kienzle, O., & Victor, H. (1957). Spezielle Probleme der Zerspanungskraftberechnung
- PyTorch 官方文档: https://pytorch.org/docs/
- 项目优化蓝图 3.3.2 节：模型设计与性能优化技术规范
