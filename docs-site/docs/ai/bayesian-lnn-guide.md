# 贝叶斯 LNN 技术指南

## 概述

贝叶斯 LNN（Bayesian LNN）通过 MC Dropout（Monte Carlo Dropout）技术为现有 LNN 模型提供不确定性量化能力。该实现严格复用原有 LNN 训练权重，仅在推理阶段通过多次采样估计预测的不确定性。

## 核心组件

### 1. BayesianLNN 模型类

**位置**: `python/app/ai/lnn/models/bayesian_lnn.py`

BayesianLNN 是对现有 LNN 模型（如 CFCModel）的包装器，主要功能：

- **权重复用**: 加载预训练的 LNN 权重，无需重新训练
- **MC Dropout**: 在推理时保持 Dropout 层激活，执行多次前向传播
- **不确定性估计**: 返回预测均值（mean）和标准差（std）

**关键参数**:
- `config`: LNNConfig 配置对象
- `dropout_prob`: Dropout 概率，默认 0.1
- `base_model`: 可选的预构建基础模型

**核心方法**:
```python
def predict_with_uncertainty(
    self,
    x: torch.Tensor,
    n_samples: int = 50,
    dt: float = 0.0,
) -> Tuple[torch.Tensor, torch.Tensor]:
    """MC Dropout 推理，返回均值和标准差"""
```

### 2. BayesianPredictor 推理接口

**位置**: `python/app/ai/lnn/inference/bayesian_predictor.py`

提供高级推理接口，支持从模型路径加载或直接使用模型实例。

**初始化方式**:

```python
# 方式1: 从模型路径加载
predictor = BayesianPredictor(
    model_path='models/cfc_v1.pt',
    dropout_prob=0.1,
    device='cpu'
)

# 方式2: 使用已有模型实例
model = BayesianLNN(config, dropout_prob=0.1)
predictor = BayesianPredictor(model=model)
```

**推理方法**:

```python
# 返回均值和标准差
mean, std = predictor.predict_with_uncertainty(
    input_data,
    n_samples=50,
    dt=0.0
)

# 仅返回均值（numpy 数组）
prediction = predictor.predict(input_data, n_samples=50)

# 获取完整不确定性指标
metrics = predictor.get_uncertainty_metrics(input_data, n_samples=50)
# 返回: {"mean": ..., "std": ..., "cv": ..., "max_std": ..., "mean_std": ...}
```

## 使用示例

### 基础用法

```python
import torch
import numpy as np
from app.ai.lnn.inference.bayesian_predictor import BayesianPredictor

# 创建预测器
predictor = BayesianPredictor(model_path='models/cfc_v1.pt')

# 准备输入数据
input_data = np.random.randn(1, 8).astype(np.float32)

# 执行贝叶斯推理
mean, std = predictor.predict_with_uncertainty(input_data, n_samples=50)

print(f"预测均值: {mean}")
print(f"不确定性(std): {std}")
print(f"最大不确定性: {std.max().item():.4f}")
```

### 批量推理

```python
# 批量输入 (batch_size=5)
batch_input = np.random.randn(5, 8).astype(np.float32)
mean, std = predictor.predict_with_uncertainty(batch_input, n_samples=50)

# 每个样本都有独立的不确定性估计
for i in range(5):
    print(f"样本 {i}: std={std[i].max().item():.4f}")
```

### 不确定性可视化

```python
import matplotlib.pyplot as plt

mean, std = predictor.predict_with_uncertainty(input_data, n_samples=100)

# 绘制预测结果和置信区间
x = np.arange(mean.shape[1])
plt.figure(figsize=(10, 6))
plt.plot(x, mean[0].detach().numpy(), 'b-', label='Mean Prediction')
plt.fill_between(
    x,
    (mean[0] - 2*std[0]).detach().numpy(),
    (mean[0] + 2*std[0]).detach().numpy(),
    alpha=0.3,
    color='blue',
    label='95% Confidence Interval'
)
plt.xlabel('Output Dimension')
plt.ylabel('Value')
plt.title('Bayesian LNN Prediction with Uncertainty')
plt.legend()
plt.show()
```

## 技术实现细节

### MC Dropout 原理

传统 Dropout 仅在训练时激活，推理时关闭。MC Dropout 在推理时保持 Dropout 激活，通过多次前向传播获得不同的预测结果：

```
for i in range(n_samples):
    output_i = model(x)  # Dropout 产生不同的输出
mean = mean(output_1, ..., output_n)
std = std(output_1, ..., output_n)
```

### Dropout 层注入

BayesianLNN 确保基础模型有 Dropout 层：

1. 如果基础模型的 `dropout` 是 `nn.Identity`（dropout=0），替换为真实的 `nn.Dropout`
2. 在 `output_layer` 的 ReLU 后插入 Dropout 层（如果不存在）

这保证了即使原模型训练时未使用 Dropout，贝叶斯推理也能正常工作。

### 权重加载

```python
# 加载预训练权重
state_dict = torch.load('models/cfc_v1.pt')
bayesian_model.load_base_weights(state_dict, strict=False)
```

使用 `strict=False` 允许加载时忽略新增的 Dropout 层参数。

## 性能特性

### 推理时间

- **采样次数**: 默认 50 次
- **时间开销**: 约为原模型的 3-5 倍（线性关系）
- **优化建议**: 对于实时性要求高的场景，可减少 `n_samples`（如 20-30）

### 内存占用

- 额外内存主要来自多次前向传播的中间结果
- 使用 `torch.no_grad()` 减少梯度存储开销
- 批量推理时注意控制 batch_size

## 测试验证

### 单元测试

```bash
cd python
pytest app/ai/lnn/tests/test_bayesian.py -v
```

测试覆盖：
- 模型创建和初始化
- MC Dropout 推理机制
- 权重加载兼容性
- 均值和标准差输出验证
- 性能基准测试（<=5x 原模型）
- BayesianPredictor 接口

### 功能验证

```bash
cd python
python -c "
from app.ai.lnn.inference.bayesian_predictor import BayesianPredictor
import torch
predictor = BayesianPredictor(model_path='models/cfc_v1.pt')
mean, std = predictor.predict_with_uncertainty(torch.randn(1, 8), n_samples=50)
print(f'mean: {mean.shape}, std: {std.shape}')
assert std.abs().max() > 0, 'std should be > 0'
print('功能验证通过')
"
```

### 性能基准测试

```bash
cd python
python -c "
import time
import torch
from app.ai.lnn.inference.bayesian_predictor import BayesianPredictor
from app.ai.lnn.models.torch_cfc_model import CFCModel
from app.ai.lnn.models.torch_base_lnn import LNNConfig

# 原模型基准
config = LNNConfig(input_size=8, hidden_size=64, output_size=4)
original = CFCModel(config)
original.eval()

x = torch.randn(1, 8)
start = time.perf_counter()
for _ in range(10):
    with torch.no_grad():
        original(x)
original_time = (time.perf_counter() - start) / 10 * 1000

# 贝叶斯模型
predictor = BayesianPredictor(model=BayesianLNN(config))
start = time.perf_counter()
for _ in range(10):
    predictor.predict_with_uncertainty(x, n_samples=50)
bayesian_time = (time.perf_counter() - start) / 10 * 1000

print(f'原模型: {original_time:.2f}ms')
print(f'贝叶斯模型: {bayesian_time:.2f}ms')
print(f'性能比: {bayesian_time/original_time:.2f}x')
assert bayesian_time <= original_time * 5, '性能超出5倍限制'
print('性能验证通过')
"
```

## 接口兼容性

### 与原 Predictor 的对比

| 特性 | LNNPredictor | BayesianPredictor |
|------|--------------|-------------------|
| 预测输出 | 单一预测值 | 均值 + 标准差 |
| 不确定性量化 | | |
| 权重加载 | | （兼容原权重） |
| 批量推理 | | |
| 流式推理 | | （可扩展） |

### 迁移指南

从原 Predictor 迁移到 BayesianPredictor：

```python
# 原代码
from app.ai.lnn.inference.predictor import LNNPredictor
predictor = LNNPredictor(model=model)
result = predictor.predict(input_data)

# 迁移后
from app.ai.lnn.inference.bayesian_predictor import BayesianPredictor
predictor = BayesianPredictor(model=bayesian_model)
mean, std = predictor.predict_with_uncertainty(input_data, n_samples=50)
result = mean  # 如需保持兼容，使用均值作为预测值
```

## 限制与注意事项

1. **不修改训练流程**: 仅通过 MC Dropout 实现贝叶斯近似，不涉及变分推断或 MCMC
2. **权重复用**: 必须使用原 LNN 训练的权重，不支持从头训练贝叶斯模型
3. **采样次数**: 默认 50 次，可根据精度-速度需求调整（建议 20-100）
4. **部署范围**: 当前仅在测试端点验证，未替换所有 LNN 端点

## 扩展方向

1. **流式推理**: 为 BayesianPredictor 添加 streaming 接口
2. **自适应采样**: 根据不确定性动态调整 n_samples
3. **GPU 加速**: 支持批量并行 MC 采样
4. **不确定性校准**: 添加温度缩放等校准方法

## 参考资料

- [Dropout as a Bayesian Approximation](https://arxiv.org/abs/1506.02142)
- [MC Dropout 原始论文](https://github.com/yaringal/DropoutUncertaintyExps)
- LNN 模型文档: `docs/ai/lnn-architecture.md`
