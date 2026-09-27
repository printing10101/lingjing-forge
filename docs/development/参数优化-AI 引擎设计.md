# 参数优化闭环设计文档（Phase D）

**文档版本**: 1.0  
**创建日期**: 2026-08-20  
**状态**: 设计完成，待 Phase A/B/C 数据就绪后实施

---

## 目标

利用 cutting_experience 数据飞轮积累的实测数据，训练 LNN 参数推荐引擎，
实现「推荐参数 → 加工 → 实测 → 再优化」的闭环，达成：

- 切削效率提升 ≥10%（节拍缩短）
- 刀具寿命延长 ≥15%（磨损降低）
- 加工误差 <0.05mm（质量稳定）

---

## 闭环架构

```
┌─────────────────────────────────────────────────────────┐
│                    参数优化闭环（4 周）                    │
│                                                         │
│  cutting_experiences 表 ──► 特征工程 ──► LNN 推荐引擎    │
│        ▲                              │                │
│        │                              ▼                │
│  采集 API ◄── 现场加工 ── 推荐参数 ──► 参数推荐          │
│        │                              │                │
│        └──── 实测结果回流 ─────────────┘                │
└─────────────────────────────────────────────────────────┘
```

---

## 模块设计

### 1. 特征工程（纯 Python，零框架依赖）

```
app/optimizer/
├── __init__.py
├── features.py        # CuttingExperience → 特征向量（白盒）
├── baseline.py        # 基线参数库（材料×刀具 经验表）
├── recommender.py     # 推荐器接口 + 规则回退
├── evaluator.py       # 推荐效果评估（节拍/磨损对比）
└── ab_test.py         # A/B 实验框架（分组/显著性）
```

### 2. 推荐引擎策略（分层）

| 层 | 策略 | 数据要求 | 状态 |
|---|---|---|---|
| L0 | 规则基线（材料-刀具经验表） | 无 | 可用 |
| L1 | 统计回归（线性/Ridge） | ≥50 条记录 | 待数据 |
| L2 | LNN 模型（既有 cutting_force_v1） | ≥200 条记录 | 待数据 |
| L3 | 贝叶斯优化（在线探索） | 连续反馈 | 待数据 |

**设计原则**：数据不足时自动降级到低层策略（优雅降级），
避免"模型不可用 → 系统不可用"。

---

## 核心 API

```python
# app/optimizer/recommender.py

@dataclass
class Recommendation:
    parameters: CuttingParameters   # 推荐参数
    strategy: str                   # L0/L1/L2/L3
    confidence: float               # 置信度 0-1
    basis: list[dict]               # 依据（相似历史案例）

class ParameterRecommender:
    """分层参数推荐器。"""
    
    def __init__(self, repo):  # cutting_experience_repository
        ...
    
    async def recommend(
        self,
        material: str,
        tool_id: str,
        machining_type: MachiningType,
        target: OptimizationTarget,   # 节拍优先/寿命优先/均衡
    ) -> Recommendation:
        """推荐切削参数。
        
        策略选择：
        1. L2/L3 可用 → 模型推荐
        2. 否则 L1 统计（同材料同刀具均值）
        3. 否则 L0 经验表
        """
        ...
    
    async def evaluate(
        self,
        recommendation_id: str,
        actual: CuttingExperience,
    ) -> float:
        """评估推荐效果（0-1 得分）。"""
        ...
```

---

## A/B 测试框架

```python
# app/optimizer/ab_test.py

class ABTestExperiment:
    """A/B 实验：对照组（基线参数）vs 实验组（推荐参数）。"""
    
    def __init__(self, name: str, min_sample: int = 10):
        ...
    
    async def assign(self, job_id: str) -> str:
        """分配组别：control / treatment（随机 + 分层）。"""
        ...
    
    async def record_result(self, job_id: str, experience: CuttingExperience):
        """记录实验结果。"""
        ...
    
    def significance(self) -> float:
        """t 检验 p 值（<0.05 视为显著）。"""
        ...
```

**自动终止**：实验组累计 20 个样本且 p<0.05 时自动判定胜者，
胜者参数写入基线库（L0 更新）。

---

## 成功指标

| 指标 | 目标 | 测量方式 |
|---|---|---|
| 节拍提升 | ≥10% | 推荐组 vs 基线组平均 cycle_time |
| 刀具寿命 | ≥15% | tool_wear_percent 对比 |
| 误差控制 | <0.05mm | dimensional_error_mm |
| 推荐采纳率 | ≥60% | 推荐参数被现场采用比例 |
| 模型覆盖率 | ≥70% | 非 L0 策略推荐占比 |

---

## 实施步骤

### Week 1-2: 推荐引擎
- [ ] features.py 特征工程（材料/刀具/几何 → 特征向量）
- [ ] baseline.py 基线经验表（从 cutting_params_db 迁移）
- [ ] recommender.py 分层推荐器
- [ ] evaluator.py 效果评估

### Week 3: A/B 测试
- [ ] ab_test.py 实验框架
- [ ] 前端「推荐参数」按钮 + 结果对比面板
- [ ] API：POST /optimizer/recommend / POST /optimizer/experiment

### Week 4: 真实案例
- [ ] 1-2 个真实工件跑通闭环
- [ ] 性能对比报告
- [ ] 视频演示

---

## 安全与可靠性

1. **参数钳制**：推荐参数必须落在物理安全区间
   （深度 ≤ 刀具最大切深 ×0.8，转速 ≤ 机床额定转速 ×0.9）
2. **人工确认**：推荐参数不直接下发机床，需操作员确认
3. **数据隔离**：A/B 实验组数据带 experiment 标记，不污染训练集
4. **审计**：推荐/采纳/结果全链路审计日志

---

## 变更日志

### v1.0 (2026-08-20)
- 初始设计版本
- 定义分层推荐策略（L0-L3）与优雅降级
- 定义 A/B 测试框架与自动终止
