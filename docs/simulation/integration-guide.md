# 仿真器与工艺规划集成指南

## 1. 概述

本模块实现仿真服务（切削力预测 + 颤振稳定性分析）与工艺规划流水线的无缝集成，使工艺规划过程能够自动调用仿真服务，并将仿真结果作为方案评估的关键依据。

## 2. 架构设计

```
工艺规划流水线 (pipeline.py)
│
├── Stage 1: 输入验证
├── Stage 2: 孔特征识别
├── Stage 3: 知识库查询
├── Stage 4: 工序规划
├── Stage 4.5: 仿真验证 ← sim_integration.py
│   ├── 切削力预测 (M2.1)
│   ├── 颤振稳定性分析 (M2.2)
│   ├── 综合评分
│   └── 方案推荐
├── Stage 5: G代码生成
└── Stage 6: 结果验证
```

## 3. 核心接口

### 3.1 SimulationResult 数据结构

```python
@dataclass
class SimulationResult:
    status: str           # 'success' | 'timeout' | 'failed' | 'not_run'
    passed: bool          # 仿真是否通过
    score: float          # 综合评分 (0-100)
    recommendation: str   # 'recommended' | 'acceptable' | 'not_recommended'
    cutting_force: dict   # 切削力预测结果 {Fx, Fy, Fz, method}
    chatter_stability: dict  # 颤振稳定性结果 {stable, limit_depth, method}
    duration_ms: float    # 仿真耗时(毫秒)
    error_message: str    # 错误信息
```

### 3.2 SimulationIntegration 类

```python
from app.process_planning.sim_integration import SimulationIntegration

simulator = SimulationIntegration(timeout_seconds=5.0)
result = simulator.run_simulation(
    material="45steel",
    tool="endmill_d10",
    spindle_rpm=8000,
    feed_rate=1200,
    depth_of_cut=2.0,
    machine="vmc_850",
)
```

### 3.3 便捷函数

```python
from app.process_planning.sim_integration import run_simulation_for_operation

result = run_simulation_for_operation(
    operation={"tool": "endmill_d10", "spindle_rpm": 8000, ...},
    material="45steel",
    timeout_seconds=5.0,
)
```

### 3.4 工艺规划便捷函数

```python
from app.process_planning.pipeline import plan_process

plan = plan_process(
    feature="pocket_cavity",
    material="45steel",
    tool="endmill_d10",
)
# plan["simulation"] 包含仿真评分和推荐结果
```

## 4. 评分规则

### 4.1 切削力评分 (权重 40%)

| 方向 | 阈值 | 说明 |
|------|------|------|
| Fx (进给力) | 500 N | 超过后按比例扣分 |
| Fy (径向力) | 400 N | 超过后按比例扣分 |
| Fz (主切削力) | 600 N | 超过后按比例扣分 |

### 4.2 颤振稳定性评分 (权重 60%)

| 条件 | 评分 |
|------|------|
| 稳定且极限切深/实际切深 > 2.0 | 100 |
| 稳定且比值 > 1.5 | 90 |
| 稳定且比值 > 1.0 | 75 |
| 稳定但比值 < 1.0 | 50 |
| 不稳定 | 20 |

### 4.3 通过条件

- 切削力在安全范围内（允许 10% 余量）
- 颤振稳定 或 极限切深 >= 实际切深

### 4.4 推荐级别

| 条件 | 推荐级别 |
|------|----------|
| passed=False | not_recommended |
| score >= 80 | recommended |
| score >= 60 | acceptable |
| score < 60 | not_recommended |

## 5. 降级机制

仿真服务失败时，工艺规划流程仍能继续执行：

- **超时处理**: 默认 5 秒超时，超时后标记为 `timeout` 状态
- **异常处理**: 仿真服务抛出异常时，返回降级结果（score=0, passed=False）
- **主流程不阻断**: 仿真失败不影响 G 代码生成等后续阶段

## 6. 文件结构

```
python/app/process_planning/
├── sim_integration.py          # 仿真集成核心逻辑
├── pipeline.py                 # 工艺规划流水线（含仿真阶段）
└── tests/
    └── test_sim_integration.py # 单元测试

docs/simulation/
├── integration-guide.md        # 本文档
├── cutting-force-usage.md      # 切削力模块文档
└── chatter-usage.md            # 颤振稳定性模块文档
```

## 7. 测试

### 单元测试

```bash
cd python && pytest app/process_planning/tests/test_sim_integration.py -v
```

### 端到端测试

```bash
cd python && python -c "
from app.process_planning.pipeline import plan_process
plan = plan_process(feature='pocket_cavity', material='45steel', tool='endmill_d10')
assert 'simulation' in plan, '工艺规划结果中未包含仿真数据'
assert 'score' in plan['simulation'], '仿真数据中缺少评分字段'
print('端到端测试通过')
"
```
