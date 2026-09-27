# 振动/颤振稳定性模块使用文档

## 模块介绍

振动/颤振稳定性模块（chatter）提供基于解析法（Tlusty公式）和神经网络的颤振稳定性极限预测功能。该模块能够：

1. **解析法预测**：基于Tlusty稳定性叶图理论，计算给定主轴转速下的极限切削深度
2. **神经网络预测**：使用训练好的神经网络模型进行快速稳定性预测
3. **稳定性叶图生成**：生成主轴转速与极限切削深度的关系曲线

### 理论基础

#### Tlusty 稳定性叶图理论

颤振是切削加工中由于刀具与工件之间的动态相互作用引起的自激振动现象。Tlusty公式基于再生颤振理论，考虑了机床动态特性与切削过程的耦合效应。

**核心公式：**

```
a_lim = -1 / (2 * K_s * Re[G(ω)])
```

其中：
- `a_lim`: 极限切削深度 (mm)
- `K_s`: 切削力系数 (N/mm²)，与材料和刀具几何相关
- `G(ω)`: 机床频率响应函数 (mm/N)
- `Re[G(ω)]`: 频率响应函数的实部

**机床频率响应函数（单自由度系统）：**

```
G(ω) = 1 / (k - m*ω² + i*c*ω)
```

其中：
- `k`: 刚度 (N/m)
- `m`: 模态质量 (kg)
- `c`: 阻尼系数 (N·s/m) = 2 * ζ * √(k*m)
- `ω`: 角频率 (rad/s) = 2π*f
- `ζ`: 阻尼比

**主轴转速与颤振频率关系：**

```
n = 60 * f / (j + 1)
```

其中：
- `n`: 主轴转速 (rpm)
- `f`: 颤振频率 (Hz)
- `j`: 叶图扇叶序号 (0, 1, 2, ...)

## 安装与依赖

### 必需依赖

```bash
pip install numpy scipy
```

### 可选依赖（神经网络功能）

```bash
pip install torch
```

**注意**：如果未安装PyTorch，模块将自动回退到解析法。

## API 说明

### 核心函数

#### `predict_stability()`

预测颤振稳定性状态和极限切削深度。

**函数签名：**

```python
def predict_stability(
    spindle_rpm: float = 8000,
    machine: str = "vmc_850",
    tool: str = "endmill_d10",
    workpiece: str = "aluminum",
) -> Dict[str, object]
```

**参数说明：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `spindle_rpm` | float | 8000 | 主轴转速 (rpm) |
| `machine` | str | "vmc_850" | 机床标识，如 "vmc_850", "cnc_lathe_ck6140" |
| `tool` | str | "endmill_d10" | 刀具标识，如 "endmill_d10", "endmill_d16" |
| `workpiece` | str | "aluminum" | 工件材料，如 "aluminum", "steel" |

**返回值：**

返回包含以下键的字典：

| 键 | 类型 | 说明 |
|----|------|------|
| `stable` | bool | 稳定性状态，True表示稳定，False表示不稳定 |
| `limit_depth` | float | 极限切削深度 (mm) |
| `method` | str | 预测方法，"neural_network" 或 "analytical" |
| `inference_time_ms` | float | 推理时间 (ms)，仅神经网络方法返回 |

**示例：**

```python
from app.simulation.chatter.predictor import predict_stability

# 基本使用
result = predict_stability(
    spindle_rpm=8000,
    machine='vmc_850',
    tool='endmill_d10',
    workpiece='aluminum'
)

print(f"稳定性状态: {'稳定' if result['stable'] else '不稳定'}")
print(f"极限切削深度: {result['limit_depth']:.2f} mm")
print(f"预测方法: {result['method']}")
```

#### `predict_stability_batch()`

批量预测颤振稳定性。

**函数签名：**

```python
def predict_stability_batch(params_list: list) -> list
```

**参数说明：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `params_list` | list | 参数列表，每个元素为字典 |

**示例：**

```python
from app.simulation.chatter.predictor import predict_stability_batch

params_list = [
    {"spindle_rpm": 4000, "machine": "vmc_850", "tool": "endmill_d10"},
    {"spindle_rpm": 6000, "machine": "vmc_850", "tool": "endmill_d10"},
    {"spindle_rpm": 8000, "machine": "vmc_850", "tool": "endmill_d10"},
]

results = predict_stability_batch(params_list)

for i, result in enumerate(results):
    print(f"转速 {params_list[i]['spindle_rpm']} rpm: "
          f"极限切深 {result['limit_depth']:.2f} mm")
```

#### `compute_stability_limit()`

使用解析法计算稳定性极限切削深度。

**函数签名：**

```python
def compute_stability_limit(params: ChatterParams) -> float
```

**参数说明：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `params` | ChatterParams | 颤振计算参数对象 |

**返回值：**

极限切削深度 (mm)

**示例：**

```python
from app.simulation.chatter.stability import (
    ChatterParams,
    MachineParams,
    ToolParams,
    compute_stability_limit,
)

# 创建参数
machine = MachineParams(
    machine_id="vmc_850",
    stiffness_z=2.0e7,
    damping_ratio=0.05,
    natural_freq=800.0,
    modal_mass=50.0,
)

tool = ToolParams(
    tool_id="endmill_d10",
    diameter=10.0,
    cutting_force_coeff=2000.0,
)

params = ChatterParams(
    spindle_rpm=8000.0,
    machine=machine,
    tool=tool,
)

# 计算极限切深
limit_depth = compute_stability_limit(params)
print(f"极限切削深度: {limit_depth:.2f} mm")
```

#### `compute_stability_lobe()`

计算稳定性叶图。

**函数签名：**

```python
def compute_stability_lobe(
    machine: MachineParams,
    tool: ToolParams,
    speed_range: Tuple[float, float] = (1000, 10000),
    num_points: int = 100,
    num_lobes: int = 5,
) -> Dict[str, List[float]]
```

**参数说明：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `machine` | MachineParams | - | 机床参数 |
| `tool` | ToolParams | - | 刀具参数 |
| `speed_range` | tuple | (1000, 10000) | 主轴转速范围 (rpm) |
| `num_points` | int | 100 | 每个叶图的点数 |
| `num_lobes` | int | 5 | 叶图扇叶数量 |

**返回值：**

返回包含以下键的字典：

| 键 | 类型 | 说明 |
|----|------|------|
| `speeds` | List[float] | 主轴转速列表 (rpm) |
| `limit_depths` | List[float] | 极限切削深度列表 (mm) |
| `lobes` | List[Tuple] | 各扇叶的 (speeds, limit_depths) 元组列表 |

**示例：**

```python
from app.simulation.chatter.stability import (
    MachineParams,
    ToolParams,
    compute_stability_lobe,
)
import matplotlib.pyplot as plt

machine = MachineParams()
tool = ToolParams()

# 计算稳定性叶图
lobe_data = compute_stability_lobe(
    machine=machine,
    tool=tool,
    speed_range=(1000, 10000),
    num_points=100,
    num_lobes=5,
)

# 绘制稳定性叶图
plt.figure(figsize=(10, 6))
for i, (speeds, depths) in enumerate(lobe_data["lobes"]):
    plt.plot(speeds, depths, label=f'Lobe {i}')

plt.xlabel('主轴转速 (rpm)')
plt.ylabel('极限切削深度 (mm)')
plt.title('稳定性叶图')
plt.legend()
plt.grid(True)
plt.show()
```

### 数据类

#### `MachineParams`

机床动态参数。

**属性：**

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `machine_id` | str | "vmc_850" | 机床标识 |
| `stiffness_x` | float | 1.5e7 | X向刚度 (N/m) |
| `stiffness_y` | float | 1.5e7 | Y向刚度 (N/m) |
| `stiffness_z` | float | 2.0e7 | Z向刚度 (N/m) |
| `damping_ratio` | float | 0.05 | 阻尼比 |
| `natural_freq` | float | 800.0 | 固有频率 (Hz) |
| `modal_mass` | float | 50.0 | 模态质量 (kg) |

#### `ToolParams`

刀具参数。

**属性：**

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `tool_id` | str | "endmill_d10" | 刀具标识 |
| `diameter` | float | 10.0 | 刀具直径 (mm) |
| `num_flutes` | int | 4 | 齿数 |
| `helix_angle` | float | 30.0 | 螺旋角 (度) |
| `cutting_force_coeff` | float | 2000.0 | 切削力系数 K_s (N/mm²) |

#### `ChatterParams`

颤振稳定性计算参数。

**属性：**

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `spindle_rpm` | float | 8000.0 | 主轴转速 (rpm) |
| `machine` | MachineParams | - | 机床参数 |
| `tool` | ToolParams | - | 刀具参数 |
| `axial_depth` | Optional[float] | None | 轴向切深 (mm) |

## 参数说明

### 支持的机床

| 机床标识 | 名称 | 固有频率 (Hz) | 阻尼比 | Z向刚度 (N/m) |
|----------|------|---------------|--------|---------------|
| `vmc_850` | 立式加工中心 VMC850 | 800 | 0.05 | 2.0e7 |
| `cnc_lathe_ck6140` | 数控车床 CK6140 | 700 | 0.04 | 1.8e7 |
| `small_vmc_640` | 小型立式加工中心 VMC640 | 900 | 0.06 | 1.5e7 |

### 支持的刀具

| 刀具标识 | 直径 (mm) | 齿数 | 螺旋角 (度) | 切削力系数 (N/mm²) |
|----------|-----------|------|-------------|-------------------|
| `endmill_d10` | 10 | 4 | 30 | 2000 |
| `endmill_d16` | 16 | 4 | 30 | 2200 |
| `endmill_d20` | 20 | 5 | 35 | 2400 |

### 参数来源

- **机床参数**：从 `python/app/database/data/machines.json` 读取，缺失时使用系统默认值
- **刀具参数**：使用模块内置的默认值
- **材料参数**：使用模块内置的切削力系数

## 使用示例

### 示例1：基本稳定性预测

```python
from app.simulation.chatter import predict_stability

# 预测给定参数下的稳定性
result = predict_stability(
    spindle_rpm=8000,
    machine='vmc_850',
    tool='endmill_d10',
    workpiece='aluminum'
)

if result['stable']:
    print(f"加工稳定，极限切深: {result['limit_depth']:.2f} mm")
else:
    print(f"加工不稳定，建议降低切深或调整转速")
```

### 示例2：批量参数优化

```python
from app.simulation.chatter import predict_stability_batch

# 测试不同转速下的稳定性
speeds = [2000, 4000, 6000, 8000, 10000]
params_list = [
    {"spindle_rpm": s, "machine": "vmc_850", "tool": "endmill_d10"}
    for s in speeds
]

results = predict_stability_batch(params_list)

print("转速-极限切深关系:")
for speed, result in zip(speeds, results):
    print(f"  {speed:5d} rpm: {result['limit_depth']:6.2f} mm")
```

### 示例3：生成稳定性叶图

```python
from app.simulation.chatter.stability import (
    MachineParams,
    ToolParams,
    compute_stability_lobe,
)
import matplotlib.pyplot as plt

# 创建参数
machine = MachineParams(
    machine_id="vmc_850",
    stiffness_z=2.0e7,
    damping_ratio=0.05,
    natural_freq=800.0,
)

tool = ToolParams(
    tool_id="endmill_d10",
    diameter=10.0,
    cutting_force_coeff=2000.0,
)

# 计算稳定性叶图
lobe_data = compute_stability_lobe(
    machine=machine,
    tool=tool,
    speed_range=(1000, 10000),
    num_points=100,
    num_lobes=5,
)

# 绘制叶图
plt.figure(figsize=(12, 8))
colors = ['b', 'g', 'r', 'c', 'm']

for i, (speeds, depths) in enumerate(lobe_data["lobes"]):
    if i < len(colors):
        plt.plot(speeds, depths, color=colors[i], linewidth=2, label=f'扇叶 {i}')

plt.xlabel('主轴转速 (rpm)', fontsize=12)
plt.ylabel('极限切削深度 (mm)', fontsize=12)
plt.title('稳定性叶图 - VMC850 + Ø10立铣刀', fontsize=14)
plt.legend(fontsize=10)
plt.grid(True, alpha=0.3)
plt.tight_layout()
plt.savefig('stability_lobe.png', dpi=300)
plt.show()
```

### 示例4：自定义机床参数

```python
from app.simulation.chatter.stability import (
    MachineParams,
    ToolParams,
    ChatterParams,
    compute_stability_limit,
)

# 自定义机床参数
custom_machine = MachineParams(
    machine_id="custom_machine",
    stiffness_x=1.8e7,  # X向刚度
    stiffness_y=1.8e7,  # Y向刚度
    stiffness_z=2.5e7,  # Z向刚度
    damping_ratio=0.04,  # 阻尼比
    natural_freq=850.0,  # 固有频率
    modal_mass=45.0,     # 模态质量
)

# 自定义刀具参数
custom_tool = ToolParams(
    tool_id="custom_tool",
    diameter=12.0,
    num_flutes=4,
    helix_angle=35.0,
    cutting_force_coeff=2100.0,
)

# 创建计算参数
params = ChatterParams(
    spindle_rpm=7500.0,
    machine=custom_machine,
    tool=custom_tool,
)

# 计算极限切深
limit_depth = compute_stability_limit(params)
print(f"自定义参数下的极限切削深度: {limit_depth:.2f} mm")
```

## 常见问题解答

### Q1: 为什么神经网络预测回退到解析法？

**A:** 可能的原因：
1. PyTorch未安装：`pip install torch`
2. 模型检查点文件不存在：需要训练模型并保存到 `python/app/simulation/chatter/checkpoints/chatter_model.pt`
3. 模型加载失败：检查模型文件格式是否正确

系统会自动回退到解析法，不影响功能使用。

### Q2: 如何训练神经网络模型？

**A:** 当前版本未包含训练脚本。建议：
1. 使用解析法生成训练数据
2. 构建神经网络模型进行监督学习
3. 保存模型到指定路径

### Q3: 解析法和神经网络法的结果不一致？

**A:** 这是正常现象。两种方法基于不同的原理：
- **解析法**：基于Tlusty理论公式，计算精确但较慢
- **神经网络**：基于训练数据学习，速度快但存在近似误差

验收标准要求两种方法的误差在±5%范围内。

### Q4: 如何添加新的机床或刀具？

**A:** 
1. **添加机床**：在 `python/app/database/data/machines.json` 中添加机床配置
2. **添加刀具**：在 `stability.py` 的 `DEFAULT_TOOL_PARAMS` 字典中添加刀具参数

### Q5: 极限切深计算结果为负数？

**A:** 这通常表示FRF实部为负，系统会自动取绝对值。如果频繁出现，建议检查：
1. 机床参数是否合理
2. 主轴转速是否在合理范围内
3. 阻尼比是否在 (0, 1) 范围内

### Q6: 性能测试不满足50ms要求？

**A:** 可能的优化方法：
1. 确保使用神经网络方法（比解析法快）
2. 减少输入特征维度
3. 使用模型量化或剪枝
4. 在GPU上运行推理

## 模块结构

```
python/app/simulation/chatter/
├── __init__.py              # 模块初始化，导出关键接口
├── stability.py             # 解析法实现（Tlusty公式）
├── predictor.py             # 神经网络预测实现
├── checkpoints/             # 模型检查点目录（需手动创建）
│   └── chatter_model.pt     # 训练好的模型文件
└── tests/                   # 单元测试目录
    ├── __init__.py
    ├── test_stability.py    # 解析法测试
    └── test_predictor.py    # 神经网络测试
```

## 测试与验证

### 运行单元测试

```bash
cd python
pytest app/simulation/chatter/tests/ -v
```

### 运行验收测试

```bash
# 测试1：推理功能测试
cd python && python -c "
from app.simulation.chatter.predictor import predict_stability
result = predict_stability(spindle_rpm=8000, machine='vmc_850', tool='endmill_d10', workpiece='aluminum')
print(result)
"

# 测试2：单元测试验证
cd python && pytest app/simulation/chatter/tests/ -v
```

## 技术限制

1. **不支持时域仿真**：本模块仅提供频域稳定性预测
2. **不支持多模态耦合**：当前仅考虑单自由度系统
3. **神经网络依赖PyTorch**：未安装PyTorch时自动回退到解析法
4. **参数范围限制**：主轴转速、刚度等参数需在合理范围内

## 参考文献

1. Tlusty, J., & Polacek, M. (1963). The stability of the machine tool against self-excited vibration in machining.
2. Altintas, Y. (2012). Manufacturing Automation: Metal Cutting Mechanics, Machine Tool Vibrations, and CNC Design. Cambridge University Press.
3. Schmitz, T. L., & Smith, K. S. (2019). Machining Dynamics: Frequency Response to Improved Productivity. Springer.
